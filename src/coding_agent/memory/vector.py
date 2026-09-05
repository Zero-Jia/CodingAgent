"""记忆向量索引（B1-2）。

复用 ``semantic/milvus.py`` 的 PyMilvus 模式，为 promoted 记忆建立独立
collection，供 B1-5 recall 做语义召回。

schema：
- ``memory_id``：主键，与 ``memories.memory_id`` 一致
- ``user_id`` / ``project_id``：召回过滤维度
- ``scope`` / ``category``：metadata，便于后续按 scope 过滤
- ``content``：embedding 源文本（VARCHAR，截断到预览长度）
- ``embedding``：FLOAT_VECTOR，COSINE 度量

只索引 promoted 记忆由调用方（B1-5 / CLI）保证，index 层不校验状态。
"""

from __future__ import annotations

import asyncio
import importlib
import math
from collections.abc import Sequence
from typing import Any

from coding_agent.memory.contracts import (
    MemoryRecord,
    MemoryVectorHit,
)
from coding_agent.tracing.store import redact

# Milvus VARCHAR max_length 按 UTF-8 字节计；记忆 content 可能含中文，留足余量。
CONTENT_FIELD_MAX_BYTES = 8_192
CONTENT_PREVIEW_CHARS = 2_000


class MilvusMemoryVectorIndex:
    """PyMilvus 实现的记忆向量索引。"""

    backend_name = "milvus"

    def __init__(
        self,
        *,
        uri: str,
        token: str = "",
        database: str = "default",
        collection_name: str,
    ) -> None:
        self.uri = uri
        self.token = token
        self.database = database or "default"
        self.collection_name = collection_name

    async def ensure_collection(self, dimension: int) -> None:
        await asyncio.to_thread(self._ensure_collection_sync, dimension)

    async def upsert(
        self,
        records: Sequence[MemoryRecord],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        await asyncio.to_thread(
            self._upsert_sync,
            list(records),
            [list(vector) for vector in vectors],
        )

    async def search(
        self,
        vector: Sequence[float],
        *,
        user_id: str,
        project_id: str,
        top_k: int,
    ) -> list[MemoryVectorHit]:
        return await asyncio.to_thread(
            self._search_sync,
            list(vector),
            user_id,
            project_id,
            top_k,
        )

    def _client(self) -> Any:
        try:
            pymilvus: Any = importlib.import_module("pymilvus")
        except ImportError as error:
            raise RuntimeError(
                "pymilvus is not installed; run `uv --cache-dir .uv-cache sync`"
            ) from error
        milvus_client = pymilvus.MilvusClient
        kwargs: dict[str, object] = {"uri": self.uri}
        if self.token:
            kwargs["token"] = self.token
        if self.database and self.database != "default":
            kwargs["db_name"] = self.database
        try:
            return milvus_client(**kwargs)
        except Exception as error:
            raise RuntimeError(
                _safe_error("failed to connect to Milvus", error, self.token)
            ) from error

    def _ensure_collection_sync(self, dimension: int) -> None:
        client = self._client()
        try:
            if client.has_collection(self.collection_name):
                description = client.describe_collection(self.collection_name)
                existing = _embedding_dimension(description)
                if existing is not None and existing != dimension:
                    raise RuntimeError(
                        f"Milvus memory collection embedding dimension is {existing}, "
                        f"expected {dimension}"
                    )
                client.load_collection(self.collection_name)
                return
            self._create_collection(client, dimension)
            client.load_collection(self.collection_name)
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError(
                _safe_error("failed to initialize Milvus memory collection", error, self.token)
            ) from error

    def _create_collection(self, client: Any, dimension: int) -> None:
        try:
            pymilvus: Any = importlib.import_module("pymilvus")
        except ImportError as error:
            raise RuntimeError(
                "pymilvus is not installed; run `uv --cache-dir .uv-cache sync`"
            ) from error
        data_type = pymilvus.DataType
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("memory_id", data_type.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("user_id", data_type.VARCHAR, max_length=64)
        schema.add_field("project_id", data_type.VARCHAR, max_length=256)
        schema.add_field("scope", data_type.VARCHAR, max_length=32)
        schema.add_field("category", data_type.VARCHAR, max_length=32)
        schema.add_field("content", data_type.VARCHAR, max_length=CONTENT_FIELD_MAX_BYTES)
        schema.add_field("embedding", data_type.FLOAT_VECTOR, dim=dimension)
        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=index_params,
        )

    def _upsert_sync(
        self, records: list[MemoryRecord], vectors: list[list[float]]
    ) -> None:
        if len(records) != len(vectors):
            raise RuntimeError("records and vectors length mismatch")
        if not records:
            return
        client = self._client()
        rows = [
            _memory_row(record, vector)
            for record, vector in zip(records, vectors, strict=True)
        ]
        try:
            client.upsert(collection_name=self.collection_name, data=rows)
        except Exception as error:
            raise RuntimeError(
                _safe_error("failed to upsert Milvus memory rows", error, self.token)
            ) from error

    def _search_sync(
        self,
        vector: list[float],
        user_id: str,
        project_id: str,
        top_k: int,
    ) -> list[MemoryVectorHit]:
        client = self._client()
        # Milvus filter 表达式：字符串值需用双引号包裹。
        expr = (
            f'user_id == "{_escape_filter(user_id)}" '
            f'&& project_id == "{_escape_filter(project_id)}"'
        )
        try:
            results = client.search(
                collection_name=self.collection_name,
                data=[vector],
                anns_field="embedding",
                limit=top_k,
                filter=expr,
                output_fields=["memory_id", "content"],
            )
        except Exception as error:
            raise RuntimeError(
                _safe_error("failed to search Milvus memory collection", error, self.token)
            ) from error
        return _hits_from_results(results)


class InMemoryMemoryVectorIndex:
    """确定性内存向量索引，用于单元测试。"""

    backend_name = "memory"

    def __init__(self, collection_name: str = "memories") -> None:
        self.collection_name = collection_name
        self._items: dict[str, tuple[MemoryRecord, list[float]]] = {}
        self._dimension = 0

    async def ensure_collection(self, dimension: int) -> None:
        self._dimension = dimension

    async def upsert(
        self,
        records: Sequence[MemoryRecord],
        vectors: Sequence[Sequence[float]],
    ) -> None:
        if len(records) != len(vectors):
            raise ValueError("records and vectors length mismatch")
        for record, vector in zip(records, vectors, strict=True):
            values = [float(item) for item in vector]
            if self._dimension and len(values) != self._dimension:
                raise ValueError("vector dimension mismatch")
            self._items[record.memory_id] = (record, values)

    async def search(
        self,
        vector: Sequence[float],
        *,
        user_id: str,
        project_id: str,
        top_k: int,
    ) -> list[MemoryVectorHit]:
        query = [float(item) for item in vector]
        hits = [
            MemoryVectorHit(
                memory_id=record.memory_id,
                content=record.content,
                score=_cosine(query, stored),
            )
            for record, stored in self._items.values()
            if record.user_id == user_id and record.project_id == project_id
        ]
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]


def _memory_row(record: MemoryRecord, vector: list[float]) -> dict[str, object]:
    return {
        "memory_id": record.memory_id,
        "user_id": record.user_id,
        "project_id": record.project_id,
        "scope": record.scope,
        "category": record.category,
        "content": record.content[:CONTENT_PREVIEW_CHARS],
        "embedding": vector,
    }


def _hits_from_results(results: object) -> list[MemoryVectorHit]:
    if not isinstance(results, list) or not results:
        return []
    hits: list[MemoryVectorHit] = []
    for item in results[0]:
        entity = _entity(item)
        if not entity:
            continue
        hits.append(
            MemoryVectorHit(
                memory_id=str(entity.get("memory_id", "")),
                content=str(entity.get("content", "")),
                score=float(_score(item)),
            )
        )
    return hits


def _entity(item: object) -> dict[str, object]:
    if isinstance(item, dict):
        entity = item.get("entity") or item.get("fields") or item
        if isinstance(entity, dict):
            return {str(key): value for key, value in entity.items()}
        return {}
    entity = getattr(item, "entity", None)
    if isinstance(entity, dict):
        return {str(key): value for key, value in entity.items()}
    return {}


def _score(item: object) -> float:
    if isinstance(item, dict):
        for key in ("distance", "score"):
            value = item.get(key)
            if isinstance(value, int | float):
                return float(value)
    value = getattr(item, "distance", getattr(item, "score", 0.0))
    return float(value) if isinstance(value, int | float) else 0.0


def _embedding_dimension(description: object) -> int | None:
    fields = description.get("fields") if isinstance(description, dict) else None
    if not isinstance(fields, list):
        return None
    for field in fields:
        if not isinstance(field, dict) or field.get("name") != "embedding":
            continue
        params = field.get("params")
        if isinstance(params, dict) and "dim" in params:
            return int(params["dim"])
    return None


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _escape_filter(value: str) -> str:
    """转义 Milvus filter 表达式中的双引号与反斜杠。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _safe_error(prefix: str, error: Exception, token: str) -> str:
    message = str(error)
    if token:
        message = message.replace(token, "***")
    redacted = redact(message)
    return f"{prefix}: {redacted}"


__all__ = ["InMemoryMemoryVectorIndex", "MilvusMemoryVectorIndex"]

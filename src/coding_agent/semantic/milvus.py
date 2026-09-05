"""Milvus-backed vector index for real workspace semantic search."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Sequence
from typing import Any

from coding_agent.semantic.contracts import (
    CodeChunk,
    SemanticIndexError,
    SemanticSearchHit,
    VectorIndex,
)
from coding_agent.tracing.store import redact

MAX_TEXT_PREVIEW_CHARS = 2_000
# Milvus measures VARCHAR max_length in UTF-8 bytes, not Python characters.
# A 2_000-char preview can reach 8_000 bytes for CJK/emoji, so allow headroom.
CONTENT_FIELD_MAX_BYTES = 8_192


class MilvusVectorIndex(VectorIndex):
    """PyMilvus implementation used by production semantic search."""

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

    async def upsert(self, chunks: Sequence[CodeChunk], vectors: Sequence[Sequence[float]]) -> None:
        await asyncio.to_thread(
            self._upsert_sync,
            list(chunks),
            [list(vector) for vector in vectors],
        )

    async def search(
        self, vector: Sequence[float], *, workspace_id: str, top_k: int
    ) -> list[SemanticSearchHit]:
        return await asyncio.to_thread(
            self._search_sync,
            list(vector),
            workspace_id,
            top_k,
        )

    def _client(self) -> Any:
        try:
            pymilvus: Any = importlib.import_module("pymilvus")
        except ImportError as error:
            raise SemanticIndexError(
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
            raise SemanticIndexError(
                _safe_error("failed to connect to Milvus", error, self.token)
            ) from error

    def _ensure_collection_sync(self, dimension: int) -> None:
        client = self._client()
        try:
            if client.has_collection(self.collection_name):
                description = client.describe_collection(self.collection_name)
                existing = _embedding_dimension(description)
                if existing is not None and existing != dimension:
                    raise SemanticIndexError(
                        f"Milvus collection embedding dimension is {existing}, expected {dimension}"
                    )
                client.load_collection(self.collection_name)
                return
            self._create_collection(client, dimension)
            client.load_collection(self.collection_name)
        except SemanticIndexError:
            raise
        except Exception as error:
            raise SemanticIndexError(
                _safe_error("failed to initialize Milvus collection", error, self.token)
            ) from error

    def _create_collection(self, client: Any, dimension: int) -> None:
        try:
            pymilvus: Any = importlib.import_module("pymilvus")
        except ImportError as error:
            raise SemanticIndexError(
                "pymilvus is not installed; run `uv --cache-dir .uv-cache sync`"
            ) from error
        data_type = pymilvus.DataType
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", data_type.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("workspace_id", data_type.VARCHAR, max_length=64)
        schema.add_field("path", data_type.VARCHAR, max_length=1024)
        schema.add_field("language", data_type.VARCHAR, max_length=64)
        schema.add_field("symbol", data_type.VARCHAR, max_length=256)
        schema.add_field("start_line", data_type.INT64)
        schema.add_field("end_line", data_type.INT64)
        schema.add_field("content_hash", data_type.VARCHAR, max_length=64)
        schema.add_field("file_hash", data_type.VARCHAR, max_length=64)
        schema.add_field(
            "content", data_type.VARCHAR, max_length=CONTENT_FIELD_MAX_BYTES
        )
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

    def _upsert_sync(self, chunks: list[CodeChunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise SemanticIndexError("chunks and vectors length mismatch")
        if not chunks:
            return
        client = self._client()
        rows = [_row(chunk, vector) for chunk, vector in zip(chunks, vectors, strict=True)]
        try:
            client.upsert(collection_name=self.collection_name, data=rows)
        except Exception as error:
            raise SemanticIndexError(
                _safe_error("failed to upsert Milvus rows", error, self.token)
            ) from error

    def _search_sync(
        self, vector: list[float], workspace_id: str, top_k: int
    ) -> list[SemanticSearchHit]:
        client = self._client()
        try:
            results = client.search(
                collection_name=self.collection_name,
                data=[vector],
                anns_field="embedding",
                limit=top_k,
                filter=f'workspace_id == "{workspace_id}"',
                output_fields=[
                    "chunk_id",
                    "workspace_id",
                    "path",
                    "language",
                    "symbol",
                    "start_line",
                    "end_line",
                    "content_hash",
                    "file_hash",
                    "content",
                ],
            )
        except Exception as error:
            raise SemanticIndexError(
                _safe_error("failed to search Milvus", error, self.token)
            ) from error
        return _hits_from_results(results)


def _row(chunk: CodeChunk, vector: list[float]) -> dict[str, object]:
    return {
        "chunk_id": chunk.chunk_id,
        "workspace_id": chunk.workspace_id,
        "path": chunk.path,
        "language": chunk.language,
        "symbol": chunk.symbol[:256],
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "content_hash": chunk.content_hash,
        "file_hash": chunk.file_hash,
        "content": chunk.content[:MAX_TEXT_PREVIEW_CHARS],
        "embedding": vector,
    }


def _hits_from_results(results: object) -> list[SemanticSearchHit]:
    if not isinstance(results, list) or not results:
        return []
    hits: list[SemanticSearchHit] = []
    for item in results[0]:
        entity = _entity(item)
        if not entity:
            continue
        hits.append(
            SemanticSearchHit(
                chunk=CodeChunk(
                    chunk_id=str(entity.get("chunk_id", "")),
                    workspace_id=str(entity.get("workspace_id", "")),
                    path=str(entity.get("path", "")),
                    language=str(entity.get("language", "")),
                    symbol=str(entity.get("symbol", "")),
                    start_line=_int(entity.get("start_line", 0)),
                    end_line=_int(entity.get("end_line", 0)),
                    content=str(entity.get("content", "")),
                    content_hash=str(entity.get("content_hash", "")),
                    file_hash=str(entity.get("file_hash", "")),
                ),
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


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _safe_error(prefix: str, error: Exception, token: str) -> str:
    message = str(error)
    if token:
        message = message.replace(token, "***")
    redacted = redact(message)
    return f"{prefix}: {redacted}"

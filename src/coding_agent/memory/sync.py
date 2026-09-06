"""把 promoted 记忆批量同步到记忆向量索引（B1-2b，B1-6 前置）。

B1-5 recall 的向量通道依赖 Milvus collection 里有 promoted 记忆向量；
本模块提供离线批量同步：``store.list_promoted`` → embed → ``index.upsert``
（以 memory_id 为主键幂等覆盖）。设计为离线 CLI 使用（``agent sync-memories``）：
同步是批处理任务的核心逻辑（决策 5），错误直接抛出让调用方感知，
不像 runtime recall 那样静默降级。

注意：仅 upsert promoted 记忆；被 reject/expire 的记忆残留索引不影响正确性
——recall 会回查 ``store.get`` 校验状态后再注入。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from coding_agent.config import AgentConfig
from coding_agent.memory.contracts import MemoryRecord, MemoryStore, MemoryVectorIndex
from coding_agent.memory.vector import MilvusMemoryVectorIndex
from coding_agent.semantic.contracts import EmbeddingProvider, SemanticIndexError
from coding_agent.semantic.embeddings import DashScopeEmbeddingProvider

_SYNC_BATCH_SIZE = 20


@dataclass(frozen=True)
class MemorySyncStats:
    """一次同步的结果统计。"""

    synced: int
    backend: str
    collection: str


class MemorySyncService:
    """把 promoted 记忆批量写入记忆向量索引（幂等，可重复执行）。"""

    def __init__(
        self,
        *,
        store: MemoryStore,
        vector_index: MemoryVectorIndex,
        embedder: EmbeddingProvider,
        batch_size: int = _SYNC_BATCH_SIZE,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._store = store
        self._vector_index = vector_index
        self._embedder = embedder
        self._batch_size = batch_size

    async def sync(self, *, user_id: str, project_id: str) -> MemorySyncStats:
        records = await self._store.list_promoted(user_id=user_id, project_id=project_id)
        if not records:
            return MemorySyncStats(
                synced=0,
                backend=self._vector_index.backend_name,
                collection=self._vector_index.collection_name,
            )
        await self._vector_index.ensure_collection(self._embedder.dimension)
        synced = 0
        for batch in _batches(records, self._batch_size):
            vectors = await self._embedder.embed_texts([record.content for record in batch])
            await self._vector_index.upsert(batch, vectors)
            synced += len(batch)
        return MemorySyncStats(
            synced=synced,
            backend=self._vector_index.backend_name,
            collection=self._vector_index.collection_name,
        )


def create_memory_sync_service(
    config: AgentConfig, store: MemoryStore
) -> MemorySyncService:
    """按配置装配同步服务；semantic 未启用或缺 DashScope key 时抛 ``SemanticIndexError``。"""
    if config.semantic_backend != "milvus":
        raise SemanticIndexError(
            "semantic backend is disabled; set CODING_AGENT_SEMANTIC_BACKEND=milvus"
        )
    if config.dashscope_api_key is None:
        raise SemanticIndexError("DASHSCOPE_API_KEY is required for memory sync")
    token = config.milvus_token.get_secret_value() if config.milvus_token is not None else ""
    vector_index = MilvusMemoryVectorIndex(
        uri=config.milvus_uri,
        token=token,
        database=config.milvus_database,
        collection_name=config.milvus_memory_collection,
    )
    embedder = DashScopeEmbeddingProvider(
        api_key=config.dashscope_api_key.get_secret_value(),
        base_url=config.dashscope_base_url,
        model=config.dashscope_embedding_model,
        dimension=config.dashscope_embedding_dimensions,
        batch_size=config.dashscope_embedding_batch_size,
    )
    return MemorySyncService(store=store, vector_index=vector_index, embedder=embedder)


def _batches(records: Sequence[MemoryRecord], size: int) -> list[list[MemoryRecord]]:
    return [list(records[index : index + size]) for index in range(0, len(records), size)]


__all__ = [
    "MemorySyncService",
    "MemorySyncStats",
    "create_memory_sync_service",
]

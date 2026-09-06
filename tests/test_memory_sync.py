"""promoted 记忆向量索引同步测试（B1-2b）。

覆盖：
- MemorySyncService.sync：upsert promoted 记录、user/project 过滤透传、
  空结果短路（不触达索引）、幂等重跑、batch_size 分批、embedding 失败传播
- create_memory_sync_service：semantic 未启用 / 缺 key 报错、milvus+key 装配成功

不依赖真实 DashScope / Milvus / MySQL。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from coding_agent.config import AgentConfig
from coding_agent.memory.contracts import MemoryRecord, MemoryStatus
from coding_agent.memory.sync import MemorySyncService, create_memory_sync_service
from coding_agent.memory.vector import InMemoryMemoryVectorIndex
from coding_agent.semantic.contracts import SemanticIndexError


def _record(
    memory_id: str,
    *,
    content: str = "always run ruff before commit",
    user_id: str = "user-1",
    project_id: str = "proj-1",
    status: str = MemoryStatus.PROMOTED,
) -> MemoryRecord:
    now = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        project_id=project_id,
        scope="project",
        category="convention",
        content=content,
        source_session_id="session-1",
        confidence=0.9,
        status=status,
        created_at=now,
        updated_at=now,
    )


class FakePromotedStore:
    """只实现 list_promoted 的 store，记录调用参数。"""

    def __init__(self, records: list[MemoryRecord] | None = None) -> None:
        self._records = records or []
        self.calls: list[tuple[str, str]] = []

    async def list_promoted(
        self, *, user_id: str, project_id: str, scope: str | None = None
    ) -> list[MemoryRecord]:
        self.calls.append((user_id, project_id))
        return [
            record
            for record in self._records
            if record.user_id == user_id and record.project_id == project_id
        ]


class RecordingEmbedder:
    """确定性 embedding，记录每次调用批大小。"""

    dimension = 2

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        return [[1.0, 0.0] for _ in texts]


class FailingEmbedder:
    dimension = 2

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("embedding unavailable")


class TrackingIndex(InMemoryMemoryVectorIndex):
    """记录 ensure/upsert 调用的内存索引。"""

    def __init__(self) -> None:
        super().__init__()
        self.ensure_calls = 0
        self.upsert_batches: list[int] = []

    async def ensure_collection(self, dimension: int) -> None:
        self.ensure_calls += 1
        await super().ensure_collection(dimension)

    async def upsert(self, records: Any, vectors: Any) -> None:
        self.upsert_batches.append(len(list(records)))
        await super().upsert(records, vectors)


def _service(
    store: FakePromotedStore,
    index: TrackingIndex,
    embedder: RecordingEmbedder | FailingEmbedder,
    *,
    batch_size: int = 20,
) -> MemorySyncService:
    return MemorySyncService(
        store=store, vector_index=index, embedder=embedder, batch_size=batch_size
    )


@pytest.mark.asyncio
async def test_sync_upserts_promoted_records() -> None:
    records = [_record("mem-1"), _record("mem-2", content="use rg for symbols")]
    store = FakePromotedStore(records)
    index = TrackingIndex()
    embedder = RecordingEmbedder()

    stats = await _service(store, index, embedder).sync(
        user_id="user-1", project_id="proj-1"
    )

    assert stats.synced == 2
    assert stats.backend == "memory"
    assert stats.collection == InMemoryMemoryVectorIndex().collection_name
    assert store.calls == [("user-1", "proj-1")]
    hits = await index.search(
        [1.0, 0.0], user_id="user-1", project_id="proj-1", top_k=10
    )
    assert {hit.memory_id for hit in hits} == {"mem-1", "mem-2"}


@pytest.mark.asyncio
async def test_sync_filters_by_user_and_project() -> None:
    records = [
        _record("mem-1"),
        _record("mem-other-user", user_id="user-2"),
        _record("mem-other-project", project_id="proj-2"),
    ]
    store = FakePromotedStore(records)
    index = TrackingIndex()

    stats = await _service(store, index, RecordingEmbedder()).sync(
        user_id="user-1", project_id="proj-1"
    )

    assert stats.synced == 1
    hits = await index.search(
        [1.0, 0.0], user_id="user-1", project_id="proj-1", top_k=10
    )
    assert [hit.memory_id for hit in hits] == ["mem-1"]


@pytest.mark.asyncio
async def test_sync_empty_promoted_short_circuits() -> None:
    index = TrackingIndex()

    stats = await _service(FakePromotedStore(), index, RecordingEmbedder()).sync(
        user_id="user-1", project_id="proj-1"
    )

    assert stats.synced == 0
    assert index.ensure_calls == 0
    assert index.upsert_batches == []


@pytest.mark.asyncio
async def test_sync_is_idempotent_on_rerun() -> None:
    store = FakePromotedStore([_record("mem-1"), _record("mem-2")])
    index = TrackingIndex()
    service = _service(store, index, RecordingEmbedder())

    await service.sync(user_id="user-1", project_id="proj-1")
    await service.sync(user_id="user-1", project_id="proj-1")

    hits = await index.search(
        [1.0, 0.0], user_id="user-1", project_id="proj-1", top_k=10
    )
    assert {hit.memory_id for hit in hits} == {"mem-1", "mem-2"}


@pytest.mark.asyncio
async def test_sync_respects_batch_size() -> None:
    records = [_record(f"mem-{i}") for i in range(3)]
    store = FakePromotedStore(records)
    index = TrackingIndex()
    embedder = RecordingEmbedder()

    stats = await _service(store, index, embedder, batch_size=1).sync(
        user_id="user-1", project_id="proj-1"
    )

    assert stats.synced == 3
    assert embedder.calls == [1, 1, 1]
    assert index.upsert_batches == [1, 1, 1]


@pytest.mark.asyncio
async def test_sync_embedder_error_propagates() -> None:
    store = FakePromotedStore([_record("mem-1")])
    index = TrackingIndex()

    with pytest.raises(RuntimeError, match="embedding unavailable"):
        await _service(store, index, FailingEmbedder()).sync(
            user_id="user-1", project_id="proj-1"
        )


@pytest.mark.asyncio
async def test_sync_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        _service(FakePromotedStore(), TrackingIndex(), RecordingEmbedder(), batch_size=0)


def test_factory_requires_milvus_backend(tmp_path: Path) -> None:
    config = AgentConfig(workspace=tmp_path, semantic_backend="disabled")
    with pytest.raises(SemanticIndexError, match="semantic backend is disabled"):
        create_memory_sync_service(config, FakePromotedStore())


def test_factory_requires_dashscope_key(tmp_path: Path) -> None:
    config = AgentConfig(workspace=tmp_path, semantic_backend="milvus")
    with pytest.raises(SemanticIndexError, match="DASHSCOPE_API_KEY"):
        create_memory_sync_service(config, FakePromotedStore())


def test_factory_builds_service_for_milvus(tmp_path: Path) -> None:
    config = AgentConfig(
        workspace=tmp_path,
        semantic_backend="milvus",
        dashscope_api_key=SecretStr("sk-test"),
        milvus_memory_collection="custom_memories",
    )
    service = create_memory_sync_service(config, FakePromotedStore())
    assert isinstance(service, MemorySyncService)
    assert service._vector_index.collection_name == "custom_memories"
    assert service._embedder.dimension == config.dashscope_embedding_dimensions

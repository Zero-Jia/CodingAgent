"""B1-2 记忆向量索引测试。

覆盖 ``InMemoryMemoryVectorIndex``（Milvus 实现需真实服务，用环境变量门控）：
- upsert + search 基本语义召回
- search 按 user_id + project_id 过滤
- top_k 截断 + 余弦相似度降序
- upsert 同 memory_id 幂等覆盖
- dimension mismatch / 长度不匹配抛错
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from coding_agent.memory.contracts import MemoryRecord, MemoryStatus
from coding_agent.memory.vector import InMemoryMemoryVectorIndex


def _record(
    memory_id: str,
    *,
    content: str = "use ruff before commit",
    user_id: str = "user-1",
    project_id: str = "proj-1",
    scope: str = "project",
    category: str = "convention",
) -> MemoryRecord:
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        project_id=project_id,
        scope=scope,
        category=category,
        content=content,
        source_session_id="session-1",
        confidence=0.8,
        status=MemoryStatus.PROMOTED,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture()
def index() -> InMemoryMemoryVectorIndex:
    return InMemoryMemoryVectorIndex()


@pytest.mark.asyncio
async def test_upsert_and_search_returns_matching_memory(
    index: InMemoryMemoryVectorIndex,
) -> None:
    record = _record("mem-1", content="always run ruff before commit")
    vector = [1.0, 0.0, 0.0]
    await index.ensure_collection(3)
    await index.upsert([record], [vector])

    hits = await index.search(
        [1.0, 0.0, 0.0], user_id="user-1", project_id="proj-1", top_k=5
    )

    assert len(hits) == 1
    assert hits[0].memory_id == "mem-1"
    assert hits[0].content == "always run ruff before commit"
    assert hits[0].score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_search_filters_by_user_and_project(
    index: InMemoryMemoryVectorIndex,
) -> None:
    await index.ensure_collection(2)
    await index.upsert(
        [
            _record("mem-1", user_id="user-1", project_id="proj-1"),
            _record("mem-2", user_id="user-2", project_id="proj-1"),
            _record("mem-3", user_id="user-1", project_id="proj-2"),
        ],
        [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
    )

    hits = await index.search(
        [1.0, 0.0], user_id="user-1", project_id="proj-1", top_k=10
    )

    assert [hit.memory_id for hit in hits] == ["mem-1"]


@pytest.mark.asyncio
async def test_search_orders_by_cosine_similarity_desc(
    index: InMemoryMemoryVectorIndex,
) -> None:
    await index.ensure_collection(2)
    await index.upsert(
        [
            _record("mem-close", content="close match"),
            _record("mem-far", content="far match"),
        ],
        [[1.0, 0.0], [0.0, 1.0]],
    )

    hits = await index.search(
        [1.0, 0.0], user_id="user-1", project_id="proj-1", top_k=10
    )

    assert [hit.memory_id for hit in hits] == ["mem-close", "mem-far"]
    assert hits[0].score > hits[1].score


@pytest.mark.asyncio
async def test_search_respects_top_k(index: InMemoryMemoryVectorIndex) -> None:
    await index.ensure_collection(2)
    await index.upsert(
        [
            _record("mem-1"),
            _record("mem-2"),
            _record("mem-3"),
        ],
        [[1.0, 0.0], [0.9, 0.1], [0.5, 0.5]],
    )

    hits = await index.search(
        [1.0, 0.0], user_id="user-1", project_id="proj-1", top_k=2
    )

    assert len(hits) == 2


@pytest.mark.asyncio
async def test_upsert_same_memory_id_overwrites(
    index: InMemoryMemoryVectorIndex,
) -> None:
    await index.ensure_collection(2)
    await index.upsert([_record("mem-1", content="original")], [[1.0, 0.0]])
    await index.upsert([_record("mem-1", content="updated")], [[0.0, 1.0]])

    hits = await index.search(
        [0.0, 1.0], user_id="user-1", project_id="proj-1", top_k=5
    )

    assert len(hits) == 1
    assert hits[0].content == "updated"
    assert hits[0].score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_upsert_length_mismatch_raises(
    index: InMemoryMemoryVectorIndex,
) -> None:
    await index.ensure_collection(2)
    with pytest.raises(ValueError):
        await index.upsert([_record("mem-1")], [[1.0, 0.0], [0.0, 1.0]])


@pytest.mark.asyncio
async def test_upsert_dimension_mismatch_raises(
    index: InMemoryMemoryVectorIndex,
) -> None:
    await index.ensure_collection(2)
    with pytest.raises(ValueError):
        await index.upsert([_record("mem-1")], [[1.0, 0.0, 0.0]])


@pytest.mark.asyncio
async def test_search_returns_empty_when_no_records(
    index: InMemoryMemoryVectorIndex,
) -> None:
    await index.ensure_collection(2)
    hits = await index.search(
        [1.0, 0.0], user_id="user-1", project_id="proj-1", top_k=5
    )
    assert hits == []

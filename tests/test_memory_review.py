"""B1-4 人工审核服务测试。

覆盖 ``MemoryReviewService``：
- list_candidates 只返回 candidate 状态记忆
- promote / reject 正确流转状态并写入 reviewer / review_note / reviewed_at
- review 返回更新后的记录
- 重复审核已审核记忆抛 ReviewError
- 审核不存在记忆、非法目标状态、空 reviewer 抛 ReviewError
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, select

from coding_agent.db import initialize_database, tables
from coding_agent.memory.contracts import (
    MemoryRecord,
    MemoryStatus,
    NoopMemoryStore,
)
from coding_agent.memory.mysql import MySqlMemoryStore
from coding_agent.memory.review import MemoryReviewService, ReviewError


def _create_engine(database_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path.as_posix()}", future=True
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _ensure_session(engine: Engine, session_id: str) -> None:
    with engine.begin() as connection:
        existing = connection.execute(
            select(tables.sessions.c.session_id).where(
                tables.sessions.c.session_id == session_id
            )
        ).first()
        if existing is None:
            connection.execute(
                tables.sessions.insert().values(
                    session_id=session_id,
                    workspace="ws",
                    model_name="deepseek-chat",
                    created_at=datetime(2026, 9, 5, tzinfo=UTC),
                    updated_at=datetime(2026, 9, 5, tzinfo=UTC),
                )
            )


def _record(
    memory_id: str,
    *,
    user_id: str = "user-1",
    project_id: str = "proj-1",
    scope: str = "project",
    category: str = "convention",
    content: str = "always run ruff before commit",
    source_session_id: str = "session-1",
    source_run_id: str = "run-1",
    confidence: float = 0.7,
    status: str = MemoryStatus.CANDIDATE,
) -> MemoryRecord:
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        project_id=project_id,
        scope=scope,
        category=category,
        content=content,
        source_session_id=source_session_id,
        source_run_id=source_run_id,
        confidence=confidence,
        status=status,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture()
def memory_store(tmp_path: Path) -> MySqlMemoryStore:
    engine = _create_engine(tmp_path / "memory.db")
    initialize_database(engine)
    _ensure_session(engine, "session-1")
    return MySqlMemoryStore(engine)


@pytest.fixture()
def review_service(memory_store: MySqlMemoryStore) -> MemoryReviewService:
    return MemoryReviewService(memory_store)


@pytest.mark.asyncio
async def test_list_candidates_returns_only_candidate(
    review_service: MemoryReviewService,
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(_record("mem-1", content="candidate one"))
    await memory_store.store(
        _record("mem-2", content="already promoted", status=MemoryStatus.PROMOTED)
    )
    await memory_store.store(
        _record("mem-3", content="already rejected", status=MemoryStatus.REJECTED)
    )

    candidates = await review_service.list_candidates(
        user_id="user-1", project_id="proj-1"
    )

    assert [record.memory_id for record in candidates] == ["mem-1"]


@pytest.mark.asyncio
async def test_promote_changes_status_to_promoted(
    review_service: MemoryReviewService,
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(_record("mem-1", content="promote me"))

    updated = await review_service.promote(
        memory_id="mem-1", reviewer="alice", review_note="looks good"
    )

    assert updated.status == MemoryStatus.PROMOTED
    assert updated.reviewer == "alice"
    assert updated.review_note == "looks good"
    assert updated.reviewed_at is not None

    stored = await memory_store.get("mem-1")
    assert stored is not None
    assert stored.status == MemoryStatus.PROMOTED
    assert stored.reviewer == "alice"
    assert stored.reviewed_at is not None


@pytest.mark.asyncio
async def test_reject_changes_status_to_rejected(
    review_service: MemoryReviewService,
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(_record("mem-1", content="reject me"))

    updated = await review_service.reject(
        memory_id="mem-1", reviewer="bob", review_note="too vague"
    )

    assert updated.status == MemoryStatus.REJECTED
    assert updated.reviewer == "bob"
    assert updated.review_note == "too vague"
    assert updated.reviewed_at is not None


@pytest.mark.asyncio
async def test_reviewing_promoted_memory_raises(
    review_service: MemoryReviewService,
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(
        _record("mem-1", status=MemoryStatus.PROMOTED)
    )

    with pytest.raises(ReviewError):
        await review_service.promote(memory_id="mem-1", reviewer="alice")


@pytest.mark.asyncio
async def test_reviewing_missing_memory_raises(
    review_service: MemoryReviewService,
) -> None:
    with pytest.raises(ReviewError):
        await review_service.promote(memory_id="nope", reviewer="alice")


@pytest.mark.asyncio
async def test_invalid_target_status_raises(
    review_service: MemoryReviewService,
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(_record("mem-1"))

    with pytest.raises(ReviewError):
        await review_service.review(
            memory_id="mem-1",
            status=MemoryStatus.CANDIDATE,  # 不能把 candidate 设为 candidate
            reviewer="alice",
        )


@pytest.mark.asyncio
async def test_empty_reviewer_raises(
    review_service: MemoryReviewService,
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(_record("mem-1"))

    with pytest.raises(ReviewError):
        await review_service.promote(memory_id="mem-1", reviewer="   ")


@pytest.mark.asyncio
async def test_promoted_memory_disappears_from_candidates(
    review_service: MemoryReviewService,
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(_record("mem-1", content="first"))
    await memory_store.store(_record("mem-2", content="second"))

    before = await review_service.list_candidates(user_id="user-1", project_id="proj-1")
    assert len(before) == 2

    await review_service.promote(memory_id="mem-1", reviewer="alice")

    after = await review_service.list_candidates(user_id="user-1", project_id="proj-1")
    assert [record.memory_id for record in after] == ["mem-2"]


@pytest.mark.asyncio
async def test_noop_store_promote_raises_review_error() -> None:
    service = MemoryReviewService(NoopMemoryStore())
    # NoopMemoryStore.get 永远返回 None，审核应抛 ReviewError 而非静默成功。
    with pytest.raises(ReviewError):
        await service.promote(memory_id="mem-1", reviewer="alice")

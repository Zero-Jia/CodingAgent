"""契约测试：``MySqlMemoryStore`` 与 ``NoopMemoryStore`` 行为。

契约覆盖：
- store 写入 candidate 记录，可按主键读回
- list_by_status 按 user/project/status 过滤，按 updated_at 降序
- update_status 变更状态并写 reviewed_at/reviewer/review_note
- list_promoted 只返回 promoted 记录，支持 scope 过滤
- search 大小写不敏感子串匹配，支持 status 过滤
- NoopMemoryStore 全部降级为空/无副作用
- memories 表出现在 schema 与 alembic migration 中
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, select

from coding_agent.db import initialize_database, tables
from coding_agent.memory.contracts import (
    MemoryRecord,
    MemoryStatus,
    NoopMemoryStore,
)
from coding_agent.memory.mysql import MySqlMemoryStore


def _create_engine(database_path: Path):
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path.as_posix()}", future=True
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _ensure_session(engine, session_id: str) -> None:
    """Insert a minimal sessions row so the memories FK is satisfied."""
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
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    expires_at: datetime | None = None,
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
        created_at=created_at or now,
        updated_at=updated_at or now,
        expires_at=expires_at,
    )


@pytest.fixture()
def memory_store(tmp_path: Path) -> MySqlMemoryStore:
    engine = _create_engine(tmp_path / "memory.db")
    initialize_database(engine)
    _ensure_session(engine, "session-1")
    _ensure_session(engine, "session-2")
    return MySqlMemoryStore(engine)


@pytest.fixture()
def noop_store() -> NoopMemoryStore:
    return NoopMemoryStore()


@pytest.mark.asyncio
async def test_store_and_get_round_trip(memory_store: MySqlMemoryStore) -> None:
    record = _record("mem-1", content="use rg for symbol search")
    await memory_store.store(record)

    fetched = await memory_store.get("mem-1")
    assert fetched is not None
    assert fetched.memory_id == "mem-1"
    assert fetched.content == "use rg for symbol search"
    assert fetched.status == MemoryStatus.CANDIDATE
    assert fetched.confidence == 0.7
    assert fetched.source_run_id == "run-1"
    assert fetched.reviewer == ""
    assert fetched.reviewed_at is None


@pytest.mark.asyncio
async def test_get_returns_none_when_missing(memory_store: MySqlMemoryStore) -> None:
    assert await memory_store.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_list_by_status_orders_by_updated_at_desc(
    memory_store: MySqlMemoryStore,
) -> None:
    older = datetime(2026, 9, 5, 10, 0, 0, tzinfo=UTC)
    newer = datetime(2026, 9, 5, 14, 0, 0, tzinfo=UTC)
    await memory_store.store(_record("mem-old", updated_at=older))
    await memory_store.store(_record("mem-new", updated_at=newer))
    await memory_store.store(
        _record("mem-promoted", status=MemoryStatus.PROMOTED, updated_at=newer)
    )

    candidates = await memory_store.list_by_status(
        user_id="user-1", project_id="proj-1", status=MemoryStatus.CANDIDATE
    )

    assert [r.memory_id for r in candidates] == ["mem-new", "mem-old"]


@pytest.mark.asyncio
async def test_list_by_status_isolates_user_project(
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(_record("mem-a", user_id="user-1", project_id="proj-1"))
    await memory_store.store(
        _record("mem-b", user_id="user-1", project_id="proj-2")
    )
    await memory_store.store(
        _record("mem-c", user_id="user-2", project_id="proj-1")
    )

    result = await memory_store.list_by_status(
        user_id="user-1", project_id="proj-1", status=MemoryStatus.CANDIDATE
    )
    assert {r.memory_id for r in result} == {"mem-a"}


@pytest.mark.asyncio
async def test_update_status_promotes_and_stamps_review(
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(_record("mem-1"))
    before = await memory_store.get("mem-1")
    assert before is not None and before.reviewed_at is None

    await memory_store.update_status(
        memory_id="mem-1",
        status=MemoryStatus.PROMOTED,
        reviewer="reviewer-alice",
        review_note="looks correct",
    )

    after = await memory_store.get("mem-1")
    assert after is not None
    assert after.status == MemoryStatus.PROMOTED
    assert after.reviewer == "reviewer-alice"
    assert after.review_note == "looks correct"
    assert after.reviewed_at is not None
    # update_status must refresh updated_at to the real current time.
    assert after.updated_at != before.updated_at
    assert after.reviewed_at == after.updated_at


@pytest.mark.asyncio
async def test_update_status_is_idempotent_for_missing_record(
    memory_store: MySqlMemoryStore,
) -> None:
    # Updating a non-existent memory must not raise; nothing changes.
    await memory_store.update_status(
        memory_id="missing",
        status=MemoryStatus.REJECTED,
        reviewer="r",
    )
    assert await memory_store.get("missing") is None


@pytest.mark.asyncio
async def test_list_promoted_filters_scope(
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(
        _record("mem-proj", status=MemoryStatus.PROMOTED, scope="project")
    )
    await memory_store.store(
        _record("mem-user", status=MemoryStatus.PROMOTED, scope="user")
    )
    await memory_store.store(
        _record("mem-cand", status=MemoryStatus.CANDIDATE, scope="project")
    )

    promoted_all = await memory_store.list_promoted(
        user_id="user-1", project_id="proj-1"
    )
    assert {r.memory_id for r in promoted_all} == {"mem-proj", "mem-user"}

    promoted_user = await memory_store.list_promoted(
        user_id="user-1", project_id="proj-1", scope="user"
    )
    assert [r.memory_id for r in promoted_user] == ["mem-user"]


@pytest.mark.asyncio
async def test_search_matches_case_insensitively_and_filters_status(
    memory_store: MySqlMemoryStore,
) -> None:
    await memory_store.store(
        _record("mem-1", content="Use Rg for symbol search", status=MemoryStatus.PROMOTED)
    )
    await memory_store.store(
        _record("mem-2", content="Run mypy strict", status=MemoryStatus.CANDIDATE)
    )

    hits = await memory_store.search(
        user_id="user-1", project_id="proj-1", query="RG"
    )
    assert [r.memory_id for r in hits] == ["mem-1"]

    candidates_only = await memory_store.search(
        user_id="user-1",
        project_id="proj-1",
        query="mypy",
        status=MemoryStatus.CANDIDATE,
    )
    assert [r.memory_id for r in candidates_only] == ["mem-2"]

    # status=None searches across all statuses.
    all_hits = await memory_store.search(
        user_id="user-1", project_id="proj-1", query="", status=None
    )
    assert {r.memory_id for r in all_hits} == {"mem-1", "mem-2"}


@pytest.mark.asyncio
async def test_search_isolates_user_project(memory_store: MySqlMemoryStore) -> None:
    await memory_store.store(
        _record("mem-1", user_id="user-1", project_id="proj-1", content="hello")
    )
    await memory_store.store(
        _record("mem-2", user_id="user-1", project_id="proj-2", content="hello")
    )

    hits = await memory_store.search(
        user_id="user-1", project_id="proj-1", query="hello", status=None
    )
    assert {r.memory_id for r in hits} == {"mem-1"}


@pytest.mark.asyncio
async def test_noop_store_is_empty(noop_store: NoopMemoryStore) -> None:
    assert await noop_store.get("anything") is None
    assert await noop_store.list_by_status(
        user_id="u", project_id="p", status=MemoryStatus.CANDIDATE
    ) == []
    assert await noop_store.list_promoted(user_id="u", project_id="p") == []
    assert await noop_store.search(user_id="u", project_id="p", query="x") == []
    # store/update_status must be no-ops without raising.
    await noop_store.store(_record("mem-1"))
    await noop_store.update_status(
        memory_id="mem-1", status=MemoryStatus.PROMOTED, reviewer="r"
    )
    assert await noop_store.get("mem-1") is None


def test_schema_contains_memories_table(tmp_path: Path) -> None:
    engine = _create_engine(tmp_path / "schema.db")
    try:
        initialize_database(engine)
        table_names = set(inspect(engine).get_table_names())
        assert "memories" in table_names

        columns = {c["name"] for c in inspect(engine).get_columns("memories")}
        assert {
            "memory_id",
            "user_id",
            "project_id",
            "scope",
            "category",
            "content",
            "source_session_id",
            "source_run_id",
            "confidence",
            "status",
            "reviewer",
            "review_note",
            "created_at",
            "updated_at",
            "reviewed_at",
            "expires_at",
        }.issubset(columns)
    finally:
        engine.dispose()


def test_alembic_migration_creates_memories_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CODING_AGENT_DATABASE_URL", raising=False)
    database_path = tmp_path / "migration.db"
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}"
    )

    command.upgrade(config, "head")

    engine = _create_engine(database_path)
    try:
        table_names = set(inspect(engine).get_table_names())
        assert "memories" in table_names
        assert "alembic_version" in table_names

        columns = {c["name"] for c in inspect(engine).get_columns("memories")}
        assert {"memory_id", "status", "confidence", "scope", "reviewed_at"}.issubset(
            columns
        )

        indexes = {idx["name"] for idx in inspect(engine).get_indexes("memories")}
        assert {
            "ix_memories_source_session_id",
            "ix_memories_status",
            "ix_memories_scope",
            "ix_memories_user_project_status",
        }.issubset(indexes)
    finally:
        engine.dispose()


def test_alembic_migration_downgrade_drops_memories_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CODING_AGENT_DATABASE_URL", raising=False)
    database_path = tmp_path / "migration.db"
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}"
    )

    command.upgrade(config, "head")
    command.downgrade(config, "0003_add_persistent_patch_packages")

    engine = _create_engine(database_path)
    try:
        table_names = set(inspect(engine).get_table_names())
        assert "memories" not in table_names
        # Downgrade must keep prior tables intact.
        assert {"sessions", "patches"}.issubset(table_names)
    finally:
        engine.dispose()


def test_schema_compiles_for_mysql_with_inno_utf8mb4() -> None:
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable

    ddl = str(
        CreateTable(tables.memories).compile(dialect=mysql.dialect())
    )
    assert "ENGINE=InnoDB" in ddl
    assert "CHARSET=utf8mb4" in ddl

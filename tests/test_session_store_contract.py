from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, inspect, select
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from coding_agent.ai.contracts import ChatMessage, ToolCall
from coding_agent.db import initialize_database, tables
from coding_agent.sessions.mysql import MySqlSessionStore
from coding_agent.sessions.store import (
    ConversationCheckpoint,
    JsonlSessionStore,
    SessionEvent,
    SessionStore,
    SessionSummary,
)


@dataclass(frozen=True)
class StoreCase:
    name: str
    store: SessionStore
    read_transcript: Callable[[str], str]
    close: Callable[[], None]


def _create_engine(database_path: Path) -> Engine:
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _store_cases(tmp_path: Path) -> list[StoreCase]:
    jsonl_root = tmp_path / "jsonl"
    jsonl_store = JsonlSessionStore(jsonl_root)
    database_path = tmp_path / "sessions.db"
    engine = _create_engine(database_path)
    initialize_database(engine)
    sql_store = MySqlSessionStore(engine)
    return [
        StoreCase(
            name="jsonl",
            store=jsonl_store,
            read_transcript=lambda session_id: (
                jsonl_root / "transcripts" / f"{session_id}.md"
            ).read_text(encoding="utf-8"),
            close=lambda: None,
        ),
        StoreCase(
            name="sqlalchemy",
            store=sql_store,
            read_transcript=lambda session_id: _read_sql_transcript(engine, session_id),
            close=engine.dispose,
        ),
    ]


def _read_sql_transcript(engine: Engine, session_id: str) -> str:
    statement = select(tables.transcripts.c.content).where(
        tables.transcripts.c.session_id == session_id
    )
    with engine.connect() as connection:
        content = connection.execute(statement).scalar_one()
    return str(content)


@pytest.mark.asyncio
async def test_store_contract_persists_events_in_order(tmp_path: Path) -> None:
    for case in _store_cases(tmp_path):
        try:
            session_id = f"{case.name}-events"
            await case.store.append(
                SessionEvent(
                    session_id=session_id,
                    run_id="run-1",
                    event_type="agent.started",
                    payload={"ordinal": 1, "nested": {"ok": True}},
                )
            )
            await case.store.append(
                SessionEvent(
                    session_id=session_id,
                    run_id="run-1",
                    event_type="agent.completed",
                    payload={"ordinal": 2},
                )
            )

            events = await case.store.load(session_id)

            assert [event.event_type for event in events] == ["agent.started", "agent.completed"]
            assert events[0].payload["nested"] == {"ok": True}
            assert events[1].payload["ordinal"] == 2
        finally:
            case.close()


@pytest.mark.asyncio
async def test_store_contract_redacts_checkpoint_tool_output(tmp_path: Path) -> None:
    for case in _store_cases(tmp_path):
        try:
            session_id = f"{case.name}-checkpoint"
            checkpoint = ConversationCheckpoint(
                session_id=session_id,
                workspace=str(tmp_path),
                model_provider="deepseek",
                model_name="deepseek-chat",
                messages=[
                    ChatMessage(role="system", content="规则"),
                    ChatMessage(role="user", content="修改文件"),
                    ChatMessage(
                        role="assistant",
                        tool_calls=[ToolCall(id="call-1", name="edit", arguments_json="{}")],
                    ),
                    ChatMessage(
                        role="tool",
                        tool_call_id="call-1",
                        content=(
                            '{"status": "success", "output": "完整工具输出", '
                            '"artifact": "artifacts/a.txt"}'
                        ),
                    ),
                ],
            )

            await case.store.save_checkpoint(checkpoint)
            restored = await case.store.load_checkpoint(session_id)

            assert restored is not None
            assert restored.messages[2].tool_calls[0].name == "edit"
            assert restored.messages[3].tool_call_id == "call-1"
            assert "完整工具输出" not in restored.messages[3].content
            assert "artifacts/a.txt" in restored.messages[3].content
        finally:
            case.close()


@pytest.mark.asyncio
async def test_store_contract_summaries_and_transcripts(tmp_path: Path) -> None:
    for case in _store_cases(tmp_path):
        try:
            older = datetime(2026, 1, 1, tzinfo=UTC)
            newer = older + timedelta(hours=1)
            await case.store.save_summary(
                SessionSummary(
                    session_id=f"{case.name}-older",
                    workspace=str(tmp_path),
                    model_name="deepseek-chat",
                    updated_at=older,
                    last_status="completed",
                )
            )
            await case.store.save_summary(
                SessionSummary(
                    session_id=f"{case.name}-newer",
                    workspace=str(tmp_path),
                    model_name="deepseek-chat",
                    updated_at=newer,
                    last_status="running",
                    message_count=3,
                )
            )
            await case.store.append_transcript(f"{case.name}-newer", "## 用户\n你好\n")
            await case.store.append_transcript(f"{case.name}-newer", "## 助手\n收到\n")

            summaries = await case.store.list_summaries()

            assert [summary.session_id for summary in summaries] == [
                f"{case.name}-newer",
                f"{case.name}-older",
            ]
            assert summaries[0].message_count == 3
            assert "你好" in case.read_transcript(f"{case.name}-newer")
            assert "收到" in case.read_transcript(f"{case.name}-newer")
        finally:
            case.close()


@pytest.mark.asyncio
async def test_mysql_store_summary_update_preserves_children(tmp_path: Path) -> None:
    engine = _create_engine(tmp_path / "mysql-contract.db")
    initialize_database(engine)
    store = MySqlSessionStore(engine)
    session_id = "summary-update-preserves-children"
    try:
        await store.append(
            SessionEvent(
                session_id=session_id,
                run_id="run-1",
                event_type="agent.started",
                payload={},
            )
        )
        await store.save_checkpoint(
            ConversationCheckpoint(
                session_id=session_id,
                workspace=str(tmp_path),
                model_provider="deepseek",
                model_name="deepseek-chat",
                messages=[ChatMessage(role="user", content="hello")],
            )
        )
        await store.save_summary(
            SessionSummary(
                session_id=session_id,
                workspace=str(tmp_path),
                model_name="deepseek-chat",
                last_status="completed",
            )
        )

        assert len(await store.load(session_id)) == 1
        assert await store.load_checkpoint(session_id) is not None
    finally:
        engine.dispose()


def test_schema_contains_platform_storage_tables(tmp_path: Path) -> None:
    engine = _create_engine(tmp_path / "schema.db")
    try:
        initialize_database(engine)
        table_names = set(inspect(engine).get_table_names())

        assert {
            "sessions",
            "runs",
            "session_events",
            "checkpoints",
            "transcripts",
            "approvals",
            "artifacts",
            "model_usage",
        }.issubset(table_names)
    finally:
        engine.dispose()


def test_alembic_migration_creates_platform_storage_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CODING_AGENT_DATABASE_URL", raising=False)
    database_path = tmp_path / "migration.db"
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    command.upgrade(config, "head")

    engine = _create_engine(database_path)
    try:
        table_names = set(inspect(engine).get_table_names())

        assert {
            "sessions",
            "runs",
            "session_events",
            "checkpoints",
            "transcripts",
            "approvals",
            "artifacts",
            "model_usage",
            "alembic_version",
        }.issubset(table_names)
    finally:
        engine.dispose()


def test_alembic_migration_uses_database_url_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "migration-env.db"
    project_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv(
        "CODING_AGENT_DATABASE_URL", f"sqlite+pysqlite:///{database_path.as_posix()}"
    )
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))

    command.upgrade(config, "head")

    engine = _create_engine(database_path)
    try:
        table_names = set(inspect(engine).get_table_names())

        assert "sessions" in table_names
        assert "alembic_version" in table_names
    finally:
        engine.dispose()


def test_schema_compiles_for_mysql_without_text_defaults() -> None:
    dialect = mysql.dialect()
    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=dialect)) for table in tables.schema_tables
    )

    assert "ENGINE=InnoDB" in ddl
    assert "CHARSET=utf8mb4" in ddl
    assert "TEXT NOT NULL DEFAULT" not in ddl

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from coding_agent.agent.coding_agent import CodingAgent
from coding_agent.ai.contracts import (
    CancellationSignal,
    Completed,
    Model,
    ModelEvent,
    ModelRequest,
    TextDelta,
)
from coding_agent.api.app import create_app
from coding_agent.config import AgentConfig
from coding_agent.db import database_connection_error_message
from coding_agent.sessions.factory import (
    StorageConfigError,
    create_session_store,
    redact_database_url,
)
from coding_agent.sessions.mysql import MySqlSessionStore
from coding_agent.sessions.store import JsonlSessionStore


class FakeModelAdapter:
    def __init__(self, responses: list[list[ModelEvent]]) -> None:
        self.model = Model(provider="fake", name="fake-model")
        self.responses = responses
        self.requests: list[ModelRequest] = []

    def stream(
        self, request: ModelRequest, signal: CancellationSignal
    ) -> AsyncIterator[ModelEvent]:
        return self._stream(request, signal)

    async def _stream(
        self, request: ModelRequest, signal: CancellationSignal
    ) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        for event in self.responses.pop(0) if self.responses else [Completed()]:
            if signal.is_set():
                return
            await asyncio.sleep(0)
            yield event


def test_default_storage_backend_is_jsonl(tmp_path: Path) -> None:
    config = AgentConfig(workspace=tmp_path)

    store = create_session_store(config, tmp_path / ".coding-agent")

    assert config.storage_backend == "jsonl"
    assert isinstance(store, JsonlSessionStore)


def test_explicit_database_url_selects_mysql_store(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'sessions.db').as_posix()}"
    config = AgentConfig(
        workspace=tmp_path,
        database_url=database_url,
        database_create_schema=True,
    )

    store = create_session_store(config, tmp_path / ".coding-agent")

    assert config.storage_backend == "mysql"
    assert isinstance(store, MySqlSessionStore)
    store.engine.dispose()


def test_environment_database_url_selects_mysql_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'env-sessions.db').as_posix()}"
    monkeypatch.setenv("CODING_AGENT_DATABASE_URL", database_url)
    monkeypatch.setenv("CODING_AGENT_DATABASE_CREATE_SCHEMA", "true")

    config = AgentConfig.from_environment(tmp_path)
    store = create_session_store(config, config.workspace / ".coding-agent")

    assert config.storage_backend == "mysql"
    assert isinstance(store, MySqlSessionStore)
    store.engine.dispose()


def test_mysql_backend_requires_database_url(tmp_path: Path) -> None:
    config = AgentConfig(workspace=tmp_path, storage_backend="mysql")

    with pytest.raises(StorageConfigError, match="requires CODING_AGENT_DATABASE_URL"):
        create_session_store(config, tmp_path / ".coding-agent")


def test_unknown_backend_name_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="storage_backend"):
        AgentConfig(workspace=tmp_path, storage_backend="sqlite")  # type: ignore[arg-type]


def test_database_config_error_redacts_credentials(tmp_path: Path) -> None:
    raw_url = "mysql+notadriver://db_user:super-secret@localhost:3306/coding_agent"
    config = AgentConfig(workspace=tmp_path, database_url=raw_url)

    with pytest.raises(StorageConfigError) as raised:
        create_session_store(config, tmp_path / ".coding-agent")

    message = str(raised.value)
    assert "super-secret" not in message
    assert "db_user" not in message
    assert "***:***@localhost" in message


def test_redact_database_url_handles_invalid_url() -> None:
    assert redact_database_url("not a valid sqlalchemy url") == "[redacted database url]"


def test_database_connection_error_message_explains_access_denied() -> None:
    message = database_connection_error_message(
        "mysql+pymysql://coding_agent:wrong@localhost:3306/coding_agent",
        RuntimeError("(1045, \"Access denied for user 'coding_agent'@'localhost'\")"),
    )

    assert "状态：连接失败" in message
    assert "wrong" not in message
    assert "mysql+pymysql://***:***@localhost:3306/coding_agent" in message
    assert "当前数据库 URL 使用的是 `coding_agent` 用户" in message
    assert "root:<root-password>" in message


@pytest.mark.asyncio
async def test_api_agent_uses_database_store_for_runtime_state(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'api-sessions.db').as_posix()}"
    config = AgentConfig(
        workspace=tmp_path,
        model_provider="fake",
        model="fake-model",
        non_interactive=True,
        database_url=database_url,
        database_create_schema=True,
    )
    agent = CodingAgent(config, FakeModelAdapter([[TextDelta(text="stored"), Completed()]]))
    app = create_app(agent)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        created = await client.post("/v1/sessions", json={"session_id": "database-session"})
        streamed = await client.post(
            "/v1/sessions/database-session/messages/stream",
            json={"message": "persist this"},
        )

    events = await agent.sessions.load("database-session")
    checkpoint = await agent.sessions.load_checkpoint("database-session")
    transcript = await agent.sessions.load_transcript("database-session")

    assert created.status_code == 200
    assert streamed.status_code == 200
    assert isinstance(agent.sessions, MySqlSessionStore)
    assert [event.event_type for event in events] == [
        "run_started",
        "message_delta",
        "run_finished",
    ]
    assert checkpoint is not None
    assert checkpoint.session_id == "database-session"
    assert "persist this" in transcript
    assert "stored" in transcript
    agent.sessions.engine.dispose()

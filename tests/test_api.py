from __future__ import annotations

import asyncio
import json
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
    Usage,
    UsageEvent,
)
from coding_agent.api.app import ApiSessionManager, create_app
from coding_agent.config import AgentConfig


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
        events = self.responses.pop(0) if self.responses else [Completed()]
        for event in events:
            if signal.is_set():
                return
            await asyncio.sleep(0)
            yield event


class CancellableModelAdapter:
    def __init__(self) -> None:
        self.model = Model(provider="fake", name="cancellable-model")
        self.started = asyncio.Event()

    def stream(
        self, request: ModelRequest, signal: CancellationSignal
    ) -> AsyncIterator[ModelEvent]:
        return self._stream(signal)

    async def _stream(self, signal: CancellationSignal) -> AsyncIterator[ModelEvent]:
        self.started.set()
        while not signal.is_set():
            await asyncio.sleep(0)
        yield TextDelta(text="cancelled after signal")


def _agent(tmp_path: Path, model: FakeModelAdapter | CancellableModelAdapter) -> CodingAgent:
    config = AgentConfig(
        workspace=tmp_path,
        model_provider="fake",
        model=model.model.name,
        non_interactive=True,
    )
    return CodingAgent(config, model)


def _client(agent: CodingAgent) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(agent))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _sse_events(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for block in text.split("\n\n"):
        data_line = next(
            (
                line.removeprefix("data: ")
                for line in block.splitlines()
                if line.startswith("data: ")
            ),
            "",
        )
        if data_line:
            events.append(json.loads(data_line))
    return events


@pytest.mark.asyncio
async def test_create_and_list_sessions(tmp_path: Path) -> None:
    model = FakeModelAdapter([])
    async with _client(_agent(tmp_path, model)) as client:
        created = await client.post("/v1/sessions", json={})
        listed = await client.get("/v1/sessions")

    assert created.status_code == 200
    session_id = created.json()["session_id"]
    assert session_id
    assert listed.status_code == 200
    assert listed.json()[0]["session_id"] == session_id


@pytest.mark.asyncio
async def test_stream_message_returns_agent_events(tmp_path: Path) -> None:
    model = FakeModelAdapter(
        [
            [
                TextDelta(text="hello"),
                TextDelta(text=" world"),
                UsageEvent(usage=Usage(prompt_tokens=7, completion_tokens=2, total_tokens=9)),
                Completed(),
            ]
        ]
    )
    async with _client(_agent(tmp_path, model)) as client:
        created = await client.post("/v1/sessions", json={"session_id": "session-api"})
        response = await client.post(
            "/v1/sessions/session-api/messages/stream",
            json={"message": "hello"},
        )

    assert created.status_code == 200
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    event_types = [event["type"] for event in _sse_events(response.text)]
    assert event_types == [
        "run_started",
        "message_delta",
        "message_delta",
        "model_usage_reported",
        "token_usage_updated",
        "run_finished",
    ]


@pytest.mark.asyncio
async def test_stream_rejects_unknown_session(tmp_path: Path) -> None:
    model = FakeModelAdapter([])
    async with _client(_agent(tmp_path, model)) as client:
        response = await client.post(
            "/v1/sessions/missing/messages/stream",
            json={"message": "hello"},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_invalid_session_id_is_rejected(tmp_path: Path) -> None:
    model = FakeModelAdapter([])
    async with _client(_agent(tmp_path, model)) as client:
        response = await client.post("/v1/sessions", json={"session_id": "../secret"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_blank_message_is_rejected(tmp_path: Path) -> None:
    model = FakeModelAdapter([])
    async with _client(_agent(tmp_path, model)) as client:
        await client.post("/v1/sessions", json={"session_id": "session-api"})
        response = await client.post(
            "/v1/sessions/session-api/messages/stream",
            json={"message": "   "},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cancel_active_session_run(tmp_path: Path) -> None:
    model = CancellableModelAdapter()
    agent = _agent(tmp_path, model)
    app = create_app(agent)
    manager = app.state.session_manager
    session = await manager.create_session("session-cancel")

    stream_task = asyncio.create_task(_collect_manager_events(manager, session.session_id))
    await model.started.wait()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/v1/sessions/session-cancel/cancel")

    events = await stream_task
    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    assert [event["type"] for event in events] == ["run_started", "run_cancelled"]


@pytest.mark.asyncio
async def test_cancel_missing_run_returns_false(tmp_path: Path) -> None:
    model = FakeModelAdapter([])
    async with _client(_agent(tmp_path, model)) as client:
        response = await client.post("/v1/runs/missing-run/cancel")

    assert response.status_code == 200
    assert response.json() == {"cancelled": False, "session_id": None, "run_id": "missing-run"}


async def _collect_manager_events(
    manager: ApiSessionManager, session_id: str
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    async for event in manager.stream_message(session_id, "cancel me"):
        events.append(event.model_dump(mode="json"))
    return events

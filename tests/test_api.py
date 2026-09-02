from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import SecretStr

from coding_agent.agent.coding_agent import CodingAgent
from coding_agent.ai.contracts import (
    CancellationSignal,
    Completed,
    Model,
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCall,
    ToolCallCompleted,
    Usage,
    UsageEvent,
)
from coding_agent.api.app import ApiSessionManager, create_app
from coding_agent.api.approvals import ApprovalRecord, ApprovalRegistry, MySqlApprovalStore
from coding_agent.config import AgentConfig
from coding_agent.sessions.mysql import MySqlSessionStore


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


def _agent(
    tmp_path: Path,
    model: FakeModelAdapter | CancellableModelAdapter,
    *,
    non_interactive: bool = True,
    plan_mode: bool = False,
) -> CodingAgent:
    config = AgentConfig(
        workspace=tmp_path,
        model_provider="fake",
        model=model.model.name,
        non_interactive=non_interactive,
        plan_mode=plan_mode,
    )
    return CodingAgent(config, model)


def _database_agent(
    tmp_path: Path,
    model: FakeModelAdapter | CancellableModelAdapter,
    database_url: str,
) -> CodingAgent:
    config = AgentConfig(
        workspace=tmp_path,
        model_provider="fake",
        model=model.model.name,
        non_interactive=False,
        database_url=SecretStr(database_url),
        database_create_schema=True,
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


@pytest.mark.asyncio
async def test_web_approval_approve_unblocks_stream(tmp_path: Path) -> None:
    model = FakeModelAdapter(
        [
            [_submit_plan_call(), Completed()],
            [TextDelta(text="plan approved"), Completed()],
        ]
    )
    app = create_app(_agent(tmp_path, model, non_interactive=False, plan_mode=True))
    manager = app.state.session_manager
    session = await manager.create_session("session-approve")

    stream_task = asyncio.create_task(
        _collect_manager_events(manager, session.session_id, "needs a plan")
    )
    approval = await _wait_for_pending(app.state.approval_registry)

    assert approval.session_id == "session-approve"
    assert approval.run_id
    assert approval.tool_name == "submit_plan"
    assert approval.details["files"] == ["src/coding_agent/api/app.py"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/approvals/{approval.approval_id}/approve",
            json={"reason": "reviewed in test"},
        )

    events = await asyncio.wait_for(stream_task, timeout=2)
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert [event["type"] for event in events] == [
        "run_started",
        "plan_submitted",
        "approval_requested",
        "approval_resolved",
        "plan_approved",
        "tool_started",
        "tool_finished",
        "message_delta",
        "run_finished",
    ]
    assert cast(dict[str, object], events[3]["payload"])["approved"] is True


@pytest.mark.asyncio
async def test_web_approval_reject_returns_denial_to_model(tmp_path: Path) -> None:
    model = FakeModelAdapter(
        [
            [_submit_plan_call(), Completed()],
            [TextDelta(text="plan rejected"), Completed()],
        ]
    )
    app = create_app(_agent(tmp_path, model, non_interactive=False, plan_mode=True))
    manager = app.state.session_manager
    session = await manager.create_session("session-reject")

    stream_task = asyncio.create_task(
        _collect_manager_events(manager, session.session_id, "needs a plan")
    )
    approval = await _wait_for_pending(app.state.approval_registry)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            f"/approvals/{approval.approval_id}/reject",
            json={"reason": "too broad"},
        )

    events = await asyncio.wait_for(stream_task, timeout=2)
    finished = [event for event in events if event["type"] == "tool_finished"]
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert cast(dict[str, object], events[3]["payload"])["approved"] is False
    assert any(event["type"] == "plan_rejected" for event in events)
    finished_payload = cast(dict[str, object], finished[0]["payload"])
    finished_result = cast(dict[str, object], finished_payload["result"])
    assert finished_payload["tool"] == "submit_plan"
    assert finished_result["status"] == "policy_denied"
    assert model.requests[-1].messages[-1].role == "tool"
    assert "policy_denied" in model.requests[-1].messages[-1].content


@pytest.mark.asyncio
async def test_approval_endpoints_expose_safe_patch_preview_and_audit(
    tmp_path: Path,
) -> None:
    app = create_app(_agent(tmp_path, FakeModelAdapter([])))
    registry = app.state.approval_registry
    request_task = asyncio.create_task(
        registry.request(
            "apply_patch",
            "patch application requires approval",
            {
                "patch_id": "patch-1",
                "changed_files": ["src/coding_agent/api/app.py"],
                "diff_preview": "token=secret-value\n+print('ok')",
                "token": "secret-value",
            },
            session_id="session-patch",
            run_id="run-patch",
        )
    )
    approval = await _wait_for_pending(registry)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        listed = await client.get("/approvals")
        detail = await client.get(f"/approvals/{approval.approval_id}")
        ui = await client.get("/approvals/ui")
        rejected = await client.post(
            f"/approvals/{approval.approval_id}/reject",
            json={"reason": "not needed"},
        )

    assert listed.status_code == 200
    assert detail.status_code == 200
    assert ui.status_code == 200
    assert "CodingAgent Approvals" in ui.text
    item = listed.json()[0]
    assert item["tool_name"] == "apply_patch"
    assert item["details"]["changed_files"] == ["src/coding_agent/api/app.py"]
    assert "secret-value" not in json.dumps(item, ensure_ascii=False)
    assert detail.json()["approval_id"] == approval.approval_id
    assert rejected.json()["status"] == "rejected"
    assert await asyncio.wait_for(request_task, timeout=2) is False

    audit = tmp_path / ".coding-agent" / "approvals" / "audit.jsonl"
    audit_text = audit.read_text(encoding="utf-8")
    assert '"event_type": "requested"' in audit_text
    assert '"event_type": "rejected"' in audit_text
    assert "secret-value" not in audit_text


@pytest.mark.asyncio
async def test_approval_resolution_is_idempotent(tmp_path: Path) -> None:
    app = create_app(_agent(tmp_path, FakeModelAdapter([])))
    registry = app.state.approval_registry
    request_task = asyncio.create_task(
        registry.request(
            "submit_plan",
            "plan mode requires approval before execution",
            {"plan": "test"},
            session_id="session-idempotent",
            run_id="run-idempotent",
        )
    )
    approval = await _wait_for_pending(registry)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        first = await client.post(f"/approvals/{approval.approval_id}/approve")
        second = await client.post(
            f"/approvals/{approval.approval_id}/reject",
            json={"reason": "late click"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "approved"
    assert second.json()["status"] == "approved"
    assert await asyncio.wait_for(request_task, timeout=2) is True


@pytest.mark.asyncio
async def test_approval_endpoints_validate_ids_and_status(tmp_path: Path) -> None:
    app = create_app(_agent(tmp_path, FakeModelAdapter([])))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        bad_status = await client.get("/approvals?status=unknown")
        bad_id = await client.get("/approvals/../secret")
        missing = await client.get("/approvals/approval-missing")

    assert bad_status.status_code == 422
    assert bad_id.status_code in {404, 422}
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_approval_list_can_include_resolved_items(tmp_path: Path) -> None:
    app = create_app(_agent(tmp_path, FakeModelAdapter([])))
    registry = app.state.approval_registry
    request_task = asyncio.create_task(
        registry.request(
            "submit_plan",
            "plan mode requires approval before execution",
            {"plan": "test"},
            session_id="session-list",
            run_id="run-list",
        )
    )
    approval = await _wait_for_pending(registry)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.post(f"/approvals/{approval.approval_id}/reject")
        pending = await client.get("/approvals")
        all_items = await client.get("/approvals?status=all")

    assert await asyncio.wait_for(request_task, timeout=2) is False
    assert pending.status_code == 200
    assert pending.json() == []
    assert all_items.status_code == 200
    assert all_items.json()[0]["status"] == "rejected"


@pytest.mark.asyncio
async def test_approval_cancel_unblocks_pending_request(tmp_path: Path) -> None:
    model = FakeModelAdapter([[_submit_plan_call(), Completed()]])
    app = create_app(_agent(tmp_path, model, non_interactive=False, plan_mode=True))
    manager = app.state.session_manager
    session = await manager.create_session("session-cancel-approval")

    stream_task = asyncio.create_task(
        _collect_manager_events(manager, session.session_id, "needs a plan")
    )
    approval = await _wait_for_pending(app.state.approval_registry)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(f"/v1/runs/{approval.run_id}/cancel")

    events = await asyncio.wait_for(stream_task, timeout=2)
    resolved = await app.state.approval_registry.get(approval.approval_id)
    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    assert resolved.status == "cancelled"
    assert any(event["type"] == "run_cancelled" for event in events)


@pytest.mark.asyncio
async def test_mysql_approval_queue_can_be_resolved_by_another_registry(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'approvals.db').as_posix()}"
    agent_one = _database_agent(tmp_path, FakeModelAdapter([]), database_url)
    agent_two = _database_agent(tmp_path, FakeModelAdapter([]), database_url)
    app_one = create_app(agent_one)
    app_two = create_app(agent_two)
    registry_one = app_one.state.approval_registry

    assert isinstance(agent_one.sessions, MySqlSessionStore)
    assert isinstance(registry_one.store, MySqlApprovalStore)

    request_task = asyncio.create_task(
        registry_one.request(
            "apply_patch",
            "patch application requires approval",
            {
                "patch_id": "patch-1",
                "changed_files": ["src/coding_agent/api/app.py"],
                "diff_preview": "password=secret\n+ok",
            },
            session_id="session-persistent-approval",
            run_id="run-persistent-approval",
        )
    )
    approval = await _wait_for_pending(registry_one)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_two), base_url="http://testserver"
    ) as client:
        listed = await client.get("/approvals")
        approved = await client.post(
            f"/approvals/{approval.approval_id}/approve",
            json={"reason": "approved from another API process"},
        )

    assert listed.status_code == 200
    assert listed.json()[0]["approval_id"] == approval.approval_id
    assert "secret" not in json.dumps(listed.json(), ensure_ascii=False)
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["resolution_reason"] == "approved from another API process"
    assert approved.json()["resolved_by"] == "local-api"
    assert await asyncio.wait_for(request_task, timeout=2) is True

    resolved = await registry_one.get(approval.approval_id)
    assert resolved.status == "approved"

    if isinstance(agent_one.sessions, MySqlSessionStore):
        agent_one.sessions.engine.dispose()
    if isinstance(agent_two.sessions, MySqlSessionStore):
        agent_two.sessions.engine.dispose()


async def _collect_manager_events(
    manager: ApiSessionManager, session_id: str, message: str = "cancel me"
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    async for event in manager.stream_message(session_id, message):
        events.append(event.model_dump(mode="json"))
    return events


def _submit_plan_call() -> ToolCallCompleted:
    return ToolCallCompleted(
        call=ToolCall(
            id="plan-call",
            name="submit_plan",
            arguments_json=(
                '{"plan": "Add the approval API and tests.", '
                '"files": ["src/coding_agent/api/app.py"], '
                '"verification_commands": ["python -m pytest tests/test_api.py"], '
                '"risks": ["approval flow regression"]}'
            ),
        )
    )


async def _wait_for_pending(registry: ApprovalRegistry) -> ApprovalRecord:
    for _ in range(100):
        records = await registry.list("pending")
        if records:
            return records[0]
        await asyncio.sleep(0.01)
    raise AssertionError("approval was not created")

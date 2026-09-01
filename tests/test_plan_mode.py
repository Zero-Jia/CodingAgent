from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from coding_agent.ai.contracts import (
    CancellationSignal,
    ChatMessage,
    Completed,
    Model,
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCall,
    ToolCallCompleted,
    ToolDefinition,
)
from coding_agent.cli.app import _status_text
from coding_agent.config import AgentConfig
from coding_agent.policy.engine import PolicyEngine
from coding_agent.runtime.events import AgentEvent
from coding_agent.runtime.loop import AgentRuntime, ApprovalProvider
from coding_agent.tools.contracts import Cancellation, Tool, ToolContext, ToolResult, ToolUpdate
from coding_agent.tools.plan import SubmitPlanTool
from coding_agent.tracing.store import JsonlTraceStore


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
        if not self.responses:
            yield TextDelta(text="done")
            yield Completed()
            return
        for event in self.responses.pop(0):
            if signal.is_set():
                return
            await asyncio.sleep(0)
            yield event


class RecordingApproval(ApprovalProvider):
    def __init__(self, approved: bool) -> None:
        self.approved = approved
        self.requests: list[tuple[str, str, dict[str, object]]] = []

    async def request(
        self,
        tool_name: str,
        reason: str,
        params: dict[str, object],
        *,
        session_id: str = "",
        run_id: str = "",
    ) -> bool:
        self.requests.append((tool_name, reason, params))
        return self.approved


class SequencedApproval(ApprovalProvider):
    def __init__(self, approvals: list[bool]) -> None:
        self.approvals = approvals
        self.requests: list[tuple[str, str, dict[str, object]]] = []

    async def request(
        self,
        tool_name: str,
        reason: str,
        params: dict[str, object],
        *,
        session_id: str = "",
        run_id: str = "",
    ) -> bool:
        self.requests.append((tool_name, reason, params))
        if not self.approvals:
            return False
        return self.approvals.pop(0)


class RecordingSandboxTool:
    def __init__(self, results: list[ToolResult] | None = None) -> None:
        self.called = False
        self.call_count = 0
        self.results = results or [ToolResult(status="success", summary="sandbox executed")]
        self.definition = ToolDefinition(
            name="sandbox_shell",
            description="fake sandbox command",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            risk="shell",
        )

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        self.called = True
        self.call_count += 1
        if self.results:
            yield self.results.pop(0)
            return
        yield ToolResult(status="success", summary="sandbox executed")


def _runtime(
    tmp_path: Path,
    model: FakeModelAdapter,
    shell: RecordingSandboxTool,
    *,
    approval: ApprovalProvider | None = None,
    allow_shell: bool = True,
    allow_write: bool = False,
    non_interactive: bool = True,
) -> AgentRuntime:
    tools: list[Tool] = [SubmitPlanTool(), shell]
    return AgentRuntime(
        model=model,
        tools=tools,
        policy=PolicyEngine(
            tmp_path,
            allow_write=allow_write,
            allow_shell=allow_shell,
            non_interactive=non_interactive,
        ),
        tool_context=ToolContext(workspace=str(tmp_path)),
        trace=JsonlTraceStore(tmp_path / ".trace"),
        max_turns=5,
        max_tool_calls=8,
        plan_mode=True,
        approval=approval,
    )


async def _collect(runtime: AgentRuntime, messages: list[ChatMessage]) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    async for event in runtime.run_turn(messages, "task", "session-a", "run-a"):
        events.append(event)
    return events


def _submit_plan_call(call_id: str = "plan-1") -> ToolCallCompleted:
    return ToolCallCompleted(
        call=ToolCall(
            id=call_id,
            name="submit_plan",
            arguments_json=(
                '{"plan": "Read the module, update runtime, then test.", '
                '"files": ["src/coding_agent/runtime/loop.py"], '
                '"verification_commands": ["python -m pytest"], '
                '"risks": ["runtime gate regression"]}'
            ),
        )
    )


def _revised_plan_call(call_id: str = "plan-2") -> ToolCallCompleted:
    return ToolCallCompleted(
        call=ToolCall(
            id=call_id,
            name="submit_plan",
            arguments_json=(
                '{"plan": "Use the failure output to narrow the command, then rerun.", '
                '"revision_of": "plan-1", '
                '"failure_summary": "pytest failed during sandbox execution", '
                '"changed_approach": "Run a focused test command before the full suite", '
                '"files": ["src/coding_agent/runtime/loop.py"], '
                '"verification_commands": ["python -m pytest tests/test_plan_mode.py"], '
                '"risks": ["runtime gate regression"]}'
            ),
        )
    )


def _invalid_revised_plan_call(call_id: str = "plan-2") -> ToolCallCompleted:
    return ToolCallCompleted(
        call=ToolCall(
            id=call_id,
            name="submit_plan",
            arguments_json='{"plan": "Try again without explaining the failure."}',
        )
    )


def _sandbox_call(call_id: str = "shell-1") -> ToolCallCompleted:
    return ToolCallCompleted(
        call=ToolCall(
            id=call_id,
            name="sandbox_shell",
            arguments_json='{"command": "python -m pytest"}',
        )
    )


@pytest.mark.asyncio
async def test_plan_mode_blocks_sandbox_before_approved_plan(tmp_path: Path) -> None:
    shell = RecordingSandboxTool()
    model = FakeModelAdapter(
        [
            [_sandbox_call(), Completed()],
            [TextDelta(text="I need to submit a plan first."), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model, shell)
    messages = [ChatMessage(role="system", content="system"), ChatMessage(role="user", content="x")]

    events = await _collect(runtime, messages)

    assert [event.type for event in events] == [
        "run_started",
        "tool_finished",
        "message_delta",
        "run_finished",
    ]
    assert events[1].payload["result"]["status"] == "policy_denied"
    assert "approved submit_plan" in events[1].payload["result"]["summary"]
    assert not shell.called


@pytest.mark.asyncio
async def test_plan_mode_allows_sandbox_after_approved_plan(tmp_path: Path) -> None:
    shell = RecordingSandboxTool()
    approval = RecordingApproval(approved=True)
    model = FakeModelAdapter(
        [
            [_submit_plan_call(), Completed()],
            [_sandbox_call(), Completed()],
            [TextDelta(text="done"), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model, shell, approval=approval)
    messages = [ChatMessage(role="system", content="system"), ChatMessage(role="user", content="x")]

    events = await _collect(runtime, messages)

    assert [event.type for event in events] == [
        "run_started",
        "plan_submitted",
        "approval_requested",
        "approval_resolved",
        "plan_approved",
        "tool_started",
        "tool_finished",
        "tool_started",
        "tool_finished",
        "message_delta",
        "run_finished",
    ]
    assert approval.requests[0][0] == "submit_plan"
    assert approval.requests[0][2]["files"] == ["src/coding_agent/runtime/loop.py"]
    assert approval.requests[0][2]["plan_status"] == "draft_required"
    assert shell.called


@pytest.mark.asyncio
async def test_plan_mode_rejected_plan_keeps_sandbox_blocked(tmp_path: Path) -> None:
    shell = RecordingSandboxTool()
    approval = RecordingApproval(approved=False)
    model = FakeModelAdapter(
        [
            [_submit_plan_call(), Completed()],
            [_sandbox_call(), Completed()],
            [TextDelta(text="plan was not approved"), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model, shell, approval=approval)
    messages = [ChatMessage(role="system", content="system"), ChatMessage(role="user", content="x")]

    events = await _collect(runtime, messages)

    finished = [event for event in events if event.type == "tool_finished"]
    assert any(event.type == "plan_rejected" for event in events)
    assert finished[0].payload["tool"] == "submit_plan"
    assert finished[0].payload["result"]["status"] == "policy_denied"
    assert "plan was rejected" in finished[0].payload["result"]["summary"]
    assert finished[1].payload["tool"] == "sandbox_shell"
    assert finished[1].payload["result"]["status"] == "policy_denied"
    assert not shell.called


@pytest.mark.asyncio
async def test_plan_mode_non_interactive_cannot_bypass_plan_gate(tmp_path: Path) -> None:
    shell = RecordingSandboxTool()
    model = FakeModelAdapter(
        [
            [_submit_plan_call(), Completed()],
            [_sandbox_call(), Completed()],
            [TextDelta(text="blocked"), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model, shell, allow_shell=True, allow_write=True)
    messages = [ChatMessage(role="system", content="system"), ChatMessage(role="user", content="x")]

    events = await _collect(runtime, messages)

    assert any(
        event.type == "approval_resolved" and event.payload["approved"] is False
        for event in events
    )
    assert not shell.called


@pytest.mark.asyncio
async def test_plan_mode_requires_revised_plan_after_gated_tool_failure(
    tmp_path: Path,
) -> None:
    shell = RecordingSandboxTool(
        [ToolResult(status="execution_error", summary="pytest failed")]
    )
    approval = RecordingApproval(approved=True)
    model = FakeModelAdapter(
        [
            [_submit_plan_call(), Completed()],
            [_sandbox_call("shell-1"), Completed()],
            [_sandbox_call("shell-2"), Completed()],
            [TextDelta(text="I need to revise the plan."), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model, shell, approval=approval)
    messages = [ChatMessage(role="system", content="system"), ChatMessage(role="user", content="x")]

    events = await _collect(runtime, messages)

    finished = [event for event in events if event.type == "tool_finished"]
    assert any(event.type == "plan_failed" for event in events)
    assert any(event.type == "plan_revision_required" for event in events)
    assert finished[-1].payload["tool"] == "sandbox_shell"
    assert finished[-1].payload["result"]["status"] == "policy_denied"
    assert "plan revision required" in finished[-1].payload["result"]["summary"]
    assert shell.call_count == 1


@pytest.mark.asyncio
async def test_plan_mode_allows_gated_tool_after_approved_revision(tmp_path: Path) -> None:
    shell = RecordingSandboxTool(
        [
            ToolResult(status="execution_error", summary="pytest failed"),
            ToolResult(status="success", summary="focused tests passed"),
        ]
    )
    approval = RecordingApproval(approved=True)
    model = FakeModelAdapter(
        [
            [_submit_plan_call(), Completed()],
            [_sandbox_call("shell-1"), Completed()],
            [_revised_plan_call(), Completed()],
            [_sandbox_call("shell-2"), Completed()],
            [TextDelta(text="done"), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model, shell, approval=approval)
    messages = [ChatMessage(role="system", content="system"), ChatMessage(role="user", content="x")]

    events = await _collect(runtime, messages)

    approved_events = [event for event in events if event.type == "plan_approved"]
    assert len(approved_events) == 2
    assert approved_events[-1].payload["revision_count"] == 1
    assert approval.requests[-1][2]["failure_summary"] == "pytest failed during sandbox execution"
    assert approval.requests[-1][2]["changed_approach"]
    assert shell.call_count == 2


@pytest.mark.asyncio
async def test_plan_mode_rejects_revision_without_failure_context(tmp_path: Path) -> None:
    shell = RecordingSandboxTool(
        [ToolResult(status="execution_error", summary="pytest failed")]
    )
    approval = RecordingApproval(approved=True)
    model = FakeModelAdapter(
        [
            [_submit_plan_call(), Completed()],
            [_sandbox_call("shell-1"), Completed()],
            [_invalid_revised_plan_call(), Completed()],
            [_sandbox_call("shell-2"), Completed()],
            [TextDelta(text="blocked"), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model, shell, approval=approval)
    messages = [ChatMessage(role="system", content="system"), ChatMessage(role="user", content="x")]

    events = await _collect(runtime, messages)

    finished = [event for event in events if event.type == "tool_finished"]
    assert any(
        event.payload["tool"] == "submit_plan"
        and event.payload["result"]["status"] == "validation_failed"
        and "failure_summary" in event.payload["result"]["summary"]
        for event in finished
    )
    assert finished[-1].payload["tool"] == "sandbox_shell"
    assert finished[-1].payload["result"]["status"] == "policy_denied"
    assert shell.call_count == 1


@pytest.mark.asyncio
async def test_plan_mode_marks_plan_failed_when_gated_tool_approval_is_denied(
    tmp_path: Path,
) -> None:
    shell = RecordingSandboxTool()
    approval = SequencedApproval([True, False])
    model = FakeModelAdapter(
        [
            [_submit_plan_call(), Completed()],
            [_sandbox_call("shell-1"), Completed()],
            [_sandbox_call("shell-2"), Completed()],
            [TextDelta(text="blocked"), Completed()],
        ]
    )
    runtime = _runtime(
        tmp_path,
        model,
        shell,
        approval=approval,
        allow_shell=False,
        non_interactive=False,
    )
    messages = [ChatMessage(role="system", content="system"), ChatMessage(role="user", content="x")]

    events = await _collect(runtime, messages)

    finished = [event for event in events if event.type == "tool_finished"]
    assert any(event.type == "plan_failed" for event in events)
    assert any(event.type == "plan_revision_required" for event in events)
    assert finished[-1].payload["tool"] == "sandbox_shell"
    assert finished[-1].payload["result"]["status"] == "policy_denied"
    assert shell.call_count == 0


def test_policy_allows_submit_plan_as_read_only(tmp_path: Path) -> None:
    policy = PolicyEngine(tmp_path, allow_write=False, allow_shell=False, non_interactive=True)

    decision = policy.tool_decision("submit_plan", {"plan": "test"})

    assert decision.decision == "allow"


def test_status_text_shows_plan_mode(tmp_path: Path) -> None:
    from types import SimpleNamespace

    session = SimpleNamespace(
        summary=SimpleNamespace(
            session_id="session-plan",
            model_name="fake-model",
            message_count=1,
            run_count=0,
            tool_count=0,
            approval_count=0,
            last_status="created",
            failed_count=0,
            cancelled_count=0,
            total_duration_ms=0.0,
        ),
        _agent=SimpleNamespace(config=AgentConfig(workspace=tmp_path, plan_mode=True)),
        token_snapshot=lambda: SimpleNamespace(
            session_total_tokens=0,
            session_prompt_tokens=0,
            session_completion_tokens=0,
            current_context_tokens=0,
            context_window_tokens=128_000,
            context_usage_ratio=0.0,
            current_context_source="estimated",
            last_compacted_tokens_saved=0,
            total_compacted_tokens_saved=0,
        ),
    )

    assert "Plan Mode：是" in _status_text(session)

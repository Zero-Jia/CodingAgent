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
    ModelError,
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCall,
    ToolCallCompleted,
    ToolDefinition,
    Usage,
    UsageEvent,
)
from coding_agent.policy.engine import PolicyEngine
from coding_agent.runtime.events import AgentEvent
from coding_agent.runtime.loop import AgentRuntime
from coding_agent.tools.builtin import ReadTool
from coding_agent.tools.contracts import Cancellation, Tool, ToolContext, ToolResult, ToolUpdate
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
            yield TextDelta(text="no scripted response")
            yield Completed()
            return
        events = self.responses.pop(0)
        for event in events:
            if signal.is_set():
                return
            await asyncio.sleep(0)
            yield event


class CancellableModelAdapter:
    def __init__(self) -> None:
        self.model = Model(provider="fake", name="cancellable-model")

    def stream(
        self, request: ModelRequest, signal: CancellationSignal
    ) -> AsyncIterator[ModelEvent]:
        return self._stream(signal)

    async def _stream(self, signal: CancellationSignal) -> AsyncIterator[ModelEvent]:
        while not signal.is_set():
            await asyncio.sleep(0)
        yield TextDelta(text="cancelled after signal")


class RecordingShellTool:
    def __init__(self) -> None:
        self.called = False
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
        yield ToolResult(status="success", summary="should not execute")


def _runtime(
    tmp_path: Path,
    model: FakeModelAdapter | CancellableModelAdapter,
    *,
    tools: list[Tool] | None = None,
    allow_shell: bool = False,
    allow_write: bool = False,
    non_interactive: bool = True,
) -> AgentRuntime:
    return AgentRuntime(
        model=model,
        tools=tools or [ReadTool()],
        policy=PolicyEngine(
            tmp_path,
            allow_write=allow_write,
            allow_shell=allow_shell,
            non_interactive=non_interactive,
        ),
        tool_context=ToolContext(workspace=str(tmp_path)),
        trace=JsonlTraceStore(tmp_path / ".trace"),
        max_turns=4,
        max_tool_calls=8,
    )


async def _collect(runtime: AgentRuntime, messages: list[ChatMessage]) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    async for event in runtime.run_turn(messages, "task", "session-a", "run-a"):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_runtime_returns_plain_text_answer(tmp_path: Path) -> None:
    model = FakeModelAdapter([[TextDelta(text="hello"), TextDelta(text=" world"), Completed()]])
    runtime = _runtime(tmp_path, model)
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="hi"),
    ]

    events = await _collect(runtime, messages)

    assert [event.type for event in events] == [
        "run_started",
        "message_delta",
        "message_delta",
        "run_finished",
    ]
    assert events[1].payload["text"] == "hello"
    assert events[2].payload["text"] == " world"
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "hello world"


@pytest.mark.asyncio
async def test_runtime_reports_model_usage_after_assistant_message(tmp_path: Path) -> None:
    model = FakeModelAdapter(
        [
            [
                TextDelta(text="hello"),
                UsageEvent(usage=Usage(prompt_tokens=10, completion_tokens=3, total_tokens=13)),
                Completed(),
            ]
        ]
    )
    runtime = _runtime(tmp_path, model)
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="hi"),
    ]

    events = await _collect(runtime, messages)

    assert [event.type for event in events] == [
        "run_started",
        "message_delta",
        "model_usage_reported",
        "run_finished",
    ]
    assert events[2].payload["total_tokens"] == 13
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "hello"


@pytest.mark.asyncio
async def test_runtime_executes_one_tool_call_then_finishes(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("sample content\n", encoding="utf-8")
    model = FakeModelAdapter(
        [
            [
                ToolCallCompleted(
                    call=ToolCall(
                        id="call-1", name="read", arguments_json='{"path": "sample.txt"}'
                    )
                ),
                Completed(),
            ],
            [TextDelta(text="read complete"), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model)
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="read file"),
    ]

    events = await _collect(runtime, messages)

    assert [event.type for event in events] == [
        "run_started",
        "tool_started",
        "tool_finished",
        "message_delta",
        "run_finished",
    ]
    assert events[2].payload["result"]["status"] == "success"
    assert len(model.requests) == 2
    assert model.requests[1].messages[-1].role == "tool"
    assert "sample content" in model.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_runtime_returns_policy_denied_tool_result_to_model(tmp_path: Path) -> None:
    shell = RecordingShellTool()
    model = FakeModelAdapter(
        [
            [
                ToolCallCompleted(
                    call=ToolCall(
                        id="call-1",
                        name="sandbox_shell",
                        arguments_json='{"command": "python -m pytest"}',
                    )
                ),
                Completed(),
            ],
            [TextDelta(text="cannot run without approval"), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model, tools=[shell])
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="run tests"),
    ]

    events = await _collect(runtime, messages)

    assert [event.type for event in events] == [
        "run_started",
        "tool_finished",
        "message_delta",
        "run_finished",
    ]
    assert events[1].payload["result"]["status"] == "policy_denied"
    assert "lacks explicit non-interactive authorization" in events[1].payload["result"]["summary"]
    assert not shell.called
    assert len(model.requests) == 2
    assert "policy_denied" in model.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_runtime_blocks_dangerous_sandbox_command_before_tool_execution(
    tmp_path: Path,
) -> None:
    shell = RecordingShellTool()
    model = FakeModelAdapter(
        [
            [
                ToolCallCompleted(
                    call=ToolCall(
                        id="call-1",
                        name="sandbox_shell",
                        arguments_json='{"command": "echo ok && rm -rf /"}',
                    )
                ),
                Completed(),
            ],
            [TextDelta(text="blocked and continuing"), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model, tools=[shell], allow_shell=True)
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="run command"),
    ]

    events = await _collect(runtime, messages)

    assert [event.type for event in events] == [
        "run_started",
        "tool_finished",
        "message_delta",
        "run_finished",
    ]
    assert events[1].payload["result"]["status"] == "policy_denied"
    assert "dangerous sandbox command" in events[1].payload["result"]["summary"]
    assert not shell.called
    assert "policy_denied" in model.requests[1].messages[-1].content


@pytest.mark.asyncio
async def test_runtime_streams_text_before_tool_execution(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("sample content\n", encoding="utf-8")
    model = FakeModelAdapter(
        [
            [
                TextDelta(text="I'll inspect that. "),
                ToolCallCompleted(
                    call=ToolCall(
                        id="call-1", name="read", arguments_json='{"path": "sample.txt"}'
                    )
                ),
                Completed(),
            ],
            [TextDelta(text="Done."), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model)
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="read file"),
    ]

    events = await _collect(runtime, messages)

    assert [event.type for event in events] == [
        "run_started",
        "message_delta",
        "tool_started",
        "tool_finished",
        "message_delta",
        "run_finished",
    ]
    assert events[1].payload["text"] == "I'll inspect that. "
    assert events[4].payload["text"] == "Done."
    assert messages[2].role == "assistant"
    assert messages[2].content == "I'll inspect that. "
    assert messages[2].tool_calls[0].name == "read"
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "Done."


@pytest.mark.asyncio
async def test_runtime_does_not_retry_after_partial_text_output(tmp_path: Path) -> None:
    model = FakeModelAdapter(
        [
            [TextDelta(text="partial"), ModelError(message="temporary", retryable=True)],
            [TextDelta(text="retried"), Completed()],
        ]
    )
    runtime = _runtime(tmp_path, model)
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="hello"),
    ]

    events = await _collect(runtime, messages)

    assert [event.type for event in events] == ["run_started", "message_delta", "run_failed"]
    assert events[1].payload["text"] == "partial"
    assert events[-1].payload["reason"] == "model retry requested after partial text output"
    assert len(model.requests) == 1
    assert messages[-1].role == "user"


@pytest.mark.asyncio
async def test_runtime_fails_on_non_retryable_model_error(tmp_path: Path) -> None:
    model = FakeModelAdapter([[ModelError(message="model failed", retryable=False)]])
    runtime = _runtime(tmp_path, model)
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="hello"),
    ]

    events = await _collect(runtime, messages)

    assert [event.type for event in events] == ["run_started", "run_failed"]
    assert events[-1].payload["reason"] == "model failed"


@pytest.mark.asyncio
async def test_runtime_can_be_cancelled_during_model_stream(tmp_path: Path) -> None:
    model = CancellableModelAdapter()
    runtime = _runtime(tmp_path, model)
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="long task"),
    ]

    task = asyncio.create_task(_collect(runtime, messages))
    await asyncio.sleep(0)
    runtime.cancel()
    events = await task

    assert [event.type for event in events] == ["run_started", "run_cancelled"]

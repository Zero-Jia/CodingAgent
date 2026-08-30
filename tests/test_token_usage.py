from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from coding_agent.agent.coding_agent import CodingAgent
from coding_agent.ai.contracts import (
    CancellationSignal,
    ChatMessage,
    Completed,
    Model,
    ModelEvent,
    ModelRequest,
    TextDelta,
    Usage,
    UsageEvent,
)
from coding_agent.cli.app import _status_text, _token_usage_line
from coding_agent.config import AgentConfig
from coding_agent.runtime.context import ContextBudget, ContextCompactionResult, ContextManager
from coding_agent.runtime.token_usage import SessionTokenState


def _context() -> ContextManager:
    return ContextManager(
        ContextBudget(
            window_tokens=1_000,
            compact_threshold_tokens=800,
            keep_recent_tokens=100,
            keep_recent_messages=3,
            chars_per_token=1.0,
            summary_max_chars=1_000,
        )
    )


def test_token_state_uses_estimate_before_provider_usage() -> None:
    context = _context()
    state = SessionTokenState(context)
    messages = [ChatMessage(role="user", content="x" * 100)]

    snapshot = state.snapshot(messages)

    assert snapshot.session_total_tokens == 0
    assert snapshot.current_context_tokens == context.estimate_tokens(messages)
    assert snapshot.current_context_source == "estimated"


def test_token_state_anchors_current_context_after_provider_usage() -> None:
    context = _context()
    state = SessionTokenState(context)
    messages = [
        ChatMessage(role="user", content="x" * 1_000),
        ChatMessage(role="assistant", content="done"),
    ]

    snapshot = state.record_usage(
        Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120), messages
    )

    assert snapshot.session_prompt_tokens == 100
    assert snapshot.session_completion_tokens == 20
    assert snapshot.session_total_tokens == 120
    assert snapshot.current_context_tokens == 120
    assert snapshot.current_context_source == "anchored"

    messages.append(ChatMessage(role="tool", tool_call_id="call-1", content="y" * 30))
    snapshot = state.snapshot(messages)

    assert snapshot.current_context_tokens == 120 + context.estimate_tokens(messages[2:])
    assert snapshot.current_context_source == "anchored"


def test_token_state_resets_anchor_after_context_compaction() -> None:
    context = _context()
    state = SessionTokenState(context)
    messages = [ChatMessage(role="user", content="x" * 100)]
    state.record_usage(Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120), messages)
    compacted_messages = [ChatMessage(role="user", content="summary")]

    snapshot = state.record_compaction(
        ContextCompactionResult(
            messages=compacted_messages,
            compacted=True,
            before_tokens=900,
            after_tokens=120,
            summarized_messages=10,
            kept_messages=2,
            summary_chars=80,
        ),
        compacted_messages,
    )

    assert snapshot.last_compacted_tokens_saved == 780
    assert snapshot.total_compacted_tokens_saved == 780
    assert snapshot.current_context_tokens == context.estimate_tokens(compacted_messages)
    assert snapshot.current_context_source == "estimated"
    assert snapshot.session_total_tokens == 120


class _UsageModelAdapter:
    def __init__(self) -> None:
        self.model = Model(provider="fake", name="fake-model")

    def stream(
        self, request: ModelRequest, signal: CancellationSignal
    ) -> AsyncIterator[ModelEvent]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="done")
        yield UsageEvent(usage=Usage(prompt_tokens=11, completion_tokens=4, total_tokens=15))
        yield Completed()


@pytest.mark.asyncio
async def test_chat_session_persists_token_usage_summary(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    agent = CodingAgent(AgentConfig(workspace=tmp_path), _UsageModelAdapter())
    session = await agent.start_chat("session-token")

    events = [event async for event in session.send("hello")]

    assert [event.type for event in events] == [
        "run_started",
        "message_delta",
        "model_usage_reported",
        "token_usage_updated",
        "run_finished",
    ]
    token_event = events[3]
    assert token_event.payload["session_total_tokens"] == 15
    assert token_event.payload["current_context_tokens"] == 15
    assert token_event.payload["current_context_source"] == "anchored"
    assert session.summary.total_tokens == 15
    assert session.summary.current_context_tokens == 15

    summaries = await agent.sessions.list_summaries()
    assert summaries[0].total_prompt_tokens == 11
    assert summaries[0].total_completion_tokens == 4
    assert summaries[0].total_tokens == 15
    assert any(
        event.event_type == "token_usage_updated"
        for event in await agent.resume("session-token")
    )


@pytest.mark.asyncio
async def test_status_text_and_token_line_include_context_ratio(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    agent = CodingAgent(AgentConfig(workspace=tmp_path), _UsageModelAdapter())
    session = await agent.start_chat("session-status")

    _ = [event async for event in session.send("hello")]
    text = _status_text(session)

    assert "Token 消耗" in text
    assert "当前上下文" in text
    assert "provider usage 锚定" in text

    line = _token_usage_line(
        {
            "session_total_tokens": 125,
            "session_prompt_tokens": 100,
            "session_completion_tokens": 25,
            "current_context_tokens": 500,
            "context_window_tokens": 1_000,
            "context_usage_ratio": 0.5,
            "current_context_source": "anchored",
        }
    )
    assert "125" in line
    assert "50.0%" in line

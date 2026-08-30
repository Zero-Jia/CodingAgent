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
    ToolCall,
)
from coding_agent.config import AgentConfig
from coding_agent.runtime.context import COMPACT_SUMMARY_PREFIX, ContextBudget, ContextManager


def _manager(
    *,
    threshold: int = 80,
    keep_tokens: int = 20,
    keep_messages: int = 3,
    summary_chars: int = 2_000,
) -> ContextManager:
    return ContextManager(
        ContextBudget(
            window_tokens=200,
            compact_threshold_tokens=threshold,
            keep_recent_tokens=keep_tokens,
            keep_recent_messages=keep_messages,
            chars_per_token=1.0,
            summary_max_chars=summary_chars,
        )
    )


def test_context_manager_leaves_small_history_unchanged() -> None:
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="short"),
    ]

    result = _manager(threshold=1_000).prepare(messages)

    assert not result.compacted
    assert result.messages == messages
    assert result.summarized_messages == 0


def test_context_manager_compacts_old_prefix_and_keeps_recent_tail() -> None:
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="old request " + "x" * 1_000),
        ChatMessage(role="assistant", content="old answer " + "y" * 1_000),
        ChatMessage(role="user", content="recent request"),
        ChatMessage(role="assistant", content="recent answer"),
        ChatMessage(role="user", content="latest request"),
    ]

    result = _manager(threshold=100, keep_tokens=1_000, keep_messages=3).prepare(messages)

    assert result.compacted
    assert result.messages[0].role == "system"
    assert result.messages[1].role == "user"
    assert result.messages[1].content.startswith(COMPACT_SUMMARY_PREFIX)
    assert "old request" in result.messages[1].content
    assert result.messages[-3:] == messages[-3:]
    assert result.summarized_messages == 2
    assert result.kept_messages == 3
    assert result.after_tokens < result.before_tokens


def test_context_manager_does_not_split_tool_call_and_result_pair() -> None:
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="old " + "x" * 3_000),
        ChatMessage(
            role="assistant",
            content="calling",
            tool_calls=[
                ToolCall(id="call-1", name="read", arguments_json='{"path": "a.txt"}'),
                ToolCall(id="call-2", name="read", arguments_json='{"path": "b.txt"}'),
            ],
        ),
        ChatMessage(role="tool", tool_call_id="call-1", content="result-a"),
        ChatMessage(role="tool", tool_call_id="call-2", content="result-b"),
    ]

    result = _manager(threshold=50, keep_tokens=1, keep_messages=1).prepare(messages)

    assert result.compacted
    retained_tool_results = {
        message.tool_call_id for message in result.messages if message.role == "tool"
    }
    retained_tool_calls = {
        call.id for message in result.messages for call in message.tool_calls
    }
    assert retained_tool_results <= retained_tool_calls
    assert result.messages[-3:] == messages[-3:]


def test_context_summary_is_bounded_and_redacted() -> None:
    messages = [
        ChatMessage(role="system", content="system"),
        ChatMessage(role="user", content="api_key=secret-value " + "x" * 1_000),
        ChatMessage(role="assistant", content="old answer " + "y" * 1_000),
        ChatMessage(role="user", content="latest"),
    ]

    result = _manager(threshold=50, keep_tokens=1, keep_messages=1, summary_chars=1_000).prepare(
        messages
    )

    summary = result.messages[1].content
    assert result.compacted
    assert len(summary) <= 1_000
    assert "secret-value" not in summary
    assert "[REDACTED]" in summary


class _FakeModelAdapter:
    def __init__(self) -> None:
        self.model = Model(provider="fake", name="fake-model")

    def stream(
        self, request: ModelRequest, signal: CancellationSignal
    ) -> AsyncIterator[ModelEvent]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="done")
        yield Completed()


@pytest.mark.asyncio
async def test_chat_session_persists_compacted_checkpoint(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    agent = CodingAgent(
        AgentConfig(
            workspace=tmp_path,
            context_compact_threshold_tokens=200,
            context_keep_recent_tokens=1,
            context_keep_recent_messages=1,
            context_chars_per_token=1.0,
            context_summary_max_chars=2_000,
        ),
        _FakeModelAdapter(),
    )
    session = await agent.start_chat("session-context")
    session.messages.extend(
        [
            ChatMessage(role="user", content="old request " + "x" * 120),
            ChatMessage(role="assistant", content="old answer " + "y" * 2_000),
        ]
    )

    events = [event async for event in session.send("latest request")]
    checkpoint = await agent.sessions.load_checkpoint("session-context")

    assert events[0].type == "context_compacted"
    assert checkpoint is not None
    assert checkpoint.messages[1].content.startswith(COMPACT_SUMMARY_PREFIX)
    assert checkpoint.messages[-1].role == "assistant"
    assert checkpoint.messages[-1].content == "done"
    session_events = await agent.resume("session-context")
    assert any(event.event_type == "context_compacted" for event in session_events)


def test_context_manager_rejects_invalid_budget() -> None:
    with pytest.raises(ValueError, match="chars_per_token"):
        ContextManager(ContextBudget(chars_per_token=0))
    with pytest.raises(ValueError, match="window_tokens"):
        ContextManager(ContextBudget(window_tokens=0))
    with pytest.raises(ValueError, match="compact_threshold_tokens"):
        ContextManager(ContextBudget(compact_threshold_tokens=0))

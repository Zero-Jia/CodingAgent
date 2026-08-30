"""Session-level token usage accounting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from coding_agent.ai.contracts import ChatMessage, Usage
from coding_agent.runtime.context import ContextCompactionResult, ContextManager

ContextTokenSource = Literal["estimated", "anchored"]
TokenEventSource = Literal[
    "context_estimate",
    "context_compaction",
    "context_cleared",
    "provider_usage",
]


@dataclass(frozen=True)
class TokenSnapshot:
    session_prompt_tokens: int
    session_completion_tokens: int
    session_total_tokens: int
    current_context_tokens: int
    context_window_tokens: int
    context_usage_ratio: float
    current_context_source: ContextTokenSource
    last_compact_before_tokens: int
    last_compact_after_tokens: int
    last_compacted_tokens_saved: int
    total_compacted_tokens_saved: int

    def event_payload(self, source: TokenEventSource) -> dict[str, object]:
        return {
            "source": source,
            "session_prompt_tokens": self.session_prompt_tokens,
            "session_completion_tokens": self.session_completion_tokens,
            "session_total_tokens": self.session_total_tokens,
            "current_context_tokens": self.current_context_tokens,
            "current_context_source": self.current_context_source,
            "context_window_tokens": self.context_window_tokens,
            "context_usage_ratio": self.context_usage_ratio,
            "last_compact_before_tokens": self.last_compact_before_tokens,
            "last_compact_after_tokens": self.last_compact_after_tokens,
            "last_compacted_tokens_saved": self.last_compacted_tokens_saved,
            "total_compacted_tokens_saved": self.total_compacted_tokens_saved,
        }


class SessionTokenState:
    """Tracks exact provider usage totals plus best-effort current context size.

    Provider-reported usage is the source of truth for session spend after each
    completed model request. Between provider reports, the current context size
    uses that last real usage value as an anchor and estimates only newly added
    tail messages.
    """

    def __init__(self, context: ContextManager) -> None:
        self._context = context
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_total_tokens = 0
        self._anchor_context_tokens = 0
        self._anchor_message_count = 0
        self.last_compact_before_tokens = 0
        self.last_compact_after_tokens = 0
        self.last_compacted_tokens_saved = 0
        self.total_compacted_tokens_saved = 0

    def restore(
        self,
        *,
        session_prompt_tokens: int = 0,
        session_completion_tokens: int = 0,
        session_total_tokens: int = 0,
        last_compact_before_tokens: int = 0,
        last_compact_after_tokens: int = 0,
        last_compacted_tokens_saved: int = 0,
        total_compacted_tokens_saved: int = 0,
    ) -> None:
        self.session_prompt_tokens = max(0, session_prompt_tokens)
        self.session_completion_tokens = max(0, session_completion_tokens)
        self.session_total_tokens = max(
            0,
            session_total_tokens
            or self.session_prompt_tokens + self.session_completion_tokens,
        )
        self.last_compact_before_tokens = max(0, last_compact_before_tokens)
        self.last_compact_after_tokens = max(0, last_compact_after_tokens)
        self.last_compacted_tokens_saved = max(0, last_compacted_tokens_saved)
        self.total_compacted_tokens_saved = max(0, total_compacted_tokens_saved)
        self._reset_anchor()

    def record_usage(self, usage: Usage, messages: list[ChatMessage]) -> TokenSnapshot:
        prompt_tokens = max(0, usage.prompt_tokens)
        completion_tokens = max(0, usage.completion_tokens)
        total_tokens = max(0, usage.total_tokens or prompt_tokens + completion_tokens)
        context_tokens = max(total_tokens, prompt_tokens + completion_tokens)

        self.session_prompt_tokens += prompt_tokens
        self.session_completion_tokens += completion_tokens
        self.session_total_tokens += total_tokens
        self._anchor_context_tokens = context_tokens
        self._anchor_message_count = len(messages)
        return self.snapshot(messages)

    def record_compaction(
        self, result: ContextCompactionResult, messages: list[ChatMessage]
    ) -> TokenSnapshot:
        saved = max(0, result.before_tokens - result.after_tokens)
        self.last_compact_before_tokens = result.before_tokens
        self.last_compact_after_tokens = result.after_tokens
        self.last_compacted_tokens_saved = saved
        self.total_compacted_tokens_saved += saved
        self._reset_anchor()
        return self.snapshot(messages)

    def reset_context_anchor(self, messages: list[ChatMessage]) -> TokenSnapshot:
        self.last_compact_before_tokens = 0
        self.last_compact_after_tokens = 0
        self.last_compacted_tokens_saved = 0
        self._reset_anchor()
        return self.snapshot(messages)

    def snapshot(self, messages: list[ChatMessage]) -> TokenSnapshot:
        current_context_tokens, source = self._current_context_tokens(messages)
        window = self._context.budget.window_tokens
        ratio = current_context_tokens / window if window > 0 else 0.0
        return TokenSnapshot(
            session_prompt_tokens=self.session_prompt_tokens,
            session_completion_tokens=self.session_completion_tokens,
            session_total_tokens=self.session_total_tokens,
            current_context_tokens=current_context_tokens,
            context_window_tokens=window,
            context_usage_ratio=ratio,
            current_context_source=source,
            last_compact_before_tokens=self.last_compact_before_tokens,
            last_compact_after_tokens=self.last_compact_after_tokens,
            last_compacted_tokens_saved=self.last_compacted_tokens_saved,
            total_compacted_tokens_saved=self.total_compacted_tokens_saved,
        )

    def _current_context_tokens(
        self, messages: list[ChatMessage]
    ) -> tuple[int, ContextTokenSource]:
        if self._anchor_context_tokens > 0 and self._anchor_message_count <= len(messages):
            tail = messages[self._anchor_message_count :]
            return self._anchor_context_tokens + self._context.estimate_tokens(tail), "anchored"
        return self._context.estimate_tokens(messages), "estimated"

    def _reset_anchor(self) -> None:
        self._anchor_context_tokens = 0
        self._anchor_message_count = 0

"""会话 JSONL 持久化与可恢复历史。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from coding_agent.ai.contracts import ChatMessage
from coding_agent.tracing.store import redact


class SessionEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: str
    run_id: str
    event_type: str
    payload: dict[str, object] = Field(default_factory=dict)


class ConversationCheckpoint(BaseModel):
    """恢复连续会话所需的最小状态；事件日志仍是审计记录。"""

    schema_version: int = 1
    session_id: str
    workspace: str
    model_provider: str
    model_name: str
    messages: list[ChatMessage]
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionStore(Protocol):
    async def append(self, event: SessionEvent) -> None: ...
    async def load(self, session_id: str) -> list[SessionEvent]: ...
    async def save_checkpoint(self, checkpoint: ConversationCheckpoint) -> None: ...
    async def load_checkpoint(self, session_id: str) -> ConversationCheckpoint | None: ...


class JsonlSessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def append(self, event: SessionEvent) -> None:
        path = self.root / "sessions" / f"{event.session_id}.jsonl"
        line = event.model_dump_json() + "\n"
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(_append, path, line)

    async def load(self, session_id: str) -> list[SessionEvent]:
        path = self.root / "sessions" / f"{session_id}.jsonl"
        if not path.exists():
            return []
        return await asyncio.to_thread(_load, path)

    async def save_checkpoint(self, checkpoint: ConversationCheckpoint) -> None:
        path = self.root / "checkpoints" / f"{checkpoint.session_id}.json"
        persisted = _redacted_checkpoint(checkpoint)
        persisted.updated_at = datetime.now(UTC)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(_atomic_write, path, persisted.model_dump_json(indent=2))

    async def load_checkpoint(self, session_id: str) -> ConversationCheckpoint | None:
        path = self.root / "checkpoints" / f"{session_id}.json"
        if not path.exists():
            return None
        return await asyncio.to_thread(_load_checkpoint, path)


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _load(path: Path) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(SessionEvent.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError):
            continue
    return events


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _load_checkpoint(path: Path) -> ConversationCheckpoint | None:
    try:
        return ConversationCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _redacted_checkpoint(checkpoint: ConversationCheckpoint) -> ConversationCheckpoint:
    messages: list[ChatMessage] = []
    for message in checkpoint.messages:
        content = redact(message.content)
        calls = [
            call.model_copy(update={"arguments_json": redact(call.arguments_json)})
            for call in message.tool_calls
        ]
        messages.append(
            message.model_copy(
                update={
                    "content": content if isinstance(content, str) else "[REDACTED]",
                    "tool_calls": calls,
                }
            )
        )
    return checkpoint.model_copy(update={"messages": messages})

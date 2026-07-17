"""脱敏 JSONL 追踪、产物和应用日志存储。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

_SECRET = re.compile(r"(?i)(api[_-]?key|authorization|token|password)\s*[:=]\s*[^\s,]+")


class TraceEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    level: str = "info"
    session_id: str
    run_id: str
    turn_id: str = ""
    span_id: str = ""
    parent_span_id: str | None = None
    event_type: str
    component: str
    duration_ms: float | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    workspace_revision: str = ""


class TraceStore(Protocol):
    async def append(self, event: TraceEvent) -> None: ...


class ArtifactStore(Protocol):
    async def put(self, session_id: str, run_id: str, name: str, content: str) -> Path: ...


def redact(value: object) -> object:
    if isinstance(value, str):
        return _SECRET.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return {
            str(key): redact(item)
            for key, item in value.items()
            if str(key).lower() not in {"authorization", "api_key", "token", "password"}
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class JsonlTraceStore:
    def __init__(self, root: Path, level: str = "redacted") -> None:
        self.root = root
        self.level = level

    async def append(self, event: TraceEvent) -> None:
        path = self.root / "traces" / event.session_id / f"{event.run_id}.jsonl"
        payload = event.model_dump(mode="json")
        if self.level == "redacted":
            payload["payload"] = redact(payload["payload"])
        await _append_jsonl(path, payload)


class JsonlArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def put(self, session_id: str, run_id: str, name: str, content: str) -> str:
        safe_name = Path(name).name
        path = self.root / "artifacts" / session_id / run_id / safe_name
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        redacted_content = redact(content)
        safe_content = redacted_content if isinstance(redacted_content, str) else ""
        await asyncio.to_thread(
            path.write_text,
            safe_content,
            encoding="utf-8",
        )
        return str(path.relative_to(self.root))


class ApplicationLog:
    def __init__(self, root: Path) -> None:
        self.path = root / "logs" / "application.jsonl"

    async def write(self, level: str, message: str, **fields: object) -> None:
        await _append_jsonl(
            self.path,
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": level,
                "message": redact(message),
                "fields": redact(fields),
            },
        )


async def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(_append_text, path, line)


def _append_text(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def output_summary(output: str, maximum: int = 1_000) -> dict[str, object]:
    return {
        "chars": len(output),
        "sha256": hashlib.sha256(output.encode()).hexdigest(),
        "preview": redact(output[:maximum]),
    }

"""Docker 沙箱的供应商无关契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

SandboxStatus = Literal["success", "execution_error", "cancelled", "timeout", "unavailable"]


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: int = 60
    memory_mb: int = 768
    cpu_count: float = 1.0
    pids_limit: int = 128
    tmpfs_mb: int = 512


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    sha256: str
    size: int


@dataclass
class WorkspaceSnapshot:
    root: Path
    archive: Path
    files: dict[str, SnapshotFile]


@dataclass(frozen=True)
class SandboxRequest:
    command: str
    snapshot: WorkspaceSnapshot
    image: str
    limits: SandboxLimits


@dataclass
class SandboxResult:
    status: SandboxStatus
    summary: str
    output: str = ""
    exit_code: int | None = None
    patch: str = ""
    changed_files: list[str] = field(default_factory=list)


class SandboxExecutor(Protocol):
    async def execute(self, request: SandboxRequest, cancellation: object) -> SandboxResult: ...

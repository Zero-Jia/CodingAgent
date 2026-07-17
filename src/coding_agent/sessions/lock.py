"""跨终端的会话独占锁，防止同一会话被并发写入。"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class SessionLockedError(RuntimeError):
    """会话已被其他仍在运行的终端持有。"""


@dataclass(frozen=True)
class SessionLease:
    path: Path
    session_id: str

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def acquire_session_lock(
    root: Path, session_id: str, *, force_unlock: bool = False
) -> SessionLease:
    path = root / "locks" / f"{session_id}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    if force_unlock and path.exists():
        metadata = _read_metadata(path)
        if _pid_running(metadata.get("pid")):
            raise SessionLockedError(_message(metadata))
        path.unlink(missing_ok=True)
    payload = {
        "session_id": session_id,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
    except FileExistsError as error:
        raise SessionLockedError(_message(_read_metadata(path))) from error
    return SessionLease(path=path, session_id=session_id)


def _read_metadata(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _pid_running(value: object) -> bool:
    if not isinstance(value, int) or value <= 0:
        return False
    try:
        os.kill(value, 0)
    except OSError:
        return False
    return True


def _message(metadata: dict[str, object]) -> str:
    pid = metadata.get("pid", "未知")
    host = metadata.get("hostname", "未知")
    started = metadata.get("started_at", "未知")
    return f"会话已被占用：pid={pid}，主机={host}，开始时间={started}"

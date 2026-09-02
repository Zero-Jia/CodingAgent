"""Validate, persist, and apply Git patches produced by the sandbox."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from sqlalchemy import Connection, Engine, select, update

from coding_agent.db import tables
from coding_agent.sandbox.contracts import WorkspaceSnapshot
from coding_agent.tracing.store import redact
from coding_agent.workspace.security import WorkspacePathPolicy

PatchStatus = Literal["pending", "applying", "applied", "rejected", "invalidated"]
FINAL_PATCH_STATUSES: frozenset[PatchStatus] = frozenset(
    {"applied", "rejected", "invalidated"}
)
MAX_PATCH_CHARS = 2_000_000
MAX_DIFF_PREVIEW_CHARS = 4_000
MAX_REASON_CHARS = 2_000

_DIFF_PATH = re.compile(r"^diff --git a/(.+) b/(.+)$", re.MULTILINE)
_MODE_CHANGE = re.compile(r"^(?:old|new) mode ", re.MULTILINE)
_NEW_FILE_MODE = re.compile(r"^new file mode (.+)$", re.MULTILINE)
_DELETED_FILE_MODE = re.compile(r"^deleted file mode (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class PendingPatch:
    patch_id: str
    patch: str
    changed_files: list[str]
    snapshot_files: dict[str, str]
    schema_version: int = 1
    session_id: str = "standalone"
    run_id: str = ""
    workspace: str = ""
    status: PatchStatus = "pending"
    patch_sha256: str = ""
    diff_preview: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    applied_at: datetime | None = None
    invalidated_reason: str = ""
    applied_by: str = ""


class PatchStore(Protocol):
    async def create(self, patch: PendingPatch) -> PendingPatch: ...
    async def get(self, patch_id: str) -> PendingPatch: ...
    async def claim_for_apply(self, patch_id: str) -> PendingPatch: ...
    async def update_status(
        self,
        patch_id: str,
        status: PatchStatus,
        reason: str = "",
        *,
        applied_by: str = "",
    ) -> PendingPatch: ...


class InMemoryPatchStore:
    """Process-local patch state used by the default JSONL/local mode."""

    def __init__(self) -> None:
        self._items: dict[str, PendingPatch] = {}
        self._lock = asyncio.Lock()

    async def create(self, patch: PendingPatch) -> PendingPatch:
        async with self._lock:
            if patch.patch_id in self._items:
                raise ValueError(f"patch already exists: {patch.patch_id}")
            self._items[patch.patch_id] = patch
            return patch

    async def get(self, patch_id: str) -> PendingPatch:
        async with self._lock:
            patch = self._items.get(patch_id)
        if patch is None:
            raise KeyError(patch_id)
        return patch

    async def claim_for_apply(self, patch_id: str) -> PendingPatch:
        async with self._lock:
            patch = self._items.get(patch_id)
            if patch is None:
                raise KeyError(patch_id)
            if patch.status != "pending":
                raise ValueError(f"pending patch is {patch.status}")
            updated = replace(patch, status="applying", updated_at=datetime.now(UTC))
            self._items[patch_id] = updated
            return updated

    async def update_status(
        self,
        patch_id: str,
        status: PatchStatus,
        reason: str = "",
        *,
        applied_by: str = "",
    ) -> PendingPatch:
        async with self._lock:
            patch = self._items.get(patch_id)
            if patch is None:
                raise KeyError(patch_id)
            if patch.status in FINAL_PATCH_STATUSES:
                return patch
            now = datetime.now(UTC)
            updated = replace(
                patch,
                status=status,
                updated_at=now,
                applied_at=now if status == "applied" else patch.applied_at,
                invalidated_reason=reason[:MAX_REASON_CHARS],
                applied_by=applied_by[:128],
            )
            self._items[patch_id] = updated
            return updated


class MySqlPatchStore:
    """SQLAlchemy-backed patch package store for MySQL deployments."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    async def create(self, patch: PendingPatch) -> PendingPatch:
        return await asyncio.to_thread(self._create_sync, patch)

    async def get(self, patch_id: str) -> PendingPatch:
        return await asyncio.to_thread(self._get_sync, patch_id)

    async def claim_for_apply(self, patch_id: str) -> PendingPatch:
        return await asyncio.to_thread(self._claim_for_apply_sync, patch_id)

    async def update_status(
        self,
        patch_id: str,
        status: PatchStatus,
        reason: str = "",
        *,
        applied_by: str = "",
    ) -> PendingPatch:
        return await asyncio.to_thread(
            self._update_status_sync,
            patch_id,
            status,
            reason[:MAX_REASON_CHARS],
            applied_by[:128],
        )

    def _create_sync(self, patch: PendingPatch) -> PendingPatch:
        with self.engine.begin() as connection:
            _ensure_session(connection, patch.session_id)
            _ensure_run(
                connection,
                patch.session_id,
                patch.run_id,
                patch.created_at or datetime.now(UTC),
            )
            connection.execute(tables.patches.insert().values(**_record_values(patch)))
        return patch

    def _get_sync(self, patch_id: str) -> PendingPatch:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(tables.patches).where(tables.patches.c.patch_id == patch_id)
                )
                .mappings()
                .first()
            )
        if row is None:
            raise KeyError(patch_id)
        return _record_from_row(row)

    def _claim_for_apply_sync(self, patch_id: str) -> PendingPatch:
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(tables.patches)
                    .where(tables.patches.c.patch_id == patch_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                raise KeyError(patch_id)
            current = _record_from_row(row)
            if current.status != "pending":
                raise ValueError(f"pending patch is {current.status}")
            now = datetime.now(UTC)
            connection.execute(
                update(tables.patches)
                .where(tables.patches.c.patch_id == patch_id)
                .values(status="applying", updated_at=now)
            )
            return replace(current, status="applying", updated_at=now)

    def _update_status_sync(
        self,
        patch_id: str,
        status: PatchStatus,
        reason: str,
        applied_by: str,
    ) -> PendingPatch:
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(tables.patches)
                    .where(tables.patches.c.patch_id == patch_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                raise KeyError(patch_id)
            current = _record_from_row(row)
            if current.status in FINAL_PATCH_STATUSES:
                return current
            now = datetime.now(UTC)
            values: dict[str, object] = {
                "status": status,
                "updated_at": now,
                "invalidated_reason": reason,
                "applied_by": applied_by,
            }
            if status == "applied":
                values["applied_at"] = now
            connection.execute(
                update(tables.patches)
                .where(tables.patches.c.patch_id == patch_id)
                .values(**values)
            )
            return replace(
                current,
                status=status,
                updated_at=now,
                applied_at=now if status == "applied" else current.applied_at,
                invalidated_reason=reason,
                applied_by=applied_by,
            )


class PatchRegistry:
    """待审批补丁集合。

    默认模式使用进程内 store，保持本地 CLI 行为轻量；MySQL 模式注入
    MySqlPatchStore 后，patch package 可跨 registry 和服务重启恢复。
    """

    def __init__(self, workspace: Path, *, store: PatchStore | None = None) -> None:
        self.workspace = workspace.resolve()
        self.paths = WorkspacePathPolicy(self.workspace)
        self.store = store or InMemoryPatchStore()

    async def add(
        self,
        patch: str,
        changed_files: list[str],
        snapshot: WorkspaceSnapshot,
        *,
        session_id: str = "",
        run_id: str = "",
    ) -> str | None:
        if not patch:
            if changed_files:
                raise ValueError("sandbox reported changed files without a patch")
            return None
        if len(patch) > MAX_PATCH_CHARS:
            raise ValueError("sandbox patch is too large to persist safely")
        patch_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        pending = PendingPatch(
            patch_id=patch_id,
            patch=patch,
            changed_files=list(changed_files),
            snapshot_files={path: item.sha256 for path, item in snapshot.files.items()},
            session_id=session_id or "standalone",
            run_id=run_id or f"patch-run-{uuid.uuid4()}",
            workspace=str(self.workspace),
            status="pending",
            patch_sha256=_sha256_text(patch),
            diff_preview=_redacted_preview(patch),
            created_at=now,
            updated_at=now,
        )
        valid, reason = self._validate_structure(pending)
        if not valid:
            raise ValueError(f"unsafe sandbox patch: {reason}")
        await self.store.create(pending)
        return patch_id

    async def approval_details(self, patch_id: str) -> dict[str, object]:
        try:
            patch = await self.store.get(patch_id)
        except KeyError:
            return {"patch_id": patch_id, "status": "unavailable"}
        return {
            "patch_id": patch_id,
            "status": patch.status,
            "changed_files": patch.changed_files,
            "diff_preview": patch.diff_preview,
            "patch_sha256": patch.patch_sha256,
            "created_at": patch.created_at.isoformat() if patch.created_at else "",
            "invalidated_reason": patch.invalidated_reason,
        }

    async def apply(
        self, patch_id: str, *, applied_by: str = "runtime"
    ) -> tuple[bool, str, list[str]]:
        try:
            pending = await self.store.claim_for_apply(patch_id)
        except KeyError:
            return False, "pending patch was not found; run the sandbox command again", []
        except ValueError as error:
            return False, str(error), []
        valid, reason = self._validate(pending)
        if not valid:
            await self._invalidate(pending.patch_id, reason)
            return False, reason, []
        applied, summary, files = await asyncio.to_thread(self._apply_sync, pending)
        if not applied:
            await self._invalidate(pending.patch_id, summary)
            return False, summary, []
        await self.store.update_status(
            pending.patch_id,
            "applied",
            "sandbox patch applied",
            applied_by=applied_by,
        )
        return True, summary, files

    async def reject(self, patch_id: str, reason: str = "") -> None:
        try:
            patch = await self.store.get(patch_id)
        except KeyError:
            return
        if patch.status == "pending":
            await self.store.update_status(
                patch_id,
                "rejected",
                reason or "patch approval was rejected",
                applied_by="approval",
            )

    async def _invalidate(self, patch_id: str, reason: str) -> None:
        try:
            await self.store.update_status(
                patch_id,
                "invalidated",
                reason[:MAX_REASON_CHARS],
                applied_by="runtime",
            )
        except KeyError:
            return

    def _validate(self, pending: PendingPatch) -> tuple[bool, str]:
        valid, reason = self._validate_structure(pending)
        if not valid:
            return valid, reason
        if pending.patch_sha256 and pending.patch_sha256 != _sha256_text(pending.patch):
            return False, "patch content hash does not match the persisted package"
        if not self._is_git_worktree():
            return False, "patch writeback requires a Git working tree"
        paths = _patch_paths(pending.patch)
        for relative in paths:
            expected = pending.snapshot_files.get(relative)
            current = self.workspace / relative
            if expected is None:
                if current.exists() or current.is_symlink():
                    return False, f"new file now exists: {relative}"
            elif not current.is_file() or current.is_symlink() or _sha256(current) != expected:
                return False, f"workspace changed since sandbox snapshot: {relative}"
        return True, "ok"

    def _is_git_worktree(self) -> bool:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _validate_structure(self, pending: PendingPatch) -> tuple[bool, str]:
        if "GIT binary patch" in pending.patch:
            return False, "binary patches are not supported"
        if "Subproject commit" in pending.patch:
            return False, "submodule patches are not supported"
        if _MODE_CHANGE.search(pending.patch):
            return False, "file mode changes are not supported"
        file_modes = [
            *_NEW_FILE_MODE.findall(pending.patch),
            *_DELETED_FILE_MODE.findall(pending.patch),
        ]
        for mode in file_modes:
            if mode != "100644":
                return False, "symlink or executable file patches are not supported"
        pairs = _DIFF_PATH.findall(pending.patch)
        if not pairs:
            return False, "patch does not contain a standard Git file diff"
        paths: set[str] = set()
        for before, after in pairs:
            if before != after:
                return False, "renames and copies are not supported"
            relative, reason = self._safe_relative_path(before)
            if relative is None:
                return False, reason
            paths.add(relative)
        try:
            reported = {self._normalise_reported_path(path) for path in pending.changed_files}
        except ValueError as error:
            return False, str(error)
        if len(reported) != len(pending.changed_files) or reported != paths:
            return False, "patch file list does not exactly match the diff"
        return True, "ok"

    def _safe_relative_path(self, raw: str) -> tuple[str | None, str]:
        if not raw or raw.startswith('"') or "\\" in raw or ":" in raw or "\x00" in raw:
            return None, f"patch contains an unsupported path: {raw}"
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts or relative == PurePosixPath("."):
            return None, f"patch touches a protected path: {raw}"
        path = self.workspace.joinpath(*relative.parts)
        if self.paths.is_protected(path):
            return None, f"patch touches a protected path: {raw}"
        return relative.as_posix(), "ok"

    def _normalise_reported_path(self, raw: str) -> str:
        relative, reason = self._safe_relative_path(raw)
        if relative is None:
            raise ValueError(reason)
        return relative

    def _apply_sync(self, pending: PendingPatch) -> tuple[bool, str, list[str]]:
        commands = (
            ["git", "apply", "--check", "--verbose", "--whitespace=nowarn", "-"],
            ["git", "apply", "--verbose", "--whitespace=nowarn", "-"],
        )
        for arguments in commands:
            result = subprocess.run(
                arguments,
                cwd=self.workspace,
                # Git patches must retain LF line endings. subprocess text mode
                # translates them to CRLF on Windows, which makes git apply fail.
                input=pending.patch.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            diagnostics = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            if result.returncode != 0 or "Skipped patch" in diagnostics:
                return False, (diagnostics or "git apply failed").strip(), []
        return True, "sandbox patch applied", pending.changed_files


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _patch_paths(patch: str) -> set[str]:
    return {before for before, after in _DIFF_PATH.findall(patch) if before == after}


def _redacted_preview(patch: str) -> str:
    value = redact(patch[:MAX_DIFF_PREVIEW_CHARS])
    return value if isinstance(value, str) else str(value)


def _ensure_session(connection: Connection, session_id: str) -> None:
    exists = connection.execute(
        select(tables.sessions.c.session_id).where(tables.sessions.c.session_id == session_id)
    ).first()
    if exists is not None:
        return
    now = datetime.now(UTC)
    connection.execute(
        tables.sessions.insert().values(
            session_id=session_id,
            workspace="",
            model_name="",
            created_at=now,
            updated_at=now,
            last_user_message_preview="",
            last_plan_failure="",
        )
    )


def _ensure_run(connection: Connection, session_id: str, run_id: str, started_at: datetime) -> None:
    exists = connection.execute(
        select(tables.runs.c.run_id).where(tables.runs.c.run_id == run_id)
    ).first()
    if exists is not None:
        return
    connection.execute(
        tables.runs.insert().values(
            run_id=run_id,
            session_id=session_id,
            status="running",
            started_at=started_at,
            last_error="",
        )
    )


def _record_values(patch: PendingPatch) -> dict[str, object]:
    return {
        "patch_id": patch.patch_id,
        "schema_version": patch.schema_version,
        "session_id": patch.session_id,
        "run_id": patch.run_id,
        "workspace": patch.workspace,
        "status": patch.status,
        "patch_sha256": patch.patch_sha256,
        "patch_text": patch.patch,
        "patch_chars": len(patch.patch),
        "changed_files": patch.changed_files,
        "snapshot_files": patch.snapshot_files,
        "diff_preview": patch.diff_preview,
        "created_at": patch.created_at or datetime.now(UTC),
        "updated_at": patch.updated_at or datetime.now(UTC),
        "applied_at": patch.applied_at,
        "invalidated_reason": patch.invalidated_reason,
        "applied_by": patch.applied_by,
    }


def _record_from_row(row: Any) -> PendingPatch:
    return PendingPatch(
        patch_id=str(row["patch_id"]),
        schema_version=int(row["schema_version"]),
        session_id=str(row["session_id"]),
        run_id=str(row["run_id"]),
        workspace=str(row["workspace"]),
        status=_patch_status(str(row["status"])),
        patch_sha256=str(row["patch_sha256"]),
        patch=str(row["patch_text"]),
        changed_files=_string_list(row["changed_files"]),
        snapshot_files=_string_dict(row["snapshot_files"]),
        diff_preview=str(row["diff_preview"]),
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
        applied_at=_optional_datetime(row["applied_at"]),
        invalidated_reason=str(row["invalidated_reason"]),
        applied_by=str(row["applied_by"]),
    )


def _patch_status(value: str) -> PatchStatus:
    if value in {"pending", "applying", "applied", "rejected", "invalidated"}:
        return value  # type: ignore[return-value]
    return "invalidated"


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    raise ValueError(f"expected datetime-compatible value, got {type(value).__name__}")


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _datetime(value)


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _string_dict(value: object) -> dict[str, str]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}

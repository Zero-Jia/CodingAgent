"""验证并应用由沙箱生成的 Git 补丁。"""

from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from coding_agent.sandbox.contracts import WorkspaceSnapshot
from coding_agent.tracing.store import redact
from coding_agent.workspace.security import WorkspacePathPolicy

_DIFF_PATH = re.compile(r"^diff --git a/(.+) b/(.+)$", re.MULTILINE)
_MODE_CHANGE = re.compile(r"^(?:old|new) mode ", re.MULTILINE)
_NEW_FILE_MODE = re.compile(r"^new file mode (.+)$", re.MULTILINE)
_DELETED_FILE_MODE = re.compile(r"^deleted file mode (.+)$", re.MULTILINE)


@dataclass
class PendingPatch:
    patch_id: str
    patch: str
    changed_files: list[str]
    snapshot_files: dict[str, str]


class PatchRegistry:
    """单个 AgentRuntime 内有效的待审批补丁集合。"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.paths = WorkspacePathPolicy(self.workspace)
        self._patches: dict[str, PendingPatch] = {}

    def add(self, patch: str, changed_files: list[str], snapshot: WorkspaceSnapshot) -> str | None:
        if not patch:
            if changed_files:
                raise ValueError("sandbox reported changed files without a patch")
            return None
        patch_id = str(uuid.uuid4())
        pending = PendingPatch(
            patch_id=patch_id,
            patch=patch,
            changed_files=changed_files,
            snapshot_files={path: item.sha256 for path, item in snapshot.files.items()},
        )
        valid, reason = self._validate_structure(pending)
        if not valid:
            raise ValueError(f"unsafe sandbox patch: {reason}")
        self._patches[patch_id] = pending
        return patch_id

    def approval_details(self, patch_id: str) -> dict[str, object]:
        patch = self._patches.get(patch_id)
        if patch is None:
            return {"patch_id": patch_id, "status": "unavailable"}
        return {
            "patch_id": patch_id,
            "changed_files": patch.changed_files,
            "diff_preview": redact(patch.patch[:4_000]),
        }

    async def apply(self, patch_id: str) -> tuple[bool, str, list[str]]:
        pending = self._patches.get(patch_id)
        if pending is None:
            return False, "pending patch was not found; run the sandbox command again", []
        valid, reason = self._validate(pending)
        if not valid:
            return False, reason, []
        return await asyncio.to_thread(self._apply_sync, pending)

    def _validate(self, pending: PendingPatch) -> tuple[bool, str]:
        valid, reason = self._validate_structure(pending)
        if not valid:
            return valid, reason
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
                # Git patches must retain LF line endings.  subprocess text mode
                # translates them to CRLF on Windows, which makes git apply fail.
                input=pending.patch.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            diagnostics = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            if result.returncode != 0 or "Skipped patch" in diagnostics:
                return False, (diagnostics or "git apply failed").strip(), []
        self._patches.pop(pending.patch_id, None)
        return True, "sandbox patch applied", pending.changed_files


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _patch_paths(patch: str) -> set[str]:
    return {before for before, after in _DIFF_PATH.findall(patch) if before == after}

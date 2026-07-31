"""仅把允许的工作区文件复制到沙箱输入快照。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from coding_agent.sandbox.contracts import SnapshotFile, WorkspaceSnapshot
from coding_agent.workspace.security import WorkspacePathPolicy


class SnapshotService:
    def __init__(self, workspace: Path, *, max_file_bytes: int = 8_000_000) -> None:
        self.workspace = workspace.resolve()
        self.paths = WorkspacePathPolicy(self.workspace)
        self.max_file_bytes = max_file_bytes

    async def create(self) -> WorkspaceSnapshot:
        return await asyncio.to_thread(self._create_sync)

    async def cleanup(self, snapshot: WorkspaceSnapshot) -> None:
        await asyncio.to_thread(shutil.rmtree, snapshot.root, ignore_errors=True)

    def _create_sync(self) -> WorkspaceSnapshot:
        root = Path(tempfile.mkdtemp(prefix="coding-agent-snapshot-"))
        try:
            source = root / "workspace"
            source.mkdir()
            files: dict[str, SnapshotFile] = {}
            for directory, directories, names in os.walk(self.workspace, followlinks=False):
                current = Path(directory)
                directories[:] = [
                    name
                    for name in directories
                    if not (current / name).is_symlink()
                    and not self.paths.is_excluded_from_snapshot(current / name)
                ]
                for name in sorted(names):
                    path = current / name
                    if (
                        not path.is_file()
                        or path.is_symlink()
                        or self.paths.is_excluded_from_snapshot(path)
                    ):
                        continue
                    size = path.stat().st_size
                    if size > self.max_file_bytes:
                        continue
                    relative = path.relative_to(self.workspace)
                    destination = source / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    files[relative.as_posix()] = SnapshotFile(
                        path=relative.as_posix(), sha256=digest, size=size
                    )
            archive = root / "workspace.tar"
            with tarfile.open(archive, "w") as tar:
                for path in sorted(source.rglob("*")):
                    if path.is_file():
                        tar.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
            return WorkspaceSnapshot(root=root, archive=archive, files=files)
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise

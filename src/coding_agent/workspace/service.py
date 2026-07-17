"""异步且有边界的仓库检查；仓库文本一律视为不可信上下文。"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field


class RepositoryContext(BaseModel):
    root: Path
    rules: list[str] = Field(default_factory=list)
    project_files: list[str] = Field(default_factory=list)
    is_git: bool = False
    languages: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
    git_status: str = ""


class WorkspaceService:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    async def inspect(self) -> RepositoryContext:
        return await asyncio.to_thread(self._inspect_sync)

    def _inspect_sync(self) -> RepositoryContext:
        markers = ["pyproject.toml", "package.json", "go.mod", "Cargo.toml", "README.md"]
        present = [marker for marker in markers if (self.root / marker).exists()]
        rules: list[str] = []
        current = self.root
        chain = [current]
        while current.parent != current:
            current = current.parent
            chain.append(current)
        for directory in reversed(chain):
            for name in ("AGENTS.md", "CONTRIBUTING.md"):
                candidate = directory / name
                if candidate.exists() and candidate.is_file():
                    rules.append(self._bounded_read(candidate, 8_000))
        suffixes = {path.suffix.lower() for path in self.root.rglob("*") if path.is_file()}
        languages = sorted(
            {
                {
                    ".py": "Python",
                    ".ts": "TypeScript",
                    ".js": "JavaScript",
                    ".go": "Go",
                    ".rs": "Rust",
                }.get(suffix, "")
                for suffix in suffixes
            }
            - {""}
        )
        commands: list[str] = []
        if "pyproject.toml" in present:
            commands.append("uv run pytest")
        if "package.json" in present:
            commands.append("npm test")
        is_git = (self.root / ".git").exists()
        status = self._git(["status", "--short"]) if is_git else ""
        return RepositoryContext(
            root=self.root,
            rules=rules,
            project_files=present,
            is_git=is_git,
            languages=languages,
            verification_commands=commands,
            git_status=status,
        )

    async def read_file(self, relative_path: str, max_chars: int = 12_000) -> str:
        return await asyncio.to_thread(self._bounded_read, self._resolve(relative_path), max_chars)

    async def search(self, query: str, max_results: int = 50) -> str:
        return await asyncio.to_thread(self._search_sync, query, max_results)

    def _search_sync(self, query: str, max_results: int) -> str:
        rg = shutil.which("rg")
        if rg:
            process = subprocess.run(
                [
                    rg,
                    "-n",
                    "--glob",
                    "!.git",
                    "--max-count",
                    str(max_results),
                    query,
                    str(self.root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            return process.stdout[:12_000]
        matches: list[str] = []
        for path in self.root.rglob("*"):
            if path.is_file() and ".git" not in path.parts and len(matches) < max_results:
                try:
                    for number, line in enumerate(
                        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                    ):
                        if query.lower() in line.lower():
                            matches.append(f"{path.relative_to(self.root)}:{number}:{line[:300]}")
                except OSError:
                    continue
        return "\n".join(matches)

    def _resolve(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        target.relative_to(self.root)
        return target

    @staticmethod
    def _bounded_read(path: Path, max_chars: int) -> str:
        data = path.read_bytes()
        if b"\x00" in data[:8_192]:
            return "[binary file omitted]"
        return data.decode("utf-8", errors="replace")[:max_chars]

    def _git(self, args: list[str]) -> str:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            ).stdout[:8_000]
        except (OSError, subprocess.SubprocessError):
            return ""

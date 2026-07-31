"""统一的工作区路径与敏感数据边界。"""

from __future__ import annotations

import re
from pathlib import Path


class WorkspacePathPolicy:
    """所有文件工具、快照和补丁回写共享的路径策略。"""

    _excluded_directories = {
        ".git",
        ".coding-agent",
        ".venv",
        ".uv-cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
    }
    _sensitive_parts = {".ssh", "credentials", "secrets", "token"}
    _sensitive_name = re.compile(
        r"(?i)(^\.env(?:\.|$)|(^|[-_.])(credential|secret|token)([-_.]|$)|"
        r"private[-_.]?key|^id_(rsa|ecdsa|ed25519)$)"
    )
    _sensitive_suffixes = {".pem", ".key", ".p12", ".pfx"}

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def resolve(self, raw_path: str | Path) -> Path:
        candidate = (self.workspace / raw_path).resolve()
        candidate.relative_to(self.workspace)
        return candidate

    def relative(self, path: Path) -> Path:
        return path.resolve().relative_to(self.workspace)

    def is_sensitive(self, path: Path) -> bool:
        try:
            relative = self.relative(path)
        except ValueError:
            return True
        lower_parts = {part.lower() for part in relative.parts}
        name = relative.name
        return (
            bool(lower_parts & self._sensitive_parts)
            or bool(self._sensitive_name.search(name))
            or relative.suffix.lower() in self._sensitive_suffixes
        )

    def is_excluded_from_snapshot(self, path: Path) -> bool:
        try:
            relative = self.relative(path)
        except ValueError:
            return True
        lower_parts = {part.lower() for part in relative.parts}
        return self.is_sensitive(path) or bool(lower_parts & self._excluded_directories)

    def is_protected(self, path: Path) -> bool:
        """返回不应由模型读取、搜索、快照或回写的路径。"""
        return self.is_excluded_from_snapshot(path)

    def dockerignore_patterns(self) -> list[str]:
        """供外部搜索工具使用的排除模式。"""
        patterns = [f"!{directory}/**" for directory in sorted(self._excluded_directories)]
        patterns.extend(["!.env*", "!**/.env*", "!**/*credential*", "!**/*secret*", "!**/*token*"])
        patterns.extend([f"!**/*{suffix}" for suffix in sorted(self._sensitive_suffixes)])
        return patterns

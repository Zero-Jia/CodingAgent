"""集中式策略引擎：路径边界、敏感文件和授权。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Decision = Literal["allow", "deny", "require_approval"]


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str


class PolicyEngine:
    _sensitive_parts = {".env", ".ssh", "credentials", "secrets", "token"}
    _blocked_shell = re.compile(
        r"\b(?:git\s+(?:commit|push)|Invoke-WebRequest|curl|wget|pip\s+install|uv\s+(?:add|sync)|npm\s+install)\b|"
        r"(?:Remove-Item|del|rm)\s+.*(?:-Recurse|/s)",
        re.IGNORECASE,
    )

    def __init__(
        self, workspace: Path, *, allow_write: bool, allow_shell: bool, non_interactive: bool
    ) -> None:
        self.workspace = workspace.resolve()
        self.allow_write = allow_write
        self.allow_shell = allow_shell
        self.non_interactive = non_interactive

    def path_decision(self, path: Path, tool_name: str) -> PolicyDecision:
        try:
            path.resolve().relative_to(self.workspace)
        except ValueError:
            return PolicyDecision("deny", "path is outside the workspace")
        lower = {part.lower() for part in path.parts}
        if any(part in lower for part in self._sensitive_parts) or path.name.lower().startswith(
            ".env"
        ):
            return PolicyDecision("deny", "sensitive file access is denied")
        if tool_name in {"read", "search", "git_diff"}:
            return PolicyDecision("allow", "bounded read-only workspace operation")
        return self._authorization("write operation", self.allow_write)

    def shell_decision(self, command: str) -> PolicyDecision:
        if self._blocked_shell.search(command):
            return PolicyDecision(
                "deny",
                "network, dependency installation, destructive git, or destructive commands "
                "are denied",
            )
        return self._authorization("shell operation", self.allow_shell)

    def tool_decision(self, tool_name: str, params: dict[str, object]) -> PolicyDecision:
        if tool_name in {"read", "edit", "write"}:
            raw_path = params.get("path")
            if not isinstance(raw_path, str):
                return PolicyDecision("deny", "tool path must be a string")
            return self.path_decision(self.workspace / raw_path, tool_name)
        if tool_name in {"shell", "verify"}:
            command = params.get("command")
            if not isinstance(command, str):
                return PolicyDecision("deny", "tool command must be a string")
            return self.shell_decision(command)
        if tool_name in {"search", "git_diff"}:
            return PolicyDecision("allow", "read-only workspace operation")
        return PolicyDecision("deny", f"unknown tool: {tool_name}")

    def _authorization(self, action: str, granted: bool) -> PolicyDecision:
        if granted:
            return PolicyDecision("allow", f"explicit authorization granted for {action}")
        if self.non_interactive:
            return PolicyDecision("deny", f"{action} lacks explicit non-interactive authorization")
        return PolicyDecision("require_approval", f"{action} requires approval")

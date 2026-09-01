"""集中式策略引擎：路径边界、敏感文件和授权。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from coding_agent.policy.command_risk import CommandRiskDetector
from coding_agent.workspace.security import WorkspacePathPolicy

Decision = Literal["allow", "deny", "require_approval"]


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str


class PolicyEngine:
    def __init__(
        self, workspace: Path, *, allow_write: bool, allow_shell: bool, non_interactive: bool
    ) -> None:
        self.workspace = workspace.resolve()
        self.paths = WorkspacePathPolicy(self.workspace)
        self.command_risk = CommandRiskDetector()
        self.allow_write = allow_write
        self.allow_shell = allow_shell
        self.non_interactive = non_interactive

    def path_decision(self, path: Path, tool_name: str) -> PolicyDecision:
        try:
            self.paths.relative(path)
        except ValueError:
            return PolicyDecision("deny", "path is outside the workspace")
        if self.paths.is_protected(path):
            return PolicyDecision("deny", "sensitive or internal file access is denied")
        if tool_name in {"read", "search", "git_diff"}:
            return PolicyDecision("allow", "bounded read-only workspace operation")
        return self._authorization("write operation", self.allow_write)

    def tool_decision(self, tool_name: str, params: dict[str, object]) -> PolicyDecision:
        if tool_name in {"read", "edit", "write"}:
            raw_path = params.get("path")
            if not isinstance(raw_path, str):
                return PolicyDecision("deny", "tool path must be a string")
            return self.path_decision(self.workspace / raw_path, tool_name)
        if tool_name in {"sandbox_shell", "verify"}:
            command = params.get("command")
            if not isinstance(command, str):
                return PolicyDecision("deny", "tool command must be a string")
            risk = self.command_risk.evaluate(command)
            if risk.level == "dangerous":
                return PolicyDecision("deny", f"dangerous sandbox command refused: {risk.reason}")
            if risk.level == "suspicious":
                if self.non_interactive:
                    return PolicyDecision(
                        "deny",
                        f"suspicious sandbox command requires interactive approval: {risk.reason}",
                    )
                return PolicyDecision(
                    "require_approval",
                    f"suspicious sandbox command requires review: {risk.reason}",
                )
            return self._authorization("sandbox operation", self.allow_shell)
        if tool_name == "apply_patch":
            patch_id = params.get("patch_id")
            if not isinstance(patch_id, str) or not patch_id:
                return PolicyDecision("deny", "patch_id must be a non-empty string")
            return self._authorization("patch application", self.allow_write)
        if tool_name == "shell":
            return PolicyDecision("deny", "host shell execution is disabled; use sandbox_shell")
        if tool_name in {"search", "git_diff", "submit_plan"}:
            return PolicyDecision("allow", "read-only workspace operation")
        return PolicyDecision("deny", f"unknown tool: {tool_name}")

    def _authorization(self, action: str, granted: bool) -> PolicyDecision:
        if granted:
            return PolicyDecision("allow", f"explicit authorization granted for {action}")
        if self.non_interactive:
            return PolicyDecision("deny", f"{action} lacks explicit non-interactive authorization")
        return PolicyDecision("require_approval", f"{action} requires approval")

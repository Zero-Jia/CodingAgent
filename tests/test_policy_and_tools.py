from __future__ import annotations

import asyncio
from pathlib import Path

from coding_agent.policy.engine import PolicyEngine
from coding_agent.tools.builtin import EditTool
from coding_agent.tools.contracts import ToolContext, ToolResult


def test_policy_denies_sensitive_write_and_unauthorized_shell(tmp_path: Path) -> None:
    policy = PolicyEngine(tmp_path, allow_write=False, allow_shell=False, non_interactive=True)
    assert policy.tool_decision("read", {"path": ".env"}).decision == "deny"
    assert policy.tool_decision("edit", {"path": "a.txt"}).decision == "deny"
    assert policy.tool_decision("shell", {"command": "git commit -m x"}).decision == "deny"
    assert policy.tool_decision("shell", {"command": "Get-ChildItem"}).decision == "deny"


def test_policy_blocks_dangerous_sandbox_command_even_when_shell_is_allowed(
    tmp_path: Path,
) -> None:
    policy = PolicyEngine(tmp_path, allow_write=False, allow_shell=True, non_interactive=True)

    decision = policy.tool_decision("sandbox_shell", {"command": "echo ok && rm -rf /"})

    assert decision.decision == "deny"
    assert "dangerous sandbox command" in decision.reason


def test_policy_forces_suspicious_sandbox_command_to_interactive_approval(
    tmp_path: Path,
) -> None:
    policy = PolicyEngine(tmp_path, allow_write=False, allow_shell=True, non_interactive=False)

    decision = policy.tool_decision("verify", {"command": "pip install -r requirements.txt"})

    assert decision.decision == "require_approval"
    assert "suspicious sandbox command" in decision.reason


def test_policy_denies_suspicious_sandbox_command_in_non_interactive_mode(
    tmp_path: Path,
) -> None:
    policy = PolicyEngine(tmp_path, allow_write=False, allow_shell=True, non_interactive=True)

    decision = policy.tool_decision("verify", {"command": "curl https://example.invalid/file"})

    assert decision.decision == "deny"
    assert "interactive approval" in decision.reason


def test_exact_edit_refuses_ambiguous_match_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("same\nsame\n", encoding="utf-8")

    async def execute() -> list[ToolResult]:
        results: list[ToolResult] = []
        async for event in EditTool().execute(
            {"path": "sample.txt", "old_text": "same", "new_text": "new"},
            ToolContext(workspace=str(tmp_path)),
            asyncio.Event(),
        ):
            if isinstance(event, ToolResult):
                results.append(event)
        return results

    results = asyncio.run(execute())
    assert results[0].status == "validation_failed"
    assert target.read_text(encoding="utf-8") == "same\nsame\n"

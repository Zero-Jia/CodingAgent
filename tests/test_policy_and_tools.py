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

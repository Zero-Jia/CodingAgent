from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from coding_agent.cli.app import _exit_text


def test_exit_text_includes_resume_command_with_quoted_workspace() -> None:
    session = SimpleNamespace(
        session_id="session-123",
        _agent=SimpleNamespace(config=SimpleNamespace(workspace=Path("D:/Work Dir/Repo"))),
    )

    text = _exit_text(session)

    assert "会话已保存，再见。" in text
    assert "uv --cache-dir .uv-cache run agent chat" in text
    assert '--workspace "D:\\Work Dir\\Repo"' in text
    assert "--resume session-123" in text

from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.ai.contracts import ChatMessage, ToolCall
from coding_agent.sessions.lock import SessionLockedError, acquire_session_lock
from coding_agent.sessions.store import ConversationCheckpoint, JsonlSessionStore


@pytest.mark.asyncio
async def test_checkpoint_round_trip_preserves_tool_call_history(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    checkpoint = ConversationCheckpoint(
        session_id="session-a",
        workspace=str(tmp_path),
        model_provider="deepseek",
        model_name="deepseek-chat",
        messages=[
            ChatMessage(role="system", content="规则"),
            ChatMessage(role="user", content="修改文件"),
            ChatMessage(
                role="assistant",
                tool_calls=[ToolCall(id="call-1", name="edit", arguments_json="{}")],
            ),
            ChatMessage(
                role="tool",
                tool_call_id="call-1",
                content=(
                    '{"status": "success", "output": "完整工具输出", "artifact": "artifacts/a.txt"}'
                ),
            ),
        ],
    )
    await store.save_checkpoint(checkpoint)
    restored = await store.load_checkpoint("session-a")
    assert restored is not None
    assert restored.messages[2].tool_calls[0].name == "edit"
    assert restored.messages[3].tool_call_id == "call-1"
    assert "完整工具输出" not in restored.messages[3].content
    assert "artifacts/a.txt" in restored.messages[3].content


def test_session_lock_rejects_second_holder_and_releases(tmp_path: Path) -> None:
    first = acquire_session_lock(tmp_path, "session-a")
    with pytest.raises(SessionLockedError):
        acquire_session_lock(tmp_path, "session-a")
    first.release()
    second = acquire_session_lock(tmp_path, "session-a")
    second.release()


@pytest.mark.asyncio
async def test_session_summary_and_transcript_are_persisted(tmp_path: Path) -> None:
    store = JsonlSessionStore(tmp_path)
    checkpoint = ConversationCheckpoint(
        session_id="session-b",
        workspace=str(tmp_path),
        model_provider="deepseek",
        model_name="deepseek-chat",
        messages=[ChatMessage(role="system", content="规则")],
    )
    await store.save_checkpoint(checkpoint)
    from coding_agent.sessions.store import SessionSummary

    await store.save_summary(
        SessionSummary(session_id="session-b", workspace=str(tmp_path), model_name="deepseek-chat")
    )
    await store.append_transcript("session-b", "## 用户\n你好\n")
    summaries = await store.list_summaries()
    assert summaries[0].session_id == "session-b"
    assert "你好" in (tmp_path / "transcripts" / "session-b.md").read_text(encoding="utf-8")

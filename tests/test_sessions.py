from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.ai.contracts import ChatMessage, ToolCall
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
            ChatMessage(role="tool", tool_call_id="call-1", content='{"status": "success"}'),
        ],
    )
    await store.save_checkpoint(checkpoint)
    restored = await store.load_checkpoint("session-a")
    assert restored is not None
    assert restored.messages[2].tool_calls[0].name == "edit"
    assert restored.messages[3].tool_call_id == "call-1"

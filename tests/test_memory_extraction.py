"""记忆提取测试：规则提取器、模型提取器、编排、持久化去重。

不依赖真实 API：模型提取器测试用本地 ``FakeModelAdapter`` 喂脚本化 JSON。
真实 DeepSeek smoke test 由 ``RUN_REAL_EXTRACTION_TESTS=1`` 显式启用。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select

from coding_agent.ai.contracts import (
    ChatMessage,
    Completed,
    Model,
    ModelError,
    ModelEvent,
    ModelRequest,
    TextDelta,
)
from coding_agent.db import initialize_database, tables
from coding_agent.memory.contracts import (
    MemoryCategory,
    MemoryRecord,
    MemoryStatus,
    NoopMemoryStore,
)
from coding_agent.memory.extraction import (
    MemoryExtractor,
    ModelExtractor,
    RuleExtractor,
    _memory_id,
    persist_candidates,
)
from coding_agent.memory.mysql import MySqlMemoryStore
from coding_agent.sessions.mysql import MySqlSessionStore
from coding_agent.sessions.store import ConversationCheckpoint


def _completed(*deltas: str) -> list[ModelEvent]:
    return [TextDelta(text=d) for d in deltas] + [Completed()]


class FakeModelAdapter:
    """脚本化模型适配器：按调用顺序返回预置事件列表，记录请求。"""

    def __init__(self, responses: list[list[ModelEvent]]) -> None:
        self.model = Model(provider="fake", name="fake-extractor")
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    def stream(
        self, request: ModelRequest, signal: Any
    ) -> AsyncIterator[ModelEvent]:
        return self._stream(request, signal)

    async def _stream(
        self, request: ModelRequest, signal: Any
    ) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if not self.responses:
            yield TextDelta(text='{"memories":[]}')
            yield Completed()
            return
        events = self.responses.pop(0)
        for evt in events:
            if signal.is_set():
                return
            await asyncio.sleep(0)
            yield evt


def _user(content: str) -> ChatMessage:
    return ChatMessage(role="user", content=content)


def _assistant(content: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=content)


def _meta_kwargs() -> dict[str, str]:
    return {
        "session_id": "sess-1",
        "run_id": "run-1",
        "user_id": "user-1",
        "project_id": "proj-1",
    }


# --------------------------------------------------------------------------- #
# RuleExtractor
# --------------------------------------------------------------------------- #


def test_rule_extractor_hits_cue_user_message() -> None:
    extractor = RuleExtractor()
    records, matched = extractor.extract(
        [_user("请记住，所有提交前必须运行 ruff。"), _user("修一下这个 bug")],
        **_meta_kwargs(),
    )
    assert len(records) == 1
    record = records[0]
    assert record.category == MemoryCategory.PREFERENCE
    assert record.confidence == 0.8
    assert record.status == MemoryStatus.CANDIDATE
    assert "记住" in record.content or "ruff" in record.content
    assert record.source_session_id == "sess-1"
    assert record.source_run_id == "run-1"
    assert record.user_id == "user-1"
    assert record.project_id == "proj-1"
    assert matched == {0}


def test_rule_extractor_english_cue_case_insensitive() -> None:
    extractor = RuleExtractor()
    records, _ = extractor.extract(
        [_user("Always run mypy strict before pushing.")], **_meta_kwargs()
    )
    assert len(records) == 1
    assert records[0].category == MemoryCategory.PREFERENCE
    assert records[0].confidence == 0.8


def test_rule_extractor_no_cue_no_candidates() -> None:
    extractor = RuleExtractor()
    records, matched = extractor.extract(
        [_user("帮我看看 main 函数"), _assistant("好的")], **_meta_kwargs()
    )
    assert records == []
    assert matched == set()


def test_rule_extractor_deterministic_memory_id() -> None:
    extractor = RuleExtractor()
    r1, _ = extractor.extract([_user("记住：用 rg 搜索")], **_meta_kwargs())
    r2, _ = extractor.extract([_user("记住：用 rg 搜索")], **_meta_kwargs())
    assert r1[0].memory_id == r2[0].memory_id


def test_rule_extractor_normalizes_whitespace_and_truncates() -> None:
    extractor = RuleExtractor()
    records, _ = extractor.extract(
        [_user("记住：   多个   空格\t制表符。\n下一行")], **_meta_kwargs()
    )
    assert len(records) == 1
    assert "  " not in records[0].content
    assert "\n" not in records[0].content


def test_rule_extractor_skips_non_user_messages() -> None:
    extractor = RuleExtractor()
    records, matched = extractor.extract(
        [_assistant("记住这条"), ChatMessage(role="system", content="记住")],
        **_meta_kwargs(),
    )
    assert records == []
    assert matched == set()


# --------------------------------------------------------------------------- #
# ModelExtractor
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_model_extractor_parses_valid_json() -> None:
    payload = json.dumps(
        {
            "memories": [
                {
                    "content": "项目使用 ruff 和 mypy strict",
                    "category": "convention",
                    "confidence": 0.9,
                },
                {
                    "content": "用户偏好中文回答",
                    "category": "preference",
                    "confidence": 0.6,
                },
            ]
        }
    )
    adapter = FakeModelAdapter([_completed(payload)])
    extractor = ModelExtractor(adapter)
    records = await extractor.extract(
        [_user("随便聊点"), _assistant("好的")], **_meta_kwargs()
    )
    assert len(records) == 2
    assert {r.category for r in records} == {"convention", "preference"}
    assert records[0].confidence == 0.9
    assert records[1].confidence == 0.6
    assert all(r.status == MemoryStatus.CANDIDATE for r in records)
    # 模型输入只含 user 非线索 + assistant 消息。
    assert len(adapter.requests) == 1
    user_msg = adapter.requests[0].messages[1]
    assert "随便聊点" in user_msg.content
    assert "好的" in user_msg.content


@pytest.mark.asyncio
async def test_model_extractor_empty_memories_returns_none() -> None:
    adapter = FakeModelAdapter([_completed('{"memories":[]}')])
    extractor = ModelExtractor(adapter)
    records = await extractor.extract(
        [_user("hi"), _assistant("hello")], **_meta_kwargs()
    )
    assert records == []


@pytest.mark.asyncio
async def test_model_extractor_invalid_json_returns_empty() -> None:
    adapter = FakeModelAdapter([_completed("not json at all")])
    extractor = ModelExtractor(adapter)
    records = await extractor.extract(
        [_user("hi"), _assistant("hello")], **_meta_kwargs()
    )
    assert records == []


@pytest.mark.asyncio
async def test_model_extractor_markdown_fenced_json_parses() -> None:
    payload = "```json\n" + json.dumps(
        {"memories": [{"content": " fenced convention", "category": "convention"}]}
    ) + "\n```"
    adapter = FakeModelAdapter([_completed(payload)])
    extractor = ModelExtractor(adapter)
    records = await extractor.extract(
        [_user("hi"), _assistant("ok")], **_meta_kwargs()
    )
    assert len(records) == 1
    assert records[0].category == MemoryCategory.CONVENTION


@pytest.mark.asyncio
async def test_model_extractor_bails_on_model_error() -> None:
    adapter = FakeModelAdapter(
        [[TextDelta(text="partial"), ModelError(message="boom", retryable=False)]]
    )
    extractor = ModelExtractor(adapter)
    records = await extractor.extract(
        [_user("hi"), _assistant("ok")], **_meta_kwargs()
    )
    assert records == []


@pytest.mark.asyncio
async def test_model_extractor_invalid_confidence_and_category_normalized() -> None:
    payload = json.dumps(
        {
            "memories": [
                {"content": "bad conf", "category": "preference", "confidence": 1.5},
                {"content": "bad cat", "category": "nonsense", "confidence": "high"},
            ]
        }
    )
    adapter = FakeModelAdapter([_completed(payload)])
    extractor = ModelExtractor(adapter)
    records = await extractor.extract(
        [_user("hi"), _assistant("ok")], **_meta_kwargs()
    )
    assert len(records) == 2
    assert records[0].confidence == 0.5  # 越界 → 默认
    assert records[1].confidence == 0.5  # 非数字 → 默认
    assert records[1].category == MemoryCategory.FACT  # 未知类别 → fact


@pytest.mark.asyncio
async def test_model_extractor_empty_transcript_returns_none() -> None:
    adapter = FakeModelAdapter([])
    extractor = ModelExtractor(adapter)
    records = await extractor.extract([], **_meta_kwargs())
    assert records == []
    assert adapter.requests == []


# --------------------------------------------------------------------------- #
# MemoryExtractor (orchestrator)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_memory_extractor_rule_plus_model_merges_duplicate_content() -> None:
    # 规则命中 "记住 ruff"；模型在未命中部分也产出 "ruff" 但措辞相同。
    rule_candidate_content = "记住：用 ruff"
    model_payload = json.dumps(
        {"memories": [{"content": rule_candidate_content, "category": "convention"}]}
    )
    adapter = FakeModelAdapter([_completed(model_payload)])
    extractor = MemoryExtractor(model=ModelExtractor(adapter))
    records = await extractor.extract(
        [_user("记住：用 ruff"), _user("聊点别的"), _assistant("好的")],
        **_meta_kwargs(),
    )
    # 相同 content → 相同 memory_id → 合并为 1 条。
    assert len(records) == 1
    # 规则版（高置信 0.8）应优先于模型版（默认 0.5）。
    assert records[0].confidence == 0.8
    assert records[0].category == MemoryCategory.PREFERENCE
    # 模型只看到未命中部分（"聊点别的" + "好的"），不含线索消息。
    assert len(adapter.requests) == 1
    model_input = adapter.requests[0].messages[1].content
    assert "记住" not in model_input
    assert "聊点别的" in model_input
    assert "好的" in model_input


@pytest.mark.asyncio
async def test_memory_extractor_no_model_runs_rule_only() -> None:
    extractor = MemoryExtractor(model=None)
    records = await extractor.extract(
        [_user("记住：用 ruff"), _user("帮我改 bug")], **_meta_kwargs()
    )
    assert len(records) == 1
    assert records[0].category == MemoryCategory.PREFERENCE


@pytest.mark.asyncio
async def test_memory_extractor_deterministic_across_runs() -> None:
    payload = json.dumps(
        {"memories": [{"content": "用 mypy strict", "category": "convention"}]}
    )
    adapter = FakeModelAdapter([_completed(payload)])
    extractor = MemoryExtractor(model=ModelExtractor(adapter))
    r1 = await extractor.extract(
        [_user("记住：用 ruff"), _assistant("好的")], **_meta_kwargs()
    )
    # 第二次需要新的 adapter response。
    adapter2 = FakeModelAdapter([_completed(payload)])
    extractor2 = MemoryExtractor(model=ModelExtractor(adapter2))
    r2 = await extractor2.extract(
        [_user("记住：用 ruff"), _assistant("好的")], **_meta_kwargs()
    )
    ids1 = {r.memory_id for r in r1}
    ids2 = {r.memory_id for r in r2}
    assert ids1 == ids2


# --------------------------------------------------------------------------- #
# persist_candidates
# --------------------------------------------------------------------------- #


def _create_engine(database_path: Path):
    engine = create_engine(
        f"sqlite+pysqlite:///{database_path.as_posix()}", future=True
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _ensure_session(engine, session_id: str) -> None:
    with engine.begin() as connection:
        existing = connection.execute(
            select(tables.sessions.c.session_id).where(
                tables.sessions.c.session_id == session_id
            )
        ).first()
        if existing is None:
            connection.execute(
                tables.sessions.insert().values(
                    session_id=session_id,
                    workspace="ws",
                    model_name="deepseek-chat",
                    created_at=datetime(2026, 9, 5, tzinfo=UTC),
                    updated_at=datetime(2026, 9, 5, tzinfo=UTC),
                )
            )


@pytest.mark.asyncio
async def test_persist_candidates_idempotent(tmp_path: Path) -> None:
    engine = _create_engine(tmp_path / "mem.db")
    initialize_database(engine)
    _ensure_session(engine, "sess-1")
    store = MySqlMemoryStore(engine)
    candidates = [
        MemoryRecord(
            memory_id="mid-1",
            user_id="user-1",
            project_id="proj-1",
            scope="project",
            category=MemoryCategory.PREFERENCE,
            content="记住 ruff",
            source_session_id="sess-1",
            confidence=0.8,
            status=MemoryStatus.CANDIDATE,
            created_at=datetime(2026, 9, 5, tzinfo=UTC),
            updated_at=datetime(2026, 9, 5, tzinfo=UTC),
        )
    ]

    new, skipped = await persist_candidates(store, candidates)
    assert new == 1 and skipped == 0

    new2, skipped2 = await persist_candidates(store, candidates)
    assert new2 == 0 and skipped2 == 1

    records = await store.list_by_status(
        user_id="user-1", project_id="proj-1", status=MemoryStatus.CANDIDATE
    )
    assert len(records) == 1


@pytest.mark.asyncio
async def test_persist_candidates_noop_store() -> None:
    store = NoopMemoryStore()
    candidates = [
        MemoryRecord(
            memory_id="mid-1",
            user_id="u",
            project_id="p",
            scope="project",
            category=MemoryCategory.PREFERENCE,
            content="x",
            source_session_id="s",
            confidence=0.8,
            status=MemoryStatus.CANDIDATE,
            created_at=datetime(2026, 9, 5, tzinfo=UTC),
            updated_at=datetime(2026, 9, 5, tzinfo=UTC),
        )
    ]
    new, skipped = await persist_candidates(store, candidates)
    # NoopMemoryStore.get 返回 None（视为未找到），store 为 no-op 不崩；
    # 计为 new=1、skipped=0，但实际未持久化（CLI 在调用前已对 Noop 分支）。
    assert new == 1 and skipped == 0


@pytest.mark.asyncio
async def test_end_to_end_extraction_to_store(tmp_path: Path) -> None:
    """端到端：规则+模型提取 → persist → 可按 candidate 状态查出。"""
    engine = _create_engine(tmp_path / "e2e.db")
    initialize_database(engine)
    _ensure_session(engine, "sess-1")
    store = MySqlMemoryStore(engine)

    payload = json.dumps(
        {
            "memories": [
                {
                    "content": "项目用 mypy strict",
                    "category": "convention",
                    "confidence": 0.7,
                }
            ]
        }
    )
    adapter = FakeModelAdapter([_completed(payload)])
    extractor = MemoryExtractor(model=ModelExtractor(adapter))
    candidates = await extractor.extract(
        [_user("记住：用 ruff"), _assistant("好的，已记录")], **_meta_kwargs()
    )

    new, skipped = await persist_candidates(store, candidates)
    assert new >= 1
    assert skipped == 0

    stored = await store.list_by_status(
        user_id="user-1", project_id="proj-1", status=MemoryStatus.CANDIDATE
    )
    assert len(stored) == len(candidates)
    contents = {r.content for r in stored}
    assert any("ruff" in c for c in contents)
    assert any("mypy" in c for c in contents)


# --------------------------------------------------------------------------- #
# create_memory_store factory
# --------------------------------------------------------------------------- #


def test_create_memory_store_jsonl_returns_noop(tmp_path: Path) -> None:
    from coding_agent.config import AgentConfig
    from coding_agent.sessions.factory import create_memory_store

    config = AgentConfig(workspace=tmp_path)  # default jsonl backend
    store = create_memory_store(config, tmp_path / ".coding-agent")
    assert isinstance(store, NoopMemoryStore)


def test_create_memory_store_mysql_returns_mysql_store(tmp_path: Path) -> None:
    from coding_agent.config import AgentConfig
    from coding_agent.sessions.factory import create_memory_store

    db_path = tmp_path / "factory.db"
    config = AgentConfig(
        workspace=tmp_path,
        database_url=f"sqlite+pysqlite:///{db_path.as_posix()}",
        database_create_schema=True,
    )
    store = create_memory_store(config, tmp_path / ".coding-agent")
    assert isinstance(store, MySqlMemoryStore)


# --------------------------------------------------------------------------- #
# CLI smoke
# --------------------------------------------------------------------------- #


def test_cli_extract_memories_persists_via_sqlite(tmp_path: Path) -> None:
    """CLI 冒烟：sqlite 后端 + 已保存 checkpoint + --no-model。"""
    from typer.testing import CliRunner

    from coding_agent.cli.app import app

    workspace = tmp_path / "ws"
    workspace.mkdir()
    session_id = "cli-session"

    db_path = tmp_path / "cli.db"
    engine = _create_engine(db_path)
    initialize_database(engine)
    sessions = MySqlSessionStore(engine)
    checkpoint = ConversationCheckpoint(
        schema_version=1,
        session_id=session_id,
        workspace=str(workspace),
        model_provider="deepseek",
        model_name="deepseek-chat",
        messages=[
            ChatMessage(role="system", content="system prompt"),
            _user("记住：所有 PR 必须先跑 ruff"),
            _assistant("已记录"),
        ],
        updated_at=datetime(2026, 9, 5, tzinfo=UTC),
    )
    asyncio.run(sessions.save_checkpoint(checkpoint))

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "extract-memories",
            session_id,
            "--workspace",
            str(workspace),
            "--no-model",
            "--storage",
            "mysql",
            "--database-url",
            f"sqlite+pysqlite:///{db_path.as_posix()}",
            "--database-create-schema",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "新增：1" in result.output


# --------------------------------------------------------------------------- #
# B1-6：TTL 与归一化去重
# --------------------------------------------------------------------------- #


def test_memory_id_normalizes_case_and_whitespace() -> None:
    assert _memory_id("Always  run RUFF ") == _memory_id("always run ruff")
    assert _memory_id("always run ruff") != _memory_id("always run mypy")


def test_memory_extractor_writes_expires_at_from_ttl() -> None:
    fixed_now = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
    extractor = MemoryExtractor(
        ttl_days=7,
        clock=lambda: fixed_now,
        model=ModelExtractor(FakeModelAdapter([])),
    )

    records = asyncio.run(
        extractor.extract([_user("请记住：所有提交前必须运行 ruff。")], **_meta_kwargs())
    )

    assert len(records) == 1
    assert records[0].expires_at == fixed_now + timedelta(days=7)


def test_memory_extractor_without_ttl_leaves_expires_at_none() -> None:
    extractor = MemoryExtractor(model=ModelExtractor(FakeModelAdapter([])))

    records = asyncio.run(
        extractor.extract([_user("请记住：所有提交前必须运行 ruff。")], **_meta_kwargs())
    )

    assert len(records) == 1
    assert records[0].expires_at is None

"""B1-5 记忆召回测试。

覆盖：
- MemoryRecallService 向量召回（InMemoryMemoryVectorIndex + FakeEmbedder）
- min_score / min_confidence / status 过滤
- metadata 保底：无向量索引、向量无命中、向量通道抛错
- 异常降级：metadata 也失败时返回空结果，绝不抛出
- top_k 截断、空 query 短路
- format_recall_block / apply_memory_section / strip_memory_section
- 装配：默认关闭 → agent.memory_recall=None；启用 + jsonl → Noop 降级
- ChatSession._apply_memory_recall 注入 / 替换 / 剥离 system message 记忆段

不依赖真实 DashScope / Milvus / MySQL。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from coding_agent.agent.coding_agent import ChatSession, CodingAgent
from coding_agent.ai.contracts import Model, ModelEvent, ModelRequest
from coding_agent.config import AgentConfig
from coding_agent.memory.contracts import MemoryRecord, MemoryStatus, NoopMemoryStore
from coding_agent.memory.recall import (
    MEMORY_SECTION_HEADER,
    MemoryRecallService,
    RecalledMemory,
    apply_memory_section,
    effective_confidence,
    format_recall_block,
    strip_memory_section,
)
from coding_agent.memory.vector import InMemoryMemoryVectorIndex


def _record(
    memory_id: str,
    *,
    content: str = "always run ruff before commit",
    user_id: str = "user-1",
    project_id: str = "proj-1",
    category: str = "convention",
    confidence: float = 0.9,
    status: str = MemoryStatus.PROMOTED,
    updated_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> MemoryRecord:
    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        project_id=project_id,
        scope="project",
        category=category,
        content=content,
        source_session_id="session-1",
        confidence=confidence,
        status=status,
        created_at=now,
        updated_at=updated_at or now,
        expires_at=expires_at,
    )


class FakeMemoryStore:
    """内存版 MemoryStore 子集，只实现 recall 用到的 get / search。"""

    def __init__(
        self, records: list[MemoryRecord] | None = None, *, search_error: bool = False
    ) -> None:
        self._records = {record.memory_id: record for record in (records or [])}
        self._search_error = search_error
        self.search_calls: list[str] = []

    async def get(self, memory_id: str) -> MemoryRecord | None:
        return self._records.get(memory_id)

    async def search(
        self,
        *,
        user_id: str,
        project_id: str,
        query: str,
        status: str | None = MemoryStatus.PROMOTED,
    ) -> list[MemoryRecord]:
        self.search_calls.append(query)
        if self._search_error:
            raise RuntimeError("memory store unavailable")
        lowered = query.lower()
        return [
            record
            for record in self._records.values()
            if record.user_id == user_id
            and record.project_id == project_id
            and (status is None or record.status == status)
            and lowered in record.content.lower()
        ]


class FakeEmbedder:
    """确定性 embedding：任意文本返回预置向量（默认 [1, 0]）。"""

    dimension = 2

    def __init__(self, vectors: dict[str, list[float]] | None = None) -> None:
        self._vectors = vectors or {}

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vectors.get(text, [1.0, 0.0]) for text in texts]


class FailingVectorIndex:
    """所有操作都抛错的向量索引，用于验证降级路径。"""

    backend_name = "failing"
    collection_name = "failing"

    async def ensure_collection(self, dimension: int) -> None:
        raise RuntimeError("milvus unavailable")

    async def upsert(self, records: Any, vectors: Any) -> None:
        raise RuntimeError("milvus unavailable")

    async def search(
        self,
        vector: Sequence[float],
        *,
        user_id: str,
        project_id: str,
        top_k: int,
    ) -> list[Any]:
        raise RuntimeError("milvus unavailable")


# --------------------------------------------------------------------------- #
# MemoryRecallService：向量通道
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_vector_recall_returns_promoted_memory() -> None:
    record = _record("mem-1", content="always run ruff before commit")
    index = InMemoryMemoryVectorIndex()
    await index.ensure_collection(2)
    await index.upsert([record], [[1.0, 0.0]])
    service = MemoryRecallService(
        store=FakeMemoryStore([record]),
        vector_index=index,
        embedder=FakeEmbedder(),
    )

    hits = await service.recall("ruff", user_id="user-1", project_id="proj-1")

    assert len(hits) == 1
    assert hits[0].memory_id == "mem-1"
    assert hits[0].source == "vector"
    assert hits[0].score == pytest.approx(1.0)
    assert hits[0].content == "always run ruff before commit"


@pytest.mark.asyncio
async def test_vector_recall_filters_low_score() -> None:
    record = _record("mem-1")
    index = InMemoryMemoryVectorIndex()
    await index.ensure_collection(2)
    await index.upsert([record], [[0.0, 1.0]])
    service = MemoryRecallService(
        store=FakeMemoryStore([record]),
        vector_index=index,
        embedder=FakeEmbedder({"unrelated query": [1.0, 0.0]}),
    )

    hits = await service.recall("unrelated query", user_id="user-1", project_id="proj-1")

    assert hits == []


@pytest.mark.asyncio
async def test_vector_recall_filters_low_confidence_and_non_promoted() -> None:
    low_confidence = _record("mem-low", confidence=0.3)
    candidate = _record("mem-candidate", status=MemoryStatus.CANDIDATE)
    index = InMemoryMemoryVectorIndex()
    await index.ensure_collection(2)
    await index.upsert(
        [low_confidence, candidate], [[1.0, 0.0], [1.0, 0.0]]
    )
    service = MemoryRecallService(
        store=FakeMemoryStore([low_confidence, candidate]),
        vector_index=index,
        embedder=FakeEmbedder(),
    )

    hits = await service.recall("ruff", user_id="user-1", project_id="proj-1")

    assert hits == []


# --------------------------------------------------------------------------- #
# MemoryRecallService：metadata 保底与降级
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_metadata_fallback_without_vector_index() -> None:
    record = _record("mem-1", content="use rg for symbol search")
    service = MemoryRecallService(store=FakeMemoryStore([record]))

    hits = await service.recall("rg", user_id="user-1", project_id="proj-1")

    assert len(hits) == 1
    assert hits[0].source == "metadata"
    assert hits[0].score == pytest.approx(record.confidence)


@pytest.mark.asyncio
async def test_metadata_fallback_when_vector_has_no_hits() -> None:
    record = _record("mem-1", content="use rg for symbol search")
    index = InMemoryMemoryVectorIndex()
    await index.ensure_collection(2)
    service = MemoryRecallService(
        store=FakeMemoryStore([record]),
        vector_index=index,
        embedder=FakeEmbedder(),
    )

    hits = await service.recall("rg", user_id="user-1", project_id="proj-1")

    assert len(hits) == 1
    assert hits[0].source == "metadata"


@pytest.mark.asyncio
async def test_vector_channel_error_falls_back_to_metadata() -> None:
    record = _record("mem-1", content="use rg for symbol search")
    service = MemoryRecallService(
        store=FakeMemoryStore([record]),
        vector_index=FailingVectorIndex(),
        embedder=FakeEmbedder(),
    )

    hits = await service.recall("rg", user_id="user-1", project_id="proj-1")

    assert len(hits) == 1
    assert hits[0].source == "metadata"


@pytest.mark.asyncio
async def test_total_failure_returns_empty_without_raising() -> None:
    service = MemoryRecallService(
        store=FakeMemoryStore(search_error=True),
        vector_index=FailingVectorIndex(),
        embedder=FakeEmbedder(),
    )

    hits = await service.recall("anything", user_id="user-1", project_id="proj-1")

    assert hits == []


@pytest.mark.asyncio
async def test_noop_store_returns_empty() -> None:
    service = MemoryRecallService(store=NoopMemoryStore())

    hits = await service.recall("ruff", user_id="", project_id="proj-1")

    assert hits == []


@pytest.mark.asyncio
async def test_empty_query_short_circuits_without_store_access() -> None:
    store = FakeMemoryStore([_record("mem-1")])
    service = MemoryRecallService(store=store)

    assert await service.recall("   ", user_id="user-1", project_id="proj-1") == []
    assert store.search_calls == []


@pytest.mark.asyncio
async def test_recall_respects_top_k() -> None:
    records = [_record(f"mem-{i}", content=f"rule number {i} about ruff") for i in range(4)]
    store = FakeMemoryStore(records)
    service = MemoryRecallService(store=store, top_k=2)

    hits = await service.recall("ruff", user_id="user-1", project_id="proj-1")

    assert len(hits) == 2


@pytest.mark.asyncio
async def test_vector_index_requires_embedder() -> None:
    with pytest.raises(ValueError):
        MemoryRecallService(store=FakeMemoryStore(), vector_index=InMemoryMemoryVectorIndex())


# --------------------------------------------------------------------------- #
# 格式化与注入辅助
# --------------------------------------------------------------------------- #


def test_format_recall_block_empty_records() -> None:
    assert format_recall_block([]) == ""


def test_format_recall_block_contains_metadata() -> None:
    block = format_recall_block(
        [
            RecalledMemory(
                memory_id="mem-1",
                content="always run ruff",
                category="convention",
                scope="project",
                confidence=0.9,
                score=0.97,
                source="vector",
            )
        ]
    )
    assert MEMORY_SECTION_HEADER in block
    assert "[convention]" in block
    assert "conf=0.90" in block
    assert "source=vector" in block
    assert "always run ruff" in block


def test_format_recall_block_respects_max_chars() -> None:
    records = [
        RecalledMemory(
            memory_id=f"mem-{i}",
            content="x" * 80,
            category="fact",
            scope="project",
            confidence=0.9,
            score=0.9,
            source="metadata",
        )
        for i in range(10)
    ]
    block = format_recall_block(records, max_chars=300)
    assert len(block) <= 300


def test_apply_and_strip_memory_section_round_trip() -> None:
    base = "SYSTEM PROMPT"
    block = MEMORY_SECTION_HEADER + "\n- [fact] conf=0.90 source=vector something"
    applied = apply_memory_section(base, block)
    assert applied.startswith(base)
    assert MEMORY_SECTION_HEADER in applied
    assert strip_memory_section(applied) == base


def test_apply_memory_section_replaces_previous_block() -> None:
    base = "SYSTEM PROMPT"
    first = apply_memory_section(base, MEMORY_SECTION_HEADER + "\n- old memory")
    second = apply_memory_section(first, MEMORY_SECTION_HEADER + "\n- new memory")
    assert second.count(MEMORY_SECTION_HEADER) == 1
    assert "new memory" in second
    assert "old memory" not in second
    assert strip_memory_section(second) == base


def test_apply_memory_section_empty_block_strips_previous() -> None:
    base = "SYSTEM PROMPT"
    applied = apply_memory_section(base, MEMORY_SECTION_HEADER + "\n- old memory")
    assert apply_memory_section(applied, "") == base


# --------------------------------------------------------------------------- #
# 装配层与 ChatSession 注入
# --------------------------------------------------------------------------- #


class FakeModelAdapter:
    """最小 ModelAdapter：召回测试不会真正调用模型。"""

    def __init__(self) -> None:
        self.model = Model(provider="fake", name="fake")

    def stream(
        self, request: ModelRequest, signal: Any
    ) -> AsyncIterator[ModelEvent]:
        raise AssertionError("model should not be called in recall tests")


def _recall_agent(tmp_path: Path, store: FakeMemoryStore | None = None) -> CodingAgent:
    config = AgentConfig(workspace=tmp_path, memory_recall_enabled=True)
    agent = CodingAgent(config, FakeModelAdapter())
    agent.memory_recall = MemoryRecallService(store=store or FakeMemoryStore())
    return agent


def test_agent_memory_recall_disabled_by_default(tmp_path: Path) -> None:
    config = AgentConfig(workspace=tmp_path)
    agent = CodingAgent(config, FakeModelAdapter())
    assert agent.memory_recall is None


@pytest.mark.asyncio
async def test_agent_memory_recall_enabled_jsonl_uses_noop(tmp_path: Path) -> None:
    config = AgentConfig(workspace=tmp_path, memory_recall_enabled=True)
    agent = CodingAgent(config, FakeModelAdapter())
    assert agent.memory_recall is not None
    hits = await agent.memory_recall.recall(
        "ruff", user_id="", project_id=str(tmp_path.resolve())
    )
    assert hits == []


@pytest.mark.asyncio
async def test_chat_session_injects_memory_section(tmp_path: Path) -> None:
    project_id = str(tmp_path.resolve())
    record = _record(
        "mem-1", content="always run ruff before commit", user_id="", project_id=project_id
    )
    agent = _recall_agent(tmp_path, FakeMemoryStore([record]))
    session = ChatSession(agent, "sess-recall", [await agent._system_message()])
    base_content = session.messages[0].content

    await session._apply_memory_recall("ruff")

    content = session.messages[0].content
    assert content.startswith(base_content)
    assert MEMORY_SECTION_HEADER in content
    assert "always run ruff before commit" in content
    # 旧 user 消息保持不变，注入只影响 system message。
    assert all(message.role != "user" for message in session.messages)


@pytest.mark.asyncio
async def test_chat_session_replaces_memory_section_each_turn(tmp_path: Path) -> None:
    project_id = str(tmp_path.resolve())
    record = _record(
        "mem-1", content="always run ruff before commit", user_id="", project_id=project_id
    )
    agent = _recall_agent(tmp_path, FakeMemoryStore([record]))
    session = ChatSession(agent, "sess-recall", [await agent._system_message()])

    await session._apply_memory_recall("ruff")
    await session._apply_memory_recall("commit")

    assert session.messages[0].content.count(MEMORY_SECTION_HEADER) == 1


@pytest.mark.asyncio
async def test_chat_session_strips_section_when_no_recall_hit(tmp_path: Path) -> None:
    project_id = str(tmp_path.resolve())
    record = _record(
        "mem-1", content="always run ruff before commit", user_id="", project_id=project_id
    )
    agent = _recall_agent(tmp_path, FakeMemoryStore([record]))
    session = ChatSession(agent, "sess-recall", [await agent._system_message()])
    base_content = session.messages[0].content

    await session._apply_memory_recall("ruff")
    await session._apply_memory_recall("quantum")

    assert session.messages[0].content == base_content


@pytest.mark.asyncio
async def test_chat_session_without_recall_service_is_noop(tmp_path: Path) -> None:
    config = AgentConfig(workspace=tmp_path)
    agent = CodingAgent(config, FakeModelAdapter())
    session = ChatSession(agent, "sess-recall", [await agent._system_message()])
    base_content = session.messages[0].content

    await session._apply_memory_recall("ruff")

    assert session.messages[0].content == base_content


# --------------------------------------------------------------------------- #
# B1-6：过期过滤与置信度衰减
# --------------------------------------------------------------------------- #


_FIXED_NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)


def test_effective_confidence_decays_with_age() -> None:
    record = _record("mem-1", updated_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC))
    assert effective_confidence(
        record, now=_FIXED_NOW, half_life_days=30.0
    ) == pytest.approx(0.9 * 0.5 ** (1 / 30))


def test_effective_confidence_clamps_future_updated_at() -> None:
    record = _record("mem-1", updated_at=datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC))
    assert effective_confidence(
        record, now=_FIXED_NOW, half_life_days=30.0
    ) == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_recall_filters_expired_record() -> None:
    record = _record("mem-1", expires_at=datetime(2026, 9, 6, 0, 0, 0, tzinfo=UTC))
    service = MemoryRecallService(store=FakeMemoryStore([record]), clock=lambda: _FIXED_NOW)

    hits = await service.recall("ruff", user_id="user-1", project_id="proj-1")

    assert hits == []


@pytest.mark.asyncio
async def test_recall_keeps_unexpired_record() -> None:
    record = _record("mem-1", expires_at=datetime(2026, 10, 6, 12, 0, 0, tzinfo=UTC))
    service = MemoryRecallService(store=FakeMemoryStore([record]), clock=lambda: _FIXED_NOW)

    hits = await service.recall("ruff", user_id="user-1", project_id="proj-1")

    assert len(hits) == 1
    assert hits[0].memory_id == "mem-1"


@pytest.mark.asyncio
async def test_recall_decay_filters_aged_low_confidence() -> None:
    # 30 天前 confidence 0.9，半衰期 30 天 → effective 0.45 < min 0.6 → 过滤。
    record = _record(
        "mem-1", updated_at=datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)
    )
    service = MemoryRecallService(
        store=FakeMemoryStore([record]),
        decay_half_life_days=30.0,
        clock=lambda: _FIXED_NOW,
    )

    hits = await service.recall("ruff", user_id="user-1", project_id="proj-1")

    assert hits == []


@pytest.mark.asyncio
async def test_recall_decay_scores_metadata_channel() -> None:
    # 15 天前 confidence 0.9，半衰期 30 天 → effective 0.9 * 0.5**0.5。
    record = _record(
        "mem-1", updated_at=datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)
    )
    service = MemoryRecallService(
        store=FakeMemoryStore([record]),
        decay_half_life_days=30.0,
        clock=lambda: _FIXED_NOW,
    )

    hits = await service.recall("ruff", user_id="user-1", project_id="proj-1")

    assert len(hits) == 1
    assert hits[0].score == pytest.approx(0.9 * 0.5**0.5)
    assert hits[0].confidence == pytest.approx(0.9 * 0.5**0.5)


@pytest.mark.asyncio
async def test_recall_without_decay_keeps_raw_confidence() -> None:
    # 服务默认不衰减（向后兼容 B1-5 行为），很老的记忆不受影响。
    record = _record("mem-1", updated_at=datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC))
    service = MemoryRecallService(store=FakeMemoryStore([record]), clock=lambda: _FIXED_NOW)

    hits = await service.recall("ruff", user_id="user-1", project_id="proj-1")

    assert len(hits) == 1
    assert hits[0].confidence == pytest.approx(0.9)

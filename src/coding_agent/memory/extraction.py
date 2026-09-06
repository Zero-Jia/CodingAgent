"""候选记忆提取：规则优先、模型补位的两段式提取。

数据源：``ConversationCheckpoint.messages``（role-tagged 对话）。

1. ``RuleExtractor`` ——在 user 消息上做确定性线索词匹配，产出高置信候选
  （confidence=0.8，category=preference）。
2. ``ModelExtractor`` ——把规则未命中的部分（非线索 user 消息 + assistant 消息，
   跳过 system 与 tool 消息以避免噪声和 token 浪费）喂给 ``ModelAdapter``，让模型
   自主判断可记项，返回 JSON，产出中低置信候选（confidence 由模型给或默认 0.5）。
3. ``MemoryExtractor`` ——编排：先规则后模型，按 ``memory_id``（content hash）去重合并。

所有候选 ``status=candidate``，统一由 B1-4 人工审核决定是否 promoted。

测试不依赖真实 API：``ModelExtractor`` 接受任意 ``ModelAdapter``，单测用
``FakeModelAdapter`` 喂脚本化 JSON；真实 DeepSeek smoke test 由
``RUN_REAL_EXTRACTION_TESTS=1`` 显式启用。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from coding_agent.ai.contracts import (
    ChatMessage,
    ModelAdapter,
    ModelError,
    ModelRequest,
    TextDelta,
)
from coding_agent.memory.contracts import (
    MemoryCategory,
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
)

# 显式记忆线索词。规则命中这些词的 user 消息句子产出高置信候选。
_MEMORY_CUES: tuple[str, ...] = (
    "记住", "别忘了", "从现在起", "从今往后", "总是", "永远", "默认",
    "约定", "偏好", "请确保", "务必",
    "remember", "don't forget", "from now on", "always", "never",
    "by default", "make sure to",
)
_MEMORY_CUES_LOWER: tuple[str, ...] = tuple(cue.lower() for cue in _MEMORY_CUES)

_EXTRACTION_SYSTEM_PROMPT = (
    "You are a memory extractor for a coding agent. Read the conversation and "
    "extract durable facts worth remembering across sessions: user preferences, "
    "project conventions, important decisions, historical fix patterns, and "
    "stable project facts. Do NOT extract transient task steps, tool outputs, "
    "or one-off questions.\n\n"
    "Return ONLY a JSON object, no prose, no markdown fences:\n"
    '{"memories":[{"content":str,"category":str,"confidence":float}]}\n'
    "- content: concise statement (<=200 chars), in the conversation's language\n"
    "- category: one of preference, convention, decision, fix, fact\n"
    "- confidence: 0.0-1.0 (lower if uncertain)\n\n"
    'If nothing is worth remembering, return {"memories":[]}.'
)

_RULE_CONFIDENCE = 0.8
_DEFAULT_MODEL_CONFIDENCE = 0.5
_CONTENT_MAX_CHARS = 200
_TRANSCRIPT_BUDGET_CHARS = 8000

_SENTENCE_SPLIT = re.compile(r"[。！？\.!\?\n]+")


class RuleExtractor:
    """确定性线索词提取器，只扫描 user 消息。"""

    def extract(
        self,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str,
        user_id: str,
        project_id: str,
    ) -> tuple[list[MemoryRecord], set[int]]:
        """返回 (候选记录, 命中线索的 user 消息下标集合)。"""
        records: list[MemoryRecord] = []
        matched_indices: set[int] = set()
        for idx, message in enumerate(messages):
            if message.role != "user" or not message.content.strip():
                continue
            hit = False
            for sentence in _split_sentences(message.content):
                if not _contains_cue(sentence):
                    continue
                content = _normalize_content(sentence)
                if not content:
                    continue
                hit = True
                records.append(
                    _make_record(
                        content=content,
                        category=MemoryCategory.PREFERENCE,
                        confidence=_RULE_CONFIDENCE,
                        session_id=session_id,
                        run_id=run_id,
                        user_id=user_id,
                        project_id=project_id,
                    )
                )
            if hit:
                matched_indices.add(idx)
        return records, matched_indices


class ModelExtractor:
    """模型驱动的补位提取器，只看规则未命中的对话部分。"""

    def __init__(
        self,
        adapter: ModelAdapter,
        *,
        transcript_budget_chars: int = _TRANSCRIPT_BUDGET_CHARS,
    ) -> None:
        self.adapter = adapter
        self.transcript_budget_chars = transcript_budget_chars

    async def extract(
        self,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str,
        user_id: str,
        project_id: str,
    ) -> list[MemoryRecord]:
        transcript = _format_transcript(messages, self.transcript_budget_chars)
        if not transcript.strip():
            return []
        request = ModelRequest(
            messages=[
                ChatMessage(role="system", content=_EXTRACTION_SYSTEM_PROMPT),
                ChatMessage(role="user", content=transcript),
            ],
            tools=[],
            model=self.adapter.model,
            temperature=0.0,
        )
        signal = asyncio.Event()
        text_parts: list[str] = []
        async for event in self.adapter.stream(request, signal):
            if isinstance(event, TextDelta):
                text_parts.append(event.text)
            elif isinstance(event, ModelError):
                # 模型出错则放弃本批模型提取，不抛异常。
                return []
            # UsageEvent / Completed / 其它事件对提取无意义，忽略。
        raw = "".join(text_parts)
        return _parse_model_memories(
            raw,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
        )


class MemoryExtractor:
    """编排：规则优先、模型补位，按 memory_id（归一化 content hash）去重合并。

    ``ttl_days``（B1-6）：产出候选时统一写入 ``expires_at = now + ttl``；
    ``None`` 表示不过期。``clock`` 仅供测试注入固定时间。
    """

    def __init__(
        self,
        *,
        rule: RuleExtractor | None = None,
        model: ModelExtractor | None = None,
        ttl_days: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.rule = rule or RuleExtractor()
        self.model = model
        self.ttl_days = ttl_days
        self.clock = clock or (lambda: datetime.now(UTC))

    async def extract(
        self,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str,
        user_id: str,
        project_id: str,
    ) -> list[MemoryRecord]:
        rule_records, matched = self.rule.extract(
            messages,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
        )
        merged: dict[str, MemoryRecord] = {
            record.memory_id: record for record in rule_records
        }
        if self.model is not None:
            unmatched = [
                message
                for idx, message in enumerate(messages)
                if idx not in matched
                and message.role in ("user", "assistant")
                and message.content.strip()
            ]
            if unmatched:
                model_records = await self.model.extract(
                    unmatched,
                    session_id=session_id,
                    run_id=run_id,
                    user_id=user_id,
                    project_id=project_id,
                )
                for record in model_records:
                    # 规则与模型产出同 content → 同 memory_id，规则版（高置信）优先。
                    merged.setdefault(record.memory_id, record)
        records = list(merged.values())
        if self.ttl_days is not None:
            expires_at = self.clock() + timedelta(days=self.ttl_days)
            for record in records:
                record.expires_at = expires_at
        return records


async def persist_candidates(
    store: MemoryStore,
    candidates: list[MemoryRecord],
) -> tuple[int, int]:
    """按 memory_id 去重写入；返回 (new, skipped)。

    已存在的候选跳过（幂等重跑不重复写、不抛 IntegrityError）。
    ``NoopMemoryStore`` 退化为全部 skipped=0、new=0。
    """
    new = 0
    skipped = 0
    for candidate in candidates:
        existing = await store.get(candidate.memory_id)
        if existing is not None:
            skipped += 1
            continue
        await store.store(candidate)
        new += 1
    return new, skipped


def _contains_cue(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(cue in lowered for cue in _MEMORY_CUES_LOWER)


def _split_sentences(content: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(content)
    return [part.strip() for part in parts if part.strip()]


def _normalize_content(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) > _CONTENT_MAX_CHARS:
        collapsed = collapsed[:_CONTENT_MAX_CHARS].rstrip()
    return collapsed


def _format_transcript(messages: list[ChatMessage], budget: int) -> str:
    lines: list[str] = []
    total = 0
    for message in messages:
        role = message.role.upper()
        content = message.content.strip()
        if not content:
            continue
        line = f"[{role}] {content}"
        if total + len(line) > budget:
            remaining = budget - total
            if remaining <= 0:
                break
            line = line[:remaining]
        lines.append(line)
        total += len(line) + 1
        if total >= budget:
            break
    return "\n".join(lines)


def _parse_model_memories(
    raw: str,
    *,
    session_id: str,
    run_id: str,
    user_id: str,
    project_id: str,
) -> list[MemoryRecord]:
    data = _extract_json_object(raw)
    if data is None:
        return []
    items = data.get("memories")
    if not isinstance(items, list):
        return []
    records: list[MemoryRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, str):
            continue
        content = _normalize_content(content)
        if not content:
            continue
        category = item.get("category")
        category_str = str(category) if isinstance(category, str) else MemoryCategory.FACT
        if category_str not in MemoryCategory.ALL:
            category_str = MemoryCategory.FACT
        confidence = item.get("confidence")
        if not isinstance(confidence, int | float) or not 0.0 <= float(confidence) <= 1.0:
            confidence = _DEFAULT_MODEL_CONFIDENCE
        records.append(
            _make_record(
                content=content,
                category=category_str,
                confidence=float(confidence),
                session_id=session_id,
                run_id=run_id,
                user_id=user_id,
                project_id=project_id,
            )
        )
    return records


def _extract_json_object(raw: str) -> dict[str, object] | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = cleaned[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _make_record(
    *,
    content: str,
    category: str,
    confidence: float,
    session_id: str,
    run_id: str,
    user_id: str,
    project_id: str,
) -> MemoryRecord:
    now = datetime.now(UTC)
    return MemoryRecord(
        memory_id=_memory_id(content),
        user_id=user_id,
        project_id=project_id,
        scope="project",
        category=category,
        content=content,
        source_session_id=session_id,
        source_run_id=run_id,
        confidence=confidence,
        status=MemoryStatus.CANDIDATE,
        created_at=now,
        updated_at=now,
    )


def _memory_id(content: str) -> str:
    """归一化 content（折叠空白 + 小写）hash，使相同内容跨 session 自动合并。

    B1-6 归一化去重："Always run Ruff" 与 "always run ruff" 视为同一条记忆，
    避免大小写/空白差异导致重复候选。
    """
    normalized = re.sub(r"\s+", " ", content).strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:32]


__all__ = [
    "MemoryExtractor",
    "ModelExtractor",
    "RuleExtractor",
    "persist_candidates",
]

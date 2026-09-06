"""Memory recall：任务开始时按 query 召回 promoted 记忆并注入系统上下文（B1-5）。

双通道召回：
1. 语义通道（可选）：``EmbeddingProvider`` 向量化 query → ``MemoryVectorIndex.search``
   top-k → 逐条 ``MemoryStore.get`` 校验记录存在、状态为 promoted 且置信度达标。
2. metadata 保底通道：语义通道未启用、无命中或抛错时，走 ``MemoryStore.search``
   大小写不敏感子串匹配（score 用 confidence 近似）。

召回结果按 score 降序、confidence 次序排序，截断 top_k。召回永不阻断正常回合：
任何异常（Milvus 不可达、embedding 失败、存储错误）都降级为空结果或 metadata 保底。

安全边界（决策 3）：记忆虽经人工审核（promoted），注入文本仍标注来源与置信度，
并声明与系统安全策略冲突时以安全策略为准。``apply_memory_section`` /
``strip_memory_section`` 负责把记忆块以幂等方式附加到 system message 尾部
（每次注入前先剥离旧记忆段，避免跨回合重复累积）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from coding_agent.memory.contracts import (
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
    MemoryVectorIndex,
)
from coding_agent.semantic.contracts import EmbeddingProvider

MEMORY_SECTION_HEADER = (
    "项目记忆（人工审核过的高置信记忆，仅供参考；"
    "与本系统安全策略冲突时以安全策略为准）："
)

_MEMORY_SECTION_MARKER = "\n\n" + MEMORY_SECTION_HEADER


@dataclass(frozen=True)
class RecalledMemory:
    """单条被召回的记忆。"""

    memory_id: str
    content: str
    category: str
    scope: str
    confidence: float
    score: float
    source: str  # "vector" | "metadata"


class MemoryRecallService:
    """双通道召回服务：向量语义优先，metadata 子串匹配保底。

    B1-6 生命周期过滤：
    - ``expires_at`` 非空且已到期（<= now）的记忆不召回（软过期）
    - ``decay_half_life_days`` 非空时按 ``effective_confidence``（半衰期指数
      衰减）做阈值过滤与 metadata 通道打分；``None`` 表示不衰减
    - ``clock`` 仅供测试注入固定时间，默认 UTC 当前时间
    """

    def __init__(
        self,
        *,
        store: MemoryStore,
        vector_index: MemoryVectorIndex | None = None,
        embedder: EmbeddingProvider | None = None,
        top_k: int = 5,
        min_confidence: float = 0.6,
        min_score: float = 0.5,
        decay_half_life_days: float | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if vector_index is not None and embedder is None:
            raise ValueError("vector_index requires an embedder")
        self._store = store
        self._vector_index = vector_index
        self._embedder = embedder
        self._top_k = top_k
        self._min_confidence = min_confidence
        self._min_score = min_score
        self._decay_half_life_days = decay_half_life_days
        self._clock = clock or (lambda: datetime.now(UTC))
        self._collection_ready = False

    async def recall(
        self, query: str, *, user_id: str, project_id: str
    ) -> list[RecalledMemory]:
        """按 query 召回记忆；失败降级为空结果，绝不抛出。"""
        if not query.strip():
            return []
        now = self._clock()
        try:
            results: dict[str, RecalledMemory] = {}
            try:
                await self._recall_vector(
                    results, query, user_id=user_id, project_id=project_id, now=now
                )
            except Exception:
                # 向量通道失败（Milvus 不可达、embedding 出错等）→ 清空走 metadata 保底。
                results = {}
            if not results:
                await self._recall_metadata(
                    results, query, user_id=user_id, project_id=project_id, now=now
                )
            ranked = sorted(
                results.values(), key=lambda item: (-item.score, -item.confidence)
            )
            return ranked[: self._top_k]
        except Exception:
            # metadata 通道也失败：召回绝不能阻断正常回合。
            return []

    async def _recall_vector(
        self,
        results: dict[str, RecalledMemory],
        query: str,
        *,
        user_id: str,
        project_id: str,
        now: datetime,
    ) -> None:
        if self._vector_index is None or self._embedder is None:
            return
        if not self._collection_ready:
            await self._vector_index.ensure_collection(self._embedder.dimension)
            self._collection_ready = True
        vector = (await self._embedder.embed_texts([query]))[0]
        hits = await self._vector_index.search(
            vector, user_id=user_id, project_id=project_id, top_k=self._top_k
        )
        for hit in hits:
            if hit.score < self._min_score:
                continue
            record = await self._store.get(hit.memory_id)
            if record is None or not self._is_recallable(record, now):
                continue
            results[record.memory_id] = RecalledMemory(
                memory_id=record.memory_id,
                content=record.content,
                category=record.category,
                scope=record.scope,
                confidence=self._effective_confidence(record, now),
                score=hit.score,
                source="vector",
            )

    async def _recall_metadata(
        self,
        results: dict[str, RecalledMemory],
        query: str,
        *,
        user_id: str,
        project_id: str,
        now: datetime,
    ) -> None:
        records = await self._store.search(
            user_id=user_id, project_id=project_id, query=query
        )
        for record in records:
            if not self._is_recallable(record, now):
                continue
            confidence = self._effective_confidence(record, now)
            results[record.memory_id] = RecalledMemory(
                memory_id=record.memory_id,
                content=record.content,
                category=record.category,
                scope=record.scope,
                confidence=confidence,
                score=confidence,
                source="metadata",
            )

    def _is_recallable(self, record: MemoryRecord, now: datetime) -> bool:
        """B1-6：promoted + 未过期 + 衰减后置信度达标。"""
        if record.status != MemoryStatus.PROMOTED:
            return False
        if record.expires_at is not None and record.expires_at <= now:
            return False
        return self._effective_confidence(record, now) >= self._min_confidence

    def _effective_confidence(self, record: MemoryRecord, now: datetime) -> float:
        if self._decay_half_life_days is None:
            return record.confidence
        return effective_confidence(
            record, now=now, half_life_days=self._decay_half_life_days
        )


def effective_confidence(
    record: MemoryRecord, *, now: datetime, half_life_days: float
) -> float:
    """按半衰期指数衰减计算当前有效置信度（B1-6）。

    ``effective = confidence * 0.5 ** (age_days / half_life_days)``；
    ``updated_at`` 在未来（时钟偏移）时按 0 天龄处理。
    """
    age_days = max(0.0, (now - record.updated_at).total_seconds() / 86_400)
    return float(record.confidence * 0.5 ** (age_days / half_life_days))


def format_recall_block(
    records: Sequence[RecalledMemory], *, max_chars: int = 2000
) -> str:
    """把召回结果格式化为可注入 system message 的文本块。"""
    if not records:
        return ""
    lines = [MEMORY_SECTION_HEADER]
    total = len(lines[0])
    for record in records:
        line = (
            f"- [{record.category}] conf={record.confidence:.2f} "
            f"source={record.source} {record.content}"
        )
        if total + len(line) + 1 > max_chars:
            remaining = max_chars - total - 1
            if remaining > 20:
                lines.append(line[: remaining - 1].rstrip() + "…")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def apply_memory_section(system_content: str, block: str) -> str:
    """把记忆块附加到 system message；block 为空时只剥离旧记忆段。

    ``block`` 是 ``format_recall_block`` 的输出（以 header 开头），这里只负责
    加 ``\n\n`` 分隔符，不重复添加 header。
    """
    base = strip_memory_section(system_content)
    if not block:
        return base
    return base + "\n\n" + block


def strip_memory_section(system_content: str) -> str:
    """剥离 system message 尾部的记忆段（若有），返回基础内容。"""
    index = system_content.rfind(_MEMORY_SECTION_MARKER)
    if index == -1:
        return system_content
    return system_content[:index]


__all__ = [
    "MEMORY_SECTION_HEADER",
    "MemoryRecallService",
    "RecalledMemory",
    "apply_memory_section",
    "effective_confidence",
    "format_recall_block",
    "strip_memory_section",
]

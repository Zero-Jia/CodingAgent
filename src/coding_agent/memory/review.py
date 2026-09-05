"""候选记忆人工审核服务。

B1-3 的 ``extract-memories`` 把候选记忆以 ``candidate`` 状态写入 store，
本模块负责把候选提升为 ``promoted``（供 B1-5 recall 注入）或拒绝为
``rejected``。

设计约束：
- 审核只允许 ``candidate -> promoted`` 或 ``candidate -> rejected``，避免
  对已审核记忆重复操作或跳过审核直接改状态。
- ``reviewer`` 不能为空，保证审计可追溯。
- 服务层不直接做终端交互；CLI（``review-memories``）负责逐条展示与收集
  决定，本层只做状态校验与持久化。
"""

from __future__ import annotations

from coding_agent.memory.contracts import (
    MemoryRecord,
    MemoryStatus,
    MemoryStore,
)

# 审核允许的目标状态：从 candidate 出发只能 promote 或 reject。
_REVIEWABLE_TARGETS: frozenset[str] = frozenset(
    (MemoryStatus.PROMOTED, MemoryStatus.REJECTED)
)


class ReviewError(Exception):
    """审核操作不合法：记忆不存在、非 candidate 状态、或目标状态非法。"""


class MemoryReviewService:
    """在 ``MemoryStore`` 之上封装候选记忆的审核状态流转。"""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def list_candidates(
        self,
        *,
        user_id: str,
        project_id: str,
    ) -> list[MemoryRecord]:
        """列出待审核的候选记忆，按 ``updated_at`` 降序。"""
        return await self.store.list_by_status(
            user_id=user_id,
            project_id=project_id,
            status=MemoryStatus.CANDIDATE,
        )

    async def promote(
        self,
        *,
        memory_id: str,
        reviewer: str,
        review_note: str = "",
    ) -> MemoryRecord:
        """审核通过：``candidate -> promoted``。"""
        return await self.review(
            memory_id=memory_id,
            status=MemoryStatus.PROMOTED,
            reviewer=reviewer,
            review_note=review_note,
        )

    async def reject(
        self,
        *,
        memory_id: str,
        reviewer: str,
        review_note: str = "",
    ) -> MemoryRecord:
        """审核拒绝：``candidate -> rejected``。"""
        return await self.review(
            memory_id=memory_id,
            status=MemoryStatus.REJECTED,
            reviewer=reviewer,
            review_note=review_note,
        )

    async def review(
        self,
        *,
        memory_id: str,
        status: str,
        reviewer: str,
        review_note: str = "",
    ) -> MemoryRecord:
        """审核单条候选记忆并返回更新后的记录。

        校验规则：
        - ``status`` 必须是 ``promoted`` 或 ``rejected``
        - ``reviewer`` 不能为空
        - 记忆必须存在且当前状态为 ``candidate``
        """
        if status not in _REVIEWABLE_TARGETS:
            raise ReviewError(
                f"invalid review target status: {status!r}; "
                f"must be one of {sorted(_REVIEWABLE_TARGETS)}"
            )
        if not reviewer.strip():
            raise ReviewError("reviewer must not be empty")

        record = await self.store.get(memory_id)
        if record is None:
            raise ReviewError(f"memory not found: {memory_id}")
        if record.status != MemoryStatus.CANDIDATE:
            raise ReviewError(
                f"memory {memory_id} is not reviewable: current status={record.status!r}"
            )

        await self.store.update_status(
            memory_id=memory_id,
            status=status,
            reviewer=reviewer,
            review_note=review_note,
        )
        updated = await self.store.get(memory_id)
        if updated is None:
            # update_status 成功后理论上不应 get 不到；保守返回原记录。
            return record
        return updated


__all__ = ["MemoryReviewService", "ReviewError"]

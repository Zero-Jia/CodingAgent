"""记忆系统契约。

当前版本只定义 metadata 层（MySQL/SQLite）的存取接口，不涉及 Milvus 向量
召回、自动提取与 recall 注入。后者由后续 B1-2 ~ B1-6 任务实现。

记忆生命周期状态：
- ``candidate``：由 extraction（B1-3）写入，等待人工审核
- ``promoted``：审核通过，可被 recall 注入 runtime context（B1-5）
- ``rejected``：审核拒绝，不再召回
- ``expired``：超过 TTL 或置信度过低（B1-6）
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class MemoryStatus:
    """记忆状态常量。"""

    CANDIDATE = "candidate"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    EXPIRED = "expired"

    ALL: tuple[str, ...] = (CANDIDATE, PROMOTED, REJECTED, EXPIRED)


class MemoryScope:
    """记忆作用域常量。"""

    SESSION = "session"
    PROJECT = "project"
    USER = "user"

    ALL: tuple[str, ...] = (SESSION, PROJECT, USER)


class MemoryCategory:
    """记忆类别受控词表。

    规则提取器只产出 ``preference``；模型提取器可产出任意类别。受控词表
    便于 B1-4 审核分组与 B1-6 去重合并。
    """

    PREFERENCE = "preference"
    CONVENTION = "convention"
    DECISION = "decision"
    FIX = "fix"
    FACT = "fact"

    ALL: tuple[str, ...] = (PREFERENCE, CONVENTION, DECISION, FIX, FACT)


class MemoryRecord(BaseModel):
    """单条记忆的 metadata 表示。"""

    memory_id: str
    user_id: str
    project_id: str
    scope: str
    category: str
    content: str
    source_session_id: str
    source_run_id: str = ""
    confidence: float
    status: str
    reviewer: str = ""
    review_note: str = ""
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None = None
    expires_at: datetime | None = None


class MemoryStore(Protocol):
    """记忆 metadata 存取协议。

    实现方需要保证：
    - ``store`` 写入的是 ``candidate`` 状态的新记录
    - ``update_status`` 只更新 ``status``/``reviewer``/``review_note``/``reviewed_at``/
      ``updated_at`` 字段
    - ``search`` / ``list_by_status`` / ``list_promoted`` 返回的记录按 ``updated_at``
      降序排列，便于审核界面优先展示最近变更
    """

    async def store(self, record: MemoryRecord) -> None:
        """新增候选记忆。如果 ``memory_id`` 已存在则抛出 ``IntegrityError``。"""
        ...

    async def get(self, memory_id: str) -> MemoryRecord | None:
        """按主键读取单条记忆。"""
        ...

    async def list_by_status(
        self,
        *,
        user_id: str,
        project_id: str,
        status: str,
    ) -> list[MemoryRecord]:
        """按 user/project/status 列出记忆，按 ``updated_at`` 降序。"""
        ...

    async def update_status(
        self,
        *,
        memory_id: str,
        status: str,
        reviewer: str,
        review_note: str = "",
    ) -> None:
        """更新记忆状态（promote/reject/expire）。``reviewed_at`` 设为当前时间。"""
        ...

    async def list_promoted(
        self,
        *,
        user_id: str,
        project_id: str,
        scope: str | None = None,
    ) -> list[MemoryRecord]:
        """列出已 promoted 的记忆，供 recall（B1-5）使用。"""
        ...

    async def search(
        self,
        *,
        user_id: str,
        project_id: str,
        query: str,
        status: str | None = MemoryStatus.PROMOTED,
    ) -> list[MemoryRecord]:
        """对 content 做大小写不敏感子串匹配的 metadata 检索。

        向量召回由 B1-2 在 ``semantic`` 模块单独实现；这里只提供保底的文本检索。
        """
        ...


class NoopMemoryStore:
    """默认降级实现：不持久化任何记忆。"""

    async def store(self, record: MemoryRecord) -> None:
        return None

    async def get(self, memory_id: str) -> MemoryRecord | None:
        return None

    async def list_by_status(
        self,
        *,
        user_id: str,
        project_id: str,
        status: str,
    ) -> list[MemoryRecord]:
        return []

    async def update_status(
        self,
        *,
        memory_id: str,
        status: str,
        reviewer: str,
        review_note: str = "",
    ) -> None:
        return None

    async def list_promoted(
        self,
        *,
        user_id: str,
        project_id: str,
        scope: str | None = None,
    ) -> list[MemoryRecord]:
        return []

    async def search(
        self,
        *,
        user_id: str,
        project_id: str,
        query: str,
        status: str | None = MemoryStatus.PROMOTED,
    ) -> list[MemoryRecord]:
        return []


__all__ = [
    "MemoryCategory",
    "MemoryRecord",
    "MemoryScope",
    "MemoryStatus",
    "MemoryStore",
    "NoopMemoryStore",
]

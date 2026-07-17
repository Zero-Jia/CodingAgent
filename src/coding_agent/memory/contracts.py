"""记忆边界；当前版本有意不提供自动提取。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class MemoryRecord(BaseModel):
    memory_id: str
    user_id: str
    project_id: str
    scope: str
    category: str
    content: str
    source_session_id: str
    confidence: float
    status: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class MemoryStore(Protocol):
    async def search(self, user_id: str, project_id: str, query: str) -> list[MemoryRecord]: ...


class NoopMemoryStore:
    async def search(self, user_id: str, project_id: str, query: str) -> list[MemoryRecord]:
        return []

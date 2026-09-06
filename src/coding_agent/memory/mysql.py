"""SQLAlchemy-backed memory metadata storage.

Mirrors the ``MySqlSessionStore`` pattern: the implementation avoids
dialect-specific syntax so the same contract tests can run on SQLite while
production can use MySQL through the shared SQLAlchemy table definitions.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Engine, and_, func, or_, select, update

from coding_agent.db import tables
from coding_agent.memory.contracts import MemoryRecord


class MySqlMemoryStore:
    """``MemoryStore`` implementation backed by SQLAlchemy Core.

    The store does not ensure that the referenced ``source_session_id`` row
    exists; callers (e.g. the B1-3 extraction step) are expected to write
    memory records only after the source session has been persisted, so the
    foreign key constraint surfaces misuse as an ``IntegrityError``.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    async def store(self, record: MemoryRecord) -> None:
        await asyncio.to_thread(self._store_sync, record)

    async def get(self, memory_id: str) -> MemoryRecord | None:
        return await asyncio.to_thread(self._get_sync, memory_id)

    async def list_by_status(
        self,
        *,
        user_id: str,
        project_id: str,
        status: str,
    ) -> list[MemoryRecord]:
        return await asyncio.to_thread(
            self._list_by_status_sync,
            user_id=user_id,
            project_id=project_id,
            status=status,
        )

    async def update_status(
        self,
        *,
        memory_id: str,
        status: str,
        reviewer: str,
        review_note: str = "",
    ) -> None:
        await asyncio.to_thread(
            self._update_status_sync,
            memory_id=memory_id,
            status=status,
            reviewer=reviewer,
            review_note=review_note,
        )

    async def list_promoted(
        self,
        *,
        user_id: str,
        project_id: str,
        scope: str | None = None,
    ) -> list[MemoryRecord]:
        return await asyncio.to_thread(
            self._list_promoted_sync,
            user_id=user_id,
            project_id=project_id,
            scope=scope,
        )

    async def search(
        self,
        *,
        user_id: str,
        project_id: str,
        query: str,
        status: str | None = None,
    ) -> list[MemoryRecord]:
        return await asyncio.to_thread(
            self._search_sync,
            user_id=user_id,
            project_id=project_id,
            query=query,
            status=status,
        )

    def _store_sync(self, record: MemoryRecord) -> None:
        with self.engine.begin() as connection:
            connection.execute(tables.memories.insert().values(**_record_values(record)))

    def _get_sync(self, memory_id: str) -> MemoryRecord | None:
        statement = select(tables.memories).where(
            tables.memories.c.memory_id == memory_id
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return _record_from_row(row) if row is not None else None

    def _list_by_status_sync(
        self,
        *,
        user_id: str,
        project_id: str,
        status: str,
    ) -> list[MemoryRecord]:
        statement = (
            select(tables.memories)
            .where(
                and_(
                    tables.memories.c.user_id == user_id,
                    tables.memories.c.project_id == project_id,
                    tables.memories.c.status == status,
                )
            )
            .order_by(tables.memories.c.updated_at.desc())
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_record_from_row(row) for row in rows]

    def _update_status_sync(
        self,
        *,
        memory_id: str,
        status: str,
        reviewer: str,
        review_note: str,
    ) -> None:
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            connection.execute(
                update(tables.memories)
                .where(tables.memories.c.memory_id == memory_id)
                .values(
                    status=status,
                    reviewer=reviewer,
                    review_note=review_note,
                    reviewed_at=now,
                    updated_at=now,
                )
            )

    def _list_promoted_sync(
        self,
        *,
        user_id: str,
        project_id: str,
        scope: str | None,
    ) -> list[MemoryRecord]:
        clauses = [
            tables.memories.c.user_id == user_id,
            tables.memories.c.project_id == project_id,
            tables.memories.c.status == "promoted",
            _not_expired_clause(),
        ]
        if scope is not None:
            clauses.append(tables.memories.c.scope == scope)
        statement = (
            select(tables.memories)
            .where(and_(*clauses))
            .order_by(tables.memories.c.updated_at.desc())
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_record_from_row(row) for row in rows]

    def _search_sync(
        self,
        *,
        user_id: str,
        project_id: str,
        query: str,
        status: str | None,
    ) -> list[MemoryRecord]:
        clauses = [
            tables.memories.c.user_id == user_id,
            tables.memories.c.project_id == project_id,
            func.lower(tables.memories.c.content).like(f"%{query.lower()}%"),
            _not_expired_clause(),
        ]
        if status is not None:
            clauses.append(tables.memories.c.status == status)
        statement = (
            select(tables.memories)
            .where(and_(*clauses))
            .order_by(tables.memories.c.updated_at.desc())
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_record_from_row(row) for row in rows]


def _not_expired_clause() -> ColumnElement[bool]:
    """B1-6：软过期过滤——expires_at 为空或晚于当前 UTC 时间的记录才可见。

    供 ``list_promoted`` 与 ``search`` 使用；``list_by_status`` 不过滤
    （审核界面需要看到全部状态）。
    """
    now = datetime.now(UTC)
    return or_(
        tables.memories.c.expires_at.is_(None),
        tables.memories.c.expires_at > now,
    )


def _record_values(record: MemoryRecord) -> dict[str, object]:
    return {
        "memory_id": record.memory_id,
        "schema_version": 1,
        "user_id": record.user_id,
        "project_id": record.project_id,
        "scope": record.scope,
        "category": record.category,
        "content": record.content,
        "source_session_id": record.source_session_id,
        "source_run_id": record.source_run_id,
        "confidence": record.confidence,
        "status": record.status,
        "reviewer": record.reviewer,
        "review_note": record.review_note,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "reviewed_at": record.reviewed_at,
        "expires_at": record.expires_at,
    }


def _record_from_row(row: Any) -> MemoryRecord:
    return MemoryRecord(
        memory_id=str(row["memory_id"]),
        user_id=str(row["user_id"]),
        project_id=str(row["project_id"]),
        scope=str(row["scope"]),
        category=str(row["category"]),
        content=str(row["content"]),
        source_session_id=str(row["source_session_id"] or ""),
        source_run_id=str(row["source_run_id"] or ""),
        confidence=float(row["confidence"]),
        status=str(row["status"]),
        reviewer=str(row["reviewer"] or ""),
        review_note=str(row["review_note"] or ""),
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
        reviewed_at=_datetime_or_none(row["reviewed_at"]),
        expires_at=_datetime_or_none(row["expires_at"]),
    )


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError(f"expected datetime-compatible value, got {type(value).__name__}")


def _datetime_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    return _datetime(value)


__all__ = ["MySqlMemoryStore"]

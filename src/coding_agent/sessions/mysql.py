"""SQLAlchemy-backed session storage for MySQL deployments."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, delete, select, update

from coding_agent.ai.contracts import ChatMessage
from coding_agent.db import tables
from coding_agent.sessions.store import (
    ConversationCheckpoint,
    SessionEvent,
    SessionSummary,
    redacted_checkpoint,
)


class MySqlSessionStore:
    """SessionStore implementation backed by SQLAlchemy Core.

    The implementation intentionally avoids dialect-specific upsert syntax so
    the same contract tests can run on SQLite while production can use
    MySQL through the same SQLAlchemy table definitions.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    async def append(self, event: SessionEvent) -> None:
        await asyncio.to_thread(self._append_sync, event)

    async def load(self, session_id: str) -> list[SessionEvent]:
        return await asyncio.to_thread(self._load_sync, session_id)

    async def save_checkpoint(self, checkpoint: ConversationCheckpoint) -> None:
        persisted = redacted_checkpoint(checkpoint)
        persisted.updated_at = datetime.now(UTC)
        await asyncio.to_thread(self._save_checkpoint_sync, persisted)

    async def load_checkpoint(self, session_id: str) -> ConversationCheckpoint | None:
        return await asyncio.to_thread(self._load_checkpoint_sync, session_id)

    async def save_summary(self, summary: SessionSummary) -> None:
        await asyncio.to_thread(self._save_summary_sync, summary)

    async def list_summaries(self) -> list[SessionSummary]:
        return await asyncio.to_thread(self._list_summaries_sync)

    async def append_transcript(self, session_id: str, content: str) -> None:
        await asyncio.to_thread(self._append_transcript_sync, session_id, content)

    async def load_transcript(self, session_id: str) -> str:
        return await asyncio.to_thread(self._load_transcript_sync, session_id)

    def _append_sync(self, event: SessionEvent) -> None:
        with self.engine.begin() as connection:
            _ensure_session(connection, event.session_id)
            _ensure_run(connection, event.session_id, event.run_id, started_at=event.timestamp)
            connection.execute(
                tables.session_events.insert().values(
                    session_id=event.session_id,
                    run_id=event.run_id,
                    event_type=event.event_type,
                    payload=event.payload,
                    timestamp=event.timestamp,
                )
            )

    def _load_sync(self, session_id: str) -> list[SessionEvent]:
        statement = (
            select(tables.session_events)
            .where(tables.session_events.c.session_id == session_id)
            .order_by(tables.session_events.c.event_id.asc())
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [
            SessionEvent(
                timestamp=_datetime(row["timestamp"]),
                session_id=str(row["session_id"]),
                run_id=str(row["run_id"]),
                event_type=str(row["event_type"]),
                payload=_dict(row["payload"]),
            )
            for row in rows
        ]

    def _save_checkpoint_sync(self, checkpoint: ConversationCheckpoint) -> None:
        payload = {
            "session_id": checkpoint.session_id,
            "schema_version": checkpoint.schema_version,
            "workspace": checkpoint.workspace,
            "model_provider": checkpoint.model_provider,
            "model_name": checkpoint.model_name,
            "messages": [message.model_dump(mode="json") for message in checkpoint.messages],
            "updated_at": checkpoint.updated_at,
        }
        with self.engine.begin() as connection:
            _ensure_session(
                connection,
                checkpoint.session_id,
                workspace=checkpoint.workspace,
                model_name=checkpoint.model_name,
            )
            connection.execute(
                delete(tables.checkpoints).where(
                    tables.checkpoints.c.session_id == checkpoint.session_id
                )
            )
            connection.execute(tables.checkpoints.insert().values(**payload))

    def _load_checkpoint_sync(self, session_id: str) -> ConversationCheckpoint | None:
        statement = select(tables.checkpoints).where(tables.checkpoints.c.session_id == session_id)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        messages = [
            ChatMessage.model_validate(item)
            for item in _list(row["messages"])
            if isinstance(item, dict)
        ]
        return ConversationCheckpoint(
            schema_version=int(row["schema_version"]),
            session_id=str(row["session_id"]),
            workspace=str(row["workspace"]),
            model_provider=str(row["model_provider"]),
            model_name=str(row["model_name"]),
            messages=messages,
            updated_at=_datetime(row["updated_at"]),
        )

    def _save_summary_sync(self, summary: SessionSummary) -> None:
        values = _summary_values(summary)
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(tables.sessions.c.session_id).where(
                    tables.sessions.c.session_id == summary.session_id
                )
            ).first()
            if existing is None:
                connection.execute(tables.sessions.insert().values(**values))
                return
            connection.execute(
                update(tables.sessions)
                .where(tables.sessions.c.session_id == summary.session_id)
                .values(**values)
            )

    def _list_summaries_sync(self) -> list[SessionSummary]:
        statement = select(tables.sessions).order_by(tables.sessions.c.updated_at.desc())
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_summary_from_row(row) for row in rows]

    def _append_transcript_sync(self, session_id: str, content: str) -> None:
        now = datetime.now(UTC)
        statement = select(tables.transcripts.c.content).where(
            tables.transcripts.c.session_id == session_id
        )
        with self.engine.begin() as connection:
            _ensure_session(connection, session_id)
            existing = connection.execute(statement).scalar_one_or_none()
            connection.execute(
                delete(tables.transcripts).where(tables.transcripts.c.session_id == session_id)
            )
            connection.execute(
                tables.transcripts.insert().values(
                    session_id=session_id,
                    content=(str(existing) if existing is not None else "") + content,
                    updated_at=now,
                )
            )

    def _load_transcript_sync(self, session_id: str) -> str:
        statement = select(tables.transcripts.c.content).where(
            tables.transcripts.c.session_id == session_id
        )
        with self.engine.connect() as connection:
            content = connection.execute(statement).scalar_one_or_none()
        return str(content) if content is not None else ""


def _ensure_session(
    connection: Connection,
    session_id: str,
    *,
    workspace: str = "",
    model_name: str = "",
) -> None:
    row = (
        connection.execute(
            select(
                tables.sessions.c.workspace,
                tables.sessions.c.model_name,
            ).where(tables.sessions.c.session_id == session_id)
        )
        .mappings()
        .first()
    )
    if row is not None:
        values: dict[str, object] = {}
        if workspace and not row["workspace"]:
            values["workspace"] = workspace
        if model_name and not row["model_name"]:
            values["model_name"] = model_name
        if values:
            values["updated_at"] = datetime.now(UTC)
            connection.execute(
                update(tables.sessions)
                .where(tables.sessions.c.session_id == session_id)
                .values(**values)
            )
        return
    now = datetime.now(UTC)
    summary = SessionSummary(
        session_id=session_id,
        workspace=workspace,
        model_name=model_name,
        created_at=now,
        updated_at=now,
    )
    connection.execute(tables.sessions.insert().values(**_summary_values(summary)))


def _ensure_run(
    connection: Connection,
    session_id: str,
    run_id: str,
    *,
    started_at: datetime,
) -> None:
    exists = connection.execute(
        select(tables.runs.c.run_id).where(tables.runs.c.run_id == run_id)
    ).first()
    if exists is not None:
        return
    connection.execute(
        tables.runs.insert().values(
            run_id=run_id,
            session_id=session_id,
            status="running",
            started_at=started_at,
            last_error="",
        )
    )


def _summary_values(summary: SessionSummary) -> dict[str, object]:
    return {
        "session_id": summary.session_id,
        "workspace": summary.workspace,
        "model_name": summary.model_name,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
        "last_status": summary.last_status,
        "last_user_message_preview": summary.last_user_message_preview,
        "message_count": summary.message_count,
        "run_count": summary.run_count,
        "tool_count": summary.tool_count,
        "approval_count": summary.approval_count,
        "cancelled_count": summary.cancelled_count,
        "failed_count": summary.failed_count,
        "total_duration_ms": summary.total_duration_ms,
        "total_prompt_tokens": summary.total_prompt_tokens,
        "total_completion_tokens": summary.total_completion_tokens,
        "total_tokens": summary.total_tokens,
        "current_context_tokens": summary.current_context_tokens,
        "context_window_tokens": summary.context_window_tokens,
        "context_usage_ratio": summary.context_usage_ratio,
        "current_context_source": summary.current_context_source,
        "last_compact_before_tokens": summary.last_compact_before_tokens,
        "last_compact_after_tokens": summary.last_compact_after_tokens,
        "last_compacted_tokens_saved": summary.last_compacted_tokens_saved,
        "total_compacted_tokens_saved": summary.total_compacted_tokens_saved,
        "last_plan_status": summary.last_plan_status,
        "last_plan_id": summary.last_plan_id,
        "plan_revision_count": summary.plan_revision_count,
        "last_plan_failure": summary.last_plan_failure,
    }


def _summary_from_row(row: Any) -> SessionSummary:
    return SessionSummary(
        session_id=str(row["session_id"]),
        workspace=str(row["workspace"]),
        model_name=str(row["model_name"]),
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
        last_status=str(row["last_status"]),
        last_user_message_preview=str(row["last_user_message_preview"]),
        message_count=int(row["message_count"]),
        run_count=int(row["run_count"]),
        tool_count=int(row["tool_count"]),
        approval_count=int(row["approval_count"]),
        cancelled_count=int(row["cancelled_count"]),
        failed_count=int(row["failed_count"]),
        total_duration_ms=float(row["total_duration_ms"]),
        total_prompt_tokens=int(row["total_prompt_tokens"]),
        total_completion_tokens=int(row["total_completion_tokens"]),
        total_tokens=int(row["total_tokens"]),
        current_context_tokens=int(row["current_context_tokens"]),
        context_window_tokens=int(row["context_window_tokens"]),
        context_usage_ratio=float(row["context_usage_ratio"]),
        current_context_source=str(row["current_context_source"]),
        last_compact_before_tokens=int(row["last_compact_before_tokens"]),
        last_compact_after_tokens=int(row["last_compact_after_tokens"]),
        last_compacted_tokens_saved=int(row["last_compacted_tokens_saved"]),
        total_compacted_tokens_saved=int(row["total_compacted_tokens_saved"]),
        last_plan_status=str(row["last_plan_status"]),
        last_plan_id=str(row["last_plan_id"]),
        plan_revision_count=int(row["plan_revision_count"]),
        last_plan_failure=str(row["last_plan_failure"]),
    )


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError(f"expected datetime-compatible value, got {type(value).__name__}")


def _dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return {str(key): item for key, item in parsed.items()}
    return {}


def _list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    return []

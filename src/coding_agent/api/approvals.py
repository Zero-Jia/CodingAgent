"""Web-backed approval queues for FastAPI sessions."""

from __future__ import annotations

import asyncio
import builtins
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import Connection, Engine, select, update

from coding_agent.db import tables
from coding_agent.tracing.store import redact

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "cancelled"]
FINAL_STATUSES: frozenset[ApprovalStatus] = frozenset(
    {"approved", "rejected", "expired", "cancelled"}
)
MAX_DETAIL_STRING_CHARS = 8_000
MAX_DETAIL_LIST_ITEMS = 200
MAX_REASON_CHARS = 2_000


class ApprovalRecord(BaseModel):
    schema_version: int = 1
    approval_id: str
    session_id: str
    run_id: str
    tool_name: str
    reason: str
    details: dict[str, object] = Field(default_factory=dict)
    status: ApprovalStatus = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution_reason: str = ""
    resolved_by: str = ""


class ApprovalResolutionRequest(BaseModel):
    reason: str = Field(default="", max_length=2_000)


@dataclass(frozen=True)
class ApprovalResolution:
    record: ApprovalRecord
    changed: bool


class ApprovalStore(Protocol):
    async def create(self, record: ApprovalRecord) -> ApprovalRecord: ...
    async def list(self, status: ApprovalStatus | None = "pending") -> list[ApprovalRecord]: ...
    async def get(self, approval_id: str) -> ApprovalRecord: ...
    async def resolve(
        self,
        approval_id: str,
        status: ApprovalStatus,
        reason: str = "",
        *,
        resolved_by: str = "",
    ) -> ApprovalResolution: ...


class ApprovalAuditStore(Protocol):
    async def append(self, record: ApprovalRecord, event_type: str) -> None: ...


class JsonlApprovalAuditStore:
    """Append-only local approval audit log.

    This is deliberately separate from the future MySQL approval queue.
    The API can be useful in local mode today while keeping the storage boundary
    replaceable for production deployments.
    """

    def __init__(self, root: Path) -> None:
        self.path = root / "approvals" / "audit.jsonl"

    async def append(self, record: ApprovalRecord, event_type: str) -> None:
        payload = record.model_dump(mode="json")
        payload["event_type"] = event_type
        payload["audit_timestamp"] = datetime.now(UTC).isoformat()
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(_append_jsonl, self.path, redact(payload))


class InMemoryApprovalStore:
    """Process-local approval state used by the default JSONL/local mode."""

    def __init__(self) -> None:
        self._items: dict[str, ApprovalRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: ApprovalRecord) -> ApprovalRecord:
        async with self._lock:
            self._items[record.approval_id] = record
            return record

    async def list(self, status: ApprovalStatus | None = "pending") -> list[ApprovalRecord]:
        async with self._lock:
            records = list(self._items.values())
        if status is not None:
            records = [record for record in records if record.status == status]
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    async def get(self, approval_id: str) -> ApprovalRecord:
        async with self._lock:
            record = self._items.get(approval_id)
        if record is None:
            raise KeyError(approval_id)
        return record

    async def resolve(
        self,
        approval_id: str,
        status: ApprovalStatus,
        reason: str = "",
        *,
        resolved_by: str = "",
    ) -> ApprovalResolution:
        if status == "pending":
            raise ValueError("pending is not a final approval status")
        async with self._lock:
            record = self._items.get(approval_id)
            if record is None:
                raise KeyError(approval_id)
            if record.status in FINAL_STATUSES:
                return ApprovalResolution(record=record, changed=False)
            record.status = status
            record.resolved_at = datetime.now(UTC)
            record.resolution_reason = reason[:MAX_REASON_CHARS]
            record.resolved_by = resolved_by[:128]
            return ApprovalResolution(record=record, changed=True)


class MySqlApprovalStore:
    """SQLAlchemy-backed approval queue for MySQL deployments.

    The implementation avoids dialect-specific upsert syntax so tests can use
    SQLite while production uses the same SQLAlchemy table definitions with
    MySQL.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    async def create(self, record: ApprovalRecord) -> ApprovalRecord:
        return await asyncio.to_thread(self._create_sync, record)

    async def list(self, status: ApprovalStatus | None = "pending") -> list[ApprovalRecord]:
        return await asyncio.to_thread(self._list_sync, status)

    async def get(self, approval_id: str) -> ApprovalRecord:
        return await asyncio.to_thread(self._get_sync, approval_id)

    async def resolve(
        self,
        approval_id: str,
        status: ApprovalStatus,
        reason: str = "",
        *,
        resolved_by: str = "",
    ) -> ApprovalResolution:
        return await asyncio.to_thread(
            self._resolve_sync,
            approval_id,
            status,
            reason[:MAX_REASON_CHARS],
            resolved_by[:128],
        )

    def _create_sync(self, record: ApprovalRecord) -> ApprovalRecord:
        with self.engine.begin() as connection:
            _ensure_session(connection, record.session_id)
            _ensure_run(connection, record.session_id, record.run_id, record.created_at)
            connection.execute(tables.approvals.insert().values(**_record_values(record)))
        return record

    def _list_sync(self, status: ApprovalStatus | None) -> builtins.list[ApprovalRecord]:
        statement = select(tables.approvals).order_by(tables.approvals.c.created_at.desc())
        if status is not None:
            statement = statement.where(tables.approvals.c.status == status)
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [_record_from_row(row) for row in rows]

    def _get_sync(self, approval_id: str) -> ApprovalRecord:
        statement = select(tables.approvals).where(
            tables.approvals.c.approval_id == approval_id
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            raise KeyError(approval_id)
        return _record_from_row(row)

    def _resolve_sync(
        self,
        approval_id: str,
        status: ApprovalStatus,
        reason: str,
        resolved_by: str,
    ) -> ApprovalResolution:
        if status == "pending":
            raise ValueError("pending is not a final approval status")
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(tables.approvals)
                    .where(tables.approvals.c.approval_id == approval_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                raise KeyError(approval_id)
            current = _record_from_row(row)
            if current.status in FINAL_STATUSES:
                return ApprovalResolution(record=current, changed=False)
            resolved_at = datetime.now(UTC)
            connection.execute(
                update(tables.approvals)
                .where(tables.approvals.c.approval_id == approval_id)
                .values(
                    status=status,
                    resolved_at=resolved_at,
                    resolution_reason=reason,
                    resolved_by=resolved_by,
                )
            )
            updated = current.model_copy(
                update={
                    "status": status,
                    "resolved_at": resolved_at,
                    "resolution_reason": reason,
                    "resolved_by": resolved_by,
                }
            )
            return ApprovalResolution(record=updated, changed=True)


class ApprovalRegistry:
    """HITL approval queue used by the local FastAPI app.

    A local future map keeps currently running runtime calls responsive. The
    store is the source of truth, so a different API process can resolve a
    MySQL-backed approval and the waiting runtime will observe it by polling.
    """

    def __init__(
        self,
        audit: ApprovalAuditStore,
        *,
        store: ApprovalStore | None = None,
        timeout_seconds: float = 600.0,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        self.audit = audit
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.store = store or InMemoryApprovalStore()
        self._futures: dict[str, asyncio.Future[bool]] = {}
        self._lock = asyncio.Lock()

    async def request(
        self,
        tool_name: str,
        reason: str,
        params: dict[str, object],
        *,
        session_id: str = "",
        run_id: str = "",
    ) -> bool:
        loop = asyncio.get_running_loop()
        approval_id = f"approval-{uuid.uuid4()}"
        record = ApprovalRecord(
            approval_id=approval_id,
            session_id=session_id or "standalone",
            run_id=run_id or f"approval-run-{uuid.uuid4()}",
            tool_name=tool_name[:128],
            reason=reason[:MAX_REASON_CHARS],
            details=_sanitize_details(params),
            expires_at=datetime.now(UTC) + timedelta(seconds=self.timeout_seconds),
        )
        await self.store.create(record)
        future: asyncio.Future[bool] = loop.create_future()
        async with self._lock:
            self._futures[record.approval_id] = future
        await self.audit.append(record, "requested")
        try:
            return await self._wait_for_resolution(record.approval_id, future)
        except TimeoutError:
            await self.resolve(record.approval_id, "expired", "approval timed out")
            return False
        except asyncio.CancelledError:
            await self.resolve(record.approval_id, "cancelled", "run was cancelled")
            raise
        finally:
            async with self._lock:
                self._futures.pop(record.approval_id, None)

    async def list(self, status: ApprovalStatus | None = "pending") -> list[ApprovalRecord]:
        return await self.store.list(status)

    async def get(self, approval_id: str) -> ApprovalRecord:
        return await self.store.get(approval_id)

    async def approve(self, approval_id: str, reason: str = "") -> ApprovalRecord:
        return await self.resolve(approval_id, "approved", reason, resolved_by="local-api")

    async def reject(self, approval_id: str, reason: str = "") -> ApprovalRecord:
        return await self.resolve(approval_id, "rejected", reason, resolved_by="local-api")

    async def cancel_run(self, session_id: str, run_id: str) -> None:
        records = await self.list(status="pending")
        for record in records:
            if record.session_id == session_id and record.run_id == run_id:
                await self.resolve(
                    record.approval_id,
                    "cancelled",
                    "run finished or disconnected",
                    resolved_by="runtime",
                )

    async def resolve(
        self,
        approval_id: str,
        status: ApprovalStatus,
        reason: str = "",
        *,
        resolved_by: str = "",
    ) -> ApprovalRecord:
        resolution = await self.store.resolve(
            approval_id,
            status,
            reason[:MAX_REASON_CHARS],
            resolved_by=resolved_by[:128],
        )
        async with self._lock:
            future = self._futures.get(approval_id)
            if future is not None and not future.done():
                future.set_result(resolution.record.status == "approved")
        if resolution.changed:
            await self.audit.append(resolution.record, resolution.record.status)
        return resolution.record

    async def _wait_for_resolution(
        self, approval_id: str, future: asyncio.Future[bool]
    ) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        while True:
            if future.done():
                return bool(future.result())
            record = await self.store.get(approval_id)
            if record.status in FINAL_STATUSES:
                return record.status == "approved"
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            try:
                return await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=min(self.poll_interval_seconds, remaining),
                )
            except TimeoutError:
                continue


def _sanitize_details(value: dict[str, object]) -> dict[str, object]:
    redacted = redact(value)
    bounded = _bound_detail(redacted)
    if not isinstance(bounded, dict):
        return {}
    return bounded


def _bound_detail(value: object) -> object:
    if isinstance(value, str):
        if len(value) <= MAX_DETAIL_STRING_CHARS:
            return value
        return value[:MAX_DETAIL_STRING_CHARS].rstrip() + "\n... [truncated]"
    if isinstance(value, dict):
        return {str(key): _bound_detail(item) for key, item in value.items()}
    if isinstance(value, list):
        bounded = [_bound_detail(item) for item in value[:MAX_DETAIL_LIST_ITEMS]]
        if len(value) > MAX_DETAIL_LIST_ITEMS:
            bounded.append(f"... [{len(value) - MAX_DETAIL_LIST_ITEMS} items truncated]")
        return bounded
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)


def _append_jsonl(path: Path, payload: object) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _ensure_session(connection: Connection, session_id: str) -> None:
    exists = connection.execute(
        select(tables.sessions.c.session_id).where(tables.sessions.c.session_id == session_id)
    ).first()
    if exists is not None:
        return
    now = datetime.now(UTC)
    connection.execute(
        tables.sessions.insert().values(
            session_id=session_id,
            workspace="",
            model_name="",
            created_at=now,
            updated_at=now,
            last_user_message_preview="",
            last_plan_failure="",
        )
    )


def _ensure_run(connection: Connection, session_id: str, run_id: str, started_at: datetime) -> None:
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


def _record_values(record: ApprovalRecord) -> dict[str, object]:
    return {
        "approval_id": record.approval_id,
        "schema_version": record.schema_version,
        "session_id": record.session_id,
        "run_id": record.run_id,
        "tool_name": record.tool_name,
        "reason": record.reason,
        "status": record.status,
        "details": record.details,
        "created_at": record.created_at,
        "expires_at": record.expires_at,
        "resolved_at": record.resolved_at,
        "resolution_reason": record.resolution_reason,
        "resolved_by": record.resolved_by,
    }


def _record_from_row(row: Any) -> ApprovalRecord:
    return ApprovalRecord(
        schema_version=int(row["schema_version"]),
        approval_id=str(row["approval_id"]),
        session_id=str(row["session_id"]),
        run_id=str(row["run_id"]),
        tool_name=str(row["tool_name"]),
        reason=str(row["reason"]),
        details=_dict(row["details"]),
        status=_approval_status(str(row["status"])),
        created_at=_datetime(row["created_at"]),
        expires_at=_optional_datetime(row["expires_at"]),
        resolved_at=_optional_datetime(row["resolved_at"]),
        resolution_reason=str(row["resolution_reason"]),
        resolved_by=str(row["resolved_by"]),
    )


def _approval_status(value: str) -> ApprovalStatus:
    if value in {"pending", "approved", "rejected", "expired", "cancelled"}:
        return value  # type: ignore[return-value]
    return "cancelled"


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    raise ValueError(f"expected datetime-compatible value, got {type(value).__name__}")


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _datetime(value)


def _dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return {str(key): item for key, item in parsed.items()}
    return {}

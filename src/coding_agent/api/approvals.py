"""Web-backed approval queue for FastAPI sessions."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from coding_agent.tracing.store import redact

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "cancelled"]
FINAL_STATUSES: frozenset[ApprovalStatus] = frozenset(
    {"approved", "rejected", "expired", "cancelled"}
)
MAX_DETAIL_STRING_CHARS = 8_000
MAX_DETAIL_LIST_ITEMS = 200


class ApprovalRecord(BaseModel):
    approval_id: str
    session_id: str
    run_id: str
    tool_name: str
    reason: str
    details: dict[str, object] = Field(default_factory=dict)
    status: ApprovalStatus = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    resolution_reason: str = ""


class ApprovalResolutionRequest(BaseModel):
    reason: str = Field(default="", max_length=2_000)


class ApprovalAuditStore(Protocol):
    async def append(self, record: ApprovalRecord, event_type: str) -> None: ...


class JsonlApprovalAuditStore:
    """Append-only local approval audit log.

    This is deliberately separate from the future PostgreSQL approval queue.
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


@dataclass
class _PendingApproval:
    record: ApprovalRecord
    future: asyncio.Future[bool]


class ApprovalRegistry:
    """In-process HITL approval queue used by the local FastAPI app."""

    def __init__(self, audit: ApprovalAuditStore, *, timeout_seconds: float = 600.0) -> None:
        self.audit = audit
        self.timeout_seconds = timeout_seconds
        self._items: dict[str, _PendingApproval] = {}
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
        approval = _PendingApproval(
            record=ApprovalRecord(
                approval_id=f"approval-{uuid.uuid4()}",
                session_id=session_id,
                run_id=run_id,
                tool_name=tool_name,
                reason=reason,
                details=_sanitize_details(params),
            ),
            future=loop.create_future(),
        )
        async with self._lock:
            self._items[approval.record.approval_id] = approval
        await self.audit.append(approval.record, "requested")
        try:
            return await asyncio.wait_for(approval.future, timeout=self.timeout_seconds)
        except TimeoutError:
            await self.resolve(approval.record.approval_id, "expired", "approval timed out")
            return False
        except asyncio.CancelledError:
            await self.resolve(approval.record.approval_id, "cancelled", "run was cancelled")
            raise

    async def list(self, status: ApprovalStatus | None = "pending") -> list[ApprovalRecord]:
        async with self._lock:
            records = [item.record for item in self._items.values()]
        if status is not None:
            records = [record for record in records if record.status == status]
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    async def get(self, approval_id: str) -> ApprovalRecord:
        async with self._lock:
            item = self._items.get(approval_id)
        if item is None:
            raise KeyError(approval_id)
        return item.record

    async def approve(self, approval_id: str, reason: str = "") -> ApprovalRecord:
        return await self.resolve(approval_id, "approved", reason)

    async def reject(self, approval_id: str, reason: str = "") -> ApprovalRecord:
        return await self.resolve(approval_id, "rejected", reason)

    async def cancel_run(self, session_id: str, run_id: str) -> None:
        records = await self.list(status="pending")
        for record in records:
            if record.session_id == session_id and record.run_id == run_id:
                await self.resolve(record.approval_id, "cancelled", "run finished or disconnected")

    async def resolve(
        self, approval_id: str, status: ApprovalStatus, reason: str = ""
    ) -> ApprovalRecord:
        if status == "pending":
            raise ValueError("pending is not a final approval status")
        async with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                raise KeyError(approval_id)
            if item.record.status in FINAL_STATUSES:
                return item.record
            item.record.status = status
            item.record.resolved_at = datetime.now(UTC)
            item.record.resolution_reason = reason[:2_000]
            if not item.future.done():
                item.future.set_result(status == "approved")
            record = item.record
        await self.audit.append(record, status)
        return record


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

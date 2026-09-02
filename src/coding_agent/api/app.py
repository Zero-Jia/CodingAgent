"""Minimal FastAPI boundary for the CodingAgent runtime."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from coding_agent.agent.coding_agent import ChatSession, CodingAgent
from coding_agent.ai.gateway import create_model_adapter
from coding_agent.api.approvals import (
    ApprovalRecord,
    ApprovalRegistry,
    ApprovalResolutionRequest,
    ApprovalStatus,
    JsonlApprovalAuditStore,
    MySqlApprovalStore,
)
from coding_agent.config import AgentConfig
from coding_agent.runtime.events import AgentEvent
from coding_agent.sessions.lock import SessionLease, SessionLockedError, acquire_session_lock
from coding_agent.sessions.mysql import MySqlSessionStore
from coding_agent.sessions.store import SessionSummary

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class CreateSessionRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=128)

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_session_id(value)


class SessionResponse(BaseModel):
    session_id: str
    summary: SessionSummary


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200_000)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message must not be blank")
        return message


class CancelResponse(BaseModel):
    cancelled: bool
    session_id: str | None = None
    run_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    workspace: str
    model_provider: str
    model_name: str


class ApiSessionManager:
    """Keeps API-visible session and active-run state outside the runtime."""

    def __init__(self, agent: CodingAgent, approvals: ApprovalRegistry) -> None:
        self.agent = agent
        self.approvals = approvals
        self._sessions: dict[str, ChatSession] = {}
        self._leases: dict[str, SessionLease] = {}
        self._active_sessions: dict[str, str] = {}
        self._active_runs: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create_session(self, session_id: str | None = None) -> ChatSession:
        requested = _validate_session_id(session_id) if session_id is not None else None
        async with self._lock:
            if requested is not None and requested in self._sessions:
                return self._sessions[requested]
            lease = self._acquire_lease(requested) if requested is not None else None
            try:
                session = await self.agent.start_chat(requested)
                if lease is None:
                    lease = self._acquire_lease(session.session_id)
                self._sessions[session.session_id] = session
                self._leases[session.session_id] = lease
                return session
            except BaseException:
                if lease is not None:
                    lease.release()
                raise

    async def get_session(self, session_id: str) -> ChatSession:
        safe_session_id = _validate_session_id(session_id)
        async with self._lock:
            cached = self._sessions.get(safe_session_id)
            if cached is not None:
                return cached
            checkpoint = await self.agent.sessions.load_checkpoint(safe_session_id)
            if checkpoint is None:
                raise KeyError(safe_session_id)
            lease = self._acquire_lease(safe_session_id)
            try:
                session = await self.agent.start_chat(safe_session_id)
                self._sessions[safe_session_id] = session
                self._leases[safe_session_id] = lease
                return session
            except BaseException:
                lease.release()
                raise

    async def session_summary(self, session_id: str) -> SessionSummary:
        session = await self.get_session(session_id)
        return session.summary

    async def list_summaries(self) -> list[SessionSummary]:
        return await self.agent.sessions.list_summaries()

    async def stream_message(
        self, session_id: str, message: str
    ) -> AsyncIterator[AgentEvent]:
        session = await self.get_session(session_id)
        async with self._lock:
            if session_id in self._active_sessions:
                raise RuntimeError("session already has an active run")
            self._active_sessions[session_id] = ""
        try:
            async for event in session.send(message):
                if event.type == "run_started":
                    await self._register_run(session_id, event.run_id)
                yield event
        except asyncio.CancelledError:
            session.cancel_current_turn()
            raise
        finally:
            run_id = await self._clear_active(session_id)
            if run_id:
                await self.approvals.cancel_run(session_id, run_id)

    async def cancel_run(self, run_id: str) -> CancelResponse:
        safe_run_id = _validate_run_id(run_id)
        async with self._lock:
            session_id = self._active_runs.get(safe_run_id)
            session = self._sessions.get(session_id or "")
        if session is None or session_id is None:
            return CancelResponse(cancelled=False, run_id=safe_run_id)
        cancelled = session.cancel_current_turn()
        await self.approvals.cancel_run(session_id, safe_run_id)
        return CancelResponse(
            cancelled=cancelled,
            session_id=session_id,
            run_id=safe_run_id,
        )

    async def cancel_session(self, session_id: str) -> CancelResponse:
        safe_session_id = _validate_session_id(session_id)
        async with self._lock:
            run_id = self._active_sessions.get(safe_session_id)
            session = self._sessions.get(safe_session_id)
        if session is None or run_id is None:
            return CancelResponse(cancelled=False, session_id=safe_session_id)
        cancelled = session.cancel_current_turn()
        if run_id:
            await self.approvals.cancel_run(safe_session_id, run_id)
        return CancelResponse(
            cancelled=cancelled,
            session_id=safe_session_id,
            run_id=run_id or None,
        )

    async def _register_run(self, session_id: str, run_id: str) -> None:
        async with self._lock:
            self._active_sessions[session_id] = run_id
            self._active_runs[run_id] = session_id

    async def _clear_active(self, session_id: str) -> str:
        async with self._lock:
            run_id = self._active_sessions.pop(session_id, "")
            if run_id:
                self._active_runs.pop(run_id, None)
            return run_id

    def close(self) -> None:
        for lease in self._leases.values():
            lease.release()
        self._leases.clear()

    def _acquire_lease(self, session_id: str) -> SessionLease:
        return acquire_session_lock(self.agent.data_root, session_id)


def create_app(
    agent: CodingAgent | None = None,
    *,
    config: AgentConfig | None = None,
) -> FastAPI:
    """Create the FastAPI app.

    Production callers normally rely on environment-based config. Tests can
    pass a prebuilt CodingAgent with a fake model adapter.
    """

    if agent is None:
        effective_config = config or AgentConfig.from_environment(Path.cwd())
        agent = CodingAgent(effective_config, create_model_adapter(effective_config))

    approvals = _approval_registry(agent)
    agent.approval = approvals
    manager = ApiSessionManager(agent, approvals)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            manager.close()

    app = FastAPI(title="CodingAgent API", version="0.1.0", lifespan=lifespan)
    app.state.session_manager = manager
    app.state.approval_registry = approvals

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            workspace=str(manager.agent.config.workspace),
            model_provider=manager.agent.model.model.provider,
            model_name=manager.agent.model.model.name,
        )

    @app.post("/v1/sessions", response_model=SessionResponse)
    async def create_session(request: CreateSessionRequest) -> SessionResponse:
        try:
            session = await manager.create_session(request.session_id)
        except SessionLockedError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return SessionResponse(session_id=session.session_id, summary=session.summary)

    @app.get("/v1/sessions", response_model=list[SessionSummary])
    async def list_sessions() -> list[SessionSummary]:
        return await manager.list_summaries()

    @app.get("/v1/sessions/{session_id}", response_model=SessionResponse)
    async def get_session(session_id: str) -> SessionResponse:
        try:
            session = await manager.get_session(session_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="session not found") from error
        except SessionLockedError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return SessionResponse(session_id=session.session_id, summary=session.summary)

    @app.post("/v1/sessions/{session_id}/messages/stream")
    async def stream_message(
        session_id: str,
        request: SendMessageRequest,
    ) -> StreamingResponse:
        try:
            _validate_session_id(session_id)
            await manager.session_summary(session_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="session not found") from error
        except SessionLockedError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        async def event_stream() -> AsyncIterator[str]:
            try:
                async for event in manager.stream_message(session_id, request.message):
                    yield _sse(event)
            except RuntimeError as error:
                yield _sse_error(session_id, str(error))

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/approvals", response_model=list[ApprovalRecord])
    async def list_approvals(status: str = "pending") -> list[ApprovalRecord]:
        try:
            return await approvals.list(_approval_status_filter(status))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/approvals/ui", response_class=HTMLResponse)
    async def approvals_ui() -> HTMLResponse:
        return HTMLResponse(_approval_ui_html())

    @app.get("/approvals/{approval_id}", response_model=ApprovalRecord)
    async def get_approval(approval_id: str) -> ApprovalRecord:
        try:
            return await approvals.get(_validate_approval_id(approval_id))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="approval not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/approvals/{approval_id}/approve", response_model=ApprovalRecord)
    async def approve_approval(
        approval_id: str, request: ApprovalResolutionRequest | None = None
    ) -> ApprovalRecord:
        try:
            payload = request or ApprovalResolutionRequest()
            return await approvals.approve(_validate_approval_id(approval_id), payload.reason)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="approval not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/approvals/{approval_id}/reject", response_model=ApprovalRecord)
    async def reject_approval(
        approval_id: str, request: ApprovalResolutionRequest | None = None
    ) -> ApprovalRecord:
        try:
            payload = request or ApprovalResolutionRequest()
            return await approvals.reject(_validate_approval_id(approval_id), payload.reason)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="approval not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/v1/runs/{run_id}/cancel", response_model=CancelResponse)
    async def cancel_run(run_id: str) -> CancelResponse:
        try:
            return await manager.cancel_run(run_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/v1/sessions/{session_id}/cancel", response_model=CancelResponse)
    async def cancel_session(session_id: str) -> CancelResponse:
        try:
            return await manager.cancel_session(session_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return app


def _approval_registry(agent: CodingAgent) -> ApprovalRegistry:
    store = None
    if isinstance(agent.sessions, MySqlSessionStore):
        store = MySqlApprovalStore(agent.sessions.engine)
    return ApprovalRegistry(JsonlApprovalAuditStore(agent.data_root), store=store)


def _sse(event: AgentEvent) -> str:
    data = event.model_dump(mode="json")
    return f"event: {event.type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_error(session_id: str, message: str) -> str:
    payload = {
        "session_id": session_id,
        "run_id": "",
        "type": "run_failed",
        "payload": {"reason": message},
    }
    return f"event: run_failed\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _validate_session_id(value: str) -> str:
    session_id = value.strip()
    if not _SESSION_ID.fullmatch(session_id):
        raise ValueError("session_id must use 1-128 characters: letters, numbers, '_' or '-'")
    return session_id


def _validate_run_id(value: str) -> str:
    run_id = value.strip()
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must use 1-128 characters: letters, numbers, '_' or '-'")
    return run_id


def _validate_approval_id(value: str) -> str:
    approval_id = value.strip()
    if not _RUN_ID.fullmatch(approval_id):
        raise ValueError("approval_id must use 1-128 characters: letters, numbers, '_' or '-'")
    return approval_id


def _approval_status_filter(value: str) -> ApprovalStatus | None:
    status = value.strip().lower()
    if status == "all":
        return None
    allowed: set[ApprovalStatus] = {"pending", "approved", "rejected", "expired", "cancelled"}
    if status not in allowed:
        raise ValueError("status must be pending, approved, rejected, expired, cancelled, or all")
    return status  # type: ignore[return-value]


def _approval_ui_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CodingAgent Approvals</title>
  <style>
    :root { color-scheme: light dark; font-family: Segoe UI, Arial, sans-serif; }
    body { margin: 0; background: #f7f7f4; color: #202124; }
    header { padding: 16px 24px; border-bottom: 1px solid #d7d7d2; background: #ffffff; }
    h1 { margin: 0; font-size: 20px; font-weight: 650; }
    main {
      display: grid;
      grid-template-columns: minmax(260px, 34%) 1fr;
      min-height: calc(100vh - 57px);
    }
    #list { border-right: 1px solid #d7d7d2; background: #fbfbf8; overflow: auto; }
    #detail { padding: 20px 24px; overflow: auto; }
    button {
      border: 1px solid #b8b8b2;
      background: #fff;
      color: #202124;
      border-radius: 6px;
      padding: 8px 12px;
      cursor: pointer;
    }
    button.primary { background: #155e75; border-color: #155e75; color: #fff; }
    button.danger { background: #9f1239; border-color: #9f1239; color: #fff; }
    .item {
      width: 100%;
      text-align: left;
      border: 0;
      border-bottom: 1px solid #e2e2dc;
      border-radius: 0;
      background: transparent;
      padding: 14px 16px;
    }
    .item:hover, .item.selected { background: #eceee7; }
    .meta { color: #63635e; font-size: 12px; margin-top: 4px; overflow-wrap: anywhere; }
    .row { display: flex; gap: 10px; align-items: center; margin: 14px 0; flex-wrap: wrap; }
    .label { font-weight: 650; min-width: 110px; }
    pre {
      max-height: 55vh;
      overflow: auto;
      padding: 14px;
      background: #1f2933;
      color: #e5e7eb;
      border-radius: 6px;
      white-space: pre-wrap;
    }
    textarea {
      width: min(720px, 100%);
      min-height: 68px;
      padding: 10px;
      border: 1px solid #b8b8b2;
      border-radius: 6px;
    }
    ul { margin-top: 6px; }
    @media (max-width: 760px) {
      main { grid-template-columns: 1fr; }
      #list {
        border-right: 0;
        border-bottom: 1px solid #d7d7d2;
        max-height: 38vh;
      }
    }
  </style>
</head>
<body>
  <header><h1>CodingAgent Approvals</h1></header>
  <main>
    <section id="list"></section>
    <section id="detail"><p class="meta">Select a pending approval.</p></section>
  </main>
  <script>
    let selected = "";
    async function loadApprovals() {
      const response = await fetch("/approvals?status=pending", {cache: "no-store"});
      const approvals = response.ok ? await response.json() : [];
      const list = document.getElementById("list");
      list.replaceChildren();
      if (!approvals.length) {
        const empty = document.createElement("p");
        empty.className = "meta";
        empty.style.padding = "16px";
        empty.textContent = "No pending approvals.";
        list.appendChild(empty);
        if (selected) document.getElementById("detail").textContent = "";
        selected = "";
        return;
      }
      approvals.forEach((approval) => {
        const button = document.createElement("button");
        button.className = "item" + (approval.approval_id === selected ? " selected" : "");
        const title = document.createElement("div");
        title.textContent = approval.tool_name + " · " + approval.status;
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = approval.approval_id + " · " + approval.reason;
        button.append(title, meta);
        button.onclick = () => { selected = approval.approval_id; showApproval(approval); };
        list.appendChild(button);
      });
      if (!selected) {
        selected = approvals[0].approval_id;
        showApproval(approvals[0]);
      }
    }
    function row(label, value) {
      const div = document.createElement("div");
      div.className = "row";
      const key = document.createElement("div");
      key.className = "label";
      key.textContent = label;
      const val = document.createElement("div");
      val.textContent = value || "";
      div.append(key, val);
      return div;
    }
    function showApproval(approval) {
      const detail = document.getElementById("detail");
      detail.replaceChildren();
      detail.append(
        row("Tool", approval.tool_name),
        row("Reason", approval.reason),
        row("Session", approval.session_id),
        row("Run", approval.run_id),
        row("Created", approval.created_at)
      );
      const files = approval.details.changed_files || approval.details.files || [];
      const fileBlock = document.createElement("div");
      const fileLabel = document.createElement("div");
      fileLabel.className = "label";
      fileLabel.textContent = "Changed files";
      const ul = document.createElement("ul");
      files.forEach((file) => {
        const li = document.createElement("li");
        li.textContent = String(file);
        ul.appendChild(li);
      });
      fileBlock.append(fileLabel, ul);
      detail.appendChild(fileBlock);
      const pre = document.createElement("pre");
      pre.textContent = approval.details.diff_preview || JSON.stringify(approval.details, null, 2);
      detail.appendChild(pre);
      const note = document.createElement("textarea");
      note.placeholder = "Optional resolution reason";
      const actions = document.createElement("div");
      actions.className = "row";
      const approve = document.createElement("button");
      approve.className = "primary";
      approve.textContent = "Approve";
      approve.onclick = () => resolveApproval(approval.approval_id, "approve", note.value);
      const reject = document.createElement("button");
      reject.className = "danger";
      reject.textContent = "Reject";
      reject.onclick = () => resolveApproval(approval.approval_id, "reject", note.value);
      actions.append(approve, reject);
      detail.append(note, actions);
    }
    async function resolveApproval(id, action, reason) {
      await fetch(`/approvals/${id}/${action}`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({reason})
      });
      selected = "";
      await loadApprovals();
    }
    loadApprovals();
    setInterval(loadApprovals, 2000);
  </script>
</body>
</html>"""

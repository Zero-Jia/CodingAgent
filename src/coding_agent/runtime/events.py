"""CLI 及未来 API/TUI 客户端消费的公开运行时事件联合类型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgentEventBase(BaseModel):
    session_id: str
    run_id: str
    type: str
    payload: dict[str, object] = Field(default_factory=dict)


class RunStarted(AgentEventBase):
    type: Literal["run_started"] = "run_started"


class MessageDelta(AgentEventBase):
    type: Literal["message_delta"] = "message_delta"


class ReasoningDelta(AgentEventBase):
    type: Literal["reasoning_delta"] = "reasoning_delta"


class ContextCompacted(AgentEventBase):
    type: Literal["context_compacted"] = "context_compacted"


class ModelUsageReported(AgentEventBase):
    type: Literal["model_usage_reported"] = "model_usage_reported"


class TokenUsageUpdated(AgentEventBase):
    type: Literal["token_usage_updated"] = "token_usage_updated"


class ToolStarted(AgentEventBase):
    type: Literal["tool_started"] = "tool_started"


class ToolUpdated(AgentEventBase):
    type: Literal["tool_updated"] = "tool_updated"


class ToolFinished(AgentEventBase):
    type: Literal["tool_finished"] = "tool_finished"


class ApprovalRequested(AgentEventBase):
    type: Literal["approval_requested"] = "approval_requested"


class ApprovalResolved(AgentEventBase):
    type: Literal["approval_resolved"] = "approval_resolved"


class PlanSubmitted(AgentEventBase):
    type: Literal["plan_submitted"] = "plan_submitted"


class PlanApproved(AgentEventBase):
    type: Literal["plan_approved"] = "plan_approved"


class PlanRejected(AgentEventBase):
    type: Literal["plan_rejected"] = "plan_rejected"


class PlanFailed(AgentEventBase):
    type: Literal["plan_failed"] = "plan_failed"


class PlanRevisionRequired(AgentEventBase):
    type: Literal["plan_revision_required"] = "plan_revision_required"


class RunFinished(AgentEventBase):
    type: Literal["run_finished"] = "run_finished"


class RunFailed(AgentEventBase):
    type: Literal["run_failed"] = "run_failed"


class RunCancelled(AgentEventBase):
    type: Literal["run_cancelled"] = "run_cancelled"


AgentEvent = (
    RunStarted
    | MessageDelta
    | ReasoningDelta
    | ContextCompacted
    | ModelUsageReported
    | TokenUsageUpdated
    | ToolStarted
    | ToolUpdated
    | ToolFinished
    | ApprovalRequested
    | ApprovalResolved
    | PlanSubmitted
    | PlanApproved
    | PlanRejected
    | PlanFailed
    | PlanRevisionRequired
    | RunFinished
    | RunFailed
    | RunCancelled
)

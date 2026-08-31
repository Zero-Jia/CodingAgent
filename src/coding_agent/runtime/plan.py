"""Runtime state for plan-mode execution gating."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

from coding_agent.tools.contracts import ToolResult
from coding_agent.tools.plan import plan_approval_details

PLAN_GATED_TOOLS = frozenset({"sandbox_shell", "verify", "apply_patch"})
PlanStatus = Literal["inactive", "draft_required", "approved", "failed", "rejected"]


@dataclass(frozen=True)
class PlanGateDecision:
    allowed: bool
    reason: str = ""


@dataclass
class PlanStateManager:
    """Tracks the approved plan and invalidates it after gated tool failures."""

    enabled: bool
    status: PlanStatus = field(init=False)
    current_plan_id: str = ""
    revision_count: int = 0
    last_failure_summary: str = ""

    def __post_init__(self) -> None:
        self.status = "draft_required" if self.enabled else "inactive"

    def requires_plan(self, tool_name: str) -> bool:
        return self.enabled and tool_name in PLAN_GATED_TOOLS

    def gate(self, tool_name: str) -> PlanGateDecision:
        if not self.requires_plan(tool_name):
            return PlanGateDecision(True)
        if self.status == "approved":
            return PlanGateDecision(True)
        if self.status == "failed":
            return PlanGateDecision(
                False,
                "plan revision required after previous sandbox or patch tool failure",
            )
        if self.status == "rejected":
            return PlanGateDecision(
                False,
                "plan was rejected; submit a revised plan before sandbox or patch tools",
            )
        return PlanGateDecision(
            False,
            "plan mode requires an approved submit_plan call before sandbox or patch tools",
        )

    def submission_error(self, params: dict[str, object]) -> str | None:
        plan = params.get("plan")
        if not isinstance(plan, str) or not plan.strip():
            return "plan must be a non-empty string"
        if self.status == "failed":
            failure_summary = params.get("failure_summary")
            changed_approach = params.get("changed_approach")
            if not isinstance(failure_summary, str) or not failure_summary.strip():
                return "failure_summary must be provided when revising a failed plan"
            if not isinstance(changed_approach, str) or not changed_approach.strip():
                return "changed_approach must be provided when revising a failed plan"
        return None

    def approval_details(self, params: dict[str, object]) -> dict[str, object]:
        details = plan_approval_details(params)
        details.update(
            {
                "plan_status": self.status,
                "current_plan_id": self.current_plan_id,
                "revision_count": self.revision_count,
                "last_failure_summary": self.last_failure_summary,
                "revision_required": self.status in {"failed", "rejected"},
            }
        )
        return details

    def approve(self, params: dict[str, object]) -> dict[str, object]:
        was_revision = self.status in {"failed", "rejected"} or bool(
            _string(params.get("revision_of"))
        )
        if was_revision:
            self.revision_count += 1
        self.current_plan_id = f"plan-{uuid.uuid4()}"
        self.status = "approved"
        self.last_failure_summary = ""
        return self.snapshot()

    def reject(self) -> dict[str, object]:
        self.status = "rejected"
        return self.snapshot()

    def record_tool_result(self, tool_name: str, result: ToolResult) -> dict[str, object] | None:
        if not self.requires_plan(tool_name) or result.status == "success":
            return None
        self.status = "failed"
        self.last_failure_summary = _failure_summary(tool_name, result)
        return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "current_plan_id": self.current_plan_id,
            "revision_count": self.revision_count,
            "last_failure_summary": self.last_failure_summary,
        }


def _failure_summary(tool_name: str, result: ToolResult) -> str:
    summary = result.summary.strip() or result.status
    return f"{tool_name} failed with {result.status}: {summary}"


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""

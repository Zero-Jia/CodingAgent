"""Plan approval tool used to gate high-risk execution."""

from __future__ import annotations

from collections.abc import AsyncIterator

from coding_agent.ai.contracts import ToolDefinition
from coding_agent.tools.contracts import Cancellation, ToolContext, ToolResult, ToolUpdate


class SubmitPlanTool:
    definition = ToolDefinition(
        name="submit_plan",
        description=(
            "Submit an implementation plan for user approval before running sandbox commands "
            "or applying patches. Include planned files, verification commands, risks, and "
            "revision fields when a previous approved plan failed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "objective": {"type": "string"},
                "plan": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "files": {"type": "array", "items": {"type": "string"}},
                "verification_commands": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "revision_of": {"type": "string"},
                "failure_summary": {"type": "string"},
                "changed_approach": {"type": "string"},
            },
            "required": ["plan"],
        },
        risk="read",
    )

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        plan = params.get("plan")
        if not isinstance(plan, str) or not plan.strip():
            yield ToolResult(status="validation_failed", summary="plan must be a non-empty string")
            return
        yield ToolResult(
            status="success",
            summary="plan accepted for this run",
            details={
                "objective": _string(params.get("objective")),
                "steps": _string_list(params.get("steps")),
                "files": _string_list(params.get("files")),
                "verification_commands": _string_list(params.get("verification_commands")),
                "risks": _string_list(params.get("risks")),
                "revision_of": _string(params.get("revision_of")),
                "failure_summary": _string(params.get("failure_summary")),
                "changed_approach": _string(params.get("changed_approach")),
            },
        )


def plan_approval_details(params: dict[str, object]) -> dict[str, object]:
    return {
        "objective": _string(params.get("objective")),
        "plan": _string(params.get("plan")),
        "steps": _string_list(params.get("steps")),
        "files": _string_list(params.get("files")),
        "verification_commands": _string_list(params.get("verification_commands")),
        "risks": _string_list(params.get("risks")),
        "revision_of": _string(params.get("revision_of")),
        "failure_summary": _string(params.get("failure_summary")),
        "changed_approach": _string(params.get("changed_approach")),
    }


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]

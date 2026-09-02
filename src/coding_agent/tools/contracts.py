"""运行时使用的工具协议与结构化结果。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from coding_agent.ai.contracts import ToolDefinition


class ToolUpdate(BaseModel):
    message: str
    stream: Literal["stdout", "stderr", "progress"] = "progress"


class ToolResult(BaseModel):
    status: Literal[
        "success", "policy_denied", "validation_failed", "execution_error", "cancelled", "timeout"
    ]
    summary: str
    output: str = ""
    exit_code: int | None = None
    changed_files: list[str] = Field(default_factory=list)
    details: dict[str, object] = Field(default_factory=dict)


class ToolContext(BaseModel):
    workspace: str
    session_id: str = ""
    run_id: str = ""
    max_output_chars: int = 12_000
    artifact_output_chars: int = 4_000


class Cancellation(Protocol):
    def is_set(self) -> bool: ...
    async def wait(self) -> bool: ...


class Tool(Protocol):
    definition: ToolDefinition

    def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]: ...

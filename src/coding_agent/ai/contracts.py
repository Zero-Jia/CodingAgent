"""供应商无关且强类型的模型流式契约。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class ModelCapabilities(BaseModel):
    streaming: bool = True
    tools: bool = True
    reasoning: bool = False


class Model(BaseModel):
    provider: str
    name: str
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, object]
    risk: Literal["read", "write", "shell"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments_json: str = "{}"


class ChatMessage(BaseModel):
    """供应商无关的会话消息；助手工具调用必须随历史一并保留。"""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ModelRequest(BaseModel):
    messages: list[ChatMessage]
    tools: list[ToolDefinition]
    model: Model
    temperature: float = 0.0


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ReasoningDelta(BaseModel):
    type: Literal["reasoning_delta"] = "reasoning_delta"
    text: str


class ToolCallStarted(BaseModel):
    type: Literal["tool_call_started"] = "tool_call_started"
    call_id: str
    name: str


class ToolCallDelta(BaseModel):
    type: Literal["tool_call_delta"] = "tool_call_delta"
    call_id: str
    arguments_delta: str


class ToolCallCompleted(BaseModel):
    type: Literal["tool_call_completed"] = "tool_call_completed"
    call: ToolCall


class UsageEvent(BaseModel):
    type: Literal["usage"] = "usage"
    usage: Usage


class Completed(BaseModel):
    type: Literal["completed"] = "completed"


class ModelError(BaseModel):
    type: Literal["error"] = "error"
    message: str
    retryable: bool = False


ModelEvent = (
    TextDelta
    | ReasoningDelta
    | ToolCallStarted
    | ToolCallDelta
    | ToolCallCompleted
    | UsageEvent
    | Completed
    | ModelError
)


class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...


class ModelAdapter(Protocol):
    model: Model

    def stream(
        self, request: ModelRequest, signal: CancellationSignal
    ) -> AsyncIterator[ModelEvent]: ...

"""兼容 OpenAI 协议的 DeepSeek SSE 适配器；测试不会调用它。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from coding_agent.ai.contracts import (
    CancellationSignal,
    ChatMessage,
    Completed,
    Model,
    ModelAdapter,
    ModelError,
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCall,
    ToolCallCompleted,
    ToolCallDelta,
    ToolCallStarted,
    Usage,
    UsageEvent,
)


class DeepSeekAdapter(ModelAdapter):
    def __init__(self, model_name: str, api_key: str, base_url: str) -> None:
        self.model = Model(provider="deepseek", name=model_name)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def stream(
        self, request: ModelRequest, signal: CancellationSignal
    ) -> AsyncIterator[ModelEvent]:
        return self._stream(request, signal)

    async def _stream(
        self, request: ModelRequest, signal: CancellationSignal
    ) -> AsyncIterator[ModelEvent]:
        payload = {
            "model": request.model.name,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [_wire_message(message) for message in request.messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ],
        }
        partial: dict[int, dict[str, str]] = {}
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                async with client.stream(
                    "POST", f"{self._base_url}/chat/completions", headers=headers, json=payload
                ) as response:
                    response.raise_for_status()
                    received_done = False
                    async for line in response.aiter_lines():
                        if signal.is_set():
                            return
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if raw == "[DONE]":
                            received_done = True
                            break
                        event = json.loads(raw)
                        usage = event.get("usage")
                        if isinstance(usage, dict):
                            yield UsageEvent(usage=Usage.model_validate(usage))
                        choices = event.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        if content := delta.get("content"):
                            yield TextDelta(text=str(content))
                        for call in delta.get("tool_calls", []):
                            index = int(call.get("index", 0))
                            function = call.get("function", {})
                            state = partial.setdefault(
                                index, {"id": str(call.get("id", "")), "name": "", "args": ""}
                            )
                            state["id"] = str(call.get("id", state["id"]))
                            state["name"] += str(function.get("name", ""))
                            fragment = str(function.get("arguments", ""))
                            if fragment:
                                state["args"] += fragment
                                yield ToolCallDelta(call_id=state["id"], arguments_delta=fragment)
                            if call.get("id"):
                                yield ToolCallStarted(call_id=state["id"], name=state["name"])
                    for state in partial.values():
                        yield ToolCallCompleted(
                            call=ToolCall(
                                id=state["id"], name=state["name"], arguments_json=state["args"]
                            )
                        )
                    if received_done or partial:
                        yield Completed()
        except (httpx.HTTPError, json.JSONDecodeError) as error:
            yield ModelError(
                message=f"DeepSeek request failed: {type(error).__name__}: {error}", retryable=True
            )


def _wire_message(message: ChatMessage) -> dict[str, object]:
    """将内部的工具调用历史转换为 OpenAI 兼容的 wire 格式。"""
    payload: dict[str, object] = {"role": message.role, "content": message.content}
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments_json},
            }
            for call in message.tool_calls
        ]
    return payload

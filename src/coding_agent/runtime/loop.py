"""只依赖模型和工具协议的通用事件驱动 Agent 循环。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Protocol

from coding_agent.ai.contracts import (
    ChatMessage,
    ModelAdapter,
    ModelError,
    ModelRequest,
    TextDelta,
    ToolCall,
    ToolCallCompleted,
    UsageEvent,
)
from coding_agent.policy.engine import PolicyEngine
from coding_agent.runtime.events import (
    AgentEvent,
    ApprovalRequested,
    ApprovalResolved,
    MessageDelta,
    ReasoningDelta,
    RunCancelled,
    RunFailed,
    RunFinished,
    RunStarted,
    ToolFinished,
    ToolStarted,
    ToolUpdated,
)
from coding_agent.tools.contracts import Tool, ToolContext, ToolResult, ToolUpdate
from coding_agent.tracing.store import TraceEvent, TraceStore, output_summary


class ApprovalProvider(Protocol):
    async def request(self, tool_name: str, reason: str, params: dict[str, object]) -> bool: ...


class DenyApproval:
    async def request(self, tool_name: str, reason: str, params: dict[str, object]) -> bool:
        return False


class AgentRuntime:
    """执行单个用户回合，并原地更新调用方持有的消息历史。"""

    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: list[Tool],
        policy: PolicyEngine,
        tool_context: ToolContext,
        trace: TraceStore,
        max_turns: int,
        max_tool_calls: int,
        approval: ApprovalProvider | None = None,
    ) -> None:
        self.model = model
        self.tools = {tool.definition.name: tool for tool in tools}
        self.policy = policy
        self.tool_context = tool_context
        self.trace = trace
        self.max_turns = max_turns
        self.max_tool_calls = max_tool_calls
        self.approval = approval or DenyApproval()
        self.cancel_signal = asyncio.Event()
        self.follow_ups: asyncio.Queue[str] = asyncio.Queue()

    def cancel(self) -> None:
        self.cancel_signal.set()

    async def steer(self, message: str) -> None:
        await self.follow_ups.put(message)

    async def run_turn(
        self, messages: list[ChatMessage], user_message: str, session_id: str, run_id: str
    ) -> AsyncIterator[AgentEvent]:
        """处理一个用户回合；messages 必须已有系统消息和本次用户消息。"""
        yield RunStarted(session_id=session_id, run_id=run_id)
        await self._trace(
            session_id, run_id, "run_started", "runtime", {"task": user_message[:2000]}
        )
        calls = 0
        for turn in range(1, self.max_turns + 1):
            if self.cancel_signal.is_set():
                yield RunCancelled(
                    session_id=session_id, run_id=run_id, payload={"reason": "user cancellation"}
                )
                return
            while not self.follow_ups.empty():
                messages.append(ChatMessage(role="user", content=await self.follow_ups.get()))
            request = ModelRequest(
                messages=messages,
                tools=[tool.definition for tool in self.tools.values()],
                model=self.model.model,
            )
            completed_calls: list[ToolCall] = []
            assistant_text: list[str] = []
            retryable_error = False
            async for event in self.model.stream(request, self.cancel_signal):
                if self.cancel_signal.is_set():
                    yield RunCancelled(
                        session_id=session_id,
                        run_id=run_id,
                        payload={"reason": "user cancellation"},
                    )
                    return
                if isinstance(event, TextDelta):
                    assistant_text.append(event.text)
                    yield MessageDelta(
                        session_id=session_id, run_id=run_id, payload={"text": event.text}
                    )
                elif event.type == "reasoning_delta":
                    yield ReasoningDelta(
                        session_id=session_id, run_id=run_id, payload={"text": event.text}
                    )
                elif isinstance(event, ToolCallCompleted):
                    completed_calls.append(event.call)
                elif isinstance(event, UsageEvent):
                    await self._trace(
                        session_id, run_id, "model_usage", "model", event.usage.model_dump()
                    )
                elif isinstance(event, ModelError):
                    retryable_error = event.retryable
                    await self._trace(
                        session_id, run_id, "model_error", "model", {"message": event.message}
                    )
                    if not retryable_error:
                        yield RunFailed(
                            session_id=session_id, run_id=run_id, payload={"reason": event.message}
                        )
                        return
            if retryable_error:
                if turn < self.max_turns:
                    continue
                yield RunFailed(
                    session_id=session_id,
                    run_id=run_id,
                    payload={"reason": "model retry limit reached"},
                )
                return
            if assistant_text or completed_calls:
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content="".join(assistant_text),
                        tool_calls=completed_calls,
                    )
                )
            if not completed_calls:
                yield RunFinished(session_id=session_id, run_id=run_id, payload={"turns": turn})
                await self._trace(session_id, run_id, "run_finished", "runtime", {"turns": turn})
                return
            for call in completed_calls:
                calls += 1
                if calls > self.max_tool_calls:
                    yield RunFailed(
                        session_id=session_id,
                        run_id=run_id,
                        payload={"reason": "tool call limit reached"},
                    )
                    return
                async for emitted in self._execute_call(call, session_id, run_id):
                    yield emitted
                    if isinstance(emitted, ToolFinished):
                        messages.append(
                            ChatMessage(
                                role="tool",
                                tool_call_id=call.id,
                                content=json.dumps(
                                    emitted.payload.get("result", {}),
                                    ensure_ascii=False,
                                    default=str,
                                ),
                            )
                        )
        yield RunFailed(
            session_id=session_id, run_id=run_id, payload={"reason": "turn limit reached"}
        )

    async def _execute_call(
        self, call: ToolCall, session_id: str, run_id: str
    ) -> AsyncIterator[AgentEvent]:
        try:
            params = json.loads(call.arguments_json)
            if not isinstance(params, dict) or not all(isinstance(key, str) for key in params):
                raise ValueError("tool arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as error:
            yield ToolFinished(
                session_id=session_id,
                run_id=run_id,
                payload={"tool": call.name, "result": f"invalid arguments: {error}"},
            )
            return
        decision = self.policy.tool_decision(call.name, params)
        await self._trace(
            session_id,
            run_id,
            "policy_decision",
            "policy",
            {"tool": call.name, "decision": decision.decision, "reason": decision.reason},
        )
        allowed = decision.decision == "allow"
        if decision.decision == "require_approval":
            yield ApprovalRequested(
                session_id=session_id,
                run_id=run_id,
                payload={"tool": call.name, "reason": decision.reason},
            )
            allowed = await self.approval.request(call.name, decision.reason, params)
            yield ApprovalResolved(
                session_id=session_id,
                run_id=run_id,
                payload={"tool": call.name, "approved": allowed},
            )
        if not allowed:
            denied_result = ToolResult(status="policy_denied", summary=decision.reason)
            yield ToolFinished(
                session_id=session_id,
                run_id=run_id,
                payload={"tool": call.name, "result": denied_result.model_dump()},
            )
            return
        tool = self.tools.get(call.name)
        if tool is None:
            unknown_result = ToolResult(status="validation_failed", summary="unknown tool")
            yield ToolFinished(
                session_id=session_id,
                run_id=run_id,
                payload={"tool": call.name, "result": unknown_result.model_dump()},
            )
            return
        yield ToolStarted(session_id=session_id, run_id=run_id, payload={"tool": call.name})
        result: ToolResult | None = None
        async for update in tool.execute(params, self.tool_context, self.cancel_signal):
            if isinstance(update, ToolUpdate):
                yield ToolUpdated(
                    session_id=session_id,
                    run_id=run_id,
                    payload={"tool": call.name, "message": update.message},
                )
            else:
                result = update
        final = result or ToolResult(status="execution_error", summary="tool returned no result")
        await self._trace(
            session_id,
            run_id,
            "tool_finished",
            "tool",
            {"tool": call.name, "status": final.status, "output": output_summary(final.output)},
        )
        yield ToolFinished(
            session_id=session_id,
            run_id=run_id,
            payload={"tool": call.name, "result": final.model_dump()},
        )

    async def _trace(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        component: str,
        payload: dict[str, object],
    ) -> None:
        await self.trace.append(
            TraceEvent(
                session_id=session_id,
                run_id=run_id,
                event_type=event_type,
                component=component,
                span_id=str(uuid.uuid4()),
                payload=payload,
            )
        )

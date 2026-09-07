from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent
from test_runtime import FakeModelAdapter

from coding_agent.agent.coding_agent import ChatSession, CodingAgent
from coding_agent.ai.contracts import (
    ChatMessage,
    Completed,
    ToolCall,
    ToolCallCompleted,
)
from coding_agent.config import AgentConfig
from coding_agent.mcp.connection import _result_to_model
from coding_agent.mcp.contracts import (
    InMemoryMcpConnection,
    McpConnectionError,
    McpServerConfig,
    McpToolResult,
    McpToolSchema,
)
from coding_agent.mcp.discovery import McpDiscoveryService, _definition
from coding_agent.mcp.execution import McpExecutionService
from coding_agent.mcp.validation import validate_arguments
from coding_agent.tools.contracts import ToolContext, ToolResult
from coding_agent.tools.mcp import McpTool

SCHEMA: dict[str, object] = {
    "type": "object", "properties": {"query": {"type": "string", "minLength": 1}},
    "required": ["query"], "additionalProperties": False,
}


def test_nontext_results_are_explicitly_omitted() -> None:
    result = _result_to_model("lookup", CallToolResult(content=[
        TextContent(type="text", text="safe"),
        ImageContent(type="image", data="base64-private-data", mimeType="image/png"),
    ]))
    assert result.content == "safe"
    assert result.omitted_content_blocks == 1
    assert "base64-private-data" not in result.model_dump_json()


def config(**changes: object) -> McpServerConfig:
    return McpServerConfig.model_validate({
        "name": "remote", "transport": "http", "url": "https://example.invalid/mcp",
        "allowed_readonly_tools": ["lookup"], "timeout_seconds": 0.2, **changes,
    })


def schema() -> McpToolSchema:
    return McpToolSchema(name="lookup", input_schema=SCHEMA)


class Connection(InMemoryMcpConnection):
    def __init__(self, cfg: McpServerConfig) -> None:
        super().__init__(cfg, tools=[schema()])
        self.content = 'token=private-value\n{"password": "hidden-value"}\n' + "x" * 200
        self.error = False
        self.block_at = ""
        self.entered = asyncio.Event()
        self.raise_at = ""

    async def pause(self, stage: str) -> None:
        if self.raise_at == stage:
            raise McpConnectionError("password=exception-secret")
        if self.block_at == stage:
            self.entered.set()
            await asyncio.Event().wait()

    async def connect(self) -> None:
        await super().connect()
        await self.pause("connect")

    async def list_tools(self) -> list[McpToolSchema]:
        await self.pause("list")
        return await super().list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        await super().call_tool(name, arguments)
        await self.pause("call")
        return McpToolResult(name=name, content=self.content, is_error=self.error)

    async def disconnect(self) -> None:
        await super().disconnect()
        await self.pause("disconnect")


def wrapped(cfg: McpServerConfig, conn: Connection) -> McpTool:
    return McpTool(_definition("remote", schema()),
                   McpExecutionService([cfg], factory=lambda _: conn))


async def execute(
    tool: McpTool, arguments: dict[str, object] | None = None,
    signal: asyncio.Event | None = None, budget: int = 100,
) -> ToolResult:
    results = [item async for item in tool.execute(
        {"query": "hello"} if arguments is None else arguments,
        ToolContext(workspace="unused", max_output_chars=budget), signal or asyncio.Event(),
    )]
    assert len(results) == 1 and isinstance(results[0], ToolResult)
    return results[0]


@pytest.mark.parametrize("changes", [
    {"allowed_readonly_tools": []}, {"allowed_readonly_tools": ["*"]},
    {"allowed_readonly_tools": ["other"]}, {"enabled": False},
    {"transport": "stdio", "command": "unused"},
])
async def test_adapter_denies_without_connecting(changes: dict[str, object]) -> None:
    cfg = config(**changes)
    conn = Connection(cfg)
    assert (await execute(wrapped(cfg, conn))).status == "policy_denied"
    assert conn.connect_calls == 0 and not conn.call_log


@pytest.mark.parametrize("arguments", [{}, {"query": 3}, {"query": ""},
                                      {"query": "ok", "extra": True}])
async def test_invalid_arguments_never_connect(arguments: dict[str, object]) -> None:
    cfg = config()
    conn = Connection(cfg)
    assert (await execute(wrapped(cfg, conn), arguments)).status == "validation_failed"
    assert conn.connect_calls == 0


@pytest.mark.parametrize("budget", [0, 30, 100])
async def test_success_sanitizes_before_budget_and_closes(budget: int) -> None:
    cfg = config()
    conn = Connection(cfg)
    result = await execute(wrapped(cfg, conn), budget=budget)
    assert result.status == "success" and len(result.output) <= budget
    assert "private-value" not in result.output and "hidden-value" not in result.output
    assert result.details["truncated"] is True
    assert conn.call_log == [("lookup", {"query": "hello"})]
    assert not conn.is_alive and conn.disconnect_calls == 1


async def test_remote_error_is_sanitized() -> None:
    cfg = config()
    conn = Connection(cfg)
    conn.error = True
    result = await execute(wrapped(cfg, conn))
    assert result.status == "execution_error" and "private-value" not in result.output


@pytest.mark.parametrize("stage", ["connect", "list", "call", "disconnect"])
async def test_transport_failure_is_classified_without_exception_text(stage: str) -> None:
    cfg = config()
    conn = Connection(cfg)
    conn.raise_at = stage
    result = await execute(wrapped(cfg, conn))
    assert result.status == "execution_error"
    assert "exception-secret" not in result.model_dump_json()
    assert not conn.is_alive and conn.disconnect_calls == 1


@pytest.mark.parametrize("stage", ["connect", "list", "call", "disconnect"])
async def test_timeout_cleans_connection(stage: str) -> None:
    cfg = config(timeout_seconds=0.01)
    conn = Connection(cfg)
    conn.block_at = stage
    assert (await execute(wrapped(cfg, conn))).status == "timeout"
    assert not conn.is_alive and conn.disconnect_calls == 1


@pytest.mark.parametrize("stage", ["connect", "list", "call"])
@pytest.mark.parametrize("task_cancel", [False, True])
async def test_cancel_during_io_releases_connection(stage: str, task_cancel: bool) -> None:
    cfg = config(timeout_seconds=2)
    conn = Connection(cfg)
    conn.block_at = stage
    signal = asyncio.Event()
    task = asyncio.create_task(execute(wrapped(cfg, conn), signal=signal))
    await asyncio.wait_for(conn.entered.wait(), 1)
    if task_cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        signal.set()
        assert (await asyncio.wait_for(task, 1)).status == "cancelled"
    assert not conn.is_alive and conn.disconnect_calls == 1


async def test_pre_cancelled_call_does_not_connect() -> None:
    cfg = config()
    conn = Connection(cfg)
    signal = asyncio.Event()
    signal.set()
    assert (await execute(wrapped(cfg, conn), signal=signal)).status == "cancelled"
    assert conn.connect_calls == 0


@pytest.mark.parametrize("schemas", [
    [], [schema(), schema()],
    [McpToolSchema(name="lookup", input_schema={"type": "object"})],
])
async def test_schema_drift_rejects_before_call(schemas: list[McpToolSchema]) -> None:
    cfg = config()
    conn = Connection(cfg)
    conn._tools = schemas
    assert (await execute(wrapped(cfg, conn))).status == "validation_failed"
    assert not conn.call_log and not conn.is_alive


def test_redaction_covers_known_credentials_and_common_formats() -> None:
    service = McpExecutionService([
        config(headers={"Authorization": "Bearer configured-credential"})
    ])
    text = ('configured-credential\nBearer unknown-credential\n'
            'api_key: raw-key\n{"access_token":"json-value"}\n'
            '-----BEGIN PRIVATE KEY-----\nprivate-data\n-----END PRIVATE KEY-----\n'
            'sk-examplekey')
    safe = service.sanitize(text)
    for secret in ("configured-credential", "unknown-credential", "raw-key", "json-value",
                   "private-data", "sk-examplekey"):
        assert secret not in safe
    assert "tail-secret" not in service.sanitize(json.dumps({"password": 'prefix"tail-secret'}))


async def test_concurrent_calls_own_separate_connections() -> None:
    cfg = config()
    instances: list[Connection] = []

    def factory(configuration: McpServerConfig) -> Connection:
        conn = Connection(configuration)
        instances.append(conn)
        return conn

    tool = McpTool(_definition("remote", schema()), McpExecutionService([cfg], factory=factory))
    results = await asyncio.gather(execute(tool), execute(tool))
    assert all(result.status == "success" for result in results)
    assert len(instances) == 2
    assert all(not conn.is_alive and conn.disconnect_calls == 1 for conn in instances)


@pytest.mark.parametrize("fragment", [
    {"$ref": "https://example.invalid/schema"}, {"pattern": ".*"},
    {"type": "string", "format": "email"}, {"type": "bogus"}, {"type": None},
    {"minLength": True},
])
def test_unsupported_optional_schema_is_rejected(fragment: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_arguments({"type": "object", "properties": {"optional": fragment}}, {})


def test_nested_schema_constraints_and_json_types() -> None:
    definition: dict[str, object] = {
        "type": "object", "properties": {"items": {"type": "array", "minItems": 1,
        "maxItems": 2, "items": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 3},
                                          {"const": "special"}]}}},
        "required": ["items"], "additionalProperties": False,
    }
    validate_arguments(definition, {"items": [1, "special"]})
    for values in ([], [0], [4], [True], [1, 2, 3]):
        with pytest.raises(ValueError):
            validate_arguments(definition, {"items": values})
    with pytest.raises(ValueError):
        validate_arguments({"properties": {"v": {"enum": [1]}}}, {"v": True})
    with pytest.raises(ValueError):
        validate_arguments({}, {"v": float("nan")})
    with pytest.raises(ValueError):
        validate_arguments({}, {"v": "x" * 128_001})


@pytest.mark.parametrize("authorized", [False, True])
async def test_chat_policy_trace_artifact_and_model_output(
    tmp_path: Path, authorized: bool,
) -> None:
    cfg = config(allowed_readonly_tools=["lookup"] if authorized else [])
    connections: list[Connection] = []

    def factory(configuration: McpServerConfig) -> Connection:
        conn = Connection(configuration)
        connections.append(conn)
        return conn

    name = _definition("remote", schema()).definition.name
    model = FakeModelAdapter([
        [ToolCallCompleted(call=ToolCall(id="lookup-call", name=name,
                                        arguments_json='{"query":"hello"}')), Completed()],
        [Completed()],
    ])
    agent = CodingAgent(AgentConfig(workspace=tmp_path, mcp_servers=[cfg],
                                   allow_shell=True, allow_write=True, max_tool_output_chars=100,
                                   trace_level="full"), model)
    agent.mcp_discovery = McpDiscoveryService([cfg], factory=factory)
    agent.mcp_execution = McpExecutionService([cfg], factory=factory)
    chat = ChatSession(agent, "mcp-test", [ChatMessage(role="system", content="test")])
    events = [event async for event in chat.send("lookup")]
    final = next(event for event in events if event.type == "tool_finished")
    assert final.payload["result"]["status"] == ("success" if authorized else "policy_denied")
    assert all(not conn.is_alive for conn in connections)
    assert sum(len(conn.call_log) for conn in connections) == int(authorized)
    traces = [json.loads(line) for path in (agent.data_root / "traces").rglob("*.jsonl")
              for line in path.read_text(encoding="utf-8").splitlines()]
    decisions = [event for event in traces if event["event_type"] == "policy_decision"]
    assert decisions[0]["payload"]["decision"] == ("allow" if authorized else "deny")
    if authorized:
        assert any(event["event_type"] == "tool_finished" for event in traces)
        artifacts = list((agent.data_root / "artifacts").rglob("*.txt"))
        assert len(artifacts) == 1
        assert len(artifacts[0].read_text(encoding="utf-8")) <= 100
        tool_message = next(msg for msg in model.requests[-1].messages if msg.role == "tool")
        assert len(json.loads(tool_message.content)["output"]) <= 100
    persisted = "\n".join(path.read_text(encoding="utf-8")
                          for path in agent.data_root.rglob("*") if path.is_file())
    assert "private-value" not in persisted and "hidden-value" not in persisted


async def test_refresh_clears_policy_authorization(tmp_path: Path) -> None:
    cfg = config()
    conn = Connection(cfg)
    agent = CodingAgent(AgentConfig(workspace=tmp_path, mcp_servers=[cfg]), FakeModelAdapter([]))
    agent.mcp_discovery = McpDiscoveryService([cfg], factory=lambda _: conn)
    runtime = agent._new_runtime()
    await agent._register_mcp_tools(runtime)
    assert runtime.policy.mcp_readonly_tools
    conn._tools = []
    await agent._register_mcp_tools(runtime)
    assert not runtime.policy.mcp_readonly_tools

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp import ClientSession
from mcp.types import ListToolsResult
from mcp.types import Tool as SdkTool

from coding_agent.agent.coding_agent import ChatSession, CodingAgent
from coding_agent.ai.contracts import (
    CancellationSignal,
    ChatMessage,
    Completed,
    Model,
    ModelEvent,
    ModelRequest,
    ToolCall,
    ToolCallCompleted,
)
from coding_agent.config import AgentConfig
from coding_agent.mcp import (
    InMemoryMcpConnection,
    McpConnectionError,
    McpDiscoveryResult,
    McpDiscoveryService,
    McpServerConfig,
    McpToolSchema,
    StdioMcpConnection,
    mcp_tool_name,
)
from coding_agent.policy.engine import PolicyEngine
from coding_agent.tools.builtin import ReadTool
from coding_agent.tools.contracts import Tool, ToolContext
from coding_agent.tools.mcp import McpTool, register_mcp_tools


def config(name: str = "server", **kwargs: object) -> McpServerConfig:
    return McpServerConfig.model_validate({"name": name, "command": "unused", **kwargs})


def schema(name: str = "read") -> McpToolSchema:
    return McpToolSchema(name=name, description="Remote tool description", input_schema={
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"], "additionalProperties": False,
        "$defs": {"nested": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
    })


class Factory:
    def __init__(self) -> None:
        self.instances: list[InMemoryMcpConnection] = []
        self.schemas = [schema()]

    def __call__(self, cfg: McpServerConfig) -> InMemoryMcpConnection:
        conn = InMemoryMcpConnection(cfg, tools=self.schemas)
        self.instances.append(conn)
        return conn


async def test_discovery_preserves_schema_and_closes_every_connection() -> None:
    factory = Factory()
    result = await McpDiscoveryService([config("a"), config("b")], factory=factory).discover()
    assert result.errors == {}
    assert len(result.tools) == 2
    assert result.tools[0].definition.parameters == schema().input_schema
    assert result.tools[0].definition.description == schema().description
    assert result.tools[0].server_name == "a"
    assert result.tools[0].tool_name == "read"
    assert result.tools[0].definition.name != result.tools[1].definition.name
    assert all(not conn.is_alive and conn.disconnect_calls == 1 for conn in factory.instances)
    factory.schemas[0].input_schema.clear()
    assert result.tools[0].definition.parameters["type"] == "object"


def test_identifiers_are_bounded_stable_and_distinguish_normalized_names() -> None:
    identities = [("a.b", "read"), ("a/b", "read"), ("a", "b__read"),
                  ("a__b", "read"), ("中文" * 100, "工具" * 100)]
    names = [mcp_tool_name(*pair) for pair in identities]
    assert len(set(names)) == len(names)
    for pair, name in zip(identities, names, strict=True):
        assert name == mcp_tool_name(*pair)
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name)


async def test_empty_disabled_and_duplicate_config() -> None:
    factory = Factory()
    assert (await McpDiscoveryService([], factory=factory).discover()).tools == []
    assert factory.instances == []
    service = McpDiscoveryService([
        config("a"), config("a", enabled=False), config("b"), config("b", command="last")
    ], factory=factory)
    assert [tool.server_name for tool in (await service.discover()).tools] == ["b"]
    assert len(factory.instances) == 1


@pytest.mark.parametrize("failure", ["connect", "list", "invalid", "duplicate", "reserved"])
async def test_bad_server_does_not_block_sibling(failure: str) -> None:
    instances = []

    def factory(cfg: McpServerConfig) -> InMemoryMcpConnection:
        conn = InMemoryMcpConnection(cfg, tools=[schema()],
                                     connect_fails=cfg.name == "bad" and failure == "connect")
        if cfg.name == "bad":
            if failure == "list":
                conn.list_tools = AsyncMock(side_effect=ValueError("SECRET transport error"))
            if failure == "invalid":
                conn.list_tools = AsyncMock(return_value=[schema(), McpToolSchema(name="invalid")])
            if failure == "duplicate":
                conn.list_tools = AsyncMock(return_value=[schema(), schema()])
        instances.append(conn)
        return conn

    result = await McpDiscoveryService([config("bad"), config("ok")], factory=factory).discover(
        reserved_names={mcp_tool_name("bad", "read")} if failure == "reserved" else set()
    )
    assert [tool.server_name for tool in result.tools] == ["ok"]
    assert set(result.errors) == {"bad"}
    assert "SECRET" not in str(result.errors)
    assert all(not conn.is_alive for conn in instances)


@pytest.mark.parametrize("parameters", [
    {}, {"type": "array"}, {"type": "object", "properties": []},
    {"type": "object", "required": "path"}, {"type": "object", "required": [1]},
    {"type": "object", "default": float("nan")},
])
async def test_invalid_schema_is_not_registered(parameters: dict[str, object]) -> None:
    factory = Factory()
    factory.schemas = [McpToolSchema(name="bad", input_schema=parameters)]
    result = await McpDiscoveryService([config()], factory=factory).discover()
    assert not result.tools
    assert result.errors == {"server": "invalid_tool_definitions"}


async def test_registration_refresh_is_idempotent_and_removes_stale_tools() -> None:
    factory = Factory()
    service = McpDiscoveryService([config()], factory=factory)
    read = ReadTool()
    registry: dict[str, Tool] = {"read": read}
    first = await service.discover(reserved_names={"read"})
    register_mcp_tools(registry, first)
    register_mcp_tools(registry, first)
    assert len(registry) == 2
    assert registry["read"] is read
    factory.schemas = [schema("new")]
    register_mcp_tools(registry, await service.discover())
    assert mcp_tool_name("server", "read") not in registry
    assert mcp_tool_name("server", "new") in registry
    register_mcp_tools(registry, McpDiscoveryResult())
    assert registry == {"read": read}


async def test_registration_collision_does_not_mutate_existing_registry() -> None:
    result = await McpDiscoveryService([config()], factory=Factory()).discover()
    occupied = result.tools[0].definition.name
    original: dict[str, Tool] = {occupied: ReadTool()}
    registry = dict(original)
    with pytest.raises(ValueError, match="already registered"):
        register_mcp_tools(registry, result)
    assert registry == original


async def test_direct_execution_and_policy_deny_even_with_all_permissions(tmp_path: Path) -> None:
    result = await McpDiscoveryService([config()], factory=Factory()).discover()
    tool = McpTool(result.tools[0])
    policy = PolicyEngine(tmp_path, allow_shell=True, allow_write=True, non_interactive=False)
    assert policy.tool_decision(tool.definition.name, {}).decision == "deny"
    results = [item async for item in tool.execute(
        {}, ToolContext(workspace=str(tmp_path)), asyncio.Event()
    )]
    assert results[0].status == "policy_denied"


def paginated_connection(pages: list[ListToolsResult]) -> StdioMcpConnection:
    conn = StdioMcpConnection(config(timeout_seconds=0.05))
    conn._session = AsyncMock(spec=ClientSession)
    conn._session.list_tools.side_effect = pages
    return conn


async def test_sdk_pagination_preserves_cursor_and_all_tools() -> None:
    conn = paginated_connection([
        ListToolsResult(tools=[SdkTool(name="a", inputSchema={"type": "object"})], nextCursor="p2"),
        ListToolsResult(tools=[], nextCursor=""),
        ListToolsResult(tools=[SdkTool(name="b", inputSchema={"type": "object"})]),
    ])
    assert [tool.name for tool in await conn.list_tools()] == ["a", "b"]
    assert conn._session is not None
    assert [call.kwargs for call in conn._session.list_tools.await_args_list] == [
        {"cursor": None}, {"cursor": "p2"}, {"cursor": ""}
    ]


async def test_repeated_cursor_rejects_partial_listing() -> None:
    conn = paginated_connection([ListToolsResult(tools=[], nextCursor="x")] * 2)
    with pytest.raises(McpConnectionError, match="repeated"):
        await conn.list_tools()


async def test_page_limit_is_bounded() -> None:
    conn = paginated_connection([ListToolsResult(tools=[], nextCursor=str(i)) for i in range(100)])
    with pytest.raises(McpConnectionError, match="100 pages"):
        await conn.list_tools()


async def test_pagination_timeout_is_for_entire_listing() -> None:
    conn = paginated_connection([])
    calls = 0

    async def page(cursor: str | None = None) -> ListToolsResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return ListToolsResult(tools=[], nextCursor=str(calls))

    conn._session.list_tools.side_effect = page
    with pytest.raises(McpConnectionError):
        await conn.list_tools()
    assert calls < 5


class ModelWithMcpCall:
    model = Model(provider="fake", name="fake")

    def __init__(self, instances: list[InMemoryMcpConnection]) -> None:
        self.requests: list[ModelRequest] = []
        self.instances = instances

    async def stream(
        self, request: ModelRequest, signal: CancellationSignal
    ) -> AsyncIterator[ModelEvent]:
        assert all(not conn.is_alive for conn in self.instances)
        self.requests.append(request)
        if len(self.requests) == 1:
            yield ToolCallCompleted(
                call=ToolCall(id="mcp-call", name=mcp_tool_name("server", "read"))
            )
        yield Completed()


async def test_chat_registers_before_model_and_remote_call_is_denied(tmp_path: Path) -> None:
    factory = Factory()
    model = ModelWithMcpCall(factory.instances)
    agent = CodingAgent(AgentConfig(workspace=tmp_path, mcp_servers=[config()]), model)
    agent.mcp_discovery = McpDiscoveryService([config()], factory=factory)
    chat = ChatSession(agent, "test-mcp", [ChatMessage(role="system", content="test")])
    events = [event async for event in chat.send("inspect tools")]
    definitions = {tool.name: tool for tool in model.requests[0].tools}
    assert "read" in definitions
    assert definitions[mcp_tool_name("server", "read")].parameters == schema().input_schema
    finished = [event for event in events if event.type == "tool_finished"]
    assert finished[0].payload["result"]["status"] == "policy_denied"
    assert all(not conn.call_log for conn in factory.instances)
    factory.schemas = [schema("changed")]
    await anext_and_drain(chat.send("refresh"))
    names = {tool.name for tool in model.requests[-1].tools}
    assert mcp_tool_name("server", "changed") in names
    assert mcp_tool_name("server", "read") not in names


async def anext_and_drain(iterator: AsyncIterator[object]) -> None:
    async for _ in iterator:
        pass


@pytest.mark.parametrize("cancel_via_session", [True, False])
async def test_cancel_discovery_cleans_connections_before_exit(
    tmp_path: Path, cancel_via_session: bool
) -> None:
    entered = asyncio.Event()

    class Stuck(InMemoryMcpConnection):
        async def list_tools(self) -> list[McpToolSchema]:
            entered.set()
            await asyncio.Event().wait()
            return []

    conn = Stuck(config())
    model = ModelWithMcpCall([conn])
    agent = CodingAgent(AgentConfig(workspace=tmp_path, mcp_servers=[config()]), model)
    agent.mcp_discovery = McpDiscoveryService([config()], factory=lambda cfg: conn)
    chat = ChatSession(agent, "cancel-mcp", [ChatMessage(role="system", content="test")])

    async def collect() -> list[str]:
        return [event.type async for event in chat.send("test")]

    task = asyncio.create_task(collect())
    await asyncio.wait_for(entered.wait(), 1)
    if cancel_via_session:
        assert chat.cancel_current_turn()
        assert "run_cancelled" in await asyncio.wait_for(task, 1)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not conn.is_alive
    assert conn.disconnect_calls == 1
    assert model.requests == []
    assert not chat.cancel_current_turn()


async def test_empty_config_does_not_attempt_discovery(tmp_path: Path) -> None:
    model = ModelWithMcpCall([])
    agent = CodingAgent(AgentConfig(workspace=tmp_path), model)
    agent.mcp_discovery.discover = AsyncMock(side_effect=AssertionError("unexpected discovery"))
    chat = ChatSession(agent, "no-mcp", [ChatMessage(role="system", content="test")])
    await anext_and_drain(chat.send("test"))
    agent.mcp_discovery.discover.assert_not_awaited()


async def test_agent_refresh_failure_removes_old_schema_and_keeps_builtins(tmp_path: Path) -> None:
    factory = Factory()
    agent = CodingAgent(AgentConfig(workspace=tmp_path, mcp_servers=[config()]),
                        ModelWithMcpCall(factory.instances))
    agent.mcp_discovery = McpDiscoveryService([config()], factory=factory)
    runtime = agent._new_runtime()
    read = runtime.tools["read"]
    await agent._register_mcp_tools(runtime)
    await agent._register_mcp_tools(runtime)
    assert mcp_tool_name("server", "read") in runtime.tools
    factory.schemas = [McpToolSchema(name="invalid")]
    await agent._register_mcp_tools(runtime)
    assert all(not isinstance(tool, McpTool) for tool in runtime.tools.values())
    assert runtime.tools["read"] is read
    log = agent.application_log.path.read_text(encoding="utf-8")
    assert "mcp_discovery_incomplete" in log
    assert "invalid_tool_definitions" in log

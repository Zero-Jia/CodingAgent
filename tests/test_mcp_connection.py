"""MCP server 配置与连接管理器测试（B2-1）。

不依赖真实 MCP server 进程或网络：``McpConnectionManager`` 通过 ``factory``
注入 ``InMemoryMcpConnection`` 替身，覆盖生命周期、并发失败隔离、上下文管理
等行为。``McpServerConfig`` 校验与 config env 解析单独覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from coding_agent.config import AgentConfig, _env_mcp_servers
from coding_agent.mcp import (
    InMemoryMcpConnection,
    McpConnectionError,
    McpConnectionManager,
    McpServerConfig,
    McpToolSchema,
    create_mcp_connection,
)


def _stdio_config(name: str = "fs", **overrides: object) -> McpServerConfig:
    base: dict[str, object] = {
        "name": name,
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
    }
    base.update(overrides)
    return McpServerConfig.model_validate(base)


def _http_config(name: str = "remote", **overrides: object) -> McpServerConfig:
    base: dict[str, object] = {
        "name": name,
        "transport": "http",
        "url": "https://example.invalid/mcp",
    }
    base.update(overrides)
    return McpServerConfig.model_validate(base)


# --------------------------------------------------------------------------- #
# McpServerConfig 校验
# --------------------------------------------------------------------------- #


class TestMcpServerConfig:
    def test_valid_stdio_config(self) -> None:
        cfg = _stdio_config()
        assert cfg.transport == "stdio"
        assert cfg.command == "npx"
        assert cfg.enabled is True
        assert cfg.timeout_seconds == 30.0

    def test_valid_http_config(self) -> None:
        cfg = _http_config()
        assert cfg.transport == "http"
        assert cfg.url.startswith("https://")

    def test_stdio_without_command_rejected(self) -> None:
        with pytest.raises(ValidationError) as info:
            McpServerConfig.model_validate(
                {"name": "bad", "transport": "stdio", "command": ""}
            )
        assert "command" in str(info.value)

    def test_http_without_url_rejected(self) -> None:
        with pytest.raises(ValidationError) as info:
            McpServerConfig.model_validate(
                {"name": "bad", "transport": "http", "url": "  "}
            )
        assert "url" in str(info.value)

    def test_disabled_flag_respected(self) -> None:
        cfg = _stdio_config(enabled=False)
        assert cfg.enabled is False


# --------------------------------------------------------------------------- #
# InMemoryMcpConnection 生命周期
# --------------------------------------------------------------------------- #


class TestInMemoryMcpConnection:
    async def test_connect_and_disconnect_lifecycle(self) -> None:
        conn = InMemoryMcpConnection(_stdio_config())
        assert conn.is_alive is False
        await conn.connect()
        assert conn.is_alive is True
        assert conn.connect_calls == 1
        await conn.disconnect()
        assert conn.is_alive is False
        assert conn.disconnect_calls == 1

    async def test_ping_when_not_connected_raises(self) -> None:
        conn = InMemoryMcpConnection(_stdio_config())
        with pytest.raises(McpConnectionError, match="not connected"):
            await conn.ping()

    async def test_list_tools_when_not_connected_raises(self) -> None:
        conn = InMemoryMcpConnection(_stdio_config())
        with pytest.raises(McpConnectionError, match="not connected"):
            await conn.list_tools()

    async def test_list_tools_returns_preset_after_connect(self) -> None:
        tools = [McpToolSchema(name="read", description="read a file")]
        conn = InMemoryMcpConnection(_stdio_config(), tools=tools)
        await conn.connect()
        result = await conn.list_tools()
        assert len(result) == 1
        assert result[0].name == "read"
        assert result[0].description == "read a file"
        # 返回的是副本，修改不影响内部状态
        result.clear()
        assert len(await conn.list_tools()) == 1

    async def test_call_tool_logs_arguments_and_returns_result(self) -> None:
        conn = InMemoryMcpConnection(_stdio_config())
        await conn.connect()
        result = await conn.call_tool("read", {"path": "/tmp/a"})
        assert result.name == "read"
        assert result.is_error is False
        assert "read" in result.content
        assert conn.call_log == [("read", {"path": "/tmp/a"})]

    async def test_connect_fails_raises(self) -> None:
        conn = InMemoryMcpConnection(_stdio_config(), connect_fails=True)
        with pytest.raises(McpConnectionError, match="connect .* failed"):
            await conn.connect()
        assert conn.is_alive is False
        assert conn.connect_calls == 1

    async def test_ping_fails_raises_after_connect(self) -> None:
        conn = InMemoryMcpConnection(_stdio_config(), ping_fails=True)
        await conn.connect()
        with pytest.raises(McpConnectionError, match="ping .* failed"):
            await conn.ping()

    async def test_call_fails_returns_error_result(self) -> None:
        conn = InMemoryMcpConnection(_stdio_config(), call_fails=True)
        await conn.connect()
        result = await conn.call_tool("write", {"x": 1})
        assert result.is_error is True

    async def test_disconnect_is_safe_when_not_connected(self) -> None:
        conn = InMemoryMcpConnection(_stdio_config())
        await conn.disconnect()  # 不应抛错
        assert conn.disconnect_calls == 1


# --------------------------------------------------------------------------- #
# McpConnectionManager
# --------------------------------------------------------------------------- #


def _fake_factory(
    behaviors: dict[str, dict[str, object]] | None = None,
) -> dict[str, InMemoryMcpConnection]:
    """返回一个工厂与实例表；通过 behaviors 配置每个 name 的失败开关。"""
    behaviors = behaviors or {}
    instances: dict[str, InMemoryMcpConnection] = {}

    def factory(config: McpServerConfig) -> InMemoryMcpConnection:
        opts = behaviors.get(config.name, {})
        conn = InMemoryMcpConnection(
            config,
            tools=list(opts.get("tools", []))  # type: ignore[arg-type]
            if opts.get("tools")
            else None,
            connect_fails=bool(opts.get("connect_fails", False)),
            ping_fails=bool(opts.get("ping_fails", False)),
            call_fails=bool(opts.get("call_fails", False)),
        )
        instances[config.name] = conn
        return conn

    factory.instances = instances  # type: ignore[attr-defined]
    return factory  # type: ignore[return-value]


class TestMcpConnectionManager:
    def test_dedupe_by_name_last_wins(self) -> None:
        configs = [
            _stdio_config("dup", command="first"),
            _stdio_config("dup", command="second"),
        ]
        factory = _fake_factory()
        mgr = McpConnectionManager(configs, factory=factory)
        names = mgr.names()
        assert names == ["dup"]
        assert mgr.configs()[0].command == "second"

    def test_disabled_servers_skipped(self) -> None:
        configs = [
            _stdio_config("on"),
            _stdio_config("off", enabled=False),
        ]
        factory = _fake_factory()
        mgr = McpConnectionManager(configs, factory=factory)
        assert mgr.names() == ["on"]

    def test_status_reports_disconnected_initially(self) -> None:
        factory = _fake_factory()
        mgr = McpConnectionManager([_stdio_config("a"), _http_config("b")], factory=factory)
        statuses = mgr.status()
        assert [s.name for s in statuses] == ["a", "b"]
        assert all(not s.connected for s in statuses)
        assert all(s.error == "" for s in statuses)

    async def test_connect_all_success_connects_every_server(self) -> None:
        configs = [_stdio_config("a"), _http_config("b")]
        factory = _fake_factory()
        mgr = McpConnectionManager(configs, factory=factory)
        await mgr.connect_all()
        statuses = mgr.status()
        assert all(s.connected for s in statuses)
        assert factory.instances["a"].connect_calls == 1  # type: ignore[attr-defined]
        assert factory.instances["b"].connect_calls == 1  # type: ignore[attr-defined]

    async def test_connect_all_single_failure_does_not_block_siblings(self) -> None:
        configs = [_stdio_config("broken"), _stdio_config("ok")]
        factory = _fake_factory({"broken": {"connect_fails": True}})
        mgr = McpConnectionManager(configs, factory=factory)
        await mgr.connect_all()
        statuses = {s.name: s for s in mgr.status()}
        assert statuses["broken"].connected is False
        assert "failed" in statuses["broken"].error
        assert statuses["ok"].connected is True
        assert statuses["ok"].error == ""

    async def test_get_returns_connected_instance(self) -> None:
        factory = _fake_factory()
        mgr = McpConnectionManager([_stdio_config("a")], factory=factory)
        await mgr.connect_all()
        conn = await mgr.get("a")
        assert conn.name == "a"
        assert conn.is_alive is True

    async def test_get_unknown_server_raises(self) -> None:
        factory = _fake_factory()
        mgr = McpConnectionManager([_stdio_config("a")], factory=factory)
        with pytest.raises(McpConnectionError, match="not configured"):
            await mgr.get("nope")

    async def test_get_failed_server_raises_with_error_message(self) -> None:
        factory = _fake_factory({"a": {"connect_fails": True}})
        mgr = McpConnectionManager([_stdio_config("a")], factory=factory)
        await mgr.connect_all()
        with pytest.raises(McpConnectionError, match="not connected"):
            await mgr.get("a")

    async def test_get_unconnected_configured_server_raises(self) -> None:
        factory = _fake_factory()
        mgr = McpConnectionManager([_stdio_config("a")], factory=factory)
        # 未调用 connect_all，直接 get
        with pytest.raises(McpConnectionError, match="not connected yet"):
            await mgr.get("a")

    async def test_connect_single_returns_existing_on_repeat(self) -> None:
        factory = _fake_factory()
        mgr = McpConnectionManager([_stdio_config("a")], factory=factory)
        first = await mgr.connect("a")
        second = await mgr.connect("a")
        assert first is second
        assert factory.instances["a"].connect_calls == 1  # type: ignore[attr-defined]

    async def test_connect_unknown_raises(self) -> None:
        factory = _fake_factory()
        mgr = McpConnectionManager([_stdio_config("a")], factory=factory)
        with pytest.raises(McpConnectionError, match="not configured"):
            await mgr.connect("nope")

    async def test_disconnect_releases_single(self) -> None:
        factory = _fake_factory()
        mgr = McpConnectionManager([_stdio_config("a")], factory=factory)
        await mgr.connect_all()
        await mgr.disconnect("a")
        statuses = mgr.status()
        assert statuses[0].connected is False

    async def test_disconnect_unknown_is_noop(self) -> None:
        factory = _fake_factory()
        mgr = McpConnectionManager([_stdio_config("a")], factory=factory)
        await mgr.disconnect("nope")  # 不抛错

    async def test_disconnect_all_releases_every_connection(self) -> None:
        configs = [_stdio_config("a"), _http_config("b")]
        factory = _fake_factory()
        mgr = McpConnectionManager(configs, factory=factory)
        await mgr.connect_all()
        await mgr.disconnect_all()
        assert all(not s.connected for s in mgr.status())
        assert factory.instances["a"].disconnect_calls == 1  # type: ignore[attr-defined]
        assert factory.instances["b"].disconnect_calls == 1  # type: ignore[attr-defined]

    async def test_async_with_connects_then_disconnects(self) -> None:
        configs = [_stdio_config("a"), _http_config("b")]
        factory = _fake_factory()
        mgr = McpConnectionManager(configs, factory=factory)
        async with mgr as ctx:
            assert ctx is mgr
            assert all(s.connected for s in mgr.status())
        assert all(not s.connected for s in mgr.status())
        assert factory.instances["a"].disconnect_calls == 1  # type: ignore[attr-defined]

    async def test_async_with_does_not_raise_on_partial_failure(self) -> None:
        # connect_all 内部吞掉单个失败；async with 仍应正常进入与退出
        configs = [_stdio_config("broken"), _stdio_config("ok")]
        factory = _fake_factory({"broken": {"connect_fails": True}})
        mgr = McpConnectionManager(configs, factory=factory)
        async with mgr:
            statuses = {s.name: s for s in mgr.status()}
            assert statuses["ok"].connected is True
            assert statuses["broken"].connected is False
        # 退出后已连接的 ok 被释放
        assert factory.instances["ok"].disconnect_calls == 1  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# create_mcp_connection 工厂
# --------------------------------------------------------------------------- #


class TestCreateMcpConnection:
    def test_stdio_returns_stdio_connection(self) -> None:
        from coding_agent.mcp import StdioMcpConnection

        conn = create_mcp_connection(_stdio_config())
        assert isinstance(conn, StdioMcpConnection)
        assert conn.transport == "stdio"
        assert not conn.is_alive

    def test_http_returns_http_connection(self) -> None:
        from coding_agent.mcp import HttpMcpConnection

        conn = create_mcp_connection(_http_config())
        assert isinstance(conn, HttpMcpConnection)
        assert conn.transport == "http"

    def test_disconnect_before_connect_is_safe(self) -> None:
        # 真实连接未 connect 即 disconnect 不应抛错
        conn = create_mcp_connection(_stdio_config())
        import asyncio

        asyncio.run(conn.disconnect())
        assert conn.is_alive is False


# --------------------------------------------------------------------------- #
# config env 解析
# --------------------------------------------------------------------------- #


class TestConfigEnvParsing:
    def test_env_mcp_servers_absent_returns_empty(self) -> None:
        assert _env_mcp_servers({}, "CODING_AGENT_MCP_SERVERS") == []
        assert _env_mcp_servers({"CODING_AGENT_MCP_SERVERS": ""}, "CODING_AGENT_MCP_SERVERS") == []

    def test_env_mcp_servers_valid_json(self) -> None:
        raw = json.dumps(
            [
                {
                    "name": "fs",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@x/fs", "."],
                },
                {
                    "name": "remote",
                    "transport": "http",
                    "url": "https://example.invalid/mcp",
                },
            ]
        )
        servers = _env_mcp_servers({"CODING_AGENT_MCP_SERVERS": raw}, "CODING_AGENT_MCP_SERVERS")
        assert len(servers) == 2
        assert servers[0].name == "fs"
        assert servers[0].transport == "stdio"
        assert servers[1].transport == "http"

    def test_env_mcp_servers_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="JSON"):
            _env_mcp_servers({"CODING_AGENT_MCP_SERVERS": "not json"}, "CODING_AGENT_MCP_SERVERS")

    def test_env_mcp_servers_non_array_raises(self) -> None:
        with pytest.raises(ValueError, match="array"):
            _env_mcp_servers(
                {"CODING_AGENT_MCP_SERVERS": json.dumps({"name": "x"})},
                "CODING_AGENT_MCP_SERVERS",
            )

    def test_env_mcp_servers_invalid_item_schema_raises(self) -> None:
        raw = json.dumps([{"name": "bad", "transport": "stdio", "command": ""}])
        with pytest.raises(ValidationError):
            _env_mcp_servers({"CODING_AGENT_MCP_SERVERS": raw}, "CODING_AGENT_MCP_SERVERS")

    def test_from_environment_loads_mcp_servers_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw = json.dumps(
            [{"name": "fs", "transport": "stdio", "command": "npx", "args": ["-y", "@x/fs"]}]
        )
        monkeypatch.setenv("CODING_AGENT_MCP_SERVERS", raw)
        config = AgentConfig.from_environment(tmp_path)
        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0].name == "fs"
        assert config.mcp_servers[0].command == "npx"

    def test_from_environment_default_mcp_servers_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CODING_AGENT_MCP_SERVERS", raising=False)
        config = AgentConfig.from_environment(tmp_path)
        assert config.mcp_servers == []

    def test_from_environment_overrides_take_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "CODING_AGENT_MCP_SERVERS",
            json.dumps([{"name": "env", "transport": "stdio", "command": "a"}]),
        )
        override = _stdio_config("code")
        config = AgentConfig.from_environment(tmp_path, mcp_servers=[override])
        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0].name == "code"

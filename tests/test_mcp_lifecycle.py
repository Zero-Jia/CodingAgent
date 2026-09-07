"""SDK-shaped transports exercise task ownership without external servers."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import pytest
from typer.testing import CliRunner

from coding_agent.cli.app import app
from coding_agent.mcp import (
    InMemoryMcpConnection,
    McpConnectionError,
    McpConnectionManager,
    McpServerConfig,
    create_mcp_connection,
)


@pytest.fixture
def sdk(monkeypatch):
    import coding_agent.mcp.connection as module

    state = SimpleNamespace(opened=0, closed=0, params=None, session=None)

    @asynccontextmanager
    async def transport(*args, **kwargs):
        state.params = (args, kwargs)
        owner = asyncio.current_task()
        async with anyio.create_task_group():
            state.opened += 1
            try:
                yield (None, None, None)
            finally:
                assert asyncio.current_task() is owner
                state.closed += 1

    @asynccontextmanager
    async def session(*args, **kwargs):
        state.session = SimpleNamespace(
            initialize=AsyncMock(), send_ping=AsyncMock(),
            list_tools=AsyncMock(return_value=SimpleNamespace(tools=[])),
            call_tool=AsyncMock(),
        )
        async with anyio.create_task_group():
            yield state.session

    monkeypatch.setattr(module, 'stdio_client', transport)
    monkeypatch.setattr(module, 'streamablehttp_client', transport)
    monkeypatch.setattr(module, 'ClientSession', session)
    return state


@pytest.mark.parametrize('transport', ['stdio', 'http'])
async def test_sdk_lifecycle_across_tasks(sdk, transport):
    config = McpServerConfig(name='a', transport=transport, command='python',
                             url='https://example.invalid/mcp', timeout_seconds=1)
    conn = create_mcp_connection(config)
    await asyncio.gather(conn.connect(), conn.connect())
    assert sdk.opened == 1
    await conn.ping()
    assert await conn.list_tools() == []
    await asyncio.create_task(conn.disconnect())
    assert sdk.closed == 1
    assert not conn.is_alive
    await conn.connect()
    await conn.disconnect()
    assert sdk.closed == 2


async def test_initialize_timeout_cleans_transport(sdk, monkeypatch):
    import coding_agent.mcp.connection as module

    @asynccontextmanager
    async def stuck_session(*args, **kwargs):
        async def initialize():
            await asyncio.Event().wait()
        yield SimpleNamespace(initialize=initialize)

    monkeypatch.setattr(module, 'ClientSession', stuck_session)
    conn = create_mcp_connection(McpServerConfig(
        name='a', command='python', timeout_seconds=0.03))
    with pytest.raises(McpConnectionError, match='connect'):
        await conn.connect()
    assert sdk.closed == 1
    assert not conn.is_alive


async def test_cancel_connect_cleans_transport(sdk, monkeypatch):
    import coding_agent.mcp.connection as module
    entered = asyncio.Event()

    @asynccontextmanager
    async def stuck_session(*args, **kwargs):
        async def initialize():
            entered.set()
            await asyncio.Event().wait()
        yield SimpleNamespace(initialize=initialize)

    monkeypatch.setattr(module, 'ClientSession', stuck_session)
    conn = create_mcp_connection(McpServerConfig(name='a', command='python'))
    task = asyncio.create_task(conn.connect())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sdk.closed == 1
    assert not conn.is_alive


async def test_ping_timeout_still_allows_cleanup(sdk):
    conn = create_mcp_connection(McpServerConfig(
        name='a', command='python', timeout_seconds=0.03))
    await conn.connect()
    sdk.session.send_ping.side_effect = asyncio.Event().wait
    with pytest.raises(McpConnectionError, match='ping'):
        await conn.ping()
    await conn.disconnect()
    assert sdk.closed == 1


async def test_manager_concurrent_connect_restart_and_factory_failure():
    instances = []

    def factory(config):
        if config.name == 'bad':
            raise ValueError('factory failed')
        conn = InMemoryMcpConnection(config)
        instances.append(conn)
        return conn

    manager = McpConnectionManager([
        McpServerConfig(name='bad', command='python'),
        McpServerConfig(name='ok', command='python')], factory=factory)
    await manager.connect_all()
    assert manager.status()[0].error
    first, second = await asyncio.gather(manager.connect('ok'), manager.connect('ok'))
    assert first is second
    restarted = await manager.restart('ok')
    assert restarted is not first
    assert not first.is_alive
    await manager.disconnect_all()
    assert all(not conn.is_alive for conn in instances)


async def test_shutdown_failure_does_not_block_siblings():
    closed = []

    class Connection(InMemoryMcpConnection):
        async def disconnect(self):
            await super().disconnect()
            closed.append(self.name)
            if self.name == 'b':
                raise ValueError('close failed')

    manager = McpConnectionManager([
        McpServerConfig(name=n, command='python') for n in ['a', 'b']
    ], factory=Connection)
    await manager.connect_all()
    await manager.disconnect_all()
    assert closed == ['b', 'a']
    assert manager.status()[1].error


@pytest.mark.parametrize('value', [0, -1, float('inf'), float('nan')])
def test_invalid_timeout(value):
    with pytest.raises(ValueError):
        McpServerConfig(name='a', command='python', timeout_seconds=value)


def test_cli_list_and_unknown(monkeypatch, tmp_path):
    monkeypatch.setenv('CODING_AGENT_MCP_SERVERS', '[]')
    runner = CliRunner()
    assert runner.invoke(app, ['mcp', 'list', '--workspace', str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ['mcp', 'ping', 'missing',
                               '--workspace', str(tmp_path)]).exit_code == 1


@pytest.mark.parametrize('fails', [False, True])
def test_cli_ping_cleans_connection(monkeypatch, tmp_path, fails):
    import importlib
    module = importlib.import_module('coding_agent.cli.app')
    monkeypatch.setenv('CODING_AGENT_MCP_SERVERS',
                       '[{"name":"a","command":"python"}]')
    conn = InMemoryMcpConnection(McpServerConfig(name='a', command='python'),
                                  ping_fails=fails)
    monkeypatch.setattr(module, 'create_mcp_connection', lambda config: conn)
    result = CliRunner().invoke(app, ['mcp', 'ping', 'a', '--workspace', str(tmp_path)])
    assert result.exit_code == int(fails), result.output
    assert conn.disconnect_calls == 1
    assert not conn.is_alive

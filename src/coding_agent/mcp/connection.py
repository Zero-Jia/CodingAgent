"""基于官方 mcp SDK 的连接实现与连接管理器（B2-1）。

- ``StdioMcpConnection``：spawn 本地子进程，JSON-RPC over stdin/stdout
- ``HttpMcpConnection``：Streamable HTTP（mcp SDK 1.9.4）
- ``McpConnectionManager``：按配置批量管理连接生命周期，单个失败不阻断兄弟
- ``create_mcp_connection``：按 ``McpServerConfig.transport`` 选择具体实现

底层 context manager 借助 ``AsyncExitStack`` 保活，使一次 ``connect`` 之后可
多次调用 ``list_tools``/``call_tool``/``ping``，``disconnect`` 统一释放。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any, Self

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, ListToolsResult, Tool

from coding_agent.mcp.contracts import (
    McpConnection,
    McpConnectionError,
    McpConnectionStatus,
    McpServerConfig,
    McpToolResult,
    McpToolSchema,
)

McpConnectionFactory = Callable[[McpServerConfig], McpConnection]


def _tool_to_schema(tool: Tool) -> McpToolSchema:
    schema = tool.inputSchema
    if isinstance(schema, dict):
        return McpToolSchema(
            name=tool.name,
            description=tool.description or "",
            input_schema={str(k): v for k, v in schema.items()},
        )
    return McpToolSchema(name=tool.name, description=tool.description or "")


def _result_to_model(name: str, result: CallToolResult) -> McpToolResult:
    texts: list[str] = []
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            texts.append(text)
    return McpToolResult(
        name=name,
        content="\n".join(texts),
        is_error=result.isError,
    )


class _BaseMcpConnection:
    """共用状态与生命周期，子类通过 ``_open_streams`` 提供底层 transport。"""

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._owner: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._session: ClientSession | None = None

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def transport(self) -> str:
        return self._config.transport

    @property
    def is_alive(self) -> bool:
        return self._session is not None and self._owner is not None and not self._owner.done()

    async def connect(self) -> None:
        async with self._lock:
            if self.is_alive:
                return
            ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._stop = asyncio.Event()
            self._owner = asyncio.create_task(self._run_session(ready))
            try:
                await asyncio.wait_for(asyncio.shield(ready), self._config.timeout_seconds)
            except BaseException as error:
                self._owner.cancel()
                await asyncio.gather(self._owner, return_exceptions=True)
                # Retrieve a late handshake failure to avoid orphaned Future warnings.
                if ready.done() and not ready.cancelled():
                    ready.exception()
                else:
                    ready.cancel()
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise McpConnectionError(
                    f"connect MCP server {self.name} failed: {error}"
                ) from error

    async def _run_session(self, ready: asyncio.Future[None]) -> None:
        # SDK task groups must enter and exit in the same task, in LIFO order.
        try:
            async with AsyncExitStack() as stack:
                read, write = await self._open_streams(stack)
                session = await stack.enter_async_context(ClientSession(
                    read, write,
                    read_timeout_seconds=timedelta(seconds=self._config.timeout_seconds),
                ))
                await session.initialize()
                self._session = session
                ready.set_result(None)
                await self._stop.wait()
        except Exception as error:
            if not ready.done():
                ready.set_exception(error)
            else:
                raise McpConnectionError(
                    f"MCP server {self.name} session failed: {error}"
                ) from error
        finally:
            self._session = None

    async def _open_streams(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        raise NotImplementedError

    async def disconnect(self) -> None:
        async with self._lock:
            owner = self._owner
            if owner is None:
                return
            self._stop.set()
            try:
                await asyncio.wait_for(asyncio.shield(owner), self._config.timeout_seconds)
            except BaseException as error:
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise McpConnectionError(
                    f"disconnect MCP server {self.name} failed: {error}"
                ) from error
            finally:
                self._owner = None
                self._session = None

    async def ping(self) -> None:
        session = self._require_session()
        try:
            await asyncio.wait_for(session.send_ping(), self._config.timeout_seconds)
        except Exception as error:
            raise McpConnectionError(
                f"ping MCP server {self._config.name} failed: {error}"
            ) from error

    async def list_tools(self) -> list[McpToolSchema]:
        session = self._require_session()
        try:
            result: ListToolsResult = await asyncio.wait_for(
                session.list_tools(), self._config.timeout_seconds
            )
        except Exception as error:
            raise McpConnectionError(
                f"list_tools on MCP server {self._config.name} failed: {error}"
            ) from error
        return [_tool_to_schema(tool) for tool in result.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        session = self._require_session()
        try:
            result: CallToolResult = await asyncio.wait_for(
                session.call_tool(name, arguments), self._config.timeout_seconds
            )
        except Exception as error:
            raise McpConnectionError(
                f"call_tool {name} on MCP server {self._config.name} failed: {error}"
            ) from error
        return _result_to_model(name, result)

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise McpConnectionError(
                f"MCP server {self._config.name} is not connected"
            )
        return self._session


class StdioMcpConnection(_BaseMcpConnection):
    """stdio transport：spawn 子进程，JSON-RPC over stdin/stdout。"""

    async def _open_streams(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        params = StdioServerParameters(
            command=self._config.command,
            args=list(self._config.args),
            env=dict(self._config.env) if self._config.env else None,
            cwd=self._config.cwd or None,
        )
        triple = await stack.enter_async_context(stdio_client(params))
        return triple[0], triple[1]


class HttpMcpConnection(_BaseMcpConnection):
    """Streamable HTTP transport（mcp SDK 1.9.4）。"""

    async def _open_streams(self, stack: AsyncExitStack) -> tuple[Any, Any]:
        headers = dict(self._config.headers) if self._config.headers else None
        triple = await stack.enter_async_context(
            streamablehttp_client(
                url=self._config.url,
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        )
        return triple[0], triple[1]


def create_mcp_connection(config: McpServerConfig) -> McpConnection:
    """按 ``config.transport`` 返回具体连接实现。

    返回 ``McpConnection``（Protocol）而非具体类，便于替换为测试替身。
    """
    if config.transport == "stdio":
        return StdioMcpConnection(config)
    if config.transport == "http":
        return HttpMcpConnection(config)
    raise McpConnectionError(f"unsupported MCP transport: {config.transport!r}")


class McpConnectionManager:
    """多个 MCP server 连接的生命周期管理器。

    - 按配置列表批量管理；同 ``name`` 后者覆盖前者，``enabled=False`` 跳过
    - ``connect_all`` 逐个连接，单个失败记录错误而不阻断兄弟连接
    - ``get`` 取已连接实例；未连接且曾失败则抛 ``McpConnectionError``
    - 支持 ``async with``，退出时逆序释放全部资源
    """

    def __init__(
        self,
        configs: list[McpServerConfig],
        *,
        factory: McpConnectionFactory | None = None,
    ) -> None:
        by_name: dict[str, McpServerConfig] = {}
        for config in configs:
            by_name[config.name] = config
        self._configs: list[McpServerConfig] = [
            cfg for cfg in by_name.values() if cfg.enabled
        ]
        self._factory: McpConnectionFactory = factory or create_mcp_connection
        self._connections: dict[str, McpConnection] = {}
        self._errors: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def names(self) -> list[str]:
        return [cfg.name for cfg in self._configs]

    def configs(self) -> list[McpServerConfig]:
        return list(self._configs)

    def status(self) -> list[McpConnectionStatus]:
        result: list[McpConnectionStatus] = []
        for cfg in self._configs:
            connected = cfg.name in self._connections and self._connections[cfg.name].is_alive
            result.append(
                McpConnectionStatus(
                    name=cfg.name,
                    transport=cfg.transport,
                    enabled=cfg.enabled,
                    connected=connected,
                    error=self._errors.get(cfg.name, ""),
                )
            )
        return result

    async def connect_all(self) -> list[McpConnectionStatus]:
        """逐个连接所有已启用配置；单个失败不阻断兄弟，结果记录到 ``status``。"""
        try:
            for cfg in self._configs:
                try:
                    await self.connect(cfg.name)
                except McpConnectionError:
                    pass
        except BaseException:
            await self.disconnect_all()
            raise
        return self.status()

    async def connect(self, name: str) -> McpConnection:
        async with self._lock:
            return await self._connect(name)

    async def _connect(self, name: str) -> McpConnection:
        cfg = self._require_config(name)
        if name in self._connections:
            if self._connections[name].is_alive:
                return self._connections[name]
            await self._disconnect(name)
        conn: McpConnection | None = None
        try:
            conn = self._factory(cfg)
            await conn.connect()
        except BaseException as error:
            if conn is not None:
                try:
                    await conn.disconnect()
                except Exception:
                    pass
            if isinstance(error, asyncio.CancelledError):
                raise
            self._errors[name] = str(error)
            raise McpConnectionError(f"connect MCP server {name} failed: {error}") from error
        self._connections[name] = conn
        self._errors.pop(name, None)
        return conn

    async def restart(self, name: str) -> McpConnection:
        async with self._lock:
            self._require_config(name)
            await self._disconnect(name)
            return await self._connect(name)

    async def get(self, name: str) -> McpConnection:
        if name in self._connections and self._connections[name].is_alive:
            return self._connections[name]
        if name in self._errors:
            raise McpConnectionError(
                f"MCP server {name} not connected: {self._errors[name]}"
            )
        if name in {cfg.name for cfg in self._configs}:
            raise McpConnectionError(f"MCP server {name} is not connected yet")
        raise McpConnectionError(f"MCP server {name} is not configured")

    async def disconnect(self, name: str) -> None:
        async with self._lock:
            await self._disconnect(name)

    async def _disconnect(self, name: str) -> None:
        conn = self._connections.pop(name, None)
        if conn is None:
            return
        try:
            await conn.disconnect()
        except Exception as error:
            self._errors[name] = str(error)
            raise McpConnectionError(f"disconnect MCP server {name} failed: {error}") from error

    async def disconnect_all(self) -> None:
        """逆序释放全部已连接资源；单个释放失败不阻断其余释放。"""
        async with self._lock:
            for name in reversed(list(self._connections)):
                try:
                    await self._disconnect(name)
                except McpConnectionError:
                    pass

    async def __aenter__(self) -> Self:
        await self.connect_all()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.disconnect_all()

    def _require_config(self, name: str) -> McpServerConfig:
        for cfg in self._configs:
            if cfg.name == name:
                return cfg
        raise McpConnectionError(f"MCP server {name} is not configured")


__all__ = [
    "HttpMcpConnection",
    "McpConnectionManager",
    "StdioMcpConnection",
    "create_mcp_connection",
]

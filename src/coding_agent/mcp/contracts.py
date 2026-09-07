"""MCP server 配置与连接契约（B2-1）。

本模块只定义配置模型与连接 Protocol，不依赖具体 mcp SDK。stdio / HTTP
具体实现位于 ``connection.py``（基于官方 mcp SDK）；测试可用
``InMemoryMcpConnection`` 替身，不触达真实 server 进程。

B2-1 边界：仅配置 + 连接生命周期 + 诊断（list/ping）。把发现的工具注册到
agent tool registry 属于 B2-2；对调用做 policy/trace 包装属于 B2-3。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, Field, model_validator

McpTransport = Literal["stdio", "http"]


class McpServerConfig(BaseModel):
    """单个 MCP server 的连接配置。

    ``transport == "stdio"`` 时使用 ``command``/``args``/``env``/``cwd`` 启动
    子进程，通过 stdin/stdout 走 JSON-RPC；``transport == "http"`` 时使用
    ``url``/``headers`` 走 Streamable HTTP。
    """

    name: str
    transport: McpTransport = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, gt=0.0, allow_inf_nan=False)
    enabled: bool = True
    # Operator-reviewed HTTP read-only capabilities; never inferred from annotations.
    allowed_readonly_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_transport_fields(self) -> Self:
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if self.transport == "stdio":
            if not self.command.strip():
                raise ValueError("stdio transport requires a non-empty 'command'")
        else:  # http
            if not self.url.strip():
                raise ValueError("http transport requires a non-empty 'url'")
        return self


class McpToolSchema(BaseModel):
    """MCP 工具的 schema 摘要。

    用于诊断输出（``mcp ping``）与 B2-2 的 agent tool registry 注册。
    """

    name: str
    description: str = ""
    input_schema: dict[str, object] = Field(default_factory=dict)


class McpToolResult(BaseModel):
    """MCP 工具调用结果的归一表示。

    ``content`` 为各文本块拼接结果；非文本块（图片等）不展开，只记录省略数量。
    """

    name: str
    content: str = ""
    is_error: bool = False
    omitted_content_blocks: int = 0


class McpConnectionStatus(BaseModel):
    """单个 MCP server 在连接管理器中的当前状态快照。"""

    name: str
    transport: str
    enabled: bool
    connected: bool
    error: str = ""


class McpConnectionError(RuntimeError):
    """MCP 连接、握手或调用失败。"""


class McpConnection(Protocol):
    """MCP server 连接协议。

    实现方需要保证：
    - ``connect`` 建立底层连接并完成 MCP initialize 握手；已连接时再次调用幂等
    - ``disconnect`` 释放底层资源；未连接时调用安全无副作用
    - ``is_alive`` 仅反映当前状态，不发起 IO
    - ``ping``/``list_tools``/``call_tool`` 在未连接时抛 ``McpConnectionError``
    - 任何底层异常都包装为 ``McpConnectionError``，不泄漏 SDK 异常类型
    """

    @property
    def name(self) -> str: ...

    @property
    def transport(self) -> str: ...

    @property
    def is_alive(self) -> bool: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def ping(self) -> None: ...

    async def list_tools(self) -> list[McpToolSchema]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult: ...


class InMemoryMcpConnection:
    """不触达真实 MCP server 的内存替身，用于测试与离线诊断。

    预置 ``tools`` 与 ``ping_fails``/``call_fails`` 控制行为；所有状态变更
    只发生在内存中。
    """

    def __init__(
        self,
        config: McpServerConfig,
        *,
        tools: list[McpToolSchema] | None = None,
        connect_fails: bool = False,
        ping_fails: bool = False,
        call_fails: bool = False,
    ) -> None:
        self._config = config
        self._alive = False
        self._tools = list(tools) if tools else []
        self._connect_fails = connect_fails
        self._ping_fails = ping_fails
        self._call_fails = call_fails
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.ping_calls = 0
        self.call_log: list[tuple[str, dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def transport(self) -> str:
        return self._config.transport

    @property
    def is_alive(self) -> bool:
        return self._alive

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_fails:
            raise McpConnectionError(
                f"connect MCP server {self._config.name} failed (injected)"
            )
        self._alive = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._alive = False

    async def ping(self) -> None:
        self.ping_calls += 1
        if self._ping_fails:
            raise McpConnectionError(f"ping MCP server {self.name} failed (injected)")
        if not self._alive:
            raise McpConnectionError(f"MCP server {self.name} is not connected")

    async def list_tools(self) -> list[McpToolSchema]:
        if not self._alive:
            raise McpConnectionError(f"MCP server {self.name} is not connected")
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        self.call_log.append((name, dict(arguments)))
        if not self._alive:
            raise McpConnectionError(f"MCP server {self.name} is not connected")
        if self._call_fails:
            return McpToolResult(name=name, content="", is_error=True)
        return McpToolResult(name=name, content=f"called {name} on {self._config.name}")


__all__ = [
    "InMemoryMcpConnection",
    "McpConnection",
    "McpConnectionError",
    "McpConnectionStatus",
    "McpServerConfig",
    "McpToolResult",
    "McpToolSchema",
    "McpTransport",
]

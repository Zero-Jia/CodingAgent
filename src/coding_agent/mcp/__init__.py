"""MCP（Model Context Protocol）集成（B2-1）。

B2-1 只提供 server 配置与 stdio / HTTP 连接生命周期管理。工具发现注册
（B2-2）、policy/trace 包装（B2-3）、调用审计脱敏（B2-4）由后续任务实现。
"""

from coding_agent.mcp.connection import (
    HttpMcpConnection,
    McpConnectionFactory,
    McpConnectionManager,
    StdioMcpConnection,
    create_mcp_connection,
)
from coding_agent.mcp.contracts import (
    InMemoryMcpConnection,
    McpConnection,
    McpConnectionError,
    McpConnectionStatus,
    McpServerConfig,
    McpToolResult,
    McpToolSchema,
    McpTransport,
)

__all__ = [
    "HttpMcpConnection",
    "InMemoryMcpConnection",
    "McpConnection",
    "McpConnectionError",
    "McpConnectionFactory",
    "McpConnectionManager",
    "McpConnectionStatus",
    "McpServerConfig",
    "McpToolResult",
    "McpToolSchema",
    "McpTransport",
    "StdioMcpConnection",
    "create_mcp_connection",
]

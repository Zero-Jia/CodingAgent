"""MCP（Model Context Protocol）配置、连接及工具发现（B2-1/B2-2）。

工具发现仅注册 schema；policy/trace 执行包装（B2-3）、调用审计脱敏
（B2-4）由后续任务实现。
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
from coding_agent.mcp.discovery import (
    DiscoveredMcpTool,
    McpDiscoveryResult,
    McpDiscoveryService,
    mcp_tool_name,
)

__all__ = [
    "DiscoveredMcpTool",
    "HttpMcpConnection",
    "InMemoryMcpConnection",
    "McpConnection",
    "McpConnectionError",
    "McpConnectionFactory",
    "McpConnectionManager",
    "McpConnectionStatus",
    "McpDiscoveryResult",
    "McpDiscoveryService",
    "McpServerConfig",
    "McpToolResult",
    "McpToolSchema",
    "McpTransport",
    "StdioMcpConnection",
    "create_mcp_connection",
    "mcp_tool_name",
]

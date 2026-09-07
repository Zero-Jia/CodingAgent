"""Per-call connections for explicitly trusted HTTP read-only MCP tools."""

from __future__ import annotations

import asyncio
import json
import re

from coding_agent.mcp.connection import McpConnectionFactory, create_mcp_connection
from coding_agent.mcp.contracts import McpServerConfig, McpToolResult
from coding_agent.mcp.discovery import DiscoveredMcpTool

_SECRET = re.compile(
    r'''(?ix)(["']?(?:[\w-]{0,64}(?:token|password|secret)|api[_-]?key|authorization|cookie)["']?'''
    r'''\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n,;}]+)'''
)
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]+")
_PEM = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)


class McpExecutionService:
    def __init__(
        self, configs: list[McpServerConfig], *, factory: McpConnectionFactory | None = None
    ) -> None:
        self._configs = {cfg.name: cfg.model_copy(deep=True) for cfg in configs}
        self._factory = factory or create_mcp_connection

    def authorized(self, tool: DiscoveredMcpTool) -> bool:
        cfg = self._configs.get(tool.server_name)
        return bool(cfg and cfg.enabled and cfg.transport == "http"
                    and tool.tool_name in cfg.allowed_readonly_tools)

    def sanitize(self, content: str) -> str:
        # Remove configured credentials before truncation can split them.
        secrets = [value for cfg in self._configs.values()
                   for value in (*cfg.headers.values(), *cfg.env.values()) if value]
        for secret in sorted(secrets, key=len, reverse=True):
            content = content.replace(secret, "[REDACTED]")
            if secret.lower().startswith("bearer "):
                content = content.replace(secret[7:], "[REDACTED]")
        return _KEY.sub("[REDACTED]", _BEARER.sub("[REDACTED]", _SECRET.sub(
            r"\1[REDACTED]", _PEM.sub("[REDACTED]", content)
        )))

    async def call(
        self, tool: DiscoveredMcpTool, arguments: dict[str, object]
    ) -> McpToolResult:
        if not self.authorized(tool):
            raise PermissionError("MCP execution not authorized")
        config = self._configs[tool.server_name]
        connection = self._factory(config.model_copy(deep=True))
        try:
            async with asyncio.timeout(config.timeout_seconds):
                await connection.connect()
                # A reconnect must not silently execute a changed or removed tool.
                matching = [item for item in await connection.list_tools()
                            if item.name == tool.tool_name]
                if len(matching) != 1 or json.dumps(
                    matching[0].input_schema, sort_keys=True, allow_nan=False
                ) != json.dumps(tool.definition.parameters, sort_keys=True, allow_nan=False):
                    raise ValueError("MCP schema changed; rediscovery required")
                return await connection.call_tool(tool.tool_name, arguments)
        finally:
            async with asyncio.timeout(config.timeout_seconds):
                await connection.disconnect()

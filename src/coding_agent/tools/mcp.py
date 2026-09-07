"""Schema-only MCP tools. Execution stays disabled until B2-3."""

from __future__ import annotations

from collections.abc import AsyncIterator

from coding_agent.mcp.discovery import DiscoveredMcpTool, McpDiscoveryResult
from coding_agent.tools.contracts import Cancellation, Tool, ToolContext, ToolResult, ToolUpdate


class McpTool:
    def __init__(self, discovered: DiscoveredMcpTool) -> None:
        self.server_name = discovered.server_name
        self.tool_name = discovered.tool_name
        self.definition = discovered.definition.model_copy(deep=True)

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        yield ToolResult(
            status="policy_denied",
            summary="MCP execution is disabled until policy and trace integration is implemented",
        )


def register_mcp_tools(registry: dict[str, Tool], discovery: McpDiscoveryResult) -> None:
    """Replace the previous MCP snapshot atomically; never replace other tools."""
    updated = {name: tool for name, tool in registry.items() if not isinstance(tool, McpTool)}
    for discovered in discovery.tools:
        name = discovered.definition.name
        if name in updated:
            raise ValueError(f"tool name already registered: {name}")
        updated[name] = McpTool(discovered)
    registry.clear()
    registry.update(updated)

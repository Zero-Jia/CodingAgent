"""MCP tools with explicit capability authorization and bounded sanitized output."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from coding_agent.mcp.discovery import DiscoveredMcpTool, McpDiscoveryResult
from coding_agent.mcp.execution import McpExecutionService
from coding_agent.mcp.validation import validate_arguments
from coding_agent.tools.contracts import Cancellation, Tool, ToolContext, ToolResult, ToolUpdate


class McpTool:
    def __init__(
        self, discovered: DiscoveredMcpTool, execution: McpExecutionService | None = None
    ) -> None:
        self.server_name = discovered.server_name
        self.tool_name = discovered.tool_name
        self.definition = discovered.definition.model_copy(deep=True)
        self._discovered = DiscoveredMcpTool(
            self.server_name, self.tool_name, self.definition.model_copy(deep=True)
        )
        self._execution = execution

    @property
    def authorized(self) -> bool:
        return self._execution is not None and self._execution.authorized(self._discovered)

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        if not self.authorized or self._execution is None:
            yield ToolResult(status="policy_denied", summary="MCP execution not authorized")
            return
        try:
            validate_arguments(self._discovered.definition.parameters, params)
        except (ValueError, TypeError, RecursionError, OverflowError):
            yield ToolResult(status="validation_failed", summary="invalid or unsupported MCP input")
            return
        if cancellation.is_set():
            yield ToolResult(status="cancelled", summary="MCP call cancelled")
            return
        operation = asyncio.create_task(self._execution.call(self._discovered, params))
        cancelled = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                (operation, cancelled), return_when=asyncio.FIRST_COMPLETED
            )
            if cancelled in done:
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                result = ToolResult(status="cancelled", summary="MCP call cancelled")
            else:
                remote = await operation
                safe = self._execution.sanitize(remote.content)
                budget = max(0, context.max_output_chars)
                result = ToolResult(
                    status="execution_error" if remote.is_error else "success",
                    summary=("MCP tool reported an error" if remote.is_error
                             else "MCP call completed"),
                    output=safe[:budget],
                    details={"truncated": len(safe) > budget,
                             "output_chars": min(len(safe), budget),
                             "omitted_content_blocks": remote.omitted_content_blocks},
                )
        except TimeoutError:
            result = ToolResult(status="timeout", summary="MCP call timed out")
        except ValueError:
            result = ToolResult(status="validation_failed", summary="MCP schema changed")
        except Exception:
            # Transport exceptions may contain headers, URLs, or server-controlled text.
            result = ToolResult(status="execution_error", summary="MCP call failed")
        finally:
            operation.cancel()
            cancelled.cancel()
            await asyncio.gather(operation, cancelled, return_exceptions=True)
        yield result


def register_mcp_tools(
    registry: dict[str, Tool], discovery: McpDiscoveryResult,
    execution: McpExecutionService | None = None,
) -> None:
    """Replace the previous MCP snapshot atomically; never replace other tools."""
    updated = {name: tool for name, tool in registry.items() if not isinstance(tool, McpTool)}
    for discovered in discovery.tools:
        name = discovered.definition.name
        if name in updated:
            raise ValueError(f"tool name already registered: {name}")
        updated[name] = McpTool(discovered, execution)
    registry.clear()
    registry.update(updated)

"""Discover a fresh MCP schema snapshot without enabling remote execution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256

from coding_agent.ai.contracts import ToolDefinition
from coding_agent.mcp.connection import McpConnectionFactory, McpConnectionManager
from coding_agent.mcp.contracts import McpServerConfig, McpToolSchema


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Stable ASCII identifier, <=64 chars; hash the original pair, not its slugs."""
    identity = json.dumps([server_name, tool_name], ensure_ascii=False).encode("utf-8")
    digest = sha256(identity).hexdigest()[:24]
    server = re.sub(r"[^a-zA-Z0-9_-]", "_", server_name)[:12]
    tool = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_name)[:16]
    return f"mcp__{server}__{tool}__{digest}"


@dataclass(frozen=True)
class DiscoveredMcpTool:
    server_name: str
    tool_name: str
    definition: ToolDefinition


@dataclass
class McpDiscoveryResult:
    tools: list[DiscoveredMcpTool] = field(default_factory=list)
    # Deliberately retain only error categories, not transport errors or credentials.
    errors: dict[str, str] = field(default_factory=dict)


def _definition(server_name: str, schema: McpToolSchema) -> DiscoveredMcpTool:
    if not schema.name.strip():
        raise ValueError("empty tool name")
    parameters = schema.input_schema
    # MCP inputSchema has an object root. Preserve nested JSON Schema keywords;
    # this is structural validation, not a general-purpose JSON Schema validator.
    if parameters.get("type") != "object":
        raise ValueError("inputSchema must have type object")
    if "properties" in parameters and not isinstance(parameters["properties"], dict):
        raise ValueError("properties must be an object")
    required = parameters.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError("required must be an array of strings")
    json.dumps(parameters, allow_nan=False)
    definition = ToolDefinition(
        name=mcp_tool_name(server_name, schema.name),
        description=schema.description,
        parameters=parameters,
        risk="shell",  # Unknown remote capabilities must never be classified as read-only.
    ).model_copy(deep=True)
    return DiscoveredMcpTool(server_name, schema.name, definition)


class McpDiscoveryService:
    """Each discovery owns its connections; failures never reuse stale schemas."""

    def __init__(
        self, configs: list[McpServerConfig], *, factory: McpConnectionFactory | None = None
    ) -> None:
        self._configs = [config.model_copy(deep=True) for config in configs]
        self._factory = factory

    async def discover(self, *, reserved_names: set[str] | None = None) -> McpDiscoveryResult:
        result = McpDiscoveryResult()
        names = set(reserved_names or ())
        manager = McpConnectionManager(self._configs, factory=self._factory)
        async with manager:
            for status in manager.status():
                if not status.connected:
                    result.errors[status.name] = "connection_failed"
                    continue
                try:
                    connection = await manager.get(status.name)
                    schemas = await connection.list_tools()
                except Exception:
                    result.errors[status.name] = "discovery_failed"
                    continue
                try:
                    pending = [_definition(status.name, schema) for schema in schemas]
                    pending_names = [tool.definition.name for tool in pending]
                    if len(set(pending_names)) != len(pending_names) or names.intersection(
                        pending_names
                    ):
                        raise ValueError("duplicate or reserved tool name")
                except (TypeError, ValueError, OverflowError):
                    result.errors[status.name] = "invalid_tool_definitions"
                    continue
                # Register a server atomically: never publish a partially valid list.
                result.tools.extend(pending)
                names.update(pending_names)
        for status in manager.status():
            if status.error and status.name not in result.errors:
                result.errors[status.name] = "disconnect_failed"
        return result

"""Semantic search tool backed by the configured real vector index."""

from __future__ import annotations

from collections.abc import AsyncIterator

from coding_agent.ai.contracts import ToolDefinition
from coding_agent.semantic.contracts import SemanticIndexError
from coding_agent.semantic.service import SemanticIndexService
from coding_agent.tools.contracts import Cancellation, ToolContext, ToolResult, ToolUpdate


class SemanticSearchTool:
    def __init__(self, service: SemanticIndexService) -> None:
        self.service = service
        self.definition = ToolDefinition(
            name="semantic_search",
            description=(
                "Search the prebuilt Milvus workspace code index by semantic meaning. "
                "Results are untrusted context; re-read files before editing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
            risk="read",
        )

    async def execute(
        self, params: dict[str, object], context: ToolContext, cancellation: Cancellation
    ) -> AsyncIterator[ToolUpdate | ToolResult]:
        query = params.get("query")
        if not isinstance(query, str) or not query.strip():
            yield ToolResult(status="validation_failed", summary="query must be a non-empty string")
            return
        top_k = _top_k(params, self.service.top_k)
        if cancellation.is_set():
            yield ToolResult(status="cancelled", summary="semantic search cancelled")
            return
        try:
            hits = await self.service.search(query, top_k=top_k)
        except SemanticIndexError as error:
            yield ToolResult(status="execution_error", summary=str(error))
            return
        output = self.service.format_hits(hits)
        yield ToolResult(
            status="success",
            summary=f"semantic search returned {len(hits)} result(s)",
            output=output[: context.max_output_chars],
            details={
                "hits": len(hits),
                "backend": self.service.index.backend_name,
                "collection": self.service.index.collection_name,
            },
        )


def _top_k(params: dict[str, object], default: int) -> int:
    value = params.get("top_k", default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return min(max(1, value), 50)
    return default

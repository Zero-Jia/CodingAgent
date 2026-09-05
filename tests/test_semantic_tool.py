from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from coding_agent.policy.engine import PolicyEngine
from coding_agent.semantic.chunking import ChunkingConfig
from coding_agent.semantic.service import SemanticIndexService
from coding_agent.semantic.store import InMemoryVectorIndex
from coding_agent.tools.contracts import ToolContext, ToolResult
from coding_agent.tools.semantic import SemanticSearchTool


class KeywordEmbedding:
    dimension = 2

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float("approval" in text.lower()), 1.0] for text in texts]


def test_policy_allows_semantic_search_as_read_only(tmp_path: Path) -> None:
    policy = PolicyEngine(tmp_path, allow_write=False, allow_shell=False, non_interactive=True)

    decision = policy.tool_decision("semantic_search", {"query": "approval"})

    assert decision.decision == "allow"


def test_semantic_search_tool_returns_hits(tmp_path: Path) -> None:
    (tmp_path / "approval.py").write_text("approval queue code\n", encoding="utf-8")
    service = SemanticIndexService(
        workspace=tmp_path,
        embedder=KeywordEmbedding(),
        index=InMemoryVectorIndex("tool_chunks"),
        chunking=ChunkingConfig(max_file_bytes=1000, max_chunk_chars=400, overlap_chars=0),
        top_k=3,
    )
    asyncio.run(service.build_index())
    tool = SemanticSearchTool(service)

    async def execute() -> list[ToolResult]:
        results: list[ToolResult] = []
        async for event in tool.execute(
            {"query": "approval workflow", "top_k": 1},
            ToolContext(workspace=str(tmp_path)),
            asyncio.Event(),
        ):
            if isinstance(event, ToolResult):
                results.append(event)
        return results

    results = asyncio.run(execute())

    assert results[0].status == "success"
    assert "approval.py" in results[0].output
    assert results[0].details["backend"] == "memory"

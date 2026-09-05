"""Semantic code indexing and search."""

from coding_agent.semantic.contracts import (
    CodeChunk,
    IndexStats,
    SemanticSearchHit,
)
from coding_agent.semantic.service import SemanticIndexService, create_semantic_service

__all__ = [
    "CodeChunk",
    "IndexStats",
    "SemanticIndexService",
    "SemanticSearchHit",
    "create_semantic_service",
]

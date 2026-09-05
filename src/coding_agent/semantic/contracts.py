"""Contracts for real workspace semantic indexing."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel


class CodeChunk(BaseModel):
    chunk_id: str
    workspace_id: str
    path: str
    language: str
    symbol: str = ""
    start_line: int
    end_line: int
    content: str
    content_hash: str
    file_hash: str


class SemanticSearchHit(BaseModel):
    chunk: CodeChunk
    score: float
    stale: bool = False


class IndexStats(BaseModel):
    indexed_files: int = 0
    indexed_chunks: int = 0
    skipped_files: int = 0
    backend: str
    collection: str = ""


class EmbeddingProvider(Protocol):
    dimension: int

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


class VectorIndex(Protocol):
    backend_name: str
    collection_name: str

    async def ensure_collection(self, dimension: int) -> None: ...
    async def upsert(
        self, chunks: Sequence[CodeChunk], vectors: Sequence[Sequence[float]]
    ) -> None: ...
    async def search(
        self, vector: Sequence[float], *, workspace_id: str, top_k: int
    ) -> list[SemanticSearchHit]: ...


class SemanticIndexError(RuntimeError):
    """Raised when semantic indexing or search cannot complete safely."""

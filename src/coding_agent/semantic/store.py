"""Vector index implementations for semantic code search."""

from __future__ import annotations

import math
from collections.abc import Sequence

from coding_agent.semantic.contracts import CodeChunk, SemanticSearchHit, VectorIndex


class InMemoryVectorIndex(VectorIndex):
    """Deterministic vector index used for unit tests and local contract checks."""

    backend_name = "memory"

    def __init__(self, collection_name: str = "memory") -> None:
        self.collection_name = collection_name
        self._items: dict[str, tuple[CodeChunk, list[float]]] = {}
        self._dimension = 0

    async def ensure_collection(self, dimension: int) -> None:
        self._dimension = dimension

    async def upsert(self, chunks: Sequence[CodeChunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length mismatch")
        for chunk, vector in zip(chunks, vectors, strict=True):
            values = [float(item) for item in vector]
            if self._dimension and len(values) != self._dimension:
                raise ValueError("vector dimension mismatch")
            self._items[chunk.chunk_id] = (chunk, values)

    async def search(
        self, vector: Sequence[float], *, workspace_id: str, top_k: int
    ) -> list[SemanticSearchHit]:
        query = [float(item) for item in vector]
        hits = [
            SemanticSearchHit(chunk=chunk, score=_cosine(query, stored))
            for chunk, stored in self._items.values()
            if chunk.workspace_id == workspace_id
        ]
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)

"""DashScope embedding provider for real semantic indexing."""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from coding_agent.semantic.contracts import EmbeddingProvider, SemanticIndexError
from coding_agent.tracing.store import redact

MAX_EMBEDDING_INPUTS = 20


class DashScopeEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible DashScope embeddings client."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
        batch_size: int,
    ) -> None:
        if not api_key.strip():
            raise SemanticIndexError("DASHSCOPE_API_KEY is required for semantic indexing")
        if "<workspace-id>" in base_url:
            raise SemanticIndexError(
                "DASHSCOPE_BASE_URL still contains <workspace-id>; set the real DashScope endpoint"
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self.dimension = dimension
        self._batch_size = min(max(1, batch_size), MAX_EMBEDDING_INPUTS)

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = [text for text in texts[start : start + self._batch_size]]
            vectors.extend(await self._embed_batch(batch))
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": self._model,
            "input": texts,
            "dimensions": self.dimension,
            "encoding_format": "float",
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                response = await client.post(
                    f"{self._base_url}/embeddings",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            message = str(error).replace(self._api_key, "***")
            safe = redact(message)
            raise SemanticIndexError(f"DashScope embedding request failed: {safe}") from error
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise SemanticIndexError("DashScope embedding response did not contain a data list")
        vectors = [_embedding_from_item(item) for item in sorted(items, key=_embedding_index)]
        for vector in vectors:
            if len(vector) != self.dimension:
                raise SemanticIndexError(
                    "DashScope returned embedding dimension "
                    f"{len(vector)}, expected {self.dimension}"
                )
        return vectors


def _embedding_index(item: object) -> int:
    if isinstance(item, dict) and isinstance(item.get("index"), int):
        return int(item["index"])
    return 0


def _embedding_from_item(item: object) -> list[float]:
    if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
        raise SemanticIndexError("DashScope embedding response item is invalid")
    vector: list[float] = []
    for value in item["embedding"]:
        if not isinstance(value, int | float):
            raise SemanticIndexError("DashScope embedding vector contains non-numeric values")
        vector.append(float(value))
    return vector

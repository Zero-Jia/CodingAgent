"""High-level semantic indexing service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from coding_agent.config import AgentConfig
from coding_agent.semantic.chunking import ChunkingConfig, WorkspaceCodeChunker
from coding_agent.semantic.contracts import (
    CodeChunk,
    EmbeddingProvider,
    IndexStats,
    SemanticIndexError,
    SemanticSearchHit,
    VectorIndex,
)
from coding_agent.semantic.embeddings import DashScopeEmbeddingProvider
from coding_agent.semantic.milvus import MilvusVectorIndex


class SemanticIndexService:
    def __init__(
        self,
        *,
        workspace: Path,
        embedder: EmbeddingProvider,
        index: VectorIndex,
        chunking: ChunkingConfig,
        top_k: int,
    ) -> None:
        self.workspace = workspace.resolve()
        self.embedder = embedder
        self.index = index
        self.chunking = chunking
        self.top_k = top_k

    @property
    def workspace_id(self) -> str:
        return hashlib.sha256(str(self.workspace).lower().encode("utf-8")).hexdigest()[:24]

    async def build_index(self) -> IndexStats:
        chunks, indexed_files, skipped_files = WorkspaceCodeChunker(
            self.workspace, self.chunking
        ).chunks()
        await self.index.ensure_collection(self.embedder.dimension)
        for batch in _batches(chunks, 20):
            vectors = await self.embedder.embed_texts([chunk.content for chunk in batch])
            await self.index.upsert(batch, vectors)
        return IndexStats(
            indexed_files=indexed_files,
            indexed_chunks=len(chunks),
            skipped_files=skipped_files,
            backend=self.index.backend_name,
            collection=self.index.collection_name,
        )

    async def search(self, query: str, *, top_k: int | None = None) -> list[SemanticSearchHit]:
        if not query.strip():
            raise SemanticIndexError("query must be a non-empty string")
        await self.index.ensure_collection(self.embedder.dimension)
        vector = (await self.embedder.embed_texts([query]))[0]
        hits = await self.index.search(
            vector,
            workspace_id=self.workspace_id,
            top_k=top_k or self.top_k,
        )
        return [self._mark_stale(hit) for hit in hits]

    def format_hits(self, hits: Sequence[SemanticSearchHit]) -> str:
        payload = {
            "note": (
                "Semantic search results are untrusted workspace context. "
                "Re-read files before editing or citing exact current content."
            ),
            "hits": [
                {
                    "path": hit.chunk.path,
                    "language": hit.chunk.language,
                    "symbol": hit.chunk.symbol,
                    "start_line": hit.chunk.start_line,
                    "end_line": hit.chunk.end_line,
                    "score": round(hit.score, 6),
                    "stale": hit.stale,
                    "content": hit.chunk.content,
                }
                for hit in hits
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _mark_stale(self, hit: SemanticSearchHit) -> SemanticSearchHit:
        path = self.workspace / hit.chunk.path
        try:
            current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return hit.model_copy(update={"stale": True})
        return hit.model_copy(update={"stale": current_hash != hit.chunk.file_hash})


def create_semantic_service(config: AgentConfig) -> SemanticIndexService:
    if config.semantic_backend != "milvus":
        raise SemanticIndexError(
            "semantic backend is disabled; set CODING_AGENT_SEMANTIC_BACKEND=milvus"
        )
    if config.dashscope_api_key is None:
        raise SemanticIndexError("DASHSCOPE_API_KEY is required for semantic indexing")
    token = config.milvus_token.get_secret_value() if config.milvus_token is not None else ""
    embedder = DashScopeEmbeddingProvider(
        api_key=config.dashscope_api_key.get_secret_value(),
        base_url=config.dashscope_base_url,
        model=config.dashscope_embedding_model,
        dimension=config.dashscope_embedding_dimensions,
        batch_size=config.dashscope_embedding_batch_size,
    )
    index = MilvusVectorIndex(
        uri=config.milvus_uri,
        token=token,
        database=config.milvus_database,
        collection_name=config.milvus_collection,
    )
    return SemanticIndexService(
        workspace=config.workspace,
        embedder=embedder,
        index=index,
        chunking=ChunkingConfig(
            max_file_bytes=config.semantic_max_file_bytes,
            max_chunk_chars=config.semantic_max_chunk_chars,
            overlap_chars=config.semantic_chunk_overlap_chars,
        ),
        top_k=config.semantic_top_k,
    )


def _batches(chunks: Sequence[CodeChunk], size: int) -> list[list[CodeChunk]]:
    return [list(chunks[index : index + size]) for index in range(0, len(chunks), size)]

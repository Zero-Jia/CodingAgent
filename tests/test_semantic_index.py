from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from coding_agent.config import AgentConfig
from coding_agent.semantic.chunking import ChunkingConfig, WorkspaceCodeChunker
from coding_agent.semantic.contracts import SemanticIndexError
from coding_agent.semantic.service import SemanticIndexService, create_semantic_service
from coding_agent.semantic.store import InMemoryVectorIndex


class KeywordEmbedding:
    dimension = 3

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [_vector(text) for text in texts]


def test_config_loads_semantic_settings_from_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DASHSCOPE_API_KEY=local-key",
                "DASHSCOPE_BASE_URL=https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                "DASHSCOPE_EMBEDDING_MODEL=qwen3.7-text-embedding",
                "DASHSCOPE_EMBEDDING_DIMENSIONS=768",
                "CODING_AGENT_SEMANTIC_BACKEND=milvus",
                "CODING_AGENT_MILVUS_URI=http://127.0.0.1:19530",
            ]
        ),
        encoding="utf-8",
    )

    config = AgentConfig.from_environment(tmp_path)

    assert config.semantic_backend == "milvus"
    assert config.dashscope_api_key is not None
    assert config.dashscope_api_key.get_secret_value() == "local-key"
    assert config.dashscope_embedding_dimensions == 768
    assert config.milvus_uri == "http://127.0.0.1:19530"


def test_chunker_indexes_safe_source_and_skips_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "token_usage.py").write_text(
        "class TokenUsage:\n    def record(self):\n        return 'tokens'\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=secret\n", encoding="utf-8")
    (tmp_path / "large.py").write_text("x" * 2000, encoding="utf-8")

    chunks, indexed_files, skipped_files = WorkspaceCodeChunker(
        tmp_path,
        ChunkingConfig(max_file_bytes=1000, max_chunk_chars=200, overlap_chars=20),
    ).chunks()

    assert indexed_files == 1
    assert skipped_files >= 1
    assert [chunk.path for chunk in chunks] == ["src/token_usage.py"]
    assert chunks[0].language == "Python"
    assert chunks[0].symbol == "TokenUsage"
    assert ".env" not in {chunk.path for chunk in chunks}


def test_semantic_service_builds_and_searches_index(tmp_path: Path) -> None:
    (tmp_path / "approval.py").write_text(
        "class ApprovalRegistry:\n    def approve(self):\n        return 'approval queue'\n",
        encoding="utf-8",
    )
    (tmp_path / "storage.py").write_text(
        "class MySqlSessionStore:\n    def save(self):\n        return 'database session'\n",
        encoding="utf-8",
    )
    service = SemanticIndexService(
        workspace=tmp_path,
        embedder=KeywordEmbedding(),
        index=InMemoryVectorIndex("test_chunks"),
        chunking=ChunkingConfig(max_file_bytes=1000, max_chunk_chars=400, overlap_chars=0),
        top_k=2,
    )

    stats = asyncio.run(service.build_index())
    hits = asyncio.run(service.search("approval workflow", top_k=1))

    assert stats.indexed_files == 2
    assert stats.indexed_chunks == 2
    assert hits[0].chunk.path == "approval.py"
    assert hits[0].stale is False


def test_semantic_service_marks_stale_hits(tmp_path: Path) -> None:
    target = tmp_path / "approval.py"
    target.write_text("approval queue\n", encoding="utf-8")
    service = SemanticIndexService(
        workspace=tmp_path,
        embedder=KeywordEmbedding(),
        index=InMemoryVectorIndex("test_chunks"),
        chunking=ChunkingConfig(max_file_bytes=1000, max_chunk_chars=400, overlap_chars=0),
        top_k=1,
    )

    asyncio.run(service.build_index())
    target.write_text("changed content\n", encoding="utf-8")
    hits = asyncio.run(service.search("approval", top_k=1))

    assert hits[0].stale is True


def test_create_semantic_service_requires_real_dashscope_key(tmp_path: Path) -> None:
    config = AgentConfig(workspace=tmp_path, semantic_backend="milvus")

    with pytest.raises(SemanticIndexError, match="DASHSCOPE_API_KEY"):
        create_semantic_service(config)


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_SEMANTIC_TESTS") != "1",
    reason="real DashScope and Milvus smoke test is opt-in",
)
def test_real_dashscope_milvus_smoke(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        "def semantic_search_smoke():\n    return 'milvus dashscope integration'\n",
        encoding="utf-8",
    )
    config = AgentConfig.from_environment(tmp_path)
    service = create_semantic_service(config)

    stats = asyncio.run(service.build_index())
    hits = asyncio.run(service.search("milvus dashscope integration", top_k=1))

    assert stats.indexed_chunks >= 1
    assert hits
    assert hits[0].chunk.path == "sample.py"


def _vector(text: str) -> list[float]:
    lowered = text.lower()
    return [
        float(lowered.count("approval")),
        float(lowered.count("database") + lowered.count("mysql")),
        1.0,
    ]

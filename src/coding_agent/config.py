"""显式应用配置；密钥永不序列化。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

StorageBackend = Literal["jsonl", "mysql"]
SemanticBackend = Literal["disabled", "milvus"]


class AgentConfig(BaseModel):
    workspace: Path
    model_provider: str = "deepseek"
    model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: SecretStr | None = Field(default=None, exclude=True)
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_api_key: SecretStr | None = Field(default=None, exclude=True)
    dashscope_embedding_model: str = "qwen3.7-text-embedding"
    dashscope_embedding_dimensions: int = Field(default=1024, ge=256, le=2560)
    dashscope_embedding_batch_size: int = Field(default=10, ge=1, le=20)
    semantic_backend: SemanticBackend = "disabled"
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: SecretStr | None = Field(default=None, exclude=True)
    milvus_database: str = "default"
    milvus_collection: str = "coding_agent_code_chunks"
    milvus_memory_collection: str = "coding_agent_memories"
    semantic_top_k: int = Field(default=8, ge=1, le=50)
    semantic_max_file_bytes: int = Field(default=800_000, ge=1)
    semantic_max_chunk_chars: int = Field(default=6_000, ge=500)
    semantic_chunk_overlap_chars: int = Field(default=600, ge=0)
    storage_backend: StorageBackend = "jsonl"
    database_url: SecretStr | None = Field(default=None, exclude=True)
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_pre_ping: bool = True
    database_connect_timeout_seconds: int = Field(default=5, ge=1)
    database_pool_recycle_seconds: int = Field(default=1800, ge=1)
    database_create_schema: bool = False
    allow_write: bool = False
    allow_shell: bool = False
    plan_mode: bool = False
    non_interactive: bool = False
    sandbox_image: str = "coding-agent-sandbox:python-3.12"
    sandbox_timeout_seconds: int = 60
    sandbox_memory_mb: int = 768
    sandbox_cpu_count: float = 1.0
    sandbox_pids_limit: int = 128
    sandbox_tmpfs_mb: int = 512
    max_turns: int = 8
    max_tool_calls: int = 24
    max_tool_output_chars: int = 12_000
    context_window_tokens: int = 128_000
    context_compact_threshold_tokens: int = 96_000
    context_keep_recent_tokens: int = 12_000
    context_keep_recent_messages: int = 6
    context_chars_per_token: float = 3.5
    context_summary_max_chars: int = 8_000
    trace_level: str = "redacted"
    memory_recall_enabled: bool = False
    memory_recall_top_k: int = Field(default=5, ge=1, le=20)
    memory_recall_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    memory_recall_min_score: float = Field(default=0.5, ge=0.0, le=1.0)
    memory_recall_max_chars: int = Field(default=2_000, ge=200)
    memory_user_id: str = ""
    memory_project_id: str = ""
    memory_ttl_days: int | None = Field(default=90, ge=1)
    memory_decay_half_life_days: float = Field(default=30.0, gt=0.0)

    @field_validator("storage_backend", mode="before")
    @classmethod
    def normalize_storage_backend(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("semantic_backend", mode="before")
    @classmethod
    def normalize_semantic_backend(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @model_validator(mode="after")
    def select_database_storage_for_explicit_url(self) -> Self:
        if self.database_url is not None:
            self.storage_backend = "mysql"
        return self

    @classmethod
    def from_environment(cls, workspace: Path, **overrides: object) -> AgentConfig:
        env = _merged_environment(workspace)
        key = env.get("DEEPSEEK_API_KEY")
        base = env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        provider = env.get("CODING_AGENT_MODEL_PROVIDER", "deepseek")
        model = env.get("CODING_AGENT_MODEL") or env.get(
            "DEEPSEEK_MODEL", "deepseek-chat"
        )
        database_url = _secret_value(
            overrides.pop("database_url", env.get("CODING_AGENT_DATABASE_URL"))
        )
        storage_backend = overrides.pop(
            "storage_backend",
            env.get("CODING_AGENT_STORAGE_BACKEND")
            or ("mysql" if database_url is not None else "jsonl"),
        )
        payload: dict[str, object] = {
            "workspace": workspace.resolve(),
            "model_provider": overrides.pop("model_provider", provider),
            "model": overrides.pop("model", model),
            "deepseek_base_url": base,
            "deepseek_api_key": SecretStr(key) if key else None,
            "dashscope_base_url": overrides.pop(
                "dashscope_base_url",
                env.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            ),
            "dashscope_api_key": _secret_value(
                overrides.pop("dashscope_api_key", env.get("DASHSCOPE_API_KEY"))
            ),
            "dashscope_embedding_model": overrides.pop(
                "dashscope_embedding_model",
                env.get("DASHSCOPE_EMBEDDING_MODEL", "qwen3.7-text-embedding"),
            ),
            "dashscope_embedding_dimensions": overrides.pop(
                "dashscope_embedding_dimensions",
                _env_int(env, "DASHSCOPE_EMBEDDING_DIMENSIONS", 1024),
            ),
            "dashscope_embedding_batch_size": overrides.pop(
                "dashscope_embedding_batch_size",
                _env_int(env, "DASHSCOPE_EMBEDDING_BATCH_SIZE", 10),
            ),
            "semantic_backend": overrides.pop(
                "semantic_backend",
                env.get("CODING_AGENT_SEMANTIC_BACKEND", "disabled"),
            ),
            "milvus_uri": overrides.pop(
                "milvus_uri", env.get("CODING_AGENT_MILVUS_URI", "http://127.0.0.1:19530")
            ),
            "milvus_token": _secret_value(
                overrides.pop("milvus_token", env.get("CODING_AGENT_MILVUS_TOKEN"))
            ),
            "milvus_database": overrides.pop(
                "milvus_database", env.get("CODING_AGENT_MILVUS_DATABASE", "default")
            ),
            "milvus_collection": overrides.pop(
                "milvus_collection",
                env.get("CODING_AGENT_MILVUS_COLLECTION", "coding_agent_code_chunks"),
            ),
            "milvus_memory_collection": overrides.pop(
                "milvus_memory_collection",
                env.get("CODING_AGENT_MILVUS_MEMORY_COLLECTION", "coding_agent_memories"),
            ),
            "memory_recall_enabled": overrides.pop(
                "memory_recall_enabled",
                _env_bool(env, "CODING_AGENT_MEMORY_RECALL", False),
            ),
            "memory_recall_top_k": overrides.pop(
                "memory_recall_top_k",
                _env_int(env, "CODING_AGENT_MEMORY_RECALL_TOP_K", 5),
            ),
            "memory_recall_min_confidence": overrides.pop(
                "memory_recall_min_confidence",
                _env_float(env, "CODING_AGENT_MEMORY_RECALL_MIN_CONFIDENCE", 0.6),
            ),
            "memory_recall_min_score": overrides.pop(
                "memory_recall_min_score",
                _env_float(env, "CODING_AGENT_MEMORY_RECALL_MIN_SCORE", 0.5),
            ),
            "memory_recall_max_chars": overrides.pop(
                "memory_recall_max_chars",
                _env_int(env, "CODING_AGENT_MEMORY_RECALL_MAX_CHARS", 2000),
            ),
            "memory_user_id": overrides.pop(
                "memory_user_id", env.get("CODING_AGENT_MEMORY_USER_ID", "")
            ),
            "memory_project_id": overrides.pop(
                "memory_project_id", env.get("CODING_AGENT_MEMORY_PROJECT_ID", "")
            ),
            "memory_ttl_days": overrides.pop(
                "memory_ttl_days",
                _env_int_or_none(env, "CODING_AGENT_MEMORY_TTL_DAYS", 90),
            ),
            "memory_decay_half_life_days": overrides.pop(
                "memory_decay_half_life_days",
                _env_float(env, "CODING_AGENT_MEMORY_DECAY_HALF_LIFE_DAYS", 30.0),
            ),
            "semantic_top_k": overrides.pop(
                "semantic_top_k", _env_int(env, "CODING_AGENT_SEMANTIC_TOP_K", 8)
            ),
            "semantic_max_file_bytes": overrides.pop(
                "semantic_max_file_bytes",
                _env_int(env, "CODING_AGENT_SEMANTIC_MAX_FILE_BYTES", 800_000),
            ),
            "semantic_max_chunk_chars": overrides.pop(
                "semantic_max_chunk_chars",
                _env_int(env, "CODING_AGENT_SEMANTIC_MAX_CHUNK_CHARS", 6_000),
            ),
            "semantic_chunk_overlap_chars": overrides.pop(
                "semantic_chunk_overlap_chars",
                _env_int(env, "CODING_AGENT_SEMANTIC_CHUNK_OVERLAP_CHARS", 600),
            ),
            "storage_backend": storage_backend,
            "database_url": database_url,
            "database_pool_size": overrides.pop(
                "database_pool_size", _env_int(env, "CODING_AGENT_DATABASE_POOL_SIZE", 5)
            ),
            "database_max_overflow": overrides.pop(
                "database_max_overflow",
                _env_int(env, "CODING_AGENT_DATABASE_MAX_OVERFLOW", 10),
            ),
            "database_pool_pre_ping": overrides.pop(
                "database_pool_pre_ping",
                _env_bool(env, "CODING_AGENT_DATABASE_POOL_PRE_PING", True),
            ),
            "database_connect_timeout_seconds": overrides.pop(
                "database_connect_timeout_seconds",
                _env_int(env, "CODING_AGENT_DATABASE_CONNECT_TIMEOUT_SECONDS", 5),
            ),
            "database_pool_recycle_seconds": overrides.pop(
                "database_pool_recycle_seconds",
                _env_int(env, "CODING_AGENT_DATABASE_POOL_RECYCLE_SECONDS", 1800),
            ),
            "database_create_schema": overrides.pop(
                "database_create_schema",
                _env_bool(env, "CODING_AGENT_DATABASE_CREATE_SCHEMA", False),
            ),
            "sandbox_image": env.get(
                "CODING_AGENT_SANDBOX_IMAGE", "coding-agent-sandbox:python-3.12"
            ),
            **overrides,
        }
        return cls.model_validate(payload)


def _secret_value(value: object) -> SecretStr | None:
    if value is None:
        return None
    if isinstance(value, SecretStr):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        return SecretStr(stripped) if stripped else None
    return SecretStr(str(value))


def _merged_environment(workspace: Path) -> dict[str, str]:
    values = _dotenv_values(workspace / ".env")
    values.update(os.environ)
    return values


def _dotenv_values(path: Path) -> dict[str, str]:
    if not path.exists() or not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        key_value = _parse_dotenv_line(line)
        if key_value is not None:
            key, value = key_value
            values[key] = value
    return values


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if len(value) >= 2 and (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        value = value[1:-1]
    return key, value


def _env_int(env: dict[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _env_float(env: dict[str, str], name: str, default: float) -> float:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error


def _env_int_or_none(env: dict[str, str], name: str, default: int | None) -> int | None:
    """解析可选正整数环境变量；显式设为 <=0 表示 None（关闭该功能）。"""
    value = env.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    return parsed if parsed > 0 else None


def _env_bool(env: dict[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of: 1, 0, true, false, yes, no, on, off")

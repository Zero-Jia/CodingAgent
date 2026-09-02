"""显式应用配置；密钥永不序列化。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

StorageBackend = Literal["jsonl", "mysql"]


class AgentConfig(BaseModel):
    workspace: Path
    model_provider: str = "deepseek"
    model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: SecretStr | None = Field(default=None, exclude=True)
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

    @field_validator("storage_backend", mode="before")
    @classmethod
    def normalize_storage_backend(cls, value: object) -> object:
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
        key = os.environ.get("DEEPSEEK_API_KEY")
        base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        provider = os.environ.get("CODING_AGENT_MODEL_PROVIDER", "deepseek")
        model = os.environ.get("CODING_AGENT_MODEL") or os.environ.get(
            "DEEPSEEK_MODEL", "deepseek-chat"
        )
        database_url = _secret_value(
            overrides.pop("database_url", os.environ.get("CODING_AGENT_DATABASE_URL"))
        )
        storage_backend = overrides.pop(
            "storage_backend",
            os.environ.get("CODING_AGENT_STORAGE_BACKEND")
            or ("mysql" if database_url is not None else "jsonl"),
        )
        payload: dict[str, object] = {
            "workspace": workspace.resolve(),
            "model_provider": overrides.pop("model_provider", provider),
            "model": overrides.pop("model", model),
            "deepseek_base_url": base,
            "deepseek_api_key": SecretStr(key) if key else None,
            "storage_backend": storage_backend,
            "database_url": database_url,
            "database_pool_size": overrides.pop(
                "database_pool_size", _env_int("CODING_AGENT_DATABASE_POOL_SIZE", 5)
            ),
            "database_max_overflow": overrides.pop(
                "database_max_overflow", _env_int("CODING_AGENT_DATABASE_MAX_OVERFLOW", 10)
            ),
            "database_pool_pre_ping": overrides.pop(
                "database_pool_pre_ping",
                _env_bool("CODING_AGENT_DATABASE_POOL_PRE_PING", True),
            ),
            "database_connect_timeout_seconds": overrides.pop(
                "database_connect_timeout_seconds",
                _env_int("CODING_AGENT_DATABASE_CONNECT_TIMEOUT_SECONDS", 5),
            ),
            "database_pool_recycle_seconds": overrides.pop(
                "database_pool_recycle_seconds",
                _env_int("CODING_AGENT_DATABASE_POOL_RECYCLE_SECONDS", 1800),
            ),
            "database_create_schema": overrides.pop(
                "database_create_schema",
                _env_bool("CODING_AGENT_DATABASE_CREATE_SCHEMA", False),
            ),
            "sandbox_image": os.environ.get(
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


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of: 1, 0, true, false, yes, no, on, off")

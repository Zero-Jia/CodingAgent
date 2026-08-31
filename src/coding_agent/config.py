"""显式应用配置；密钥永不序列化。"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr


class AgentConfig(BaseModel):
    workspace: Path
    model_provider: str = "deepseek"
    model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: SecretStr | None = Field(default=None, exclude=True)
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

    @classmethod
    def from_environment(cls, workspace: Path, **overrides: object) -> AgentConfig:
        key = os.environ.get("DEEPSEEK_API_KEY")
        base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        provider = os.environ.get("CODING_AGENT_MODEL_PROVIDER", "deepseek")
        model = os.environ.get("CODING_AGENT_MODEL") or os.environ.get(
            "DEEPSEEK_MODEL", "deepseek-chat"
        )
        payload: dict[str, object] = {
            "workspace": workspace.resolve(),
            "model_provider": overrides.pop("model_provider", provider),
            "model": overrides.pop("model", model),
            "deepseek_base_url": base,
            "deepseek_api_key": SecretStr(key) if key else None,
            "sandbox_image": os.environ.get(
                "CODING_AGENT_SANDBOX_IMAGE", "coding-agent-sandbox:python-3.12"
            ),
            **overrides,
        }
        return cls.model_validate(payload)

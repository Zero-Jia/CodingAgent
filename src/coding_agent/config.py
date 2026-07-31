"""显式应用配置；密钥永不序列化。"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr


class AgentConfig(BaseModel):
    workspace: Path
    model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: SecretStr | None = Field(default=None, exclude=True)
    allow_write: bool = False
    allow_shell: bool = False
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
    max_history_messages: int = 32
    trace_level: str = "redacted"

    @classmethod
    def from_environment(cls, workspace: Path, **overrides: object) -> AgentConfig:
        key = os.environ.get("DEEPSEEK_API_KEY")
        base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        payload: dict[str, object] = {
            "workspace": workspace.resolve(),
            "model": overrides.pop("model", model),
            "deepseek_base_url": base,
            "deepseek_api_key": SecretStr(key) if key else None,
            "sandbox_image": os.environ.get(
                "CODING_AGENT_SANDBOX_IMAGE", "coding-agent-sandbox:python-3.12"
            ),
            **overrides,
        }
        return cls.model_validate(payload)

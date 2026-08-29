from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from coding_agent.ai.deepseek import DeepSeekAdapter
from coding_agent.ai.gateway import (
    ModelProviderError,
    available_model_providers,
    create_model_adapter,
)
from coding_agent.config import AgentConfig


def test_model_gateway_creates_deepseek_adapter(tmp_path: Path) -> None:
    config = AgentConfig(
        workspace=tmp_path,
        model_provider="deepseek",
        model="deepseek-chat",
        deepseek_api_key=SecretStr("test-key"),
    )

    adapter = create_model_adapter(config)

    assert isinstance(adapter, DeepSeekAdapter)
    assert adapter.model.provider == "deepseek"
    assert adapter.model.name == "deepseek-chat"


def test_model_gateway_rejects_missing_deepseek_key(tmp_path: Path) -> None:
    config = AgentConfig(workspace=tmp_path, model_provider="deepseek")

    with pytest.raises(ModelProviderError, match="DEEPSEEK_API_KEY"):
        create_model_adapter(config)


def test_model_gateway_rejects_unknown_provider(tmp_path: Path) -> None:
    config = AgentConfig(workspace=tmp_path, model_provider="openai")

    with pytest.raises(ModelProviderError, match="unsupported model provider: openai"):
        create_model_adapter(config)


def test_model_gateway_lists_available_providers() -> None:
    assert available_model_providers() == ("deepseek",)


def test_config_selects_provider_and_model_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_AGENT_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("CODING_AGENT_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    config = AgentConfig.from_environment(tmp_path)

    assert config.model_provider == "deepseek"
    assert config.model == "deepseek-reasoner"
    assert config.deepseek_api_key is not None


def test_config_preserves_provider_specific_model_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CODING_AGENT_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-coder")

    config = AgentConfig.from_environment(tmp_path)

    assert config.model_provider == "deepseek"
    assert config.model == "deepseek-coder"


def test_explicit_model_override_wins_over_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_AGENT_MODEL", "deepseek-reasoner")

    config = AgentConfig.from_environment(tmp_path, model="deepseek-chat")

    assert config.model == "deepseek-chat"

"""模型 provider registry 和 adapter 工厂。"""

from __future__ import annotations

from collections.abc import Callable

from coding_agent.ai.contracts import ModelAdapter
from coding_agent.ai.deepseek import DeepSeekAdapter
from coding_agent.config import AgentConfig


class ModelProviderError(ValueError):
    """模型 provider 配置无效或当前未支持。"""


ModelAdapterFactory = Callable[[AgentConfig], ModelAdapter]


def create_model_adapter(config: AgentConfig) -> ModelAdapter:
    """根据配置创建具体模型适配器。"""
    provider = config.model_provider.strip().lower()
    factory = _PROVIDER_FACTORIES.get(provider)
    if factory is None:
        available = ", ".join(available_model_providers())
        raise ModelProviderError(
            f"unsupported model provider: {config.model_provider}; available providers: {available}"
        )
    return factory(config)


def available_model_providers() -> tuple[str, ...]:
    """返回当前已实现的 provider 名称。"""
    return tuple(sorted(_PROVIDER_FACTORIES))


def _create_deepseek_adapter(config: AgentConfig) -> ModelAdapter:
    if config.deepseek_api_key is None:
        raise ModelProviderError("未设置 DEEPSEEK_API_KEY，无法运行 deepseek provider")
    return DeepSeekAdapter(
        config.model,
        config.deepseek_api_key.get_secret_value(),
        config.deepseek_base_url,
    )


_PROVIDER_FACTORIES: dict[str, ModelAdapterFactory] = {
    "deepseek": _create_deepseek_adapter,
}

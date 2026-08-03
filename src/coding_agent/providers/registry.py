"""Model provider registration and selection."""

from __future__ import annotations

from collections.abc import Iterable

from langchain_core.language_models.chat_models import BaseChatModel

from coding_agent.providers.base import (
    ModelCapabilities,
    ModelProvider,
    ModelSelection,
    ProviderError,
)


class ProviderRegistry:
    def __init__(self, model_catalog: Iterable[ModelSelection] = ()) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._model_catalog = tuple(model_catalog)
        if len(set(self._model_catalog)) != len(self._model_catalog):
            raise ProviderError("模型目录包含重复条目")

    def register(self, provider: ModelProvider) -> None:
        provider_id = provider.provider_id.strip().casefold()
        if provider_id in self._providers:
            raise ProviderError(f"Provider 已注册：{provider_id}")
        self._providers[provider_id] = provider

    @property
    def models(self) -> tuple[ModelSelection, ...]:
        return self._model_catalog

    def contains(self, selection: ModelSelection) -> bool:
        return selection in self._model_catalog

    def capabilities(self, selection: ModelSelection) -> ModelCapabilities:
        return self._provider(selection).capabilities(selection.model_id)

    def create_model(self, selection: ModelSelection) -> BaseChatModel:
        if not self.contains(selection):
            raise ProviderError(f"模型不在 AGENT_MODELS 目录中：{selection.reference}")
        capabilities = self.capabilities(selection)
        if not capabilities.streaming or not capabilities.tool_calling:
            raise ProviderError(
                f"模型缺少 Agent 所需的 streaming/tool-calling 能力：{selection.reference}"
            )
        try:
            return self._provider(selection).create_model(selection.model_id)
        except ProviderError:
            raise
        except Exception as error:
            message = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
            raise ProviderError(
                f"创建模型失败 {selection.reference}：{message[:300]}"
            ) from None

    def _provider(self, selection: ModelSelection) -> ModelProvider:
        provider = self._providers.get(selection.provider_id)
        if provider is None:
            raise ProviderError(f"未知 Provider：{selection.provider_id}")
        return provider


__all__ = ["ProviderRegistry"]

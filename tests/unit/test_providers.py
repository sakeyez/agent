from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from coding_agent.providers import (
    ModelCapabilities,
    ModelSelection,
    ProviderError,
    ProviderRegistry,
)


class FakeProvider:
    provider_id = "fake"

    def capabilities(self, model_id: str) -> ModelCapabilities:
        return ModelCapabilities(streaming=True, tool_calling=model_id != "no-tools")

    def create_model(self, model_id: str):
        return FakeListChatModel(responses=[model_id])


def test_registry_selects_catalog_models_and_checks_capabilities() -> None:
    good = ModelSelection("fake", "good")
    unsupported = ModelSelection("fake", "no-tools")
    registry = ProviderRegistry([good, unsupported])
    registry.register(FakeProvider())

    assert registry.create_model(good)._llm_type == "fake-list-chat-model"
    with pytest.raises(ProviderError, match="tool-calling"):
        registry.create_model(unsupported)
    with pytest.raises(ProviderError, match="AGENT_MODELS"):
        registry.create_model(ModelSelection("fake", "missing"))


def test_registry_rejects_duplicate_provider() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider())
    with pytest.raises(ProviderError, match="已注册"):
        registry.register(FakeProvider())

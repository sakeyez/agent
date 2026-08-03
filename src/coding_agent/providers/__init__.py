"""Model providers supported by the agent."""

from coding_agent.providers.base import (
    ModelCapabilities,
    ModelProvider,
    ModelSelection,
    ProviderError,
)
from coding_agent.providers.kimi import KimiProvider, create_kimi_client
from coding_agent.providers.openai_compatible import OpenAICompatibleProvider
from coding_agent.providers.registry import ProviderRegistry

__all__ = [
    "KimiProvider",
    "ModelCapabilities",
    "ModelProvider",
    "ModelSelection",
    "OpenAICompatibleProvider",
    "ProviderError",
    "ProviderRegistry",
    "create_kimi_client",
]

"""Generic OpenAI-compatible chat-completions provider."""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from coding_agent.providers.base import ModelCapabilities


class OpenAICompatibleProvider:
    provider_id = "openai-compatible"

    def __init__(self, api_key: SecretStr, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def capabilities(self, model_id: str) -> ModelCapabilities:
        return ModelCapabilities(streaming=True, tool_calling=True)

    def create_model(self, model_id: str) -> ChatOpenAI:
        return ChatOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            model=model_id,
            streaming=True,
            timeout=60,
            max_retries=2,
            use_responses_api=False,
        )


__all__ = ["OpenAICompatibleProvider"]

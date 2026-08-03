"""Provider-neutral model contracts and capability metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from langchain_core.language_models.chat_models import BaseChatModel


class ProviderError(ValueError):
    """A provider error safe to display to an interface."""


_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class ModelSelection:
    provider_id: str
    model_id: str

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip().casefold()
        model_id = self.model_id.strip()
        if not _PROVIDER_ID.fullmatch(provider_id):
            raise ProviderError(f"无效的 Provider 标识：{self.provider_id}")
        if not model_id:
            raise ProviderError("模型 ID 不能为空")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "model_id", model_id)

    @classmethod
    def parse(cls, value: str) -> "ModelSelection":
        provider_id, separator, model_id = value.strip().partition(":")
        if not separator:
            raise ProviderError(f"模型必须使用 provider:model-id 格式：{value}")
        return cls(provider_id, model_id)

    @property
    def reference(self) -> str:
        return f"{self.provider_id}:{self.model_id}"


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    streaming: bool
    tool_calling: bool


class ModelProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def capabilities(self, model_id: str) -> ModelCapabilities: ...

    def create_model(self, model_id: str) -> BaseChatModel: ...


__all__ = [
    "ModelCapabilities",
    "ModelProvider",
    "ModelSelection",
    "ProviderError",
]

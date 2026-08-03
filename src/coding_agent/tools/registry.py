"""Tool registration, lookup, and duplicate-name validation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from coding_agent.tools.contracts import ToolDefinition


class ToolRegistry:
    def __init__(self, tools: Iterable[ToolDefinition] = ()) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolDefinition) -> None:
        if not tool.name or not tool.name.isidentifier():
            raise ValueError(f"invalid tool name: {tool.name!r}")
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        if tool.timeout_seconds is not None and tool.timeout_seconds <= 0:
            raise ValueError("tool timeout_seconds must be positive")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def model_schemas(self, names: set[str] | None = None) -> list[dict[str, Any]]:
        return [
            tool.model_schema()
            for name, tool in self._tools.items()
            if names is None or name in names
        ]

    def __len__(self) -> int:
        return len(self._tools)

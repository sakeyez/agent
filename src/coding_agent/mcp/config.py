"""Validated workspace MCP configuration."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class McpConfigError(ValueError):
    """An MCP configuration error safe to display in the terminal."""


class McpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = "."
    enabled: bool = True
    tool_prefix: str | None = Field(default=None, alias="toolPrefix")
    startup_timeout_seconds: float = Field(
        default=15, ge=1, le=120, alias="startupTimeoutSeconds"
    )
    tool_timeout_seconds: float = Field(
        default=30, ge=1, le=300, alias="toolTimeoutSeconds"
    )

    @field_validator("command")
    @classmethod
    def strip_command(cls, value: str) -> str:
        return value.strip()

    def resolved_env(self, environment: dict[str, str] | None = None) -> dict[str, str]:
        source = dict(os.environ) if environment is None else environment
        resolved: dict[str, str] = {}
        for name, value in self.env.items():
            match = _ENV_REFERENCE.fullmatch(value)
            if match is None:
                resolved[name] = value
                continue
            source_name = match.group(1)
            if source_name not in source:
                raise McpConfigError(f"missing environment variable {source_name}")
            resolved[name] = source[source_name]
        return resolved


class McpConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    mcp_servers: dict[str, McpServerConfig] = Field(alias="mcpServers")

    @field_validator("mcp_servers")
    @classmethod
    def validate_server_names(
        cls, value: dict[str, McpServerConfig]
    ) -> dict[str, McpServerConfig]:
        invalid = [name for name in value if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)]
        if invalid:
            raise ValueError(f"invalid server name: {invalid[0]}")
        return value


def load_mcp_config(path: Path) -> McpConfiguration:
    """Read an MCP JSON file, returning an empty configuration when absent."""

    if not path.exists():
        return McpConfiguration(mcpServers={})
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return McpConfiguration.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        detail = str(error).strip().splitlines()[0] or type(error).__name__
        raise McpConfigError(detail[:500]) from None

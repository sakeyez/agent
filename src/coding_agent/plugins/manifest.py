"""Plugin manifest schema and compatibility validation."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator

PLUGIN_API_VERSION = Version("0.1.0")
_ENTRYPOINT = re.compile(
    r"^(?P<module>[A-Za-z_][A-Za-z0-9_.]*):(?P<callable>[A-Za-z_][A-Za-z0-9_]*)$"
)


class PluginManifestError(ValueError):
    """A plugin manifest error safe to show to the user."""


class PluginManifest(BaseModel):
    """Validated contents of a plugin.toml file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    version: str
    description: str = Field(min_length=1, max_length=500)
    entrypoint: str
    requires_agent: str = ">=0.1,<0.2"
    enabled: bool = True

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        try:
            Version(value)
        except InvalidVersion as error:
            raise ValueError("must be a valid PEP 440 version") from error
        return value

    @field_validator("requires_agent")
    @classmethod
    def validate_agent_requirement(cls, value: str) -> str:
        try:
            SpecifierSet(value)
        except InvalidSpecifier as error:
            raise ValueError("must be a valid PEP 440 specifier") from error
        return value

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        if _ENTRYPOINT.fullmatch(value) is None:
            raise ValueError("must use the 'module:callable' format")
        return value

    def is_compatible(self, agent_version: Version = PLUGIN_API_VERSION) -> bool:
        return agent_version in SpecifierSet(self.requires_agent)

    def entrypoint_parts(self) -> tuple[str, str]:
        match = _ENTRYPOINT.fullmatch(self.entrypoint)
        if match is None:  # Already guaranteed by model validation.
            raise RuntimeError("invalid validated entrypoint")
        return match.group("module"), match.group("callable")


def load_manifest(path: Path) -> PluginManifest:
    """Parse and validate a plugin manifest without importing plugin code."""

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return PluginManifest.model_validate(data)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError) as error:
        detail = str(error).strip().splitlines()[0] or type(error).__name__
        raise PluginManifestError(detail[:500]) from None

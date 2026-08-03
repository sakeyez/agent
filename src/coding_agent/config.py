"""Application configuration loaded from environment variables and .env."""

from pathlib import Path
from typing import Any

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(ValueError):
    """A configuration error safe to display in the terminal."""


class Settings(BaseSettings):
    """Runtime settings for the single Kimi provider and workspace."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    kimi_api_key: SecretStr = Field(validation_alias="KIMI_API_KEY")
    kimi_model: str = Field(validation_alias="KIMI_MODEL")
    kimi_base_url: AnyHttpUrl = Field(
        default="https://api.moonshot.cn/v1",
        validation_alias="KIMI_BASE_URL",
    )
    workspace: Path = Field(default_factory=Path.cwd, validation_alias="AGENT_WORKSPACE")
    database_path: Path | None = Field(default=None, validation_alias="AGENT_DB_PATH")
    audit_path: Path | None = Field(default=None, validation_alias="AGENT_AUDIT_PATH")
    context_max_chars: int = Field(
        default=80_000, ge=1_000, validation_alias="AGENT_CONTEXT_MAX_CHARS"
    )
    context_keep_recent_turns: int = Field(
        default=4, ge=1, le=50, validation_alias="AGENT_CONTEXT_KEEP_RECENT_TURNS"
    )
    memory_summary_max_chars: int = Field(
        default=12_000, ge=500, validation_alias="AGENT_MEMORY_SUMMARY_MAX_CHARS"
    )
    plugins_enabled: bool = Field(default=False, validation_alias="AGENT_ENABLE_PLUGINS")
    plugins_path: Path | None = Field(default=None, validation_alias="AGENT_PLUGINS_PATH")
    enabled_plugins: str | None = Field(
        default=None, validation_alias="AGENT_ENABLED_PLUGINS"
    )
    mcp_enabled: bool = Field(default=False, validation_alias="AGENT_ENABLE_MCP")
    mcp_config_path: Path | None = Field(
        default=None, validation_alias="AGENT_MCP_CONFIG_PATH"
    )

    @field_validator("kimi_api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("kimi_model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("workspace")
    @classmethod
    def validate_workspace(cls, value: Path) -> Path:
        workspace = value.expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError("must be an existing directory")
        return workspace

    @model_validator(mode="after")
    def resolve_runtime_paths(self) -> "Settings":
        database_path = self.database_path
        if database_path is None:
            database_path = self.workspace / ".coding_agent" / "checkpoints.sqlite3"
        else:
            database_path = database_path.expanduser()
            if not database_path.is_absolute():
                database_path = self.workspace / database_path
        self.database_path = database_path.resolve()
        audit_path = self.audit_path
        if audit_path is None:
            audit_path = self.workspace / ".coding_agent" / "audit.jsonl"
        else:
            audit_path = audit_path.expanduser()
            if not audit_path.is_absolute():
                audit_path = self.workspace / audit_path
        self.audit_path = audit_path.resolve()
        self.plugins_path = self._workspace_path(self.plugins_path, "plugins")
        self.mcp_config_path = self._workspace_path(
            self.mcp_config_path, ".coding_agent/mcp.json"
        )
        return self

    def _workspace_path(self, value: Path | None, default: str) -> Path:
        path = Path(default) if value is None else value.expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        return path.resolve()

    @property
    def enabled_plugin_names(self) -> frozenset[str] | None:
        """Return an optional allow-list parsed from a comma-separated setting."""

        if self.enabled_plugins is None:
            return None
        return frozenset(
            name.strip() for name in self.enabled_plugins.split(",") if name.strip()
        )


_ENV_NAMES = {
    "kimi_api_key": "KIMI_API_KEY",
    "KIMI_API_KEY": "KIMI_API_KEY",
    "kimi_model": "KIMI_MODEL",
    "KIMI_MODEL": "KIMI_MODEL",
    "kimi_base_url": "KIMI_BASE_URL",
    "KIMI_BASE_URL": "KIMI_BASE_URL",
    "workspace": "AGENT_WORKSPACE",
    "AGENT_WORKSPACE": "AGENT_WORKSPACE",
    "database_path": "AGENT_DB_PATH",
    "AGENT_DB_PATH": "AGENT_DB_PATH",
    "audit_path": "AGENT_AUDIT_PATH",
    "AGENT_AUDIT_PATH": "AGENT_AUDIT_PATH",
    "context_max_chars": "AGENT_CONTEXT_MAX_CHARS",
    "AGENT_CONTEXT_MAX_CHARS": "AGENT_CONTEXT_MAX_CHARS",
    "context_keep_recent_turns": "AGENT_CONTEXT_KEEP_RECENT_TURNS",
    "AGENT_CONTEXT_KEEP_RECENT_TURNS": "AGENT_CONTEXT_KEEP_RECENT_TURNS",
    "memory_summary_max_chars": "AGENT_MEMORY_SUMMARY_MAX_CHARS",
    "AGENT_MEMORY_SUMMARY_MAX_CHARS": "AGENT_MEMORY_SUMMARY_MAX_CHARS",
    "plugins_enabled": "AGENT_ENABLE_PLUGINS",
    "AGENT_ENABLE_PLUGINS": "AGENT_ENABLE_PLUGINS",
    "plugins_path": "AGENT_PLUGINS_PATH",
    "AGENT_PLUGINS_PATH": "AGENT_PLUGINS_PATH",
    "enabled_plugins": "AGENT_ENABLED_PLUGINS",
    "AGENT_ENABLED_PLUGINS": "AGENT_ENABLED_PLUGINS",
    "mcp_enabled": "AGENT_ENABLE_MCP",
    "AGENT_ENABLE_MCP": "AGENT_ENABLE_MCP",
    "mcp_config_path": "AGENT_MCP_CONFIG_PATH",
    "AGENT_MCP_CONFIG_PATH": "AGENT_MCP_CONFIG_PATH",
}


def _format_validation_error(error: ValidationError) -> str:
    problems: list[str] = []
    for detail in error.errors(include_url=False, include_context=False, include_input=False):
        field = str(detail["loc"][-1])
        env_name = _ENV_NAMES.get(field, field)
        if detail["type"] == "missing":
            problems.append(f"缺少必要配置 {env_name}")
        else:
            problems.append(f"{env_name} 无效: {detail['msg']}")
    return "；".join(problems)


def load_settings(**overrides: Any) -> Settings:
    """Load validated settings and raise only sanitized configuration errors."""

    try:
        return Settings(**overrides)
    except ValidationError as error:
        raise ConfigError(_format_validation_error(error)) from None

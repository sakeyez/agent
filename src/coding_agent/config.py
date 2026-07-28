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
    def resolve_database_path(self) -> "Settings":
        database_path = self.database_path
        if database_path is None:
            database_path = self.workspace / ".coding_agent" / "checkpoints.sqlite3"
        else:
            database_path = database_path.expanduser()
            if not database_path.is_absolute():
                database_path = self.workspace / database_path
        self.database_path = database_path.resolve()
        return self


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

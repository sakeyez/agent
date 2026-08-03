from __future__ import annotations

from pathlib import Path

import pytest

from pydantic import ValidationError

from coding_agent.config import ConfigError, Settings, load_settings


def test_load_settings_requires_key_and_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_MODEL", raising=False)

    with pytest.raises(ConfigError) as caught:
        load_settings(_env_file=None)

    message = str(caught.value)
    assert "KIMI_API_KEY" in message
    assert "KIMI_MODEL" in message


def test_defaults_and_relative_database_path(tmp_path: Path) -> None:
    settings = Settings(
        KIMI_API_KEY="secret",
        KIMI_MODEL="kimi-model",
        AGENT_WORKSPACE=tmp_path,
        _env_file=None,
    )

    assert str(settings.kimi_base_url).rstrip("/") == "https://api.moonshot.cn/v1"
    assert settings.workspace == tmp_path.resolve()
    assert settings.database_path == (tmp_path / ".coding_agent/checkpoints.sqlite3").resolve()
    assert settings.audit_path == (tmp_path / ".coding_agent/audit.jsonl").resolve()
    assert settings.context_max_chars == 80_000
    assert settings.context_keep_recent_turns == 4
    assert settings.memory_summary_max_chars == 12_000
    assert settings.plugins_enabled is False
    assert settings.plugins_path == (tmp_path / "plugins").resolve()
    assert settings.enabled_plugin_names is None
    assert settings.mcp_enabled is False
    assert settings.mcp_config_path == (tmp_path / ".coding_agent/mcp.json").resolve()

    relative = Settings(
        KIMI_API_KEY="secret",
        KIMI_MODEL="kimi-model",
        AGENT_WORKSPACE=tmp_path,
        AGENT_DB_PATH="state/agent.sqlite3",
        AGENT_AUDIT_PATH="state/audit.jsonl",
        _env_file=None,
    )
    assert relative.database_path == (tmp_path / "state/agent.sqlite3").resolve()
    assert relative.audit_path == (tmp_path / "state/audit.jsonl").resolve()


def test_invalid_workspace_has_sanitized_error(tmp_path: Path) -> None:
    secret = "do-not-print-this-key"
    missing_workspace = tmp_path / "missing"

    with pytest.raises(ConfigError) as caught:
        load_settings(
            KIMI_API_KEY=secret,
            KIMI_MODEL="kimi-model",
            AGENT_WORKSPACE=missing_workspace,
            _env_file=None,
        )

    message = str(caught.value)
    assert "AGENT_WORKSPACE" in message
    assert secret not in message


def test_api_key_is_masked_in_settings_repr(tmp_path: Path) -> None:
    secret = "do-not-print-this-key"
    settings = Settings(
        KIMI_API_KEY=secret,
        KIMI_MODEL="kimi-model",
        AGENT_WORKSPACE=tmp_path,
        _env_file=None,
    )

    assert secret not in repr(settings)
    assert settings.kimi_api_key.get_secret_value() == secret


def test_explicit_model_catalog_only_requires_referenced_provider(tmp_path: Path) -> None:
    settings = Settings(
        AGENT_MODELS="openai-compatible:qwen-coder, openai-compatible:qwen-fast",
        AGENT_DEFAULT_MODEL="openai-compatible:qwen-fast",
        OPENAI_COMPAT_API_KEY="compat-secret",
        OPENAI_COMPAT_BASE_URL="http://localhost:8000/v1",
        AGENT_WORKSPACE=tmp_path,
        _env_file=None,
    )

    assert [item.reference for item in settings.model_catalog] == [
        "openai-compatible:qwen-coder",
        "openai-compatible:qwen-fast",
    ]
    assert settings.default_model_selection.reference == "openai-compatible:qwen-fast"
    assert settings.kimi_api_key is None
    assert settings.provider_secrets == ("compat-secret",)


def test_default_model_must_be_in_catalog(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            AGENT_MODELS="kimi:kimi-a",
            AGENT_DEFAULT_MODEL="kimi:kimi-b",
            KIMI_API_KEY="secret",
            AGENT_WORKSPACE=tmp_path,
            _env_file=None,
        )

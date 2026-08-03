from __future__ import annotations

from pathlib import Path

import pytest

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

from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        KIMI_API_KEY="test-secret-key",
        KIMI_MODEL="test-kimi-model",
        AGENT_WORKSPACE=tmp_path,
        AGENT_DB_PATH=".coding_agent/test.sqlite3",
        _env_file=None,
    )

from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.mcp import McpConfigError, load_mcp_config


def test_missing_configuration_is_empty(tmp_path: Path) -> None:
    configuration = load_mcp_config(tmp_path / "missing.json")

    assert configuration.mcp_servers == {}


def test_resolves_environment_references(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        '{"mcpServers":{"demo":{"command":"server","env":{"TOKEN":"${TOKEN}"}}}}',
        encoding="utf-8",
    )
    server = load_mcp_config(path).mcp_servers["demo"]

    assert server.resolved_env({"TOKEN": "secret"}) == {"TOKEN": "secret"}
    with pytest.raises(McpConfigError, match="TOKEN"):
        server.resolved_env({})

from __future__ import annotations

from pathlib import Path

from coding_agent.plugins import load_plugins
from coding_agent.tools import ToolCall, ToolExecutionContext, ToolExecutor, ToolRegistry
from coding_agent.workspace import WorkspaceContext


def _write_plugin(root: Path, name: str, source: str, *, enabled: bool = True) -> None:
    plugin = root / name
    plugin.mkdir(parents=True)
    (plugin / "plugin.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                f'name = "{name}"',
                'version = "1.0.0"',
                f'description = "{name} test plugin"',
                'entrypoint = "plugin:register"',
                'requires_agent = ">=0.1,<0.2"',
                f"enabled = {str(enabled).lower()}",
            ]
        ),
        encoding="utf-8",
    )
    (plugin / "plugin.py").write_text(source, encoding="utf-8")


_ECHO_PLUGIN = """
from pydantic import BaseModel, ConfigDict
from coding_agent.plugins.api import ToolDefinition

class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str

def register():
    return [ToolDefinition(
        name="plugin_echo",
        description="Echo text from a plugin",
        args_schema=Arguments,
        handler=lambda arguments, context: arguments.text,
    )]
"""


def test_loads_enabled_plugin_and_executes_registered_tool(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    _write_plugin(plugin_root, "echo-plugin", _ECHO_PLUGIN)
    registry = ToolRegistry()

    report = load_plugins(plugin_root, registry)
    result = ToolExecutor(registry).execute(
        ToolCall("call-1", "plugin_echo", {"text": "hello"}),
        ToolExecutionContext(WorkspaceContext.from_path(tmp_path)),
    )

    assert report.loaded == ("echo-plugin",)
    assert report.issues == ()
    assert result.content == "hello"


def test_isolates_broken_plugin_and_keeps_registry_atomic(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    _write_plugin(plugin_root, "good-plugin", _ECHO_PLUGIN)
    _write_plugin(
        plugin_root,
        "bad-plugin",
        _ECHO_PLUGIN.replace('name="plugin_echo"', 'name="invalid-name"'),
    )
    registry = ToolRegistry()

    report = load_plugins(plugin_root, registry)

    assert report.loaded == ("good-plugin",)
    assert [issue.plugin for issue in report.issues] == ["bad-plugin"]
    assert registry.names() == frozenset({"plugin_echo"})


def test_respects_manifest_and_runtime_enable_filters(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    _write_plugin(plugin_root, "disabled-plugin", _ECHO_PLUGIN, enabled=False)
    _write_plugin(plugin_root, "unselected-plugin", _ECHO_PLUGIN)

    report = load_plugins(plugin_root, ToolRegistry(), enabled_plugins=frozenset())

    assert report.loaded == ()
    assert report.skipped == ("disabled-plugin", "unselected-plugin")

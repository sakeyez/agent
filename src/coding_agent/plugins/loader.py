"""Controlled loading and tool registration for valid plugins."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from coding_agent.plugins.discovery import PluginCandidate, discover_plugins
from coding_agent.plugins.manifest import PluginManifest, PluginManifestError, load_manifest
from coding_agent.tools.contracts import ToolDefinition
from coding_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class PluginLoadIssue:
    plugin: str
    message: str


@dataclass(frozen=True, slots=True)
class PluginLoadReport:
    loaded: tuple[str, ...]
    skipped: tuple[str, ...]
    issues: tuple[PluginLoadIssue, ...]


def _load_module(candidate: PluginCandidate, manifest: PluginManifest) -> ModuleType:
    module_name, _ = manifest.entrypoint_parts()
    relative = Path(*module_name.split("."))
    module_file = candidate.root / relative.with_suffix(".py")
    package_file = candidate.root / relative / "__init__.py"
    if module_file.is_file():
        source = module_file
        search_locations = None
    elif package_file.is_file():
        source = package_file
        search_locations = [str(package_file.parent)]
    else:
        raise PluginManifestError(f"entrypoint module not found: {module_name}")

    import_name = f"_coding_agent_plugin_{manifest.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(
        import_name,
        source,
        submodule_search_locations=search_locations,
    )
    if spec is None or spec.loader is None:
        raise PluginManifestError(f"cannot load entrypoint module: {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[import_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(import_name, None)
        raise
    return module


def _plugin_tools(
    candidate: PluginCandidate, manifest: PluginManifest
) -> tuple[ToolDefinition, ...]:
    module = _load_module(candidate, manifest)
    _, callable_name = manifest.entrypoint_parts()
    factory = getattr(module, callable_name, None)
    if not callable(factory):
        raise PluginManifestError(f"entrypoint callable not found: {callable_name}")
    result = factory()
    if not isinstance(result, Iterable):
        raise PluginManifestError("entrypoint must return an iterable of ToolDefinition objects")
    tools = tuple(result)
    if not tools:
        raise PluginManifestError("entrypoint returned no tools")
    if not all(isinstance(tool, ToolDefinition) for tool in tools):
        raise PluginManifestError("entrypoint returned an item that is not a ToolDefinition")
    return tools


def load_plugins(
    directory: Path,
    registry: ToolRegistry,
    *,
    enabled_plugins: frozenset[str] | None = None,
) -> PluginLoadReport:
    """Load valid plugins independently and register each plugin atomically."""

    loaded: list[str] = []
    skipped: list[str] = []
    issues: list[PluginLoadIssue] = []
    for candidate in discover_plugins(directory):
        identity = candidate.root.name
        try:
            manifest = load_manifest(candidate.manifest_path)
            identity = manifest.name
            if candidate.root.name != manifest.name:
                raise PluginManifestError("directory name must match manifest name")
            if not manifest.enabled or (
                enabled_plugins is not None and manifest.name not in enabled_plugins
            ):
                skipped.append(manifest.name)
                continue
            if not manifest.is_compatible():
                raise PluginManifestError(
                    f"requires agent {manifest.requires_agent}, current API is 0.1.0"
                )
            tools = _plugin_tools(candidate, manifest)
            names = [tool.name for tool in tools]
            if len(names) != len(set(names)):
                raise PluginManifestError("plugin declares duplicate tool names")
            conflicts = sorted(set(names) & registry.names())
            if conflicts:
                raise PluginManifestError(
                    f"tool name conflicts with an existing tool: {', '.join(conflicts)}"
                )
            # Validate the complete batch before mutating the shared registry.
            ToolRegistry(tools)
            for tool in tools:
                registry.register(tool)
            loaded.append(manifest.name)
        except Exception as error:
            detail = str(error).strip().splitlines()[0] or type(error).__name__
            issues.append(PluginLoadIssue(identity, detail[:500]))
    return PluginLoadReport(tuple(loaded), tuple(skipped), tuple(issues))

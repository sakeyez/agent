"""Plugin discovery, validation, and loading infrastructure."""

from coding_agent.plugins.discovery import PluginCandidate, discover_plugins
from coding_agent.plugins.loader import (
    PluginLoadIssue,
    PluginLoadReport,
    load_plugins,
)
from coding_agent.plugins.manifest import PluginManifest, PluginManifestError, load_manifest

__all__ = [
    "PluginCandidate",
    "PluginLoadIssue",
    "PluginLoadReport",
    "PluginManifest",
    "PluginManifestError",
    "discover_plugins",
    "load_manifest",
    "load_plugins",
]

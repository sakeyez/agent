"""Discovery of installed plugins without importing them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PluginCandidate:
    root: Path
    manifest_path: Path


def discover_plugins(directory: Path) -> tuple[PluginCandidate, ...]:
    """Find immediate child directories containing plugin.toml."""

    if not directory.is_dir():
        return ()
    candidates = [
        PluginCandidate(root=child, manifest_path=child / "plugin.toml")
        for child in directory.iterdir()
        if child.is_dir()
        and not child.name.startswith((".", "_"))
        and (child / "plugin.toml").is_file()
    ]
    return tuple(sorted(candidates, key=lambda item: item.root.name.casefold()))

"""Parsing for the small stable set of CLI slash commands."""

from __future__ import annotations

from enum import Enum


class CliCommand(str, Enum):
    EXIT = "exit"
    STATUS = "status"
    CANCEL = "cancel"


def parse_command(value: str) -> CliCommand | None:
    normalized = value.strip().casefold()
    if not normalized.startswith("/"):
        return None
    try:
        return CliCommand(normalized[1:])
    except ValueError:
        return None


__all__ = ["CliCommand", "parse_command"]

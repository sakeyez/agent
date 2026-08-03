"""Parsing for CLI slash commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CliCommandError(ValueError):
    pass


class CliCommandName(str, Enum):
    EXIT = "exit"
    HELP = "help"
    STATUS = "status"
    CANCEL = "cancel"
    SESSION = "session"
    SESSIONS = "sessions"
    NEW = "new"
    USE = "use"
    RENAME = "rename"
    DELETE = "delete"
    MODELS = "models"
    MODEL = "model"


@dataclass(frozen=True, slots=True)
class CliCommand:
    name: CliCommandName
    argument: str | None = None


_REQUIRES_ARGUMENT = {
    CliCommandName.USE,
    CliCommandName.RENAME,
    CliCommandName.DELETE,
    CliCommandName.MODEL,
}
_ALLOWS_ARGUMENT = {*_REQUIRES_ARGUMENT, CliCommandName.NEW}


def parse_command(value: str) -> CliCommand | None:
    normalized = value.strip()
    if not normalized.startswith("/"):
        return None
    parts = normalized[1:].split(maxsplit=1)
    raw_name = parts[0] if parts else ""
    try:
        name = CliCommandName(raw_name.casefold())
    except ValueError:
        raise CliCommandError(f"未知命令：/{raw_name}；使用 /help 查看命令") from None
    argument = parts[1].strip() if len(parts) == 2 and parts[1].strip() else None
    if name in _REQUIRES_ARGUMENT and argument is None:
        raise CliCommandError(f"/{name.value} 缺少参数")
    if name not in _ALLOWS_ARGUMENT and argument is not None:
        raise CliCommandError(f"/{name.value} 不接受参数")
    return CliCommand(name, argument)


__all__ = ["CliCommand", "CliCommandError", "CliCommandName", "parse_command"]

"""Compatibility entry point for the layered CLI application."""

from __future__ import annotations

import sys

from coding_agent.config import ConfigError, load_settings
from coding_agent.interfaces.cli.app import _safe_error_message, run_cli

THREAD_CONFIG = {"configurable": {"thread_id": "default"}}


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as error:
        print(f"配置错误：{error}", file=sys.stderr)
        return 2
    try:
        return run_cli(settings)
    except Exception as error:
        print(f"启动失败：{_safe_error_message(error, settings)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["THREAD_CONFIG", "main", "run_cli"]

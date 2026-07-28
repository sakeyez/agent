"""Interactive command-line interface for the minimal Kimi agent."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from coding_agent.config import ConfigError, Settings, load_settings
from coding_agent.graph import create_agent_graph
from coding_agent.providers.kimi import create_kimi_client

THREAD_CONFIG = {"configurable": {"thread_id": "default"}}


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {"text", "text_delta"}:
            text = block.get("text") or block.get("value")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _safe_error_message(error: Exception, settings: Settings) -> str:
    message = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    secret = settings.kimi_api_key.get_secret_value()
    if secret:
        message = message.replace(secret, "***")
    return message[:300]


def run_cli(
    settings: Settings,
    *,
    model: BaseChatModel | None = None,
    input_fn: Callable[[str], str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the input loop until /exit, EOF, or Ctrl+C."""

    reader = input_fn or input
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    database_path = settings.database_path
    if database_path is None:  # Kept explicit for type checkers; Settings always resolves it.
        raise RuntimeError("数据库路径未配置")
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    chat_model = model or create_kimi_client(settings)

    with SqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        graph = create_agent_graph(chat_model, checkpointer=checkpointer)
        while True:
            try:
                user_input = reader("你> ")
            except (EOFError, KeyboardInterrupt):
                output.write("\n")
                output.flush()
                return 0

            if user_input.strip() == "/exit":
                return 0
            if not user_input.strip():
                continue

            output.write("Kimi> ")
            output.flush()
            try:
                events = graph.stream(
                    {
                        "messages": [HumanMessage(content=user_input)],
                        "workspace": str(settings.workspace),
                        "tool_rounds": 0,
                    },
                    config=THREAD_CONFIG,
                    stream_mode="messages",
                )
                for message, metadata in events:
                    if not isinstance(message, AIMessageChunk):
                        continue
                    if metadata.get("langgraph_node") != "model":
                        continue
                    text = _content_text(message.content)
                    if text:
                        output.write(text)
                        output.flush()
                output.write("\n")
                output.flush()
            except KeyboardInterrupt:
                output.write("\n")
                output.flush()
                return 0
            except Exception as error:
                output.write("\n")
                output.flush()
                errors.write(f"请求失败：{_safe_error_message(error, settings)}\n")
                errors.flush()


def main() -> int:
    """Load configuration and start the CLI without exposing tracebacks."""

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

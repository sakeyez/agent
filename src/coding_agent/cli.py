"""Interactive command-line interface for the minimal Kimi agent."""

from __future__ import annotations

import os
import sys
from copy import deepcopy
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

from dotenv import dotenv_values
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from coding_agent.config import ConfigError, Settings, load_settings
from coding_agent.graph import create_agent_graph
from coding_agent.interfaces.cli.approvals import CliApprovalProvider
from coding_agent.interfaces.cli.commands import CliCommand, parse_command
from coding_agent.interfaces.cli.renderer import render_task, render_task_transition
from coding_agent.mcp import McpConfigError, McpManager, load_mcp_config
from coding_agent.observability import JsonlAuditSink, SecretRedactor
from coding_agent.plugins import load_plugins
from coding_agent.providers.kimi import create_kimi_client
from coding_agent.tools.builtin import create_coding_tool_registry
from coding_agent.tools.executor import ToolExecutor
from coding_agent.agents.coding.tasks import TERMINAL_TASK_STATUSES, TaskStatus
from coding_agent.agents.coding.planner import TaskPlanner

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


def _safe_startup_text(value: str, redactor: SecretRedactor) -> str:
    return redactor.redact(value).replace("\r", " ").replace("\n", " ")[:500]


def run_cli(
    settings: Settings,
    *,
    model: BaseChatModel | None = None,
    input_fn: Callable[[str], str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    task_planner: TaskPlanner | None = None,
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
    tool_registry = create_coding_tool_registry()
    env_values = dotenv_values(settings.workspace / ".env")
    discovered_secrets = [
        value for value in env_values.values() if isinstance(value, str)
    ]
    extension_secrets: list[str] = []
    plugin_report = None
    plugins_path = settings.plugins_path
    if settings.plugins_enabled and plugins_path is not None:
        plugin_report = load_plugins(
            plugins_path,
            tool_registry,
            enabled_plugins=settings.enabled_plugin_names,
        )

    mcp_configuration = None
    mcp_config_error: str | None = None
    if settings.mcp_enabled and settings.mcp_config_path is not None:
        try:
            mcp_configuration = load_mcp_config(settings.mcp_config_path)
        except McpConfigError as error:
            mcp_config_error = str(error)
        else:
            for server in mcp_configuration.mcp_servers.values():
                try:
                    extension_secrets.extend(server.resolved_env().values())
                except McpConfigError:
                    pass
    redactor = SecretRedactor.from_environment(
        os.environ,
        extra_secrets=[
            settings.kimi_api_key.get_secret_value(),
            *discovered_secrets,
            *extension_secrets,
        ],
    )
    audit_path = settings.audit_path
    if audit_path is None:  # Settings always resolves it; kept explicit for type checkers.
        raise RuntimeError("审计路径未配置")
    tool_executor = ToolExecutor(
        tool_registry,
        approval_provider=CliApprovalProvider(reader, output),
        audit_sink=JsonlAuditSink(audit_path),
        redactor=redactor,
    )
    mcp_manager = McpManager(settings.workspace)
    mcp_report = (
        mcp_manager.connect(mcp_configuration, tool_registry)
        if mcp_configuration is not None
        else None
    )

    if plugin_report is not None:
        if plugin_report.loaded:
            output.write(f"已加载插件：{', '.join(plugin_report.loaded)}\n")
        for issue in plugin_report.issues:
            name = _safe_startup_text(issue.plugin, redactor)
            message = _safe_startup_text(issue.message, redactor)
            errors.write(f"插件 {name} 加载失败：{message}\n")
    if mcp_config_error is not None:
        errors.write(f"MCP 配置无效：{_safe_startup_text(mcp_config_error, redactor)}\n")
    if mcp_report is not None:
        if mcp_report.connected:
            output.write(f"已连接 MCP：{', '.join(mcp_report.connected)}\n")
        for issue in mcp_report.issues:
            message = _safe_startup_text(issue.message, redactor)
            errors.write(f"MCP {issue.server} 连接失败：{message}\n")
    output.flush()
    errors.flush()

    with mcp_manager, SqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        graph = create_agent_graph(
            chat_model,
            checkpointer=checkpointer,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            task_planner=task_planner,
            context_max_chars=settings.context_max_chars,
            context_keep_recent_turns=settings.context_keep_recent_turns,
            memory_summary_max_chars=settings.memory_summary_max_chars,
        )
        def stream_run(payload: dict[str, Any] | None, *, resumed: bool = False) -> None:
            if resumed:
                output.write("正在恢复未完成任务。\n")
                output.flush()
            previous_task = None
            snapshot = graph.get_state(THREAD_CONFIG)
            if snapshot.values.get("task") is not None:
                previous_task = deepcopy(snapshot.values["task"])
            prefix_written = False
            events = graph.stream(
                payload,
                config=THREAD_CONFIG,
                stream_mode=["messages", "updates"],
            )
            for mode, event in events:
                if mode == "updates":
                    for update in event.values():
                        task = update.get("task") if isinstance(update, dict) else None
                        if task is None:
                            continue
                        render_task_transition(output, previous_task, task)
                        previous_task = deepcopy(task)
                    continue
                message, metadata = event
                if not isinstance(message, (AIMessage, AIMessageChunk)):
                    continue
                if metadata.get("langgraph_node") not in {
                    "chat_model",
                    "chat_final",
                    "task_final",
                }:
                    continue
                text = _content_text(message.content)
                if not text:
                    continue
                if not prefix_written:
                    output.write("Kimi> ")
                    prefix_written = True
                output.write(text)
                output.flush()
            if prefix_written:
                output.write("\n")
            output.flush()

        snapshot = graph.get_state(THREAD_CONFIG)
        restored_task = snapshot.values.get("task")
        if (
            restored_task is not None
            and restored_task.get("status") not in TERMINAL_TASK_STATUSES
            and snapshot.next
        ):
            try:
                stream_run(None, resumed=True)
            except KeyboardInterrupt:
                output.write("\n任务已在最近的检查点暂停，可使用 /status 或 /cancel。\n")
                output.flush()
            except Exception as error:
                errors.write(f"恢复失败：{_safe_error_message(error, settings)}\n")
                errors.flush()

        while True:
            try:
                user_input = reader("你> ")
            except (EOFError, KeyboardInterrupt):
                output.write("\n")
                output.flush()
                return 0

            command = parse_command(user_input)
            if command is CliCommand.EXIT:
                return 0
            if command is CliCommand.STATUS:
                task = graph.get_state(THREAD_CONFIG).values.get("task")
                output.write(render_task(task) + "\n")
                output.flush()
                continue
            if command is CliCommand.CANCEL:
                current = graph.get_state(THREAD_CONFIG)
                task = current.values.get("task")
                if task is None or task.get("status") in TERMINAL_TASK_STATUSES:
                    output.write("当前没有可取消的任务。\n")
                    output.flush()
                    continue
                cancelled = deepcopy(task)
                cancelled["status"] = TaskStatus.CANCELLED.value
                cancelled["final_summary"] = "Cancelled by user."
                graph.update_state(
                    THREAD_CONFIG,
                    {
                        "task": cancelled,
                        "control_action": "final",
                        "termination_reason": "cancelled",
                    },
                )
                if graph.get_state(THREAD_CONFIG).next:
                    graph.invoke(None, config=THREAD_CONFIG)
                output.write("任务已取消，计划与验证证据已保留。\n")
                output.flush()
                continue
            if not user_input.strip():
                continue
            try:
                stream_run(
                    {
                        "messages": [HumanMessage(content=user_input)],
                        "workspace": str(settings.workspace),
                        "tool_rounds": 0,
                        "run_id": uuid4().hex,
                        "termination_reason": None,
                        "request": user_input,
                    },
                )
            except KeyboardInterrupt:
                output.write("\n任务已在最近的检查点暂停，可使用 /status 或 /cancel。\n")
                output.flush()
                continue
            except Exception as error:
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

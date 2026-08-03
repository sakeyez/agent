"""Interactive command-line interface adapter."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from langchain_core.language_models.chat_models import BaseChatModel

from coding_agent.agents.coding.planner import TaskPlanner
from coding_agent.application import AssistantText, TaskTransition, create_application
from coding_agent.config import ConfigError, Settings, load_settings
from coding_agent.interfaces.cli.approvals import CliApprovalProvider
from coding_agent.interfaces.cli.commands import (
    CliCommand,
    CliCommandError,
    CliCommandName,
    parse_command,
)
from coding_agent.interfaces.cli.renderer import (
    assistant_label,
    render_help,
    render_models,
    render_session,
    render_sessions,
    render_task,
    render_task_transition,
)
from coding_agent.providers import ModelSelection, ProviderError
from coding_agent.sessions import SessionError


def _safe_error_message(error: Exception, settings: Settings) -> str:
    message = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    for secret in settings.provider_secrets:
        message = message.replace(secret, "***")
    return message[:300]


def run_cli(
    settings: Settings,
    *,
    model: BaseChatModel | None = None,
    input_fn: Callable[[str], str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    task_planner: TaskPlanner | None = None,
) -> int:
    reader = input_fn or input
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    approval = CliApprovalProvider(reader, output)

    with create_application(
        settings,
        approval_provider=approval,
        model_override=model,
        task_planner=task_planner,
    ) as application:
        runtime = application.runtime
        sessions = application.sessions
        for message in application.startup_messages:
            stream = errors if message.level == "error" else output
            stream.write(message.text + "\n")
        output.flush()
        errors.flush()

        def stream_run(*, resumed: bool = False) -> None:
            if resumed:
                output.write("正在恢复未完成任务。\n")
                output.flush()
            prefix_written = False
            events = runtime.resume() if resumed else runtime.stream_turn(pending_text)
            for event in events:
                if isinstance(event, TaskTransition):
                    render_task_transition(output, event.previous, event.current)
                    continue
                if isinstance(event, AssistantText):
                    if not prefix_written:
                        output.write(assistant_label(sessions.current().model))
                        prefix_written = True
                    output.write(event.text)
                    output.flush()
            if prefix_written:
                output.write("\n")
            output.flush()

        def resume_current_if_needed() -> None:
            if not runtime.is_model_available():
                errors.write(
                    f"当前会话模型不可用：{sessions.current().model.reference}；"
                    "可使用 /cancel 后通过 /model 重新选择。\n"
                )
                errors.flush()
                return
            if not runtime.should_resume():
                return
            stream_run(resumed=True)

        pending_text = ""
        try:
            resume_current_if_needed()
        except KeyboardInterrupt:
            output.write("\n任务已在最近的检查点暂停，可使用 /status 或 /cancel。\n")
            output.flush()
        except Exception as error:
            errors.write(f"恢复失败：{application.redactor.redact(str(error))[:300]}\n")
            errors.flush()

        while True:
            try:
                user_input = reader("你> ")
            except (EOFError, KeyboardInterrupt, StopIteration):
                output.write("\n")
                output.flush()
                return 0

            try:
                command = parse_command(user_input)
            except CliCommandError as error:
                errors.write(str(error) + "\n")
                errors.flush()
                continue

            if command is not None:
                if command.name is CliCommandName.EXIT:
                    return 0
                try:
                    _handle_command(command, application, output)
                    if command.name is CliCommandName.USE:
                        resume_current_if_needed()
                except KeyboardInterrupt:
                    output.write("\n任务已在最近的检查点暂停，可使用 /status 或 /cancel。\n")
                    output.flush()
                except (SessionError, ProviderError, CliCommandError) as error:
                    errors.write(application.redactor.redact(str(error))[:300] + "\n")
                    errors.flush()
                except Exception as error:
                    errors.write(f"命令失败：{application.redactor.redact(str(error))[:300]}\n")
                    errors.flush()
                continue

            if not user_input.strip():
                continue
            if not runtime.is_model_available():
                errors.write(
                    f"当前会话模型不可用：{sessions.current().model.reference}；"
                    "请切换会话，或在任务终态后使用 /model。\n"
                )
                errors.flush()
                continue
            pending_text = user_input
            try:
                stream_run()
            except KeyboardInterrupt:
                output.write("\n任务已在最近的检查点暂停，可使用 /status 或 /cancel。\n")
                output.flush()
            except Exception as error:
                errors.write(f"请求失败：{application.redactor.redact(str(error))[:300]}\n")
                errors.flush()


def _handle_command(command: CliCommand, application, output: TextIO) -> None:
    sessions = application.sessions
    runtime = application.runtime
    argument = command.argument
    if command.name is CliCommandName.HELP:
        output.write(render_help() + "\n")
    elif command.name is CliCommandName.STATUS:
        output.write(render_task(runtime.task()) + "\n")
    elif command.name is CliCommandName.CANCEL:
        output.write(
            "任务已取消，计划与验证证据已保留。\n"
            if runtime.cancel()
            else "当前没有可取消的任务。\n"
        )
    elif command.name is CliCommandName.SESSION:
        output.write(render_session(sessions.current()) + "\n")
    elif command.name is CliCommandName.SESSIONS:
        current = sessions.current()
        output.write(render_sessions(sessions.list_sessions(), current.id) + "\n")
    elif command.name is CliCommandName.NEW:
        output.write(render_session(sessions.create(argument)) + "\n")
    elif command.name is CliCommandName.USE:
        assert argument is not None
        output.write(render_session(sessions.activate(argument)) + "\n")
    elif command.name is CliCommandName.RENAME:
        assert argument is not None
        current = sessions.current()
        output.write(render_session(sessions.rename(str(current.id), argument)) + "\n")
    elif command.name is CliCommandName.DELETE:
        assert argument is not None
        deleted, replacement = runtime.delete_session(argument)
        output.write(f"已删除会话：{deleted.name}\n{render_session(replacement)}\n")
    elif command.name is CliCommandName.MODELS:
        current = sessions.current()
        output.write(
            render_models(
                application.providers.models,
                current.model,
                runtime.settings.default_model_selection,
            )
            + "\n"
        )
    elif command.name is CliCommandName.MODEL:
        assert argument is not None
        output.write(render_session(runtime.change_model(ModelSelection.parse(argument))) + "\n")
    output.flush()


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


__all__ = ["main", "run_cli"]

"""Dependency composition for the executable application."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from dotenv import dotenv_values
from langchain_core.language_models.chat_models import BaseChatModel

from coding_agent.agents.coding.planner import TaskPlanner
from coding_agent.application.runtime import AgentRuntime
from coding_agent.config import Settings
from coding_agent.mcp import McpConfigError, McpManager, load_mcp_config
from coding_agent.observability import JsonlAuditSink, SecretRedactor
from coding_agent.persistence import open_sqlite_persistence
from coding_agent.plugins import load_plugins
from coding_agent.providers import (
    KimiProvider,
    OpenAICompatibleProvider,
    ProviderRegistry,
)
from coding_agent.security import ApprovalProvider
from coding_agent.sessions import SessionService
from coding_agent.tools.builtin import create_coding_tool_registry
from coding_agent.tools.executor import ToolExecutor


@dataclass(frozen=True, slots=True)
class StartupMessage:
    level: str
    text: str


@dataclass(frozen=True, slots=True)
class Application:
    runtime: AgentRuntime
    sessions: SessionService
    providers: ProviderRegistry
    startup_messages: tuple[StartupMessage, ...]
    redactor: SecretRedactor


def _safe_text(value: str, redactor: SecretRedactor, limit: int = 500) -> str:
    return redactor.redact(value).replace("\r", " ").replace("\n", " ")[:limit]


@contextmanager
def create_application(
    settings: Settings,
    *,
    approval_provider: ApprovalProvider | None = None,
    model_override: BaseChatModel | None = None,
    task_planner: TaskPlanner | None = None,
) -> Iterator[Application]:
    if settings.database_path is None or settings.audit_path is None:
        raise RuntimeError("数据库或审计路径未配置")

    tool_registry = create_coding_tool_registry()
    env_values = dotenv_values(settings.workspace / ".env")
    extension_secrets: list[str] = []
    discovered_secrets = [value for value in env_values.values() if isinstance(value, str)]

    plugin_report = None
    if settings.plugins_enabled and settings.plugins_path is not None:
        plugin_report = load_plugins(
            settings.plugins_path,
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
        extra_secrets=[*settings.provider_secrets, *discovered_secrets, *extension_secrets],
    )
    tool_executor = ToolExecutor(
        tool_registry,
        approval_provider=approval_provider,
        audit_sink=JsonlAuditSink(settings.audit_path),
        redactor=redactor,
    )

    providers = ProviderRegistry(settings.model_catalog)
    used_providers = {item.provider_id for item in settings.model_catalog}
    if "kimi" in used_providers:
        assert settings.kimi_api_key is not None
        providers.register(KimiProvider(settings.kimi_api_key, str(settings.kimi_base_url)))
    if "openai-compatible" in used_providers:
        assert settings.openai_compatible_api_key is not None
        assert settings.openai_compatible_base_url is not None
        providers.register(
            OpenAICompatibleProvider(
                settings.openai_compatible_api_key,
                str(settings.openai_compatible_base_url),
            )
        )

    startup_messages: list[StartupMessage] = []
    if plugin_report is not None:
        if plugin_report.loaded:
            startup_messages.append(
                StartupMessage("info", f"已加载插件：{', '.join(plugin_report.loaded)}")
            )
        for issue in plugin_report.issues:
            startup_messages.append(
                StartupMessage(
                    "error",
                    f"插件 {_safe_text(issue.plugin, redactor)} 加载失败："
                    f"{_safe_text(issue.message, redactor)}",
                )
            )
    if mcp_config_error is not None:
        startup_messages.append(
            StartupMessage("error", f"MCP 配置无效：{_safe_text(mcp_config_error, redactor)}")
        )

    mcp_manager = McpManager(settings.workspace)
    mcp_report = (
        mcp_manager.connect(mcp_configuration, tool_registry)
        if mcp_configuration is not None
        else None
    )
    if mcp_report is not None:
        if mcp_report.connected:
            startup_messages.append(
                StartupMessage("info", f"已连接 MCP：{', '.join(mcp_report.connected)}")
            )
        for issue in mcp_report.issues:
            startup_messages.append(
                StartupMessage(
                    "error",
                    f"MCP {issue.server} 连接失败：{_safe_text(issue.message, redactor)}",
                )
            )

    with mcp_manager, open_sqlite_persistence(settings.database_path) as persistence:
        sessions = SessionService(persistence.sessions, settings.default_model_selection)
        sessions.initialize()
        runtime = AgentRuntime(
            settings,
            sessions,
            providers,
            checkpointer=persistence.checkpointer,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            model_override=model_override,
            task_planner=task_planner,
        )
        yield Application(
            runtime,
            sessions,
            providers,
            tuple(startup_messages),
            redactor,
        )


__all__ = ["Application", "StartupMessage", "create_application"]

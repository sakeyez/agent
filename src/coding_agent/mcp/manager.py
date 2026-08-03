"""Long-lived MCP stdio sessions adapted to the agent tool protocol."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool
from pydantic import BaseModel, ConfigDict

from coding_agent.mcp.config import McpConfiguration, McpServerConfig
from coding_agent.tools.contracts import (
    ToolDefinition,
    ToolEffect,
    ToolExecutionContext,
    ToolHandlerOutput,
)
from coding_agent.tools.registry import ToolRegistry
from coding_agent.workspace import WorkspaceContext, WorkspaceGuard


class _McpArguments(BaseModel):
    model_config = ConfigDict(extra="allow")


@dataclass(frozen=True, slots=True)
class McpConnectionIssue:
    server: str
    message: str


@dataclass(frozen=True, slots=True)
class McpConnectionReport:
    connected: tuple[str, ...]
    tools: tuple[str, ...]
    skipped: tuple[str, ...]
    issues: tuple[McpConnectionIssue, ...]


@dataclass(slots=True)
class _Command:
    kind: Literal["call", "close"]
    future: Future[Any]
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None


class _ServerConnection:
    def __init__(
        self,
        name: str,
        config: McpServerConfig,
        workspace: WorkspaceContext,
    ) -> None:
        self.name = name
        self.config = config
        self.workspace = workspace
        self._commands: queue.Queue[_Command] = queue.Queue()
        self._ready: Future[tuple[Tool, ...]] = Future()
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"mcp-{name}",
            daemon=True,
        )

    def start(self) -> tuple[Tool, ...]:
        self._thread.start()
        return self._ready.result(timeout=self.config.startup_timeout_seconds)

    def call(self, tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
        future: Future[CallToolResult] = Future()
        self._commands.put(_Command("call", future, tool_name, arguments))
        return future.result(timeout=self.config.tool_timeout_seconds + 2)

    def close(self) -> None:
        if not self._thread.is_alive():
            return
        future: Future[None] = Future()
        self._commands.put(_Command("close", future))
        try:
            future.result(timeout=5)
        except Exception:
            return
        self._thread.join(timeout=5)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except BaseException as error:
            if not self._ready.done():
                self._ready.set_exception(error)
            self._fail_pending(error)

    async def _run(self) -> None:
        guard = WorkspaceGuard(self.workspace)
        cwd = guard.resolve(self.config.cwd, allow_sensitive=True)
        if not cwd.is_dir():
            raise ValueError("MCP server cwd must be an existing workspace directory")
        parameters = StdioServerParameters(
            command=self.config.command,
            args=list(self.config.args),
            env=self.config.resolved_env(),
            cwd=cwd,
        )
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with stdio_client(parameters, errlog=errlog) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    tools = await self._list_tools(session)
                    self._ready.set_result(tools)
                    while True:
                        command = await asyncio.to_thread(self._commands.get)
                        if command.kind == "close":
                            command.future.set_result(None)
                            return
                        try:
                            result = await session.call_tool(
                                command.tool_name or "",
                                command.arguments,
                                read_timeout_seconds=timedelta(
                                    seconds=self.config.tool_timeout_seconds
                                ),
                            )
                        except BaseException as error:
                            command.future.set_exception(error)
                        else:
                            command.future.set_result(result)

    @staticmethod
    async def _list_tools(session: ClientSession) -> tuple[Tool, ...]:
        tools: list[Tool] = []
        cursor: str | None = None
        while True:
            result = await session.list_tools(cursor)
            tools.extend(result.tools)
            cursor = result.nextCursor
            if cursor is None:
                return tuple(tools)

    def _fail_pending(self, error: BaseException) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if not command.future.done():
                command.future.set_exception(error)


def _identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value)
    normalized = re.sub(r"_+", "_", normalized).strip("_").lower()
    if not normalized:
        normalized = "tool"
    if normalized[0].isdigit():
        normalized = f"tool_{normalized}"
    return normalized


def _effect(tool: Tool) -> ToolEffect:
    if tool.annotations is not None and tool.annotations.readOnlyHint is True:
        return ToolEffect.READ
    if tool.annotations is not None and tool.annotations.destructiveHint is True:
        return ToolEffect.WRITE
    return ToolEffect.EXECUTE


def _render_result(result: CallToolResult) -> str:
    parts: list[str] = []
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
        else:
            serialized = item.model_dump(mode="json", by_alias=True)
            parts.append(json.dumps(serialized, ensure_ascii=False))
    if not parts and result.structuredContent is not None:
        parts.append(json.dumps(result.structuredContent, ensure_ascii=False))
    return "\n".join(parts)


class McpManager:
    """Own MCP sessions and register their tools in a shared registry."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = WorkspaceContext.from_path(workspace)
        self._connections: dict[str, _ServerConnection] = {}

    def __enter__(self) -> "McpManager":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def connect(
        self,
        configuration: McpConfiguration,
        registry: ToolRegistry,
    ) -> McpConnectionReport:
        connected: list[str] = []
        registered: list[str] = []
        skipped: list[str] = []
        issues: list[McpConnectionIssue] = []
        for server_name, server_config in configuration.mcp_servers.items():
            if not server_config.enabled:
                skipped.append(server_name)
                continue
            connection = _ServerConnection(server_name, server_config, self.workspace)
            try:
                remote_tools = connection.start()
                definitions = tuple(
                    self._definition(server_name, server_config, connection, tool)
                    for tool in remote_tools
                )
                names = [tool.name for tool in definitions]
                if len(names) != len(set(names)):
                    raise ValueError("MCP tools collide after name normalization")
                conflicts = sorted(set(names) & registry.names())
                if conflicts:
                    raise ValueError(f"tool name conflict: {', '.join(conflicts)}")
                ToolRegistry(definitions)
                for definition in definitions:
                    registry.register(definition)
                self._connections[server_name] = connection
                connected.append(server_name)
                registered.extend(names)
            except Exception as error:
                connection.close()
                detail = str(error).strip().splitlines()[0] or type(error).__name__
                issues.append(McpConnectionIssue(server_name, detail[:500]))
        return McpConnectionReport(
            tuple(connected), tuple(registered), tuple(skipped), tuple(issues)
        )

    def close(self) -> None:
        for connection in reversed(tuple(self._connections.values())):
            connection.close()
        self._connections.clear()

    @staticmethod
    def _definition(
        server_name: str,
        config: McpServerConfig,
        connection: _ServerConnection,
        remote: Tool,
    ) -> ToolDefinition:
        prefix = _identifier(config.tool_prefix or server_name)
        name = f"mcp_{prefix}_{_identifier(remote.name)}"
        schema = remote.inputSchema or {"type": "object", "properties": {}}
        validator_type = validator_for(schema)
        validator_type.check_schema(schema)
        validator = validator_type(schema)

        def handler(
            arguments: BaseModel,
            _context: ToolExecutionContext,
        ) -> ToolHandlerOutput:
            data = arguments.model_dump(exclude_none=True)
            try:
                validator.validate(data)
            except JsonSchemaValidationError as error:
                path = ".".join(str(part) for part in error.absolute_path)
                location = f" at {path}" if path else ""
                return ToolHandlerOutput(
                    content=f"Invalid MCP tool arguments{location}: {error.message}",
                    is_error=True,
                    error_code="invalid_arguments",
                )
            result = connection.call(remote.name, data)
            return ToolHandlerOutput(
                content=_render_result(result),
                is_error=result.isError,
                error_code="mcp_tool_error" if result.isError else None,
                metadata={"mcp_server": server_name, "mcp_tool": remote.name},
            )

        def summary(_arguments: BaseModel, _context: ToolExecutionContext) -> str:
            return f"MCP {server_name}/{remote.name}"

        return ToolDefinition(
            name=name,
            description=remote.description or remote.title or f"MCP tool {remote.name}",
            args_schema=_McpArguments,
            handler=handler,
            effect=_effect(remote),
            timeout_seconds=config.tool_timeout_seconds + 3,
            summary_builder=summary,
            parameters_schema=schema,
        )

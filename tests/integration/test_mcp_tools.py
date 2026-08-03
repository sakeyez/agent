from __future__ import annotations

import sys
from pathlib import Path

from coding_agent.mcp import McpConfiguration, McpManager
from coding_agent.tools import ToolCall, ToolExecutionContext, ToolExecutor, ToolRegistry
from coding_agent.workspace import WorkspaceContext


_SERVER = """
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("integration-test")

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def echo(text: str) -> str:
    return f"remote:{text}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
"""


def test_discovers_calls_and_closes_stdio_mcp_server(tmp_path: Path) -> None:
    server_file = tmp_path / "server.py"
    server_file.write_text(_SERVER, encoding="utf-8")
    configuration = McpConfiguration.model_validate(
        {
            "mcpServers": {
                "local": {
                    "command": sys.executable,
                    "args": [str(server_file)],
                    "toolTimeoutSeconds": 5,
                }
            }
        }
    )
    registry = ToolRegistry()

    with McpManager(tmp_path) as manager:
        report = manager.connect(configuration, registry)
        executor = ToolExecutor(registry)
        context = ToolExecutionContext(WorkspaceContext.from_path(tmp_path))
        valid = executor.execute(
            ToolCall("call-1", "mcp_local_echo", {"text": "hello"}), context
        )
        invalid = executor.execute(
            ToolCall("call-2", "mcp_local_echo", {}), context
        )

    assert report.connected == ("local",)
    assert report.tools == ("mcp_local_echo",)
    assert report.issues == ()
    assert valid.content == "remote:hello"
    assert valid.metadata == {"mcp_server": "local", "mcp_tool": "echo"}
    assert invalid.error_code == "invalid_arguments"

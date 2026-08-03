"""Built-in tools shipped with the coding agent."""

from coding_agent.tools.builtin.command import command_tools
from coding_agent.tools.builtin.filesystem import filesystem_tools
from coding_agent.tools.builtin.git import git_tools
from coding_agent.tools.builtin.patch import patch_tools
from coding_agent.tools.builtin.search import search_tools
from coding_agent.tools.registry import ToolRegistry


def create_readonly_tool_registry() -> ToolRegistry:
    return ToolRegistry([*filesystem_tools(), *search_tools()])


def create_coding_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            *filesystem_tools(),
            *search_tools(),
            *patch_tools(),
            *command_tools(),
            *git_tools(),
        ]
    )


__all__ = ["create_coding_tool_registry", "create_readonly_tool_registry"]

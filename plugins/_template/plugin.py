from pydantic import BaseModel, ConfigDict

from coding_agent.plugins.api import ToolDefinition, ToolEffect


class EchoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


def _echo(arguments: EchoArguments, _context) -> str:
    return arguments.text


def register() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="plugin_echo",
            description="Echo text through the example local plugin.",
            args_schema=EchoArguments,
            handler=_echo,
            effect=ToolEffect.READ,
        )
    ]

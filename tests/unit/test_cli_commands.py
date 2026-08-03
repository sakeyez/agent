import pytest

from coding_agent.interfaces.cli.commands import (
    CliCommandError,
    CliCommandName,
    parse_command,
)


def test_commands_preserve_free_text_arguments() -> None:
    command = parse_command(" /rename   Design Review ")
    assert command is not None
    assert command.name is CliCommandName.RENAME
    assert command.argument == "Design Review"
    assert parse_command("ordinary text") is None


def test_commands_validate_names_and_arguments() -> None:
    with pytest.raises(CliCommandError, match="缺少参数"):
        parse_command("/use")
    with pytest.raises(CliCommandError, match="不接受参数"):
        parse_command("/status extra")
    with pytest.raises(CliCommandError, match="未知命令"):
        parse_command("/unknown")

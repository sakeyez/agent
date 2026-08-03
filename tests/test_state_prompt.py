from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph.message import add_messages

from coding_agent.prompt import PromptBuilder


def test_add_messages_reducer_preserves_conversation_order() -> None:
    merged = add_messages(
        [HumanMessage(content="first")],
        [AIMessage(content="second")],
    )

    assert [message.content for message in merged] == ["first", "second"]


def test_prompt_contains_identity_workspace_and_history() -> None:
    history = [HumanMessage(content="hello")]
    messages = PromptBuilder().build(
        {"messages": history, "workspace": "C:/work/project", "tool_rounds": 0}
    )

    assert isinstance(messages[0], SystemMessage)
    assert "Kimi" in str(messages[0].content)
    assert "C:/work/project" in str(messages[0].content)
    assert "apply_patch" in str(messages[0].content)
    assert "run_command accepts an argv array" in str(messages[0].content)
    assert messages[1:] == history


def test_prompt_injects_structured_memory_before_recent_history() -> None:
    history = [HumanMessage(content="continue")]
    messages = PromptBuilder(template="Workspace: {workspace}").build(
        {
            "messages": history,
            "workspace": "C:/work/project",
            "tool_rounds": 0,
            "memory": {
                "conversation_summary": "Earlier work updated the parser.",
                "session_decisions": ["Keep the public API compatible."],
                "project_constraints": ["Tests run with pytest."],
            },
        }
    )

    prompt = str(messages[0].content)
    assert "Project constraints:\n- Tests run with pytest." in prompt
    assert "Session decisions:\n- Keep the public API compatible." in prompt
    assert "Earlier conversation summary:\nEarlier work updated the parser." in prompt
    assert messages[1:] == history

"""Minimal START -> model -> END LangGraph."""

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from coding_agent.prompt import PromptBuilder
from coding_agent.state import AgentState


def create_agent_graph(
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
    prompt_builder: PromptBuilder | None = None,
) -> CompiledStateGraph:
    """Compile the stage-one graph with injectable dependencies for testing."""

    prompts = prompt_builder or PromptBuilder()

    def call_model(state: AgentState) -> dict[str, list]:
        response = model.invoke(prompts.build(state))
        return {"messages": [response]}

    builder = StateGraph(AgentState)
    builder.add_node("model", call_model)
    builder.add_edge(START, "model")
    builder.add_edge("model", END)
    return builder.compile(checkpointer=checkpointer)

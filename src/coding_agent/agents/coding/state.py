"""State schema owned by the coding agent graph."""

from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from coding_agent.agents.coding.tasks import TaskPlan


class CodingAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    workspace: str
    tool_rounds: int
    run_id: NotRequired[str]
    termination_reason: NotRequired[str | None]
    request: NotRequired[str | None]
    task_mode: NotRequired[bool]
    task: NotRequired[TaskPlan | None]
    protocol_retries: NotRequired[int]
    control_action: NotRequired[str | None]

"""Agent run lifecycle orchestration independent of user interfaces."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from coding_agent.agents.coding.graph import create_agent_graph
from coding_agent.agents.coding.planner import TaskPlanner
from coding_agent.agents.coding.tasks import TERMINAL_TASK_STATUSES, TaskPlan, TaskStatus
from coding_agent.config import Settings
from coding_agent.providers import ModelSelection, ProviderError, ProviderRegistry
from coding_agent.sessions import RunId, Session, SessionId, SessionService
from coding_agent.tools.executor import ToolExecutor
from coding_agent.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class AssistantText:
    text: str


@dataclass(frozen=True, slots=True)
class TaskTransition:
    previous: TaskPlan | None
    current: TaskPlan


RuntimeEvent = AssistantText | TaskTransition


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in {"text", "text_delta"}:
            text = block.get("text") or block.get("value")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


class AgentRuntime:
    def __init__(
        self,
        settings: Settings,
        sessions: SessionService,
        providers: ProviderRegistry,
        *,
        checkpointer: Any,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        model_override: BaseChatModel | None = None,
        task_planner: TaskPlanner | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.providers = providers
        self.checkpointer = checkpointer
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.model_override = model_override
        self.task_planner = task_planner
        self._graphs: dict[ModelSelection, Any] = {}

    def is_model_available(self, session: Session | None = None) -> bool:
        selected = (session or self.sessions.current()).model
        return self.providers.contains(selected)

    def stream_turn(self, text: str) -> Any:
        session = self.sessions.current()
        graph = self._graph(session.model)
        self.sessions.touch(session.id)
        payload = {
            "messages": [HumanMessage(content=text)],
            "workspace": str(self.settings.workspace),
            "tool_rounds": 0,
            "run_id": RunId(uuid4().hex),
            "termination_reason": None,
            "request": text,
        }
        yield from self._stream(graph, session.id, payload)

    def resume(self) -> Any:
        session = self.sessions.current()
        graph = self._graph(session.model)
        yield from self._stream(graph, session.id, None)

    def should_resume(self, session: Session | None = None) -> bool:
        target = session or self.sessions.current()
        snapshot = self._inspection_graph().get_state(self._config(target.id))
        task = snapshot.values.get("task")
        return bool(
            task is not None
            and task.get("status") not in TERMINAL_TASK_STATUSES
            and snapshot.next
        )

    def task(self, session: Session | None = None) -> TaskPlan | None:
        target = session or self.sessions.current()
        return self._inspection_graph().get_state(self._config(target.id)).values.get("task")

    def has_unfinished_task(self, session: Session | None = None) -> bool:
        task = self.task(session)
        return task is not None and task.get("status") not in TERMINAL_TASK_STATUSES

    def cancel(self, session: Session | None = None) -> bool:
        target = session or self.sessions.current()
        graph = self._inspection_graph()
        current = graph.get_state(self._config(target.id))
        task = current.values.get("task")
        if task is None or task.get("status") in TERMINAL_TASK_STATUSES:
            return False
        cancelled = deepcopy(task)
        cancelled["status"] = TaskStatus.CANCELLED.value
        cancelled["final_summary"] = "Cancelled by user."
        graph.update_state(
            self._config(target.id),
            {"task": cancelled, "control_action": "final", "termination_reason": "cancelled"},
            as_node="task_final",
        )
        self.sessions.touch(target.id)
        return True

    def change_model(self, selection: ModelSelection) -> Session:
        if not self.providers.contains(selection):
            raise ProviderError(f"模型不在 AGENT_MODELS 目录中：{selection.reference}")
        current = self.sessions.current()
        return self.sessions.change_model(
            str(current.id),
            selection,
            has_unfinished_task=self.has_unfinished_task(current),
        )

    def delete_session(self, reference: str) -> tuple[Session, Session]:
        target = self.sessions.resolve(reference)
        replacement = self.sessions.delete(
            reference, has_unfinished_task=self.has_unfinished_task(target)
        )
        return target, replacement

    def _stream(self, graph: Any, session_id: SessionId, payload: dict[str, Any] | None) -> Any:
        previous_task = None
        snapshot = graph.get_state(self._config(session_id))
        if snapshot.values.get("task") is not None:
            previous_task = deepcopy(snapshot.values["task"])
        events = graph.stream(
            payload,
            config=self._config(session_id),
            stream_mode=["messages", "updates"],
        )
        for mode, event in events:
            if mode == "updates":
                for update in event.values():
                    task = update.get("task") if isinstance(update, dict) else None
                    if task is None:
                        continue
                    current = deepcopy(task)
                    yield TaskTransition(previous_task, current)
                    previous_task = current
                continue
            message, metadata = event
            if not isinstance(message, (AIMessage, AIMessageChunk)):
                continue
            if metadata.get("langgraph_node") not in {"chat_model", "chat_final", "task_final"}:
                continue
            text = _content_text(message.content)
            if text:
                yield AssistantText(text)

    def _inspection_graph(self) -> Any:
        current = self.sessions.current()
        selection = current.model if self.providers.contains(current.model) else self.providers.models[0]
        return self._graph(selection)

    def _graph(self, selection: ModelSelection) -> Any:
        graph = self._graphs.get(selection)
        if graph is not None:
            return graph
        if self.model_override is not None and selection == self.settings.default_model_selection:
            model = self.model_override
        else:
            model = self.providers.create_model(selection)
        graph = create_agent_graph(
            model,
            checkpointer=self.checkpointer,
            tool_registry=self.tool_registry,
            tool_executor=self.tool_executor,
            task_planner=self.task_planner,
            context_max_chars=self.settings.context_max_chars,
            context_keep_recent_turns=self.settings.context_keep_recent_turns,
            memory_summary_max_chars=self.settings.memory_summary_max_chars,
        )
        self._graphs[selection] = graph
        return graph

    @staticmethod
    def _config(session_id: SessionId) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": str(session_id)}}


__all__ = ["AgentRuntime", "AssistantText", "RuntimeEvent", "TaskTransition"]

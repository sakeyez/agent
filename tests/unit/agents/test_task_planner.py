import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from coding_agent.agents.coding.planner import ModelTaskPlanner, PlanningError


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "planner-test"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, *args, **kwargs) -> ChatResult:
        response = self.responses[self.index]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


def _plan_call(call_id: str, args: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "submit_task_plan", "args": args}],
    )


def test_model_planner_retries_invalid_structured_plan() -> None:
    model = ScriptedModel(
        responses=[
            _plan_call(
                "invalid",
                {"mode": "task", "objective": "change code", "steps": [], "change_scope": "code"},
            ),
            _plan_call(
                "valid",
                {
                    "mode": "task",
                    "objective": "change code",
                    "steps": ["inspect", "implement"],
                    "change_scope": "code",
                },
            ),
        ]
    )

    decision = ModelTaskPlanner(model).plan("change code", "C:/work")

    assert decision.steps == ["inspect", "implement"]
    assert model.index == 2


def test_model_planner_fails_after_two_invalid_responses() -> None:
    model = ScriptedModel(responses=[AIMessage(content="invalid"), AIMessage(content="invalid")])

    with pytest.raises(PlanningError):
        ModelTaskPlanner(model).plan("change code", "C:/work")

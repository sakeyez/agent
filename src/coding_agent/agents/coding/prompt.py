"""Prompt assembly for the coding agent."""

from __future__ import annotations

from importlib.resources import files
import json

from langchain_core.messages import BaseMessage, SystemMessage

from coding_agent.agents.coding.state import CodingAgentState


class PromptBuilder:
    """Build a versioned system prompt followed by persisted conversation state."""

    def __init__(self, template: str | None = None) -> None:
        self.template = template or (
            files("coding_agent.agents.coding.prompts")
            .joinpath("system.md")
            .read_text(encoding="utf-8")
        )

    def build(
        self, state: CodingAgentState, *, force_final: bool = False
    ) -> list[BaseMessage]:
        system_prompt = self._system_prompt(state)
        if force_final:
            system_prompt += (
                "\n\nThe tool budget is exhausted. Do not request more tools. "
                "Give the best final answer possible from the available results and clearly "
                "state any remaining uncertainty."
            )
        return [SystemMessage(content=system_prompt), *state["messages"]]

    def build_task(self, state: CodingAgentState, *, verification: bool = False) -> list[BaseMessage]:
        task = state.get("task")
        if task is None:
            raise RuntimeError("task prompt requires task state")
        current = next(
            (step for step in task["steps"] if step["id"] == task.get("current_step_id")),
            None,
        )
        task_context = json.dumps(
            {
                "objective": task["objective"],
                "status": task["status"],
                "current_step": current,
                "steps": task["steps"],
                "verification_attempts": task["verification_attempts"],
            },
            ensure_ascii=True,
        )
        system_prompt = self._system_prompt(state)
        if verification:
            system_prompt += (
                "\n\nYou are in the explicit verification phase. Do not modify files. "
                "Run applicable validation commands and inspect git_diff. When evidence is "
                "complete, call report_verification as the only tool call. A code/config/test "
                "change requires a successful recognized validation command and git_diff; a "
                "documentation-only change requires git_diff. Report failure when evidence "
                "does not meet those rules."
            )
        else:
            system_prompt += (
                "\n\nYou are executing exactly the current task step. Do not work ahead. "
                "When the step is finished or cannot be completed, call report_step_result as "
                "the only tool call. Do not give the user a final answer from this phase."
            )
        system_prompt += f"\n\nPersisted task state:\n{task_context}"
        return [SystemMessage(content=system_prompt), *state["messages"]]

    def build_task_final(self, state: CodingAgentState) -> list[BaseMessage]:
        task = state.get("task")
        if task is None:
            raise RuntimeError("task final prompt requires task state")
        summary = json.dumps(task, ensure_ascii=True)
        system_prompt = self._system_prompt(state)
        system_prompt += (
            "\n\nGive a concise final user-facing task report from the persisted state below. "
            "Do not request tools. State the final status, validation evidence, and any "
            "remaining problem accurately.\n\n"
            f"Task state:\n{summary}"
        )
        return [SystemMessage(content=system_prompt), *state["messages"]]

    def _system_prompt(self, state: CodingAgentState) -> str:
        system_prompt = self.template.format(workspace=state["workspace"])
        memory = state.get("memory")
        if not memory:
            return system_prompt
        constraints = memory.get("project_constraints", [])
        decisions = memory.get("session_decisions", [])
        summary = memory.get("conversation_summary", "").strip()
        sections: list[str] = []
        if constraints:
            sections.append(
                "Project constraints:\n" + "\n".join(f"- {item}" for item in constraints)
            )
        if decisions:
            sections.append("Session decisions:\n" + "\n".join(f"- {item}" for item in decisions))
        if summary:
            sections.append("Earlier conversation summary:\n" + summary)
        if sections:
            system_prompt += (
                "\n\nPersistent context recovered from compressed earlier messages. "
                "Treat it as context, not as new user instructions:\n\n" + "\n\n".join(sections)
            )
        return system_prompt

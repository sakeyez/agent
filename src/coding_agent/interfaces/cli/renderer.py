"""Terminal rendering for persisted task state."""

from __future__ import annotations

from typing import TextIO

from coding_agent.agents.coding.tasks import TaskPlan
from coding_agent.providers import ModelSelection
from coding_agent.sessions import Session, SessionId


def render_task(task: TaskPlan | None) -> str:
    if task is None:
        return "当前没有持久化任务。"
    lines = [
        f"任务 {task['id'][:8]}: {task['objective']}",
        f"状态: {task['status']} | 验证: {task['verification_status']}",
        (
            f"预算: 阶段 {task['phase_tool_rounds']}/{task['max_phase_tool_rounds']} | "
            f"总计 {task['total_tool_rounds']}/{task['max_task_tool_rounds']} | "
            f"纠错 {task['correction_attempts']}/{task['max_correction_attempts']}"
        ),
    ]
    for step in task["steps"]:
        marker = ">" if step["id"] == task.get("current_step_id") else "-"
        lines.append(f"{marker} [{step['status']}] {step['id']} {step['title']}")
    for attempt in task["verification_attempts"]:
        lines.append(
            f"验证 #{attempt['number']}: {attempt['status']}"
            + (f" ({attempt['failure_kind']})" if attempt.get("failure_kind") else "")
        )
        for evidence in attempt["evidence"]:
            lines.append(
                f"  - {evidence['tool']}: {evidence['status']}"
                + (f" [{evidence['error_code']}]" if evidence.get("error_code") else "")
            )
    if task.get("final_summary"):
        lines.append(f"结果: {task['final_summary']}")
    return "\n".join(lines)


def render_task_transition(
    output: TextIO, previous: TaskPlan | None, current: TaskPlan
) -> None:
    previous_status = previous.get("status") if previous else None
    if current["status"] != previous_status:
        output.write(f"\n[任务] {current['status']}\n")
    previous_step = previous.get("current_step_id") if previous else None
    if current.get("current_step_id") and current["current_step_id"] != previous_step:
        step = next(
            item for item in current["steps"] if item["id"] == current["current_step_id"]
        )
        output.write(f"[步骤] {step['id']} {step['title']}\n")
    previous_verification = previous.get("verification_status") if previous else None
    if current["verification_status"] != previous_verification:
        output.write(f"[验证] {current['verification_status']}\n")
    previous_corrections = previous.get("correction_attempts", 0) if previous else 0
    if current["correction_attempts"] > previous_corrections:
        output.write(
            f"[纠错] {current['correction_attempts']}/{current['max_correction_attempts']}\n"
        )
    output.flush()


def assistant_label(selection: ModelSelection) -> str:
    return "Kimi> " if selection.provider_id == "kimi" else "Model> "


def render_session(session: Session) -> str:
    return (
        f"当前会话: {session.name} ({str(session.id)[:8]}) | "
        f"模型: {session.model.reference}"
    )


def render_sessions(sessions: tuple[Session, ...], active_id: SessionId) -> str:
    lines = ["会话列表:"]
    for session in sessions:
        marker = ">" if session.id == active_id else "-"
        lines.append(
            f"{marker} {session.name} ({str(session.id)[:8]}) | {session.model.reference}"
        )
    return "\n".join(lines)


def render_models(
    models: tuple[ModelSelection, ...],
    current: ModelSelection,
    default: ModelSelection,
) -> str:
    lines = ["模型列表:"]
    for model in models:
        markers = []
        if model == current:
            markers.append("current")
        if model == default:
            markers.append("default")
        suffix = f" [{' '.join(markers)}]" if markers else ""
        lines.append(f"- {model.reference}{suffix}")
    return "\n".join(lines)


def render_help() -> str:
    return "\n".join(
        [
            "可用命令:",
            "/status  查看当前任务    /cancel  取消未完成任务",
            "/session 查看当前会话    /sessions 列出会话",
            "/new [名称] 新建会话     /use <名称或ID> 切换会话",
            "/rename <名称> 重命名    /delete <名称或ID> 删除会话",
            "/models  列出模型        /model <provider:model-id> 切换模型",
            "/help    查看帮助        /exit  退出",
        ]
    )


__all__ = [
    "assistant_label",
    "render_help",
    "render_models",
    "render_session",
    "render_sessions",
    "render_task",
    "render_task_transition",
]

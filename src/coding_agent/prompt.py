"""System prompt construction."""

from langchain_core.messages import BaseMessage, SystemMessage

from coding_agent.state import AgentState


class PromptBuilder:
    """Build the model input from the current agent state."""

    def build(self, state: AgentState) -> list[BaseMessage]:
        workspace = state["workspace"]
        system_prompt = f"""你是一个最小化的 Kimi 编程对话 Agent。
当前唯一工作区：{workspace}

基本规则：
- 回答应清晰、准确、简洁，并在信息不足时明确说明假设。
- 只能围绕当前工作区提供帮助，不要声称访问了工作区之外的内容。
- 当前版本没有工具调用能力；不要声称已经读取、修改或运行了任何内容。
- 不确定时如实说明，不要编造执行结果或外部事实。
"""
        return [SystemMessage(content=system_prompt), *state["messages"]]

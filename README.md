# Minimal Kimi Coding Agent

这是一个基于 LangGraph 和 Kimi 的最小命令行对话 Agent。它面向单用户、单工作区场景，通过 SQLite 保存对话检查点，并在下次启动时自动继续同一段对话。

## 第一阶段范围

- Python 3.11+
- 固定使用 Kimi 的 OpenAI Chat Completions 兼容接口
- 单用户 CLI
- 单工作区、单个 `default` 会话
- LangGraph 流程固定为 `START -> model -> END`

第一版不支持多模型、网页界面、MCP、工具调用、插件加载或子 Agent。`plugins` 目录和 `tool_rounds` 状态仅为后续阶段预留。

## 安装

推荐使用 [uv](https://docs.astral.sh/uv/)：

```powershell
uv sync
```

项目也可以作为普通 Python 包安装：

```powershell
python -m pip install -e .
```

## 配置

复制 `.env.example` 为 `.env`，至少填写以下两项：

```dotenv
KIMI_API_KEY=your-moonshot-api-key
KIMI_MODEL=your-kimi-model-id
KIMI_BASE_URL=https://api.moonshot.cn/v1
```

可选配置：

```dotenv
AGENT_WORKSPACE=.
AGENT_DB_PATH=.coding_agent/checkpoints.sqlite3
```

`AGENT_WORKSPACE` 默认为启动命令时的当前目录，并且必须已经存在。相对的 `AGENT_DB_PATH` 从工作区解析；默认数据库位于 `<workspace>/.coding_agent/checkpoints.sqlite3`。API Key 使用 Pydantic `SecretStr` 保存，不会出现在配置错误或终端日志中。

## 使用

```powershell
uv run python -m coding_agent.cli
```

安装后也可以运行：

```powershell
coding-agent
```

输入普通文本即可对话。输入 `/exit`、按 `Ctrl+C` 或发送 EOF 会正常退出。空输入会被忽略。模型输出按 token 流式显示；单次请求失败时会显示简短错误并继续接受输入。

CLI 始终使用 SQLite 中的 `default` 会话。重新启动不会回放旧文本，但旧消息会继续作为模型上下文。第一阶段不提供新建或清空会话的命令；如需完全重置，可以在 CLI 退出后手动删除检查点数据库。

## 工作原理

1. `config.py` 从环境变量和 `.env` 加载并验证 Kimi、工作区和数据库配置。
2. `providers/kimi.py` 创建唯一的 Kimi `ChatOpenAI` 客户端。
3. `PromptBuilder` 将 Agent 身份、工作区和行为边界放入 System Prompt。
4. LangGraph 将用户消息合并进 `AgentState`，调用模型后把回复写回状态。
5. SQLite checkpointer 保存 `default` 会话，CLI 只把当前生成的模型 token 输出到终端。

## 测试

常规测试完全使用假模型，不连接网络：

```powershell
uv run pytest
```

真实 Kimi smoke test 默认跳过。确认 `.env` 中配置的是官方 Moonshot 凭据后显式运行：

```powershell
$env:RUN_KIMI_LIVE_TEST="1"
uv run pytest -m live tests/test_kimi_live.py
```

该命令会产生一次真实 API 请求，可能计费。

## 目录结构

```text
src/coding_agent/
  cli.py              # 输入循环、流式输出和异常边界
  config.py           # 环境配置与校验
  graph.py            # START -> model -> END
  prompt.py           # System Prompt
  state.py            # AgentState
  providers/kimi.py   # 固定 Kimi 客户端
tests/                # 自动化测试
plugins/              # 后续阶段预留
```

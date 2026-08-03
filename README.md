# Kimi Coding Agent

这是一个基于 LangGraph 和 Kimi 的命令行 Coding Agent。它面向单用户、单工作区场景，能够检查代码、应用补丁、运行验证命令、查看 Git 差异，并通过 SQLite 保存对话、工具调用和显式任务计划检查点。

## 当前范围

- Python 3.11+
- 固定使用 Kimi 的 OpenAI Chat Completions 兼容接口
- 单用户 CLI
- 单工作区、单个 `default` 会话
- LangGraph 模型/工具推理循环，默认最多 8 个工具轮次
- 复杂修改请求的持久化规划、步骤进度、显式验证和最多 2 轮自动纠错
- 阶段 8 轮、任务总计 24 轮的双重工具预算
- 自动上下文压缩，以及按会话持久化的决策、项目约束和滚动摘要
- 工作区文件列表、UTF-8 文件读取、字面量搜索和 Git 差异检查
- 逐次批准的 unified diff 修改和分级批准的单程序命令执行
- 路径越界、符号链接逃逸、危险命令和敏感凭据防护
- 工具参数校验、独立超时、64 KiB 头尾截断、结果脱敏和 JSONL 审计
- 显式启用的本地 Python 插件发现、兼容校验、故障隔离和工具注册
- 显式启用的 MCP stdio 长期会话、工具发现、JSON Schema 校验和调用适配

当前版本不支持完整 Shell 字符串、多模型、网页界面、MCP HTTP/SSE、MCP resources/prompts 或子 Agent。`run_command` 只接受参数数组并使用 `shell=False`；Shell 解释器和明确危险的命令会被拒绝。

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
AGENT_AUDIT_PATH=.coding_agent/audit.jsonl
AGENT_CONTEXT_MAX_CHARS=80000
AGENT_CONTEXT_KEEP_RECENT_TURNS=4
AGENT_MEMORY_SUMMARY_MAX_CHARS=12000
AGENT_ENABLE_PLUGINS=false
AGENT_PLUGINS_PATH=plugins
AGENT_ENABLED_PLUGINS=my-plugin,other-plugin
AGENT_ENABLE_MCP=false
AGENT_MCP_CONFIG_PATH=.coding_agent/mcp.json
```

`AGENT_WORKSPACE` 默认为启动命令时的当前目录，并且必须已经存在。相对的数据库和审计路径均从工作区解析；默认位于 `<workspace>/.coding_agent/`。上下文达到近似序列化字符阈值后，系统保留最近若干轮，将更早消息压缩为滚动摘要、会话决策和项目约束；这些记忆随 checkpoint 持久化。API Key 使用 Pydantic `SecretStr` 保存，并与 `.env` 中的值一起从工具输出和审计摘要中脱敏。

插件与 MCP 均默认关闭。插件协议、模板、MCP JSON 示例、工具命名和安全边界见 [插件与 MCP 扩展系统](docs/extensions.md)。

## 使用

```powershell
uv run python -m coding_agent.cli
```

安装后也可以运行：

```powershell
coding-agent
```

输入普通文本即可对话。普通问答保持只读；复杂修改请求会自动建立任务并显示规划、执行、验证和纠错状态。输入 `/status` 可查看当前或最近任务，输入 `/cancel` 可取消中断后的未完成任务，输入 `/exit`、在输入提示处按 `Ctrl+C` 或发送 EOF 会正常退出。执行期间按 `Ctrl+C` 会保留最近 checkpoint 并返回输入提示。

CLI 始终使用 SQLite 中的 `default` 会话。重新启动不会回放旧文本，但最近消息和压缩后的长期记忆会继续作为模型上下文；非终态任务会从最近节点自动续跑。当前仅保留当前或最近任务，不提供任务历史、新建或清空会话命令；如需完全重置，可以在 CLI 退出后手动删除检查点数据库。

## 工作原理

1. `config.py` 从环境变量和 `.env` 加载并验证 Kimi、工作区和数据库配置。
2. `providers/kimi.py` 创建唯一的 Kimi `ChatOpenAI` 客户端。
3. 上下文管理器在达到阈值时压缩旧消息；`PromptBuilder` 将 Agent 身份、工作区、行为边界和结构化长期记忆放入 System Prompt。
4. 请求入口自动区分普通只读聊天与复杂任务；复杂任务通过结构化 schema 生成 1-8 个持久化步骤。
5. 任务按步骤执行，并显式记录 `planning`、`executing`、`verifying`、`correcting`、`completed`、`failed` 或 `cancelled` 状态。
6. 统一执行器依次完成参数校验、模式允许列表、策略判定、必要的用户批准、预执行审计、执行、脱敏和截断。
7. 验证阶段禁止修改；代码类任务必须同时具备成功的测试/检查命令和 `git_diff` 复查证据，文档类任务必须完成 diff 复查。
8. 验证非零退出会触发最多 2 轮纠错；依赖缺失、策略拒绝、批准拒绝或预算耗尽直接失败并保留证据。
9. SQLite checkpointer 保存 `default` 会话、当前任务、近期消息和长期记忆；JSONL 审计保存脱敏后的工具决策和结果，不保存补丁正文或文件内容。

## 内置工具

| 工具 | 用途 | 默认策略 |
| --- | --- | --- |
| `list_files` | 列出工作区文件 | 自动执行 |
| `read_file` | 分段读取 UTF-8 文本 | 自动执行 |
| `search_text` | 字面量文本搜索 | 自动执行 |
| `git_diff` | 查看状态及 staged/unstaged diff | 自动执行 |
| `apply_patch` | 校验并应用 unified diff | 每次询问 |
| `run_command` | 以参数数组运行单个程序 | 验证命令自动，其余询问或拒绝 |

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
  graph.py            # 新 Agent 图的兼容入口
  prompt.py           # PromptBuilder 兼容入口
  state.py            # AgentState 兼容入口
  agents/coding/      # 状态、节点、路由、推理循环和 Prompt
  tools/              # 工具契约、注册表、执行器和六个内置工具
  security/           # 操作策略和批准协议
  observability/      # 脱敏与 JSONL 审计
  workspace/          # 工作区上下文、路径边界和敏感文件防护
  providers/kimi.py   # 固定 Kimi 客户端
tests/                # 自动化测试
  plugins/              # 本地插件发现、清单校验和隔离加载
  mcp/                  # MCP stdio 配置、会话管理和工具适配
```

## 架构

编码工具循环已经迁移到分层目录，根目录模块继续提供兼容导入。模块职责、依赖方向和后续迁移顺序见 [docs/architecture.md](docs/architecture.md)。

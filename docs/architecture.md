# Coding Agent 架构设计

## 目标

在保留现有 CLI 与 SQLite 会话兼容性的前提下，为工具调用、工作区访问、批准、审计、会话管理和后续扩展建立清晰边界。编码工具循环位于 `agents/coding`、`tools`、`workspace` 和 `security`；根目录的 `cli.py`、`graph.py`、`prompt.py` 和 `state.py` 继续作为兼容入口。

## 分层与依赖方向

```text
interfaces -> application -> agents -> capability contracts
                         |-> tools -> workspace/security
                         |-> sessions -> persistence
                         |-> providers
                         |-> plugins

observability <- all runtime layers (only through event/logging contracts)
```

- `interfaces`：处理 CLI 输入输出、命令解析和终端渲染，不包含 Agent 决策。
- `application`：组装配置与依赖，管理一次 Agent 运行的生命周期。
- `agents`：定义 LangGraph 状态、节点、路由和 Prompt，只依赖抽象能力。
- `providers`：封装模型厂商差异，并通过注册表选择模型实现。
- `tools`：定义工具协议、注册、调用和结果标准化；内置工具放在 `builtin`。
- `workspace`：解析工作区上下文、限制路径边界，为文件和搜索工具提供安全入口。
- `security`：表达操作策略和批准流程，不直接执行工具。
- `sessions`：表达会话模型和用例，不了解 SQLite 等存储细节。
- `persistence`：实现检查点和会话仓储等基础设施适配器。
- `plugins`：发现并加载顶层 `plugins/` 中的扩展，校验插件清单。
- `mcp`：管理显式启用的 stdio 服务器生命周期，并将远端工具适配到公开工具协议。
- `observability`：定义运行事件和日志配置，不承载业务分支。

依赖应从外向内；Agent 节点不直接访问 SQLite、终端或具体模型厂商。跨边界协作通过构造参数和协议完成，避免使用全局单例。

## 目录结构

```text
src/coding_agent/
  application/            # 启动组装与运行生命周期
  agents/coding/          # Coding Agent 的图、节点、路由、状态和 Prompt
  interfaces/cli/         # CLI 适配层
  providers/              # LLM Provider 与注册机制
  tools/builtin/          # 工具协议、执行器和内置工具
  workspace/              # 工作区上下文、路径与边界保护
  security/               # 策略与人工批准
  sessions/               # 会话领域模型与服务
  persistence/            # 检查点和仓储实现
  plugins/                # 插件发现、清单和加载
  mcp/                    # MCP 配置、stdio 会话和工具适配
  observability/          # 事件与日志
plugins/
  _template/              # 外部插件模板
tests/
  unit/                   # 单模块行为
  integration/            # 多模块契约与基础设施
  e2e/                    # 用户入口到 Agent 输出
  fixtures/               # 公共测试数据
```

顶层 `plugins/` 是用户可安装扩展的位置；`src/coding_agent/plugins/` 是负责发现与加载扩展的框架代码，两者职责不同。

## 核心运行流程

```text
CLI input
  -> application runtime
  -> coding agent graph (new run_id per user turn)
  -> context threshold -> compact old messages -> rolling summary + durable decisions/constraints
  -> intake
     -> ordinary chat -> read-only model/tool loop
     -> complex task -> structured plan -> persisted steps
        -> execute one step at a time
        -> verify with command + diff evidence
        -> validation failure -> correction plan -> re-verify (max 2)
        -> completed / failed / cancelled
  -> tool executor -> mode allow-list -> argument validation -> operation policy
                      -> optional CLI approval -> pre-execution audit
                      -> workspace guard -> built-in tool
                      -> redaction/truncation -> completion audit -> tool result
  -> phase budget exhausted -> persisted failure + final model response
  -> checkpoint/session/task/memory persistence
  -> CLI renderer
```

工具执行必须经过统一执行器。执行器负责参数校验、策略、批准、审计、独立超时、输出脱敏、头尾截断和异常标准化，路径访问统一经过工作区防护。副作用工具在预执行审计失败时关闭执行；具体工具不得绕过执行器直接运行。

## 关键约束

1. `agents` 不导入 `interfaces` 或具体 `persistence` 实现。
2. 路径类工具只能通过 `workspace` 层解析路径，解析后的目标必须位于工作区边界内。
3. `apply_patch` 每次要求批准；`run_command` 仅接受参数数组，并按自动、批准、拒绝三级策略执行。
4. Provider 返回统一的模型能力，不把 Kimi 特有字段传播到 Agent 状态。
5. 插件只能通过公开工具协议注册能力，不能修改运行时内部注册表或会话数据库。
6. 会话 ID、运行 ID 和工具调用 ID 分开建模，便于恢复、审计和调试。
7. 运行事件不得包含 API Key、完整环境变量或未经处理的敏感工具输出。
8. 普通聊天限制为 8 个工具轮次；任务每阶段限制 8 轮、总计限制 24 轮，并最多纠错 2 次。只读和补丁工具通常限制为 10-15 秒，命令参数上限 120 秒，单条结果限制为 64 KiB；终止原因、计划、步骤和验证证据保存在 Agent 状态。
9. `.env`、私钥和常见凭据文件不得进入模型上下文；`.env.example` 可以正常读取。
10. 审计仅记录脱敏摘要、策略、批准、耗时和状态，不记录补丁正文、文件内容或原始敏感参数。
11. 当前任务使用结构化 `TaskPlan` 独立持久化；压缩只移除较早消息，长期保留会话决策、项目约束和滚动摘要。压缩失败时不得删除原消息。
12. 插件和 MCP 必须显式启用；MCP 未声明只读的工具按执行类操作处理，不能绕过批准和审计。

## 现有模块的渐进迁移

| 当前模块 | 目标位置 | 迁移时机 |
| --- | --- | --- |
| `cli.py` | `interfaces/cli/` + `application/` | 已迁移，保留兼容入口 |
| `graph.py` | `agents/coding/graph.py` | 已迁移，保留兼容入口 |
| `state.py` | `agents/coding/state.py` | 已迁移，保留兼容入口 |
| `prompt.py` | `agents/coding/prompt.py` | 已迁移，保留兼容入口 |
| `providers/kimi.py` | 保持原位 | 已接入 Provider 协议和注册表 |
| SQLite 创建逻辑 | `persistence/checkpoints.py` | 已迁移并支持多会话元数据 |

迁移期间，根目录旧模块应作为兼容入口转发到新模块，直到调用方和测试全部切换，再删除兼容层。

## 测试边界

- 单元测试覆盖路由、策略、批准、审计、路径保护、工具参数和结果标准化。
- 集成测试覆盖模型假实现与六工具循环、SQLite 恢复和非交互批准边界。
- 端到端测试从 CLI 输入到流式输出，默认不访问真实网络和用户工作区。
- 真实模型与危险工具测试必须显式启用，并使用隔离的临时工作区。

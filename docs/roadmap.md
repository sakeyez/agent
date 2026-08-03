# Coding Agent 路线图

## 已完成

- Kimi Provider 配置、流式输出、错误脱敏和 SQLite 会话检查点。
- 有界 LangGraph 模型/工具循环，默认最多 8 个工具轮次。
- 统一工具契约、注册表、参数校验、独立超时、结构化结果和 64 KiB 头尾截断。
- `list_files`、`read_file`、`search_text`、`apply_patch`、`run_command`、`git_diff` 编码闭环。
- 工作区路径、敏感文件、符号链接、危险命令、批准和 JSONL 审计边界。
- 假模型单元/集成/E2E 测试和显式启用的真实 Kimi 冒烟测试。
- 复杂任务计划、步骤进度、验证证据、自动纠错和 CLI 状态的 SQLite 持久化恢复。
- 有界上下文与长期记忆，分离当前任务、会话决策、项目约束和可丢弃日志。
- 本地插件清单、无导入发现、兼容性校验、启停配置、原子注册和异常隔离。
- MCP stdio 长期会话、工具发现与调用、JSON Schema 校验、annotations 风险映射和隔离关闭。

## 下一阶段

1. 将 CLI、应用组装、会话服务和结构化终端渲染迁移到已经预留的分层模块。
2. 扩展 MCP HTTP/SSE、resources、prompts、OAuth 和 capability negotiation。
3. 多模型、网页界面和子 Agent 保持后续独立里程碑。

## 上线约束

- 完整 Shell 字符串、永久授权和危险 Git 命令在安全模型重新设计前保持禁用。
- 插件不得绕过统一执行器、工作区防护、批准或审计。
- 新增副作用工具必须先定义策略、失败模式、脱敏规则和隔离工作区测试。

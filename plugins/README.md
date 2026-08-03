# Plugins

该目录用于存放用户安装的本地 Python 插件。插件需要包含有效的 `plugin.toml`，并通过
`coding_agent.plugins.api` 返回工具定义。复制 `_template/`、重命名目录并修改清单即可开始。

插件默认不加载。设置 `AGENT_ENABLE_PLUGINS=true` 后才会执行插件入口；可用
`AGENT_ENABLED_PLUGINS=name-a,name-b` 设置允许列表。插件工具仍由统一执行器处理参数校验、
批准、审计、超时和结果脱敏。

完整协议和 MCP 配置见 [扩展系统文档](../docs/extensions.md)。

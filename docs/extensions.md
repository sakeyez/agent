# 插件与 MCP 扩展系统

## 安全边界

本地插件和 MCP 默认关闭。插件入口是受信任的 Python 代码，MCP `command` 是受信任的本地
进程配置；只有明确设置 `AGENT_ENABLE_PLUGINS=true` 或 `AGENT_ENABLE_MCP=true` 才会启动。
扩展提供的工具不能直接获得注册表、批准器、审计存储或会话数据库，所有调用仍经过统一
`ToolExecutor`。

只读工具自动执行；写入和一般执行工具要求逐次批准。MCP 工具根据标准 annotations 映射：
`readOnlyHint=true` 为只读，`destructiveHint=true` 为写入，未声明时按执行类工具处理并要求批准。

## 本地插件

插件位于 `plugins/<name>/`，目录名必须与清单名称一致：

```toml
schema_version = 1
name = "my-plugin"
version = "0.1.0"
description = "Project-specific tools."
entrypoint = "plugin:register"
requires_agent = ">=0.1,<0.2"
enabled = true
```

入口函数不接收运行时对象，并返回 `ToolDefinition` 可迭代对象：

```python
from coding_agent.plugins.api import ToolDefinition, ToolEffect

def register():
    return [ToolDefinition(..., effect=ToolEffect.READ)]
```

加载器先验证整个插件的工具集合，再原子注册。单个插件清单错误、导入异常、重复工具名或
API 版本不兼容只会生成一条隔离错误，不影响内置工具和其他插件。

配置：

```dotenv
AGENT_ENABLE_PLUGINS=true
AGENT_PLUGINS_PATH=plugins
AGENT_ENABLED_PLUGINS=my-plugin,other-plugin
```

省略 `AGENT_ENABLED_PLUGINS` 时加载目录中清单 `enabled=true` 的所有插件。

## MCP stdio

默认配置文件是 `<workspace>/.coding_agent/mcp.json`：

```json
{
  "mcpServers": {
    "project": {
      "command": "uv",
      "args": ["run", "python", "tools/mcp_server.py"],
      "cwd": ".",
      "env": {
        "SERVICE_TOKEN": "${SERVICE_TOKEN}"
      },
      "enabled": true,
      "toolPrefix": "project",
      "startupTimeoutSeconds": 15,
      "toolTimeoutSeconds": 30
    }
  }
}
```

`cwd` 必须是工作区内已经存在的目录。`${NAME}` 从 Agent 进程环境解析，缺失时该服务器会
被隔离并报告错误。远端工具在模型中的名称为 `mcp_<prefix>_<tool>`，其中非法标点会转成
下划线。每个服务器保持一个长期会话，支持分页 `tools/list` 和 `tools/call`；服务器之间的
连接、Schema 或名称冲突互不影响。

启用：

```dotenv
AGENT_ENABLE_MCP=true
AGENT_MCP_CONFIG_PATH=.coding_agent/mcp.json
```

当前首版只支持本地 `stdio` 传输。HTTP/SSE、resources、prompts、sampling 和 OAuth 尚未接入。

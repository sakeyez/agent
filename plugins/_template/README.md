# Plugin Template

```text
plugin-name/
  plugin.toml          # 名称、版本、Agent API 兼容范围和入口
  plugin.py            # register() 返回 ToolDefinition 列表
  README.md            # 使用方式、权限需求和风险说明
  tests/               # 插件自己的自动化测试
```

复制模板后，必须让目录名与 `plugin.toml` 的 `name` 一致。插件只应从
`coding_agent.plugins.api` 导入公开协议，不应访问运行时注册表、会话数据库或内部模块。

# Plugin Template

```text
plugin-name/
  plugin.toml          # 名称、版本、Agent 兼容范围和入口
  README.md            # 使用方式、权限需求和风险说明
  src/                 # 插件实现
  tests/               # 插件自己的自动化测试
```

插件应通过公开协议注册工具，不应导入 `coding_agent` 的内部运行模块。清单格式将在插件加载器实现时确定，因此此模板暂不提供有效 `plugin.toml`。

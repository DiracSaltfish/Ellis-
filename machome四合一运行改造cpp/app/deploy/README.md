# LaunchAgent 模板

`com.ellis.machome-hub-agent.plist.in` 只用于新的 Machome Hub Agent。安装脚本会对
路径做 XML 转义，再替换以下占位符：

- `@LABEL@`
- `@AGENT_PROGRAM@`
- `@CONFIG_PATH@`
- `@WORKING_DIRECTORY@`
- `@STDOUT_LOG@`
- `@STDERR_LOG@`

不要把四套旧业务的 plist 内容合并进这个模板。Hub 对旧业务的观测和控制边界由
`modules.json` 的 `ownership` 与 `control_enabled` 决定；plist 只负责维持 Hub Agent
自身运行。

模板启用 `RunAtLoad`、`KeepAlive` 和 30 秒节流。`install.sh` 在实际加载前会运行
`machome-hub-agent --check-config`，以避免用无效配置启动反复重试。安装默认只落盘
plist；只有显式传入 `--load-agent` 才会操作这个 Hub label。

`com.ellis.machome-ibkr-bridge.plist.in` 是可选原生 IBKR 行情 bridge 的独立模板，由
`scripts/install-ibkr-bridge.sh` 渲染。它有自己的 label、配置和日志；默认同样只预演，
`--apply` 只写文件，只有再加 `--load-bridge` 才加载或重载 bridge。该流程不操作任何旧
Python uploader 的 plist 或进程。

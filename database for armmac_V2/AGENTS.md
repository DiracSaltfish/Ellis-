# macOS ARM64 适配工程 Agent 规则

后续 Agent 开始工作前必须完整阅读：

1. `docs/DEVELOPMENT_STATUS_AND_HANDOFF.md`
2. `docs/API_STATUS.md`
3. `docs/AGENT_PARITY_WORKFLOW.md`
4. 本次接口对应的 `docs/evidence/*.md`、两份手册页面和 V1.0.8 头文件

只在本目录继续 Mac 适配开发；旧 `数据库桥接` 目录作为历史来源，只读，不再把新实现或
临时文件写回其中。一个任务只验证一个接口或一个明确限定的枚举/市场/周期。

禁止事项：

- 不得把登录成功、请求返回 0 或收到任意帧等同于接口已对齐。
- 不得把未验证枚举原样发送给服务端；未知分支必须显式失败。
- 不得把账号、密码、token、MAC、原始行情、完整抓包或会话二进制写入仓库、日志或 fixture。
- 不得把 `native/experimental` 的本地状态机成功称为服务端鉴权成功。
- 不得执行密码修改等写操作，除非用户对该具体写操作另行明确授权。

每项开发必须提交结构/协议测试、Linux 官方 SDK 的脱敏 oracle 摘要、Mac 同参摘要和
`docs/evidence/<接口>.md`。状态由验收者按工作流提升；没有 Linux/Mac 同参证据不得标为
`LIVE_ALIGNED`，没有重连、恢复、资源和持续压力验收不得标为 `PILOT_READY`。

常用验收命令：

```bash
python -m unittest discover -s tests -v
python -m compileall -q src/python examples tools
```

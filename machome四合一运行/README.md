# Machome 四合一运行中心

这里已经不是只读原型，而是一套可构建、可打包、可回滚的 **Qt 6 / C++ 完整迁移候选版**。它把四项业务放进一个统一桌面控制面，同时保留独立业务进程和既有二级能力：

1. Upload 网站与上传器进程组；
2. 溢价率上升监控 A 端（8421 + 19195/L1）；
3. Webull 行情转发；
4. 实时申购赎回监控（6787 + QMT1/QMT2）。

本机自动化已经通过；machome 上的完整交易日 shadow、控制权交接、实盘依赖和长稳测试仍必须按验收手册执行，不能用 mock 结果替代。

## 交付形态

```text
Machome 四合一运行中心.app（Qt Widgets / C++）
        │ 0600 QLocalSocket，长度帧 + 版本化 JSON
machome-hub-agent（Qt Core / C++，可由 launchd 常驻）
        ├── Upload C++ adapter：HTTP + health 文件 + launchd 进程组
        ├── Premium C++ adapter：8421 WebSocket + 19195 NDJSON
        ├── Webull C++ adapter：v2 REST/WS + Python 控制 runner
        └── Realtime C++ adapter：6787 REST/WS + 两路 QMT NDJSON
```

UI、Agent、四个 adapter 和审计写入分别隔离在线程/进程边界；同一模块的命令串行，跨模块互不等待。Python/PyQt、Wind、WebTrade、TGW 等高风险专有依赖继续作为 sidecar，不嵌入 C++ Qt 进程。以后替换为 C++ 时保持同一协议和 `ModuleBackend` 边界即可。

## 已实现

- 首页实时四卡片、统一事件流、模块状态和主动控制；关闭主窗口只隐藏到托盘，不停止 Agent 或业务。
- 四个完整子页面和非模态二级详情；Upload 网站以浏览器深链保留，原 Qt 的二级查看能力在四合一内建页面中重建。旧 Qt UI 只可在未接管阶段按维护手册人工对照，owner 阶段硬禁用，避免旧 supervisor 被重新拉起。
- Upload：网站健康、上传器健康矩阵、scheduled-idle 判定、原网站深链、精确 launchd 生命周期。
- Premium：实时摘要/信号、同步、raw snapshot、观察清单、L1 热清单、独立 `/ws/v2/detail` 十档详情、历史筛选与后台 CSV 导出。
- Webull：live/ready/auth/collector/data/browser 分态、严格 v2 盘口和客户端列表、模式/采集器/浏览器/登录控制桥。
- 实时申赎：13 列主表、变化历史、观察清单与名称、PCF 四页详情、Wind/监控/PCF 控制、QMT1/QMT2 独立连接/同步/状态/固定一篮下单。
- 变更命令具备白名单边界、deadline、状态修订号、精确重放幂等、冲突拒绝、最终结果与脱敏 SQLite 审计；QMT 下单还要求真实双击和全局二次确认。
- 每个模块都支持 `shadow / logic / owner` 三阶段迁移。默认生产示例为 `shadow + control_enabled=false`，不会在首次安装时接管旧进程。
- Release 打包、自包含 Qt、构建清单、ad-hoc 签名、只读 preflight、默认 dry-run 安装、版本化备份和保守回滚脚本。

## 本机验证

```bash
cd "/Users/ellis/工具程序开发/machome四合一运行/app"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build -j 4
ctest --test-dir build --output-on-failure
```

当前 Debug 结果为 **13/13 通过**：公共协议/限帧、Premium adapter、QMT 生产合同、命令账本、关键审计 WAL、Upload 精确生命周期、Webull Python 控制 runner、合同一致性、owner 交接工具、构建清单、发布 dry-run、四个页面和四模块端到端 smoke。全链路 smoke 覆盖 UI↔Agent 握手、四 adapter、11 条成功命令、stale revision、生命周期拒绝封装、幂等重放/ID 冲突、双 QMT、关键事件崩溃恢复和 SIGTERM 优雅退出。

最终 Release 同样 **13/13 通过**，签名后的成品包又以 Cocoa 后端直接执行了一遍完整 smoke。当前 zip 的 SHA-256 为 `15223a44c983c86f2a3a529b2d21db5c7712b9d8a3569f2a600cbf9df9bd0474`；包内构建 ID、时间与源码状态见 `Contents/Resources/build_manifest.json`。

Release、安装和回滚命令见 [app/README.md](app/README.md)。真机首次运行必须先看 [machome 真机验证与切换手册](docs/09_machome真机验证与切换手册.md)。

## 文档导航

- [现状审计与源码映射](docs/01_现状审计与源码映射.md)
- [总体架构与多线程设计](docs/02_总体架构与多线程设计.md)
- [UI、四个子页与二级页面迁移规格](docs/03_UI与页面迁移规格.md)
- [桥接协议与未来 C++ 预留](docs/04_桥接协议与C++预留.md)
- [迁移、部署、切换与回滚](docs/05_迁移部署与回滚.md)
- [完整功能验收清单](docs/06_完整功能验收清单.md)
- [风险台账与实现决策](docs/07_风险台账与待决策项.md)
- [本地实现与测试报告](docs/08_本地实现与测试报告.md)
- [machome 真机验证与切换手册](docs/09_machome真机验证与切换手册.md)
- [命令](contracts/module-command.schema.json)、[命令结果](contracts/module-command-result.schema.json)、[状态](contracts/module-status.schema.json)、[事件](contracts/module-event.schema.json)合同
- [生产 shadow 配置示例](config/modules.example.json)
- [早期交互原型](prototype/README.md)

## 安全边界

- 四套原始源码未被修改；所有实现都在本目录内。
- 本轮没有停止、重启、发信号或改写 machome 上任何业务进程、配置、数据或日志。
- 默认安装只新增 Hub 自身文件；`install.sh` 默认 dry-run，且即使 `--apply` 也不会操作四套旧业务 label。
- “完整迁移候选版”表示代码和交付链已完成，不表示未经真机交易日验收就可以一次性接管生产。四个 owner 必须逐模块、在维护窗口交接，并保留旧页面和回滚路径。

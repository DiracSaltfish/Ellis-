# Machome 四合一运行中心

这里是一套可构建、可打包、可回滚的 **Qt 6 / C++ 原生整合候选版**。四项业务逻辑都由四合一 Agent 内的独立 Engine 承载，不再拉起四个旧独立程序：

1. Upload 网站与上传器进程组；
2. 溢价率上升监控 A 端（8421 + 19195/L1）；
3. Webull 行情转发；
4. 实时申购赎回监控（6787 + QMT1/QMT2）。

本机自动化已经通过；machome 上的完整交易日 shadow、控制权交接、实盘依赖和长稳测试仍必须按验收手册执行，不能用 mock 结果替代。

## 交付形态

```text
Machome 四合一运行中心.app（Qt Widgets / C++ 原生 UI）
        │ 0600 QLocalSocket，长度帧 + 版本化 JSON
machome-hub-agent（Qt Core / C++，可由 launchd 常驻）
        ├── UploadEngine（独立 QThread）── 原生 IBKR bridge helper
        ├── PremiumAEngine（独立 QThread）── 包内 TGW helper
        ├── WebullEngine（独立 QThread）── 包内无 UI Browser helper
        └── RedemptionEngine（独立 QThread）── 包内 Wind probe helper + QMT1/QMT2
```

UI、四个 Engine 和审计写入分别隔离在线程边界；Chrome、Wind、TGW、IBKR 等不可信 ABI/进程边界使用应用包内 helper。helper 没有独立 UI、LaunchAgent 或旧程序路径依赖。溢价率模块只包含 A 端；现有 B 端继续通过 8421/19195 兼容协议连接。

## 已实现

- 首页实时四卡片、统一事件流、模块状态和主动控制；关闭主窗口只隐藏到托盘，不停止 Agent 或业务。
- 四个完整子页面和非模态二级详情；Upload 网站以浏览器深链保留，原 Qt 的二级查看能力在四合一内建页面中重建。旧 Qt UI 只可在未接管阶段按维护手册人工对照，owner 阶段硬禁用，避免旧 supervisor 被重新拉起。
- Upload：25 项调度任务、基金/净值/份额/仓位/历史存储、行情与 PCF 采集、估值、fallback、ACK、健康状态及原生 IBKR 生命周期；生产 mutation 默认 record-only。
- Premium：实时摘要/信号、同步、raw snapshot、观察清单、L1 热清单、独立 `/ws/v2/detail` 十档详情、历史筛选与后台 CSV 导出。
- Webull：自包含 C++ Engine 和包内 Chrome/CDP helper，提供 18765 v2 REST/WS、严格盘口和客户端列表，并由原生 UI 控制模式、采集器、浏览器和登录窗口。
- 实时申赎：七标的、13 列主表、变化历史、PCF 四页详情、包内 Wind probe、6787 只读兼容协议、QMT1/QMT2 独立同步门禁；真实下单编译和配置双重关闭。
- 变更命令具备白名单边界、deadline、状态修订号、精确重放幂等、冲突拒绝、最终结果与脱敏 SQLite 审计；QMT 下单还要求真实双击和全局二次确认。
- 默认配置关闭生产上传、通知、真实 QMT 下单和外部数据 helper，不会在首次运行时接管或操作旧进程。
- Release 打包、自包含 Qt、构建清单、ad-hoc 签名、只读 preflight、默认 dry-run 安装、版本化备份和保守回滚脚本。
- 可选原生 IBKR 行情 sidecar：官方 C++ `EClientSocket/EReader/EWrapper`、单 TWS 会话、动态订阅租约去重、线程安全快照、本机扇出、退避重连和慢客户端隔离。
- 四个源码来源均有只读来源 manifest；运行时不依赖旧源码目录、旧 venv、旧 runner 或旧二进制。

## 本机验证

```bash
cd "/Users/ellis/工具程序开发/machome四合一运行改造cpp/app"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build build -j 4
ctest --test-dir build --output-on-failure
```

最终整合构建结果：Debug **32/32**，带原生 IBKR 的 Release **33/33**。测试覆盖四个 Engine、原生 UI、协议兼容、fixture/golden、时间边界、断线/损坏输入、命令审计、线程生命周期、旧目录不可见和运行时隔离；测试未执行生产上传、通知或 QMT 下单。

本改造版已使用 `--with-native-ibkr` 完成 Release 全量构建、测试、依赖收口与
ad-hoc 签名验证。包内构建 ID、时间、源码状态和实际组件见
`Contents/Resources/build_manifest.json`；本轮候选包位于 `app/dist-integration-root/`，
ZIP SHA-256 为 `a411411a538d3c81fb731d78be0df2f5cfa5b328e53685c2fd4249af57d9d1dc`。
安装脚本仍默认 dry-run，不会自动部署到 machome 或改写任何旧程序。

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
- [Go/Python 到 C++ 评估与原生 IBKR 实施](docs/11_Go_Python到C++迁移评估与原生IBKR实施.md)
- [原生四合一串行迭代方案](docs/12_原生四合一串行迭代方案.md)、[四模块派发清单](plans/four-module-serial-work-packages.yaml) 与 [内部细分检查表](plans/native-consolidation-work-packages.yaml)
- [四模块原生整合最终审查报告](docs/13_四模块原生整合最终审查报告.md)
- [newnavnav 七组 uploader 单 TWS 覆盖层、测试与安装](migration/newnavnav-shared-ibkr/README.md)
- [IBKR bridge 配置 schema](contracts/ibkr-native-bridge-config.schema.json) 与 [v1 行情合同](contracts/ibkr-quote-v1.md)
- [生产 shadow 配置示例](config/modules.example.json)
- [早期交互原型](prototype/README.md)

## 安全边界

- 四套原始源码未被修改；所有实现都在本目录内。
- 本轮没有停止、重启、发信号或改写 machome 上任何业务进程、配置、数据或日志。
- 默认安装只新增 Hub 自身文件；`install.sh` 默认 dry-run，且即使 `--apply` 也不会操作四套旧业务 label。
- “完整迁移候选版”表示代码和交付链已完成，不表示未经真机交易日验收就可以一次性接管生产。四个 owner 必须逐模块、在维护窗口交接，并保留旧页面和回滚路径。

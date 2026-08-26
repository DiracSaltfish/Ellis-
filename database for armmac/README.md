# TGW macOS ARM64 适配工程

这是从原 `数据库桥接/reverse-macos` 中整理出的独立 Mac 工程。当前可运行主线是
Python 实现的 TGW internet-mode TLS/WebSocket 中间层；它在 Apple Silicon 上原生运行，
不加载 Linux x86_64 `.so` 或 Windows `.pyd/.dll`。

它不是官方完整 SDK，也不是对 x86_64 指令的逐条翻译。当前只对少量只读接口和参数范围
完成了 Linux 官方 SDK 与 macOS 的同参验证。请先阅读：

1. [`docs/MACOS_SDK_USAGE.md`](docs/MACOS_SDK_USAGE.md)：安装、登录、查询、订阅、数据结构和回调/增量逻辑。
2. [`docs/DEVELOPMENT_STATUS_AND_HANDOFF.md`](docs/DEVELOPMENT_STATUS_AND_HANDOFF.md)：逆向来源、真实进度、风险和后续 Agent 接手方式。
3. [`docs/API_STATUS.md`](docs/API_STATUS.md)：逐接口支持边界。
4. [`docs/POST_MARKET_TEST_WORKFLOW.md`](docs/POST_MARKET_TEST_WORKFLOW.md)：盘后 OpenCode/Ox Alpha 单接口测试、修复与验收循环。
5. [`docs/PDF_API_INVENTORY.md`](docs/PDF_API_INVENTORY.md)：两份 PDF 与发行头文件的完整候选接口、结构和差异清单。

当前历史行情已完成 `QueryKline` 日线、周线与月线三个独立子范围的 Linux 官方 SDK / Mac
同参对齐；其它周期仍按“一个周期一个证据”继续推进。`QueryETFInfo` 已完成 SSE `510300`
单 ETF 的 Linux/wire/Mac 闭环；`QueryCodeTable` 目前仍只有静态契约，尚不可调用。

## 快速开始

```bash
cd "/Users/ellis/工具程序开发/database for armmac"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dataframe]'
cp config/galaxy_account.example.ini config/galaxy_account.ini
chmod 600 config/galaxy_account.ini
```

填入本地授权账号后，先运行离线测试，再做低频在线烟测：

```bash
python -m unittest discover -s tests -v
python tools/live_smoke.py --config config/galaxy_account.ini
```

## 目录

| 路径 | 用途 | 生产状态 |
|---|---|---|
| `src/python/tgw_macos/` | 当前真实 TLS/WSS Mac SDK 主线 | 仅文档列明的小范围可灰度 |
| `examples/` | 无凭据、可复制的使用示例 | 与主线同步 |
| `tools/live_smoke.py` | 脱敏在线烟测 | 可用于受控验证 |
| `tests/` | 合成协议与 ABI 测试 | 必须保持通过 |
| `runtime/arm64/experimental/` | arm64 dylib/demo 骨架二进制 | **不可用于真实鉴权/行情** |
| `native/experimental/` | 上述骨架源码 | 后续原生化实验 |
| `experimental/amazingdata_compat/` | 尚未验收的 AmazingData 高层兼容代码 | 不随 wheel 安装 |
| `docs/evidence/` | 已脱敏的 Linux/Mac 验证证据 | 状态判定依据之一 |
| `reference/` | 厂商手册与 V1.0.8 公开头文件 | 只读参考，不参与构建 |
| `tools/build_api_acceptance_tracker.mjs` | 从受审文档重建 Excel 验收台账 | 使用 `@oai/artifact-tool` |
| `outputs/01a03c21-40ce-7230-8082-fa2313f6d1c6/` | 当前接口验收 Excel | 公式化状态/批次/结构台账 |

凭据、token、MAC 地址、原始抓包和原始行情数据不得进入本目录或版本控制。

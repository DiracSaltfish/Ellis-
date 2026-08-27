# ETF 溢价率拉升监控

已实现第一阶段可编译版本：Qt6/C++20 服务端 A（可视化控制台 + 后台核心）、Python TGW 适配器、macOS/Windows 共用 C++ 源码的客户端 B、以 202 标的为初始观察清单且可在 A 界面增删沪深代码、8421 WebSocket、19195 L1 兼容网关、QMT 人工交易界面、仿真/压测/回放/数据质量工具。

## 当前状态

已通过 C++ 域测试、Python 桥接测试、8421/19195 端到端冒烟、假 QMT 的申购/赎回/卖出/撤单测试，以及 1000 标的短时仿真运行。第一阶段未发送任何真实 QMT 委托。

TGW 1–21 数字键和 202 标的短时批量/追加/精确移除已于 2026-08-27 盘中验证。11:28–11:29 生产链路复验中，159866/164824/164701 的 TGW 最新价、五档价格和数量与 Sina L1 近时样本逐项一致；完整证据见 `docs/13_20260827_盘中生产联调记录.md`。
真实 500/1000 标的容量、全日长跑、重连恢复与 IOPV 独立质量仍为 **待盘中验证**，
不得因短时成功而标记全面生产通过。

## 构建与测试

```bash
cd '/Users/ellis/工具程序开发/溢价率拉升监控'
cmake --preset macos-arm64-debug
cmake --build --preset macos-arm64-debug -j4
ctest --test-dir build/macos-arm64-debug-make --output-on-failure
/Users/ellis/miniconda3/envs/ag/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

从 `database for armmac` 临时副本构建 wheel，以 Conda `ag` 的 arm64 Python 3.10.19 创建项目私有环境：

```bash
zsh tools/build_private_tgw_env.sh
```

已验证私有环境中安装 `tgw-macos-arm64 1.0.9.2.6`、`zstandard 0.25.0`。
`database for armmac` 已按用户授权修复批量 ZSTD 帧解码并补充调用文档；项目不再使用运行时 monkeypatch。

## 离线启动

```bash
./build/macos-arm64-debug-make/etf-premium-core --config config/app.example.json --simulation
.venv/bin/python adapter/tgw_adapter.py \
  --socket runtime/tgw.sock --watchlist config/watchlist.json --simulate
.venv/bin/python tools/protocol_smoke.py --symbol 159866.SZ
```

```bash
build/macos-arm64-debug-make/etf-premium-console.app/Contents/MacOS/etf-premium-console \
  --root '/Users/ellis/工具程序开发/溢价率拉升监控'
  build/macos-arm64-debug-make/etf-premium-client.app/Contents/MacOS/etf-premium-client \
  --server ws://192.168.1.113:8421 --config config/app.example.json
```

B 首页默认打开独立“信号列表”：同一标的只保留最新触发并置顶，重连/重启不自动清除，每行可点“本次移除”；“全局列表”始终按证券代码固定排序。两表都可双击进入详情。信号缓存位于设置同目录的 `client-signal-list.json`，本次移除不改变 A 观察清单或 TGW 订阅。

B 默认开放人工申购、赎回、快速卖出和撤单。`--read-only` 仅保留为紧急验收开关；任何真实 QMT 写指令必须由操作者本人在界面双击，自动化测试不得发送。顶部「设置…」可维护 A/QMT1/QMT2 地址、三种可试听提示音、提醒次数、弹窗与主表刷新间隔，原子保存到 `.app` 同级程序目录的 `config/client-settings.json`；也可用 `--settings` 指定路径。

## 生产配置

1. 复制 `config/app.example.json` 为不入库的 `config/app.json`，将 `mode` 改为 `live`。
2. 复制 `config/tgw_account.example.ini` 为不入库的 `config/tgw_account.ini`并填写账户。
3. 核对 QMT1/QMT2 主机、端口和 A 内网地址。
4. 仅在 500/1000 容量验收时将 `capture_dynamic_market_data=true`。

8421、19195 和 QMT JSONL 均为无 TLS/无认证明文，只能用于可信内网。

## macOS arm64 发布包

`dist/ETF溢价率拉升监控-macOS-arm64.zip` 已于 2026-08-27 按当前合并源码重新生成。两个 `.app` 已通过 `macdeployqt` 和完整 Apple Development bundle 签名，包含 macOS 局域网用途声明；B 识别 `--settings`、`--read-only`，并以只读方式实际连通 machome 的 summary/detail 双连接。当前依赖决定最低系统版本为 macOS 26.0；未做 Apple Developer ID 公证，Finder 首次启动时的局域网授权必须由操作者本人批准。独立 A-core 仍要求服务端安装 Homebrew Qt/zstd，machome 已满足。

重复发布命令：

```bash
zsh tools/package_macos_arm64.sh
```

完整重编记录见 `docs/15_20260827_全量重编译与发布记录.md`。

## 工具与文档

- `protocol_smoke.py`：8421/19195 只读冒烟。
- `load_test.py`：202/500/1000 容量，正式验收默认每级 900 秒。
- `qmt_mock.py`：绝不连券商的假 QMT。
- `qmt_protocol_smoke.py`：对假 QMT 的 Backend 10.1 full/delta/result 合约做申购、赎回、卖出、撤单冒烟；默认安全端口 19527。
- `replay_raw.py`：raw 历史回放。
- `quality_report.py`：字段类型、缩放、时间、单调性、档位和延迟统计。
- `compare_sina_l1.py`：A 与 Sina L1 的价格/五档近时对照；按要求不使用 QMT L1 作 baseline。
- `tgw_multi_probe.py` / `tgw_unsubscribe_probe.py`：202 批量、追加和单标的精确移除取证。
- `watchlist_runtime_smoke.py`：通过 8421 临时追加沪/深观察标的、验证 full 后恢复原清单并验证精确退订。
- `lan_stream_probe.py`：从独立 B 主机统计 8421 长连断线、ping RTT、初始/持续流量以及详情缓存首帧和后续主动推送；不连接 QMT。

`docs/01`–`docs/12` 为实现、运维和盘中验收文档；`docs/13`–`docs/15` 保存当日实盘、局域网和全量重编证据；`docs/附录_*` 保存清单、单位和状态码。

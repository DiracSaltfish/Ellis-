# Go/Python → C++ 迁移评估与首批实施

## 1. 结论

本轮不做“按语言重写”，而按数据面热度、第三方边界和业务回归风险拆分。结论如下：

| 业务边界 | 当前主体 | 决策 | 价值/风险 | 本轮状态 |
|---|---|---|---|---|
| Upload 网站/API/快照/估值服务 | Go | 保留 Go | 已是编译型并发服务；重写 C++ 收益低、回归面最大 | 不改 |
| IBKR TWS 连接、订阅、行情接收、重连 | 多个 Python `ib_insync` 会话 | 原生 C++ 单会话扇出 | 减少会话、线程和重复 market-data line；官方 C++ API 可直接覆盖 | bridge + 七入口覆盖层已实现，尚未部署 machome |
| PCF、期货换月、FX 锚点、估值公式、上传确认 | Python | 保留 Python | 业务规则密集；耗时主要是外部 I/O，不是 Python 算术 | 已保持原公式，只替换实时行情边界 |
| Webull WebTrade 登录/浏览器控制 | Python + Playwright | 保留 Python sidecar | 页面、登录态和浏览器事件是脆弱外部边界，C++ 无性能优势 | 不改 |
| Webull MQTT/protobuf 解码、规范化、store/API | Python | 第二优先级可迁 C++ | 数据热路径清晰，但必须先保存原始帧回放语料 | 未切流，协议已由 Hub v2 隔离 |
| 实时申赎 poll/normalize/history/server | Python asyncio/FastAPI | 第三优先级可迁 C++ | 可降运行依赖；当前吞吐不构成证据充分的瓶颈 | 保持桥接 |
| Wind 私有 dylib/LLDB probe | Python + C probe | 隔离保留 | 风险来自私有 ABI，不来自 Python；改 C++ 不能消除版本漂移 | 不改 |
| QMT 双路客户端和固定一篮下单 UI | Python/现有 C++ Hub adapter | 保持协议边界 | 已有欢迎帧、全量同步、delta、checksum、节流和二次确认语义 | 不重写后端 |
| 溢价率监控核心/服务/客户端 | C++ Qt | 保持原生 | 已是 C++，`CoreServer/SignalEngine/PersistenceWriter` 边界成熟 | 不改 |

关键判断：Go 与 C++ 都能承担高并发网络服务；没有 profile 证明的前提下，把稳定 Go 服务改成
C++ 不会自然带来业务收益。本轮已把多个 Python uploader 的 IBKR 实时采集层合并为一条
官方 C++ TWS 会话，而没有把每套估值规则一起重写。Python 上传器仍然按进程隔离，
因此它们不共享一个 GIL；单个终端计算或网络 I/O 慢，不会占住其他上传器的 Python 解释器。

## 2. 源码证据

### Upload / newnavnav

知识图中该工程含 129 个 Go、104 个 Python、35 个 Vue 文件。生产入口包括
`cmd/web/main.go`、多项 Go 同步任务和 `scripts/` 下的私有 uploader。Go 服务已经承担
HTTP 路由、快照、估值、历史份额、监控和前端 API；Python 文件还混有研究、回填、测试，
不能把“Python 文件数量”误当成生产迁移量。

行情连接实际散落在：

- `scripts/private_valuation_uploader.py::IBQuoteStream.connect`；
- `scripts/private_513350_valuation_uploader.py::OvernightXOPQuoteStream.connect`；
- `scripts/private_xop_family_uploader.py::XOPMarketHub.connect`；
- `scripts/private_159605_valuation_uploader.py::IBMultiQuoteStream.connect`；
- `scripts/private_china_internet_valuation_uploader.py::MarketHub.sync`；
- `scripts/private_164824_valuation_uploader.py::INDAMarket.connect`；
- `scripts/private_nasdaq_valuation_uploader.py::NQMarket.connect`。

七个入口都在上层 `run/main` 中同时负责估值和上传。这正适合在连接层“横切”出共享行情
bridge；若直接把整文件翻译成 C++，反而会把合约选择、换月、日历、异常回退和上传逻辑一起置于回归风险中。

### Webull

工程为 18 个 Python 文件，主依赖 PyQt6、aiohttp、Playwright。
`BrowserCollector._process_target_response` 的调用链随后进入
`normalize_webull_depth → StateStore.publish_depth → persist_depth`。可迁移边界在规范化之后；
浏览器启动、页面附着、鉴权检查、标的选择和响应拦截应继续作为 Python sidecar。

### 实时申购赎回

工程主体为 Python，另有 2 个 C 文件。`MonitorEngine.poll_once` 同时读取 capture、产生
`SymbolState`、分类申赎机会、记录变化并 broadcast；PCF 有独立 loop，QMT 有独立欢迎帧、
双全量 ready、delta/checksum、静默重连和 5 秒下单节流测试。未来原生化应先拆成
`capture adapter → canonical event → C++ state/history/server`，不能从 UI 类开始逐页翻译。

Wind probe 的 ABI 探测和 frame parser 必须保持单独进程；即使未来 parser 改为 C++，也要让
probe 崩溃只影响 Wind source，不影响 MonitorEngine、QMT 或 Hub。

### 溢价率监控

当前核心已经是 C++：`CoreServer.publishSnapshot` 会进入持久化、信号引擎、summary/detail
广播与 operational 健康；Python TGW adapter 是专有接入层。这里没有“Python 核心待重写”，
继续保留 adapter 进程隔离更合理。

## 3. 本轮已经落地的 C++ 改造

新增 `machome-ibkr-bridge`：

1. 使用官方 `EClientSocket + EReader + EReaderOSSignal + DefaultEWrapper`；
2. 仅在 `nextValidId` 后进入 ready 并订阅；
3. 一条 TWS 会话服务 pinned 和动态合约，支持 live/frozen/delayed 类型；
4. 心跳超时、连接关闭和 1100/1101/1102/1300/502/504 状态分流，指数退避重连；
5. reader 线程与 Qt socket/control 线程分开，`QuoteBook` 线程安全；
6. 本机 owner-only NDJSON 协议支持 `hello/status/quotes/quote/subscribe/unsubscribe/ping`；
7. 同一 subscription ID + 完全合约身份只向 TWS 发一次 `reqMktData`；最后一个客户租约释放后才 `cancelMktData`；
8. 帧大小、客户端数、订阅数、待写字节有限额，慢消费者不反压行情线程；
9. 原子写入现有 Upload health 目录，四合一 UI 自动展示；
10. 不包含订单/账户/持仓接口，首版只读；
11. 官方 SDK 外部引用，不把受许可约束源码复制进本工程。

IBKR 新 SDK 带生成的 protobuf 源码，生成代码与 runtime 必须匹配。本工程增加了独立 include
优先级、显式 protobuf/Abseil root、Mach-O install-name 修正和 Release bundle 扫描，避免
本机更新 Homebrew protobuf 后出现“能编译但不能启动”或生成代码版本冲突。

同时新增 `migration/newnavnav-shared-ibkr/`：一个不依赖 `ib_insync`、不包含 HTTP/WS
上传的严格 Unix-socket consumer，以及上述七个 uploader 的可安装覆盖版。XOP、159605
成分和中概 US 成分直接走 bridge；INDA/NIFTY 与 NQ/ES/N225M/FDXM 使用混合模式：
Python 低频解析合约/读历史后立即断开，长期实时行情由 C++ 持有。

## 4. 线程/进程设计

```text
TWS / IB Gateway
       │ 单一 clientId，只读行情
       ▼
machome-ibkr-bridge（独立进程）
  ├─ EReader thread ──> QuoteBook（短临界区）
  └─ Qt main thread ──> Unix socket fan-out / health / reconnect
          │
          ├─ Python uploader A（公式与上传）
          ├─ Python uploader B（公式与上传）
          └─ Python uploader C（公式与上传）

Go 网站、Premium C++、Webull Python、实时申赎 Python、Hub Agent/UI
均在独立进程或既有 worker 线程，任何一支断线不进入其他模块事件循环。
```

消费者只能读取快照，不能让 bridge 代替业务判断“市场是否可用”。它仍须验证：ready、fresh、
market data type、交易时段、合约月份和自身容错阈值。

Python 的 GIL 只影响单个 uploader 进程内的 Python 线程；这些 uploader 本来就是不同进程，
因此不会因共用 consumer 而变成一个全局 GIL。每个进程持有自己的 Unix socket；如果其中
一个长时间不读，C++ bridge 只丢弃该客户端，不会让 EReader 或其他客户端等待。

## 5. machome 分阶段切换

### 阶段 A：基线与 canary（不切业务）

- 固定当前 Python 直连版本和配置；保存至少 2 个完整交易日的 bid/ask/last、时间戳、上传 payload、
  reconnect 和错误码样本；
- 先只给 bridge 配 1–2 个标的并使用不冲突 client ID；
- 启动 bridge 前检查 TWS API 端口、Read-Only、允许本机 socket client；
- 注意 canary 与旧连接并行时会暂时增加行情订阅，先核对 market-data line 额度；
- Hub Upload 页出现 `ibkr_native_bridge`，但所有 Python uploader 仍走旧路径。

### 阶段 B：逐 uploader 双读

- v1 socket consumer 和七组覆盖文件已在改造目录完成，原 `newnavnav` 目录未改；
- 用不调用 uploader `run/main` 的本地对比工具记录 bridge 结果，测试期不向网站上传；
- 每次只迁一组（建议 XOP family → INDA/164824 → Nasdaq → 其他）；
- 同一时刻不得让双读结果重复上传；差异只写 shadow 日志。

### 阶段 C：逐组切流

- 双读达标后，用 dry-run/apply 覆盖层切换该 uploader；consumer 严格 fail closed，不静默建立新的 direct TWS 行情长连；
- 观察完整交易日后关闭该组旧会话，确认 TWS client 数和 market-data line 下降；
- 任何门槛失败，从 timestamp 备份恢复该组旧脚本并重启该组 uploader，不回退 Go 网站或其他三模块。

### 阶段 D：可选公式原生化

只有 profile 显示 Python 公式/序列化占据显著 CPU 或延迟时才进入。先把公式提取为纯函数，建立
历史 PCF + 行情 + FX + 换月的 golden corpus；C++ 与 Python 对同一输入输出并行至少 5 个交易日，
逐字段一致后再切。上传、凭据和告警仍保持独立 adapter。

## 6. 验收门槛

### 原生 IBKR bridge

- [x] 默认 Qt 四合一构建通过；不开启选项时不引入 IBKR/protobuf 依赖。
- [x] 本机官方 C++ SDK + protobuf 6.33.4 完整编译链接。
- [x] `--validate-config` 成功；远端 TWS host、重复订阅、非法范围会拒绝。
- [x] 无 TWS 时保持运行、产生错误健康、指数退避；不阻塞协议客户端。
- [x] `hello/status/quotes/quote/subscribe/unsubscribe/ping` 实际 Unix socket 往返成功。
- [x] socket 与健康文件为 `0600`；SIGINT 后 socket 被删除并写 `stage=stopped`。
- [x] 本机 TWS `127.0.0.1:7496` 的 `nextValidId`、server version、reqCurrentTime 与 XOP/INDA Live 回调通过；测试上传为零。
- [x] 两个本地客户端租用同一 XOP OVERNIGHT 只新建一条订阅；最后客户端断开后订阅数回到 baseline。
- [x] 自动化慢客户端隔离测试通过，快客户端往返低于 750 ms 门槛。
- [ ] machome TWS 上重复上述真实回调与租约测试。
- [ ] 模拟 1100→1101、TWS 重启、网络恢复，30 秒内回 ready 或达到当前基线。
- [ ] 8 个并发本机消费者连续 1 小时；慢消费者被隔离且行情 update 不停。
- [ ] 连续 2 个完整交易日无崩溃、无无限增长、无 TWS pacing/line 异常。

### 双读与业务等价

- [x] 七个 uploader 覆盖层全部通过旧业务回归与新 consumer adapter 测试（77/77）。
- [x] 长期实时报价路径无 `ib_insync.reqMktData`；保留直连仅用于低频合约解析/历史 bars 并立即断开。
- [ ] 相同合约/market-data type 下，bid/ask/last 非陈旧样本逐值一致率 100%；允许到达时间不同。
- [ ] 每条样本包含 UTC receive time、exchange time（若 IBKR 提供）和 sequence，乱序不覆盖更新值。
- [ ] 合约解析（conId/交易所/币种/expiry/multiplier）与旧路径逐项一致。
- [ ] 每组至少 2 个完整交易日 shadow，开盘、午间、收盘、跨日和期货换月均覆盖。
- [ ] 同输入下最终估值字段、fallback source、过期判定、上传 body 和服务端 ack 语义一致。
- [ ] 切流后不产生重复上传；旧路径回退能在一个 uploader 重启周期内恢复。
- [ ] TWS API client 数量和 market-data line 不高于切流前，目标是显著下降。

### 其余三模块不回归

- [ ] Go 网站 API/WS、页面深链、历史数据和后台任务无差异。
- [ ] Premium 8421/19195、summary/detail、信号历史和 L1 转发无差异。
- [ ] Webull 登录、浏览器显隐、采集、v2 REST/WS 和落盘无差异。
- [ ] 实时申赎 13 列、变化历史、PCF 四页、QMT1/QMT2 和固定一篮下单无差异。
- [ ] 停止/重启 bridge 不改变以上任一进程 PID 或 Hub 模块状态机。

## 7. 回退与禁止项

- 每个 uploader 单独回退，不能以“一键全切”为验收手段；
- bridge 未通过真机 shadow 前，不关旧 TWS 会话，不改 launchd owner；
- 不在 bridge 内加入订单权限；TWS 保持 Read-Only API；
- 不把 IBKR SDK、账户信息、cookie、token 或绝对生产凭据提交进工程；
- 在原生合约解析/历史 bars 协议通过 golden 回放前，不删除这两类低频 `ib_insync` 代码；
- Webull 浏览器和 Wind 私有 ABI 仍视为不可信外部进程，永不嵌入 Hub UI 线程。

## 8. 官方依据

- IBKR TWS API introduction：<https://ibkrcampus.com/docs/tws-api/doc/introduction>
- Essential components（EClient/EWrapper、EReader、nextValidId）：
  <https://ibkrcampus.com/campus/trading-lessons/essential-components-of-tws-api-programs/>
- TWS API reference：<https://ibkrcampus.com/campus/ibkr-api-page/twsapi-ref/>
- API settings：<https://ibkrcampus.com/docs/tws-api/protobuf/api-settings-config>
- Market data lines：<https://ibkrcampus.com/docs/general/market-data-subscriptions/market-data-lines/introduction>
- Protobuf introduction：<https://ibkrcampus.com/docs/tws-api/protobuf/introduction>

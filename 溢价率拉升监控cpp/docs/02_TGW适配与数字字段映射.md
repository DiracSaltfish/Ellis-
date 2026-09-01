# 02｜TGW 适配与数字字段映射

## 当前结论

项目使用已修复的 `tgw-macos-arm64 1.0.9.2.6` (`tgw_macos 1.0.9.2.macos.re6`)。
2026-08-27 盘中已验证：单标的 full/delta、202 标的批量订阅、同会话追加一个
标的，以及从原 202 批次中精确移除一个标的。1000 标的实盘、全日长跑、断线自动
恢复和 IOPV 的独立基准仍未完成，不得据此标记为全面生产通过。

## 传输与批量帧修复

TGW 推送可为普通 JSON、ZSTD，或 `0x59 + ZSTD`。202 标的压缩帧解压后不是单个
JSON，而是多个 JSON object 以 ASCII `0x60` 反引号分隔，末尾可带 C NUL。旧 macOS
实现单次 `json.loads` 会因 `Extra data` 断开。re6 在 SDK reader 内严格拆分：

- 仅接受空白或 ASCII `0x60` 对象分隔符；
- 每个成员必须为 JSON object，其它类型或分隔符明确报协议错误；
- 每个 object 重用原请求关联/事件分发路径；
- `ReceiveRawEvent()` 对调用方仍每次返回一个 dict，不暴露压缩或 batch 包装。

`tgw-adapter` 只负责 TGW 登录、订阅/退订、原类型事件审计和 UDS 转发，不在 Python
中缩放价格、解释 IOPV 或计算溢价率。

## 已验证的 L1 数字 key

映射由 Linux 原生 `MDSnapshotL1` 字段顺序与 macOS 实盘 full 共同固化。`159866.SZ`
价格/五档与 Sina L1 近时样本匹配；`164824.SZ` LOF 的价格/五档也匹配，但 key 19
为 0，因此可做 L1 分发而不能产生 IOPV 溢价信号。

| key | 字段 | 原始类型 / 缩放 |
| ---: | --- | --- |
| 1 | `market_type` | integer；101 SSE / 102 SZSE |
| 2 | `security_code` | string |
| 3 | `variety_category` | integer |
| 4 | `orig_time` | integer；`YYYYMMDDHHMMSSsss` |
| 5 | `trading_phase_code` | string；可带 C NUL，解析边界 trim |
| 6 | `pre_close_price` | integer / `1e6` |
| 7 | `open_price` | integer / `1e6` |
| 8 | `high_price` | integer / `1e6` |
| 9 | `low_price` | integer / `1e6` |
| 10 | `last_price` | integer / `1e6` |
| 11 | `close_price` | integer / `1e6`；盘中通常为 0 |
| 12 | `bid_price[10]` | `|` 分隔 integer / `1e6` |
| 13 | `bid_volume[10]` | `|` 分隔 integer / `1e2` |
| 14 | `offer_price[10]` | `|` 分隔 integer / `1e6` |
| 15 | `offer_volume[10]` | `|` 分隔 integer / `1e2` |
| 16 | `num_trades` | integer |
| 17 | `total_volume_trade` | integer / `1e2` |
| 18 | `total_value_trade` | integer / `1e5` |
| 19 | `IOPV` | integer / `1e6`；为 0 时禁止信号 |
| 20 | `high_limited` | integer / `1e6` |
| 21 | `low_limited` | integer / `1e6` |

C++ 权威值始终为 `qint64`；只在 UI、QMT 价格边界和 19195 旧协议格式化为小数。
`orig_time` 直接使用 Qt 6 的 64 位 JSON integer，不先转 `double`。
交易所 ETF/LOF 报价按 0.001 最小价位校验，即全部非零价格 e6 必须为 1,000 的整数倍；
违规帧标记 `price_tick_0_001_invalid` 并禁止信号计算。IOPV 不是场内报价，保留 e6
原精度，不套用三位价位检查。B 的最新价、买卖档和缓存卖出价固定显示三位，IOPV 显示四位。

## full/delta 与隔离

- 状态主键 `(session, normalized_symbol, tag)`；full 替换，delta 仅覆盖出现的 key。
- 会话变化、adapter 断开或分片队列溢出后清状态，每个标的重新取得 full 前不发布/计算。
- `adapter_seq` 不连续使全部分片和 19195 ready 集失效；当前帧是 delta 时等后续 full。
- 必需整数变 string/float/bool/null、十档长度/项类型错误、未知 key、价格序列或 OHLC 异常进
  `quality`，不进信号。
- 价格不落在 0.001 价位进入 `price_tick_0_001_invalid`；核心不做四舍五入修复，以免掩盖 TGW 映射或缩放错误。

## 202 订阅、追加与精确移除证据

| 测试 | 结果 |
| --- | --- |
| 202 初始订阅 | 按 20 分 11 批，全部 `result=0` |
| re6 同会话追加 `164824.SZ` | `result=0`，33.787 ms |
| re6 30 s 覆盖 | 1342 事件，356 full / 986 delta，203/203 有 full，status 全 0 |
| 从原批次移除 `159866.SZ` | 两次均 `result=0`，30.866 / 31.180 ms |
| 移除效果 | 移除前目标均有事件；3 s 宽限 + 后 20/30 s 均 0 条 |
| 其他订阅未被误伤 | 加长复验的 30 s 内其他标的 1332 事件，覆盖剩余 201/201 |
| re6 解码器 | 本次 `decoder_failures=[]` |

证据文件：

- `logs/live-validation/tgw-202-plus-164824-summary-20260827.json`
- `logs/live-validation/tgw-202-plus-164824-events-20260827.jsonl`
- `logs/live-validation/tgw-re6-202-plus-164824-summary-20260827.json`
- `logs/live-validation/tgw-re6-202-plus-164824-events-20260827.jsonl`
- `logs/live-validation/tgw-202-remove-159866-summary-20260827.json`
- `logs/live-validation/tgw-202-remove-159866-events-20260827.jsonl`
- `logs/live-validation/tgw-202-remove-159866-v2-summary-20260827.json`
- `logs/live-validation/tgw-202-remove-159866-v2-events-20260827.jsonl`

## 仍待验证

- 500/1000 标的实盘容量与每级至少 15 分钟稳定性；
- 全日队列高水位、丢帧、CPU/内存与延迟分布；
- 断线重连后清状态、重订阅和每标的等 full；
- IOPV 的独立权威基准；Sina L1 不含可用的交易所 IOPV，不得伪装已验证。

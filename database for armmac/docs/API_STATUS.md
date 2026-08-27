# macOS API 支持矩阵

更新时间：2026-08-27；实现版本：`1.0.9.2.macos.re6`。

本表描述的是已验证的**参数子范围**，不是对同名官方接口的整体承诺。证据状态采用
`AGENT_PARITY_WORKFLOW.md` 的定义；“可用建议”额外考虑公开返回合约、重连和持续运行风险。

| API / 功能 | 已验证范围 | 证据状态 | 当前建议 |
|---|---|---|---|
| `Login` | internet mode、真实 TLS/WSS、服务端鉴权 | `LIVE_ALIGNED(internet login)` | 受控试点 |
| `Close` | 单连接正常关闭、凭据内存清空 | `LIVE_ALIGNED(internet basic)` | 每次放在 `finally` |
| `GetTaskID` | 单进程递增整数 | `ARM_IMPLEMENTED` | 可用；尚未做并发唯一性验收 |
| `SetLogSpi` | `on_log(level, message)` | `ARM_IMPLEMENTED` | 可用；不是官方完整日志 SPI |
| `Subscribe` | 大陆 L1：单标的及 list 批量；202 标的按 20 分批后同会话追加 `164824.SZ` | `LIVE_ALIGNED(202+1 raw full/delta; 2026-08-27)` | 可受控试点；仍需业务层分批、监测积压与重连恢复 |
| `Subscribe` | HKT `02800`，SSE 路由，flag `kHKTSnapshot=12` | `LIVE_ALIGNED(raw full/delta; SH route)` | 可短时订阅原始事件 |
| `ReceiveRawEvent` | 单 JSON 及 `0x59 + ZSTD` 内反引号分隔的多 JSON 推送；向调用方仍每次交付一个 dict | `LIVE_ALIGNED(202+1 bulk push)` | 原始字段仍为数字 key；事件队列不是持久消息系统 |
| `UnSubscribe` | 202 标的 list 订阅后单独移除原批次中 `159866.SZ`；ETF/HKT 清理 | `LIVE_ALIGNED(single removal from batch; 2026-08-27)` | 返回 0 只代表服务端接受；长时稳定性仍需监测 |
| `QueryKline` | SZSE `159691` 1 分钟 `10000`（仅 2026-08-26 09:00–15:00，`normalized=True` 可给出经核验的元/股/元单位）；SSE `510300` 日线 `10008`、周线 `10009`、月线 `10010`、季线 `10011` 与年线 `10012`，同步返回 | `LIVE_ALIGNED(159691 one-minute sample + daily + weekly + monthly + quarterly + yearly)` | 仅已验证子范围可低频试点；其它分钟周期、标的、日期和单位均待独立取证 |
| `QueryCodeTable` | 无业务入参；wire 已证（`dgw*_query` one-shot、`ReqGetReduceCodeTable`、tag `11103`、反引号 6 字段）；Mac 已实现同步全量累计、缺包 `ReqGetPackage` 补拉 | `ARM_IMPLEMENTED` | 可低频尝试；服务端全市场大表曾持续缺第 3 包（Linux `-83` / Mac 缺包超时同因同果），完整成功同参样本不可得，未达 `LIVE_ALIGNED`；`query_spi` 显式拒绝 |
| `QueryETFInfo` | SSE `510300` 单 ETF、同步 JSON/DataFrame 嵌套返回 | `LIVE_ALIGNED(single SSE ETF only)` | 可低频试点；SZSE、多 item、异步和错误分支拒绝或未实现 |
| `SetThirdInfoParam` + `QueryThirdInfo` | 日历 `A010061003`，SSE 日期范围，同步返回 | `LIVE_ALIGNED(calendar function only)` | 可低频试点；其它 function id 未验 |
| `QuerySnapshot` | SZSE `159518`、单日窄窗口、`data_type=0`、`level_type=0`；同步 `(rows,0)`/`(None,-76)` 与异步 `query_spi(result,err_code)` 错误合约 | `LIVE_ALIGNED(SZSE ETF L1; data_type=0 sync+async error contract)` | 可低频试点；异步多包交付语义未观测，其它市场/data_type 拒绝 |
| `GetVersion` | 返回本实现/后端版本字符串 | `INVENTORIED` | 不等于厂商 SDK 版本 |
| `GetErrorMsg` | 全量公开错误码中文文案（对齐官方 wheel/PDF 表）；逐码线上行为未验 | `STATIC_MATCHED(official table)` | 文本可用；具体错误码触发条件以各接口证据为准 |
| `query_spi` | 仅 `QuerySnapshot`：提交 `(True/False, err)` + 后台 `spi(result, err_code)` | `LIVE_ALIGNED(with snapshot sub-range)` | 其它查询传 `query_spi` 仍显式报错 |
| `push_spi` | 无 | `NOT_IMPLEMENTED` | 传入时明确抛错 |
| 自动重连/恢复订阅 | 无 | `NOT_IMPLEMENTED` | 必须由进程监管与业务层处理 |
| coloc/QTCP/RTCP | 无 | `OUT_OF_SCOPE_COLOC` | 不可用 |
| `UpdatePassWord` 等写操作 | 无 | `INVENTORIED` | 不实现、不执行 |
| `QuerySecuritiesInfo` | SSE `510300` 单代码（market=101）；wire 已证（push 通道、`ReqGetCodeTableList`、tag `"109"`、43 字段记录） | `LIVE_ALIGNED(SSE single code only)` | 可低频试点；全市场/多 item/SZSE/NEEQ 显式拒绝 |
| `QueryExFactorTable` | `000001` 单代码；wire 已证（one-shot `dgw*_query`、`ReqGetExFactor`、tag `11102`、5 字段 CSV、double 18 位小数字符串） | `LIVE_ALIGNED(000001 only)` | 可低频试点；其它代码/多代码/异步显式拒绝 |
| 其它订阅/查询/因子/代码表/财务功能 | 无 | `INVENTORIED` 或 `NOT_IMPLEMENTED` | 不可用 |
| `amazingdata_re` 高层兼容 | 仅保留未验源码 | `EXPERIMENTAL` | 不随 wheel 安装 |
| `libtgw_core.dylib` / `tgw_demo` | arm64 加载、TCP 和本地状态机 | `SKELETON_ONLY` | 绝对不可当真实 SDK |

## 已锁定的公开值与 wire 值

| 功能 | 公开参数 | internet wire | 响应/推送 tag |
|---|---:|---:|---:|
| 大陆 ETF L1 订阅 | `kSnapshot=10` | `subscribeDataType=14` | `"14"` |
| 港股通 L1 订阅 | `kHKTSnapshot=12` | `subscribeDataType=16` | `"16"` |
| 日 K 线查询 | `cyc_type=10008` | `period_type=10100` | `10100` |
| 周 K 线查询 | `cyc_type=10009` | `period_type=10101` | `10101` |
| 月 K 线查询 | `cyc_type=10010` | `period_type=10102` | `10102` |
| 历史 L1 快照查询 | `data_type=0` | `ReqGetSnapshot` | `11000`；空数据帧 tag=`"DataEmpty"`/status=-100 → 公开 -76 |
| ETF 成分查询 | `SubCodeTableItem{101,"510300"}` | `ReqGetETFCodeTableList`（push WSS） | `"111"` |
| ThirdInfo 日历 | `function_id=A010061003` | `ReqGetThirdInfo` | `11101` |

代码对未知订阅 flag、未验证 K 线周期、非零快照 data/level 类型和未验快照市场/代码明确失败，
避免把“服务端可能接受”误写成“客户端已支持”。快照错误帧映射表只收录已捕获的
`"DataEmpty"`；其它字符串标签显式失败。

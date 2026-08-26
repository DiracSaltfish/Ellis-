# macOS API 支持矩阵

更新时间：2026-08-26；实现版本：`1.0.9.2.macos.re5`。

本表描述的是已验证的**参数子范围**，不是对同名官方接口的整体承诺。证据状态采用
`AGENT_PARITY_WORKFLOW.md` 的定义；“可用建议”额外考虑公开返回合约、重连和持续运行风险。

| API / 功能 | 已验证范围 | 证据状态 | 当前建议 |
|---|---|---|---|
| `Login` | internet mode、真实 TLS/WSS、服务端鉴权 | `LIVE_ALIGNED(internet login)` | 受控试点 |
| `Close` | 单连接正常关闭、凭据内存清空 | `LIVE_ALIGNED(internet basic)` | 每次放在 `finally` |
| `GetTaskID` | 单进程递增整数 | `ARM_IMPLEMENTED` | 可用；尚未做并发唯一性验收 |
| `SetLogSpi` | `on_log(level, message)` | `ARM_IMPLEMENTED` | 可用；不是官方完整日志 SPI |
| `Subscribe` | SZSE `159518`，flag `kSnapshot=10` | `LIVE_ALIGNED(raw full/delta)` | 可短时订阅原始事件 |
| `Subscribe` | HKT `02800`，SSE 路由，flag `kHKTSnapshot=12` | `LIVE_ALIGNED(raw full/delta; SH route)` | 可短时订阅原始事件 |
| `ReceiveRawEvent` | 上述两类订阅的解压 JSON 事件 | `ARM_IMPLEMENTED` on live-aligned wire | 临时交付 API；字段仍为数字 key |
| `UnSubscribe` | ETF L1 正常取消；HKT 已在验证清理中调用 | ETF `LIVE_ALIGNED`；HKT basic observed | 关闭前必须调用 |
| `QueryKline` | SSE `510300`、日线 `10008`、周线 `10009` 与月线 `10010`、同步返回 | `LIVE_ALIGNED(daily + weekly + monthly only)` | 可低频试点；其它周期拒绝 |
| `QueryCodeTable` | 无业务入参；PDF/HDR/官方 Python 同步与回调契约 | `STATIC_MATCHED(static contract only)` | 尚不可调用；先验证异步完整分包 |
| `QueryETFInfo` | SSE `510300` 单 ETF、同步 JSON/DataFrame 嵌套返回 | `LIVE_ALIGNED(single SSE ETF only)` | 可低频试点；SZSE、多 item、异步和错误分支拒绝或未实现 |
| `SetThirdInfoParam` + `QueryThirdInfo` | 日历 `A010061003`，SSE 日期范围，同步返回 | `LIVE_ALIGNED(calendar function only)` | 可低频试点；其它 function id 未验 |
| `QuerySnapshot` | SZSE `159518`、单日窄窗口、`data_type=0`、`level_type=0`、同步 JSON | 数据/协议已同参对齐；公开合约 `CHANGES_REQUESTED` | 仅实验；不要作为稳定生产 API |
| `GetVersion` | 返回本实现/后端版本字符串 | `INVENTORIED` | 不等于厂商 SDK 版本 |
| `GetErrorMsg` | 仅 `0 -> success` | `ARM_IMPLEMENTED(partial)` | 非零错误不要依赖此文本 |
| `query_spi` / `push_spi` | 无 | `NOT_IMPLEMENTED` | 传入时明确抛错 |
| 自动重连/恢复订阅 | 无 | `NOT_IMPLEMENTED` | 必须由进程监管与业务层处理 |
| coloc/QTCP/RTCP | 无 | `OUT_OF_SCOPE_COLOC` | 不可用 |
| `UpdatePassWord` 等写操作 | 无 | `INVENTORIED` | 不实现、不执行 |
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
| 历史 L1 快照查询 | `data_type=0` | `ReqGetSnapshot` | `11000` |
| ETF 成分查询 | `SubCodeTableItem{101,"510300"}` | `ReqGetETFCodeTableList`（push WSS） | `"111"` |
| ThirdInfo 日历 | `function_id=A010061003` | `ReqGetThirdInfo` | `11101` |

代码对未知订阅 flag、未验证 K 线周期、非零快照 data/level 类型和未验快照市场/代码明确失败，
避免把“服务端可能接受”误写成“客户端已支持”。

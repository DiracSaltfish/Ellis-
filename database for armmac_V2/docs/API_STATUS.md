# macOS API 支持矩阵

更新时间：2026-09-09；实现版本：`1.0.9.2.macos.re8`。

本表描述的是已验证的**参数子范围**，不是对同名官方接口的整体承诺。证据状态采用
`AGENT_PARITY_WORKFLOW.md` 的定义；“可用建议”额外考虑公开返回合约、重连和持续运行风险。

| API / 功能 | 已验证范围 | 证据状态 | 当前建议 |
|---|---|---|---|
| `Login` | internet mode、真实 TLS/WSS、服务端鉴权；`Cfg.server_port=0` 解析为默认 8600 | `LIVE_ALIGNED(internet login)` | 受控试点；账号须登录其**专属 VIP 集群**，用原生/Linux 集群 VIP 登录稳定返回 `-98`；同账号残留会话也会 `-98`，需 `force_logout=True` 或有界重试 |
| `Close` | 单连接正常关闭、凭据/登录响应清空；释放全局 backend，下一次 `Login` 创建新 transport | `LIVE_ALIGNED(internet basic) + ARM_IMPLEMENTED(re-entry; Mac live passed)` | 每次放在 `finally`；Mac 两轮登录均成功且 backend 不同；Linux 官方在第二轮返回 `False`（5 秒冷却亦同），故复登入不能标 Linux 同参 |
| `GetTaskID` | 本地 `MMDDHHmmSS + sequence(1..1000000)`；同秒连续、跨秒重置，并发唯一 | `LIVE_ALIGNED(local task-id format; overflow unobserved)` | 可用；官方同秒溢出、时钟回拨和跨进程唯一性仍未取证 |
| `SetLogSpi` | `on_log(level, message)` | `ARM_IMPLEMENTED` | 可用；不是官方完整日志 SPI |
| `Subscribe` | 大陆 L1：单标的及 list 批量；202 标的按 20 分批后同会话追加 `164824.SZ` | `LIVE_ALIGNED(202+1 raw full/delta; 2026-08-27)` | 可受控试点；仍需业务层分批、监测积压与重连恢复 |
| `Subscribe` | HKT `02800`，SSE 路由，flag `kHKTSnapshot=12` | `LIVE_ALIGNED(raw full/delta; SH route)` | 可短时订阅原始事件 |
| `ReceiveRawEvent` | 单 JSON 及 `0x59 + ZSTD` 内反引号分隔的多 JSON 推送；向调用方仍每次交付一个 dict | `LIVE_ALIGNED(202+1 bulk push)` | 原始字段仍为数字 key；事件队列不是持久消息系统 |
| `UnSubscribe` | 202 标的 list 订阅后单独移除原批次中 `159866.SZ`；ETF/HKT 清理 | `LIVE_ALIGNED(single removal from batch; 2026-08-27)` | 返回 0 只代表服务端接受；长时稳定性仍需监测 |
| `QueryKline` | SZSE `159691` 1 分钟 `10000`（仅 2026-08-26 09:00–15:00，`normalized=True` 可给出经核验的元/股/元单位）；SSE `510300` 日线 `10008`、周线 `10009`、月线 `10010`、季线 `10011` 与年线 `10012`，同步返回；HKEx/港股 `00001/00177/00187/00200/00300/00700`（market=103）与 A 股 `000001.SZ/600000.SH`（market=102/101）1 分钟 `10000`（2026-09-07..09） | `LIVE_ALIGNED(159691 one-minute sample + daily + weekly + monthly + quarterly + yearly + HKEx/A-share one-minute samples)` | 已验子范围可低频试点；HKEx/A 股样本与 Linux 官方逐行摘要一致（见 evidence `query_kline_hk_hkex_minute_20260909`），长区间与其它周期未验；3 分钟 `10001` 仅静态契约，Mac 仍显式拒绝 |
| `QueryCodeTable` | 无业务入参；wire 已证（`dgw*_query` one-shot、`ReqGetReduceCodeTable`、tag `11103`、反引号 6 字段）；Mac 已实现同步全量累计、缺包 `ReqGetPackage` 补拉 | `ARM_IMPLEMENTED` | 可低频尝试；服务端全市场大表曾持续缺第 3 包（Linux `-83` / Mac 缺包超时同因同果），完整成功同参样本不可得，未达 `LIVE_ALIGNED`；`query_spi` 显式拒绝 |
| `QueryETFInfo` | SSE `510300` 与 SZSE `159919`，各单 ETF、同步 JSON/DataFrame 嵌套返回 | `LIVE_ALIGNED(SSE+SZSE single ETF, synchronous only)` | 可低频试点；双 item 本轮在 Linux 登录阶段被 `-95` 阻塞、未发查询，仍未接受；异步、空/错误和多帧分支拒绝或未实现 |
| `SetThirdInfoParam` + `QueryThirdInfo` | 日历 `A010061003`，SSE 日期范围，同步返回 | `LIVE_ALIGNED(calendar function only)` | 可低频试点；其它 function id 未验 |
| `BaseData.get_calendar` | 实验高层 wrapper；默认 `market='SH'`、默认日期、隐式 `data_type='str'`；全量 8,714 条 | `LIVE_ALIGNED(default SH; implicit str only)` | 已修复真实 ThirdInfo 映射和分页；仍在 `experimental/`，不随 wheel 安装；显式 `str`/`datetime` 各一次 Mac live 均在登录成功后遇到 `TgwTransportError`，维持 `ARM_IMPLEMENTED` |
| `BaseData.get_code_info` | Linux 官方 `EXTRA_ETF`；三市场空代码 `QuerySecuritiesInfo`；1,631 行、7 列 | `LINUX_OBSERVED(EXTRA_ETF only)` | Mac 全市场 wire/分页/完成语义无可用 capture，高层入口继续显式 `NotImplementedError` |
| `QuerySnapshot` | SZSE `159518` 既有窄窗口及 SSE `510300`（仅 2026-08-25 09:30:00.000–09:30:30.000），均为 `data_type=0`、`level_type=0`；同步返回，SZSE 另验空结果/异步错误合约 | `LIVE_ALIGNED(SZSE 159518 + SSE 510300 historical L1 narrow subranges)` | 两个精确目标可低频试点；SSE 样本 Linux/Mac 均为 11 行×57 列；异步多包、其它市场/代码/日期/data_type 拒绝或未验 |
| `GetVersion` | 登录前纯本地返回官方客户端版本 `V4.3.0.260626-rc2.0-YHZQ`；与 `ReqLogon.Version` 默认值同源 | `LIVE_ALIGNED(local version string)` | 可用；不再初始化 backend 或返回兼容层/机器架构字符串，厂商升级时需同步基线 |
| `GetErrorMsg` | 官方 31 个公开错误码中文文案及未知码 fallback | `LINUX_OBSERVED(pure local 31-code table)` | 31/31 与 Linux 官方精确一致；具体错误码触发条件仍以各接口证据为准 |
| `query_spi` | 仅 `QuerySnapshot`：提交 `(True/False, err)` + 后台 `spi(result, err_code)` | `LIVE_ALIGNED(with snapshot sub-range)` | 其它查询传 `query_spi` 仍显式报错 |
| `push_spi` | 无 | `NOT_IMPLEMENTED` | 传入时明确抛错 |
| 自动重连/恢复订阅 | 无 | `NOT_IMPLEMENTED` | 必须由进程监管与业务层处理 |
| coloc/QTCP/RTCP | 无 | `OUT_OF_SCOPE_COLOC` | 不可用 |
| `UpdatePassWord` 等写操作 | 无 | `INVENTORIED` | 不实现、不执行 |
| `QuerySecuritiesInfo` | SSE `510300` 单项、SZSE `159919` 单项，以及固定输入 `[SSE 510300, SZSE 159919]` 双项同步查询；push `ReqGetCodeTableList`、tag `"109"`、43 字段 | `LIVE_ALIGNED(SSE single + SZSE single + ordered SSE/SZSE pair only)` | 双项 wire 为单请求、`code_num=2`，官方/Mac 都按服务端顺序 `[102,101]` 返回；其它代码/顺序、全市场、NEEQ、异步显式拒绝 |
| `QueryExFactorTable` | `000001` 单代码；wire 已证（one-shot `dgw*_query`、`ReqGetExFactor`、tag `11102`、5 字段 CSV、double 18 位小数字符串） | `LIVE_ALIGNED(000001 only)` | 可低频试点；其它代码/多代码/异步显式拒绝 |
| 其它订阅/查询/因子/代码表/财务功能 | 无 | `INVENTORIED` 或 `NOT_IMPLEMENTED` | 不可用 |
| `amazingdata_re` 高层兼容 | `get_calendar` 默认 SH 子范围已在线对齐；`get_code_info(EXTRA_ETF)` 仅 Linux 观测+离线转换契约 | `EXPERIMENTAL(partial)` | 不随 wheel 安装；仅使用上方精确列明的默认日历分支 |
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
| ETF 成分查询 | `SubCodeTableItem{101,"510300"}` 或 `{102,"159919"}` | `ReqGetETFCodeTableList`（push WSS，独立低位 codelist request id） | `"111"` |
| ThirdInfo 日历 | `function_id=A010061003` | `ReqGetThirdInfo` | `11101` |

代码对未知订阅 flag、未验证 K 线周期、非零快照 data/level 类型和未验快照市场/代码明确失败，
避免把“服务端可能接受”误写成“客户端已支持”。快照错误帧映射表只收录已捕获的
`"DataEmpty"`；其它字符串标签显式失败。`QueryETFInfo` 与
`QuerySecuritiesInfo` 的 codelist 推送通道使用从 1 开始的独立 wire id，不会把公开的
时间格式 `GetTaskID` 放到该通道上。

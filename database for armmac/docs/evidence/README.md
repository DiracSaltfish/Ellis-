# 对齐证据索引

| 文件 | 已证明子范围 |
|---|---|
| `query_kline_daily.md` | SSE 单代码日 K 线 |
| `query_kline_week.md` | SSE `510300` 单周周 K 线 |
| `query_kline_month.md` | SSE `510300` 单月月 K 线 |
| `query_code_table_static.md` | `QueryCodeTable` PDF/HDR/官方 Python 静态契约 |
| `query_code_table_live_closure.md` | `QueryCodeTable` 互联网全市场 wire 取证（`dgw*_query`/`ReqGetReduceCodeTable`/tag 11103/反引号 6 字段/缺包补拉）与 Mac 实现；服务端缺第 3 包阻塞成功同参 → `ARM_IMPLEMENTED` |
| `query_kline_quarter_static.md` | `QueryKline` 季线 `cyc_type=10011` PDF/HDR/官方 Python 静态三方契约（`STATIC_MATCHED`） |
| `query_kline_quarter_live.md` | `QueryKline` 季线 `cyc_type=10011` Linux/wire/Mac 同参闭环（`10011→10103` 实捕证明；`LIVE_ALIGNED(quarterly only)`） |
| `query_kline_year_live.md` | `QueryKline` 年线 `cyc_type=10012` Linux/wire/Mac 同参闭环（`10012→10104` 实捕证明；`LIVE_ALIGNED(yearly only)`） |
| `query_etf_info_static.md` | `QueryETFInfo` PDF/HDR/官方 Python 静态契约 |
| `query_etf_info_sse_etf.md` | `QueryETFInfo` SSE `510300` 单 ETF 的 Linux/wire/Mac 在线闭环 |
| `query_securities_info_sse.md` | `QuerySecuritiesInfo` SSE `510300` 单代码的 Linux/wire/Mac 闭环（push `ReqGetCodeTableList`/tag `"109"`/43 字段；`LIVE_ALIGNED(SSE single code only)`） |
| `query_ex_factor_table_000001.md` | `QueryExFactorTable` `000001` 单代码的 Linux/wire/Mac 闭环（one-shot `ReqGetExFactor`/tag 11102/5 字段/double 18 位；`LIVE_ALIGNED(000001 only)`） |
| `query_snapshot_szse_etf.md` | SZSE `159518` 历史 L1 快照的数据/wire 形状；公开 API 仍有验收项 |
| `query_snapshot_error_async_contract.md` | 同上子范围的同步 `-76` 错误码与异步 `query_spi` 合约（Linux/Mac 同参） |
| `subscribe_etf_l1.md` | SZSE `159518` L1 raw full/delta |
| `subscribe_hkt_02800_l1.md` | HKT `02800.SH` 路由 L1 raw full/delta |

这里的报告只保存返回码、shape、类型、控制字段、计数和间隔，不保存凭据、token、MAC、
原始价格、原始响应或完整 capture。新增接口按 `AGENT_PARITY_WORKFLOW.md` 的固定格式追加。

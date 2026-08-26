# 对齐证据索引

| 文件 | 已证明子范围 |
|---|---|
| `query_kline_daily.md` | SSE 单代码日 K 线 |
| `query_kline_week.md` | SSE `510300` 单周周 K 线 |
| `query_kline_month.md` | SSE `510300` 单月月 K 线 |
| `query_code_table_static.md` | `QueryCodeTable` PDF/HDR/官方 Python 静态契约 |
| `query_etf_info_static.md` | `QueryETFInfo` PDF/HDR/官方 Python 静态契约 |
| `query_etf_info_sse_etf.md` | `QueryETFInfo` SSE `510300` 单 ETF 的 Linux/wire/Mac 在线闭环 |
| `query_snapshot_szse_etf.md` | SZSE `159518` 历史 L1 快照的数据/wire 形状；公开 API 仍有验收项 |
| `subscribe_etf_l1.md` | SZSE `159518` L1 raw full/delta |
| `subscribe_hkt_02800_l1.md` | HKT `02800.SH` 路由 L1 raw full/delta |

这里的报告只保存返回码、shape、类型、控制字段、计数和间隔，不保存凭据、token、MAC、
原始价格、原始响应或完整 capture。新增接口按 `AGENT_PARITY_WORKFLOW.md` 的固定格式追加。

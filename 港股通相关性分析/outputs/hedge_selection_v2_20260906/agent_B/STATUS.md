# 港股通对冲 B｜科技互联网汽车

更新时间：2026-09-06（Asia/Shanghai）

## 当前状态

已完成 81 只分配基金的独占交付包。81/81 均判为 `INSUFFICIENT_EVIDENCE`，没有把旧窗口结果升级为新确认，也没有填造 PCF 缺失数量、报价、借券费或成交数量。

已完成：

- 520600 官方产品资料与 21 个官方 PCF 页面归档；每页解析 50 行。
- HSIU6、HHIU6、HTIU6 的 IBKR 只读合约确认与 2026-08-04 至 2026-09-04 日内历史抓取。
- 520600 旧样本复现；核心残差标准差和方差降低与归档结果逐项一致。
- 16 只技术结果导入旧样本机器表，并标记 `REUSED_OR_UNPROVEN`。
- 81 只基金 × 8 个候选工具的候选池审计、决策、覆盖、事件、敏感性、任务和 QA 表。

## 阻断

新增基金/成分价格端点未形成完整的共同 1 分钟面板：Eastmoney 端点出现远端关闭或覆盖不足，Tencent 替代端点不能覆盖港股成分；因此新确认期有效外测日数、严格重拟合 VR、事件覆盖和执行成本均未通过。所有主方案字段保持为空，探索结果只写入 `exploratory_best_policy_id`。

## 交付物

- `RESULTS.xlsx`：11 张明细表 +“阅读说明”。
- `FINAL_REPORT.md`：结论、阻断、复现路径与后续动作。
- `fund_decisions.csv/json` 等 11 张机器表，以及 `selection_lock.json`。
- `data/raw/ibkr_new_period/ibkr_fetch_manifest.json`、`data/new_period_520600/pcf_summary.json` 和 `runs/520600_old_repro/` 为主要复核入口。

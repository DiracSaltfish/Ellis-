# 港股通对冲研究 C 组最终报告

- 运行：`C-20260906-partial-v2`；生成时间：`2026-09-06T12:12:15.775913+00:00`；模型：GPT-5.6 Luna / xhigh。
- 研究对象：55 只分配基金；主周期 30 分钟，另列 5/15/60 分钟；结论仅限已测试范围。

## 结论

55 只基金全部落为 `INSUFFICIENT_EVIDENCE`（{'INSUFFICIENT_EVIDENCE': 55}）。没有将数据缺口写成没有对冲，也没有把旧窗口结果冒充新确认。`OUT_OF_SCOPE` 未使用：即使前轮快照标为 QDII，本轮也未缓存逐只官方范围文件。

## 新确认期数据审计

远端清单时间 `2026-09-06T11:41:55.622100+00:00`。2026-08-04 至 2026-09-04 内境内 ETF 分钟文件 0 日、PCF 明细 5 日、港股成交 15 日，三者共同日 0 日；因此没有满足至少20个有效新 OOS 日的基金。PCF 记录实际落在 52 只基金、5 个日期，其中数量完整日期只对部分基金成立；缺失数量被保留，没有以固定金额替代。
FX 本地缓存至 2026-08-19，目标期内仅 12 个日期。

## 行业候选

HKEX 官方资料确认 HBI 恒生生科期货（HKATS code HBI，合约乘数 HK$50/点）以及 03069、03174 生物科技 ETF；HKEX 2026-06-30 做空指定证券页列出 03069、03174。TWS 只读探针在 2026-08-25 返回 HBI 280 根 1 分钟历史条，但 03069/03174 返回 0 根，因此行业候选只进入“待验证池”，没有进入确认模型。

## 旧技术结果的边界

永久归档中有 9 只 C 组基金有完整旧技术运行；这些运行结束于 2026-08-03，均标记 `REUSED_OR_UNPROVEN`。最重要的旧窗口数值保存在 `model_metrics.csv` 和 `fund_decisions.csv` 的探索字段，仅用于下一轮候选优先级，不是本轮政策。

## 验收限制与下一步

- 当前不能执行 60 日训练 / 10 日验证 / ≥20 日新 OOS 的 5/15/30/60 分钟门槛、严格陈旧重拟合、bootstrap CI 或成本 Pareto 认证。
- 事件表对当前可读 PCF 的证券逐一留有 `REVIEW_INCOMPLETE`，已知 verified_events 单独保留；未把未核验证券默认为无事件。
- 每只基金的官方范围文件、PCF 全量日期、港股组件分钟、结算汇率和行业候选历史行情刷新后，应在新 selection lock 版本下重建全部结果。

## 机器交付

根目录包含 11 张 CSV/JSON 明细、`RESULTS.xlsx`、`research_manifest.json`、`selection_lock.json`、`FINAL_REPORT.md`、`STATUS.md` 和 `data/`/`evidence/` 审计输入。

## 官方来源

- https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index/Hang-Seng-Biotech-index/Hang-Seng-Biotech-Index-Futures?sc_lang=en
- https://ifp.hkex.hk/fund-repository/fund/BQQ795
- https://www.hkex.com.hk/-/media/HKEX-Market/Products/Securities/ETP/ETF-and-L-and-I-Product-Market-Perspective/2026/ETFLIProductMarketPerspective_2026-Apr.pdf
- https://www.hkex.com.hk/eng/market/sec_tradinfo/ds20260630.htm

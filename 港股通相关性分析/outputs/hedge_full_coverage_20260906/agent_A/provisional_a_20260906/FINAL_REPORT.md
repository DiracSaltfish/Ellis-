# Agent A Full Coverage R0/R1交付

## 覆盖

- A分母：101只；逐基金映射：101只；UNPROCESSED：0。
- 实际生成至少一条目标流水的基金：92只。
- PCF_BASKET原始包调查：尝试26只，严格2分钟陈旧上限下形成完整篮子日0只；ETF_MARKET_PRICE路径已逐只尝试，结构候选对全部基金保留。
- 主历史数据窗口：20260303—20260803；新确认窗口：20260804—20260904，本次没有把旧样本冒称未使用新确认。

## 结论口径

- 只有自身目标、同样本、正向OOS相关性≥0.60且正确系数使残差方差降低的目标才可进入MATCH。
- ETF_MARKET_PRICE结果明确包含折溢价、报价和汇率基差，不冒充PCF篮子申赎风险。
- 没有足够实际目标数据的基金不填0；保留具体候选、已尝试路径和缺口。
- 费用、借券、资金、容量未知时为UNKNOWN，不假设为零。

## 当前计数

- decision_counts：{"INSUFFICIENT_DATA":57,"MATCH":33,"OUT_OF_SCOPE":11}
- target_type_counts：{"ETF_MARKET_PRICE":92,"INDEX_STRUCTURAL":9}
- target_results rows：380；candidate_metrics rows：8464；fetch_attempts：213；checks：5。
- provisional selection lock hash：`2a30b215fe4f385f172816e0c58e7554285e9a302d2c23a5df0190bb10f2a749`。

## 未决

- B通用引擎发布后需按其固定hash重跑共同选择接口；本轮A锁仅用于先完成全量数据／候选覆盖和可复跑探索。
- PCF之外的ETF市场价目标不等于基金内在价值；全量逐证券公司行动、点时FX与执行成本仍需独立补证。

详见 `mapping.json/csv`、`target_results.jsonl`、`candidate_metrics.jsonl`、`inventory.jsonl`、`fetch_attempts.jsonl`、`checks.jsonl`、`evidence.jsonl`、`residuals/`、`weights/`。

# Agent B 全量覆盖报告

生成时间：2026-09-06T15:24:23.857208+00:00

## 结论

本交付覆盖 B 组 81/81 只基金，已为每只基金写入终态映射、具体候选、数据尝试、任务与核验记录。独立的结构门禁结果为 `structural_pass=true`；这只是覆盖与引用完整性检查，不等同于投资或生产批准。

| 指标 | 数量 |
|---|---:|
| 分配基金 | 81 |
| 已建共同目标/候选面板 | 79 |
| 有真实 OOS 残差/权重的唯一基金 | 63 |
| MATCH | 63 |
| INSUFFICIENT_DATA | 18 |
| NO_MATCH_IN_TESTED_SET | 0 |
| 目标结果行（4 期限及路径） | 648 |
| 候选比较行 | 4176 |
| 数据覆盖行 | 810 |
| fetch attempts | 17828 |
| 逐项任务 | 567 |

主路径计数：PCF_BASKET 1（520600 使用既有 R1 成分篮子证据），ETF_MARKET_PRICE 78，INDEX_STRUCTURAL 2。其中 18 只没有达到可采用的真实 OOS 门槛：16 只只有短样本，2 只在远端 ETF/PCF 归档中没有目标价。

## 计算口径

- 引擎版本 `FULL237_RHO060_V1`；代码 SHA256 `fe73e1368e8b48b276646d0130c662c9de785d6ab306d7f2780d1416d31d8661`。
- 主期限 30 分钟，同时输出 5/15/60 分钟；收益只使用同日同一连续交易时段的分钟端点，午休与跨日端点丢弃。
- 滚动样本为过去 60 个有效日，其中 50 日拟合、10 日验证，然后以前 60 日重拟合并预测下一日；beta 非负、单腿不超过 2、总 beta 不超过 2。
- 单腿优先；候选工具为 HSI_FUT、HHI_FUT、HTI_FUT、02800、02828、03032、03033、02845；跨族双腿只作为备选。
- MATCH 同时要求相关系数不低于 0.60 且残差方差下降；没有把相关系数单独当作通过。

## 数据与局限

ETF 目标归档共 1,803,927 行，79/81 只基金有目标价；PCF 明细共 279,948 行，79/81 只基金有明细行。候选分钟归档来自共享 PM 面板，覆盖 2026-03-03 至 2026-08-03；因此本包的候选实际样本主要是 13:00–15:00，不能替代完整 AM+PM 生产行情。

ETF_MARKET_PRICE 是二级市场价格目标，会含溢价/折价、申赎和时点基差；它不是 PCF 成分篮子。除 520600 外，PCF 明细没有配套的自有成分 1 分钟价格包，未跨基金复用。官方指数范围证据在多数 B assignment 中仍是 UNVERIFIED；事件/公司行动数据源本包未闭环，81 只任务均明确记录为 `BLOCKED_NO_EVENT_FEED`。

## 文件索引

- [mapping.json](mapping.json) / [mapping.csv](mapping.csv)：81 只逐项终态映射。
- [RESULTS.xlsx](RESULTS.xlsx)：8 个约定工作表。
- [target_results.jsonl](target_results.jsonl)、[candidate_comparison.jsonl](candidate_comparison.jsonl)：机器结果。
- [data_coverage.jsonl](data_coverage.jsonl)、[fetch_attempts.jsonl](fetch_attempts.jsonl)：覆盖与实际尝试。
- [tasks.jsonl](tasks.jsonl)、[checks.jsonl](checks.jsonl)、[evidence.jsonl](evidence.jsonl)：执行、核验与证据索引。
- [delivery_check.json](delivery_check.json)：结构门禁记录。

# Agent B R1 返工报告

## 结论

本组的 `repair_status=COMPLETE`，`research_status=PARTIAL`。81 只基金的分母已保留；只有 520600.SH 完成了本轮逐只证据、PCF、成分 1 分钟价格和锁定政策研究。其余 80 只不继承 520600 的结果，明确保留为 `INSUFFICIENT_EVIDENCE / NOT_RUN`。

520600.SH 的 30 分钟价格代理通过本轮主研究门槛：锁定的 HHI+HTI 期货名义 beta 为 HHI=0.5092797294、HTI=0.4490979543；24 个有效新 OOS 日的 Pearson 相关为 0.7400，日块 bootstrap 95% CI 为 [0.6880, 0.7842]，残差方差降低 54.73%。因此基金决策为 `SUITABLE_PRICE_PROXY`；执行状态为 `CONDITIONAL`，不是无条件交易授权。

## 关键修复

| 修复项 | 实际结果 |
|---|---|
| 产品范围证据 | 归档广发基金官方产品资料 PDF，确认代码、2024-12-30 上市日、港股通汽车主题和全复制策略。 |
| PCF | 参数化请求并严格按返回日期校验；2026-08-04—09-04 共 24 个精确页面，均为 50 行。 |
| 期货 | 合并旧 21 日与新补抓 08-10/11/12；HSI/HHI/HTI manifest 共 24 个交易日。 |
| 港股成分 | 50 个非零 PCF 代码均补到 24 日实际 SEHK STK 1 分钟记录；每次记录 `includeExpired=false`。 |
| 篮子面板 | PCF 固定数量、HKD 同币种、现金替代行不贡献价格风险；24 日共 7,791 个共同端点，无重复键。 |
| 研究口径 | 先锁定旧窗口 HHI+HTI 政策，再读取新期；Pearson 相关和 VR 分开记录，未把 rho 当 VR。 |

## 新期指标

| 周期 | OOS Pearson | 日块 95% CI | 残差方差降低 | 状态 |
|---:|---:|---:|---:|---|
| 5m | 0.6409 | [0.5983, 0.6805] | 40.50% | CONFIRMED |
| 15m | 0.7337 | [0.6923, 0.7675] | 53.17% | CONFIRMED |
| 30m | 0.7400 | [0.6880, 0.7842] | 54.73% | CONFIRMED |
| 60m | 0.7188 | [0.6502, 0.7817] | 51.57% | CONFIRMED |

主相关门槛为 Pearson OOS ≥ 0.60；VR 单独要求实际名义 beta 使残差方差下降。旧的 VR50%、CI30%、严格 VR40% 未作为本轮硬门槛。

## 证据、表格和复现

- [RESULTS.xlsx](./RESULTS.xlsx)：16 个工作表（阅读说明 + 11 个基础表 + `repair_checks`、`fetch_attempts`、`research_gates`、`exploratory_policies`）。
- [assets.json](./assets.json)：资产路径、日期和 SHA-256。
- [SHARED_FINDINGS.md](./SHARED_FINDINGS.md)：供主线程复用的事实、来源和缺口。
- [checks/technical_check.json](./checks/technical_check.json)：分母、日期、哈希、无重复和泄漏检查；全部通过。
- [checks/workbook_render.json](./checks/workbook_render.json)：16 张表逐表渲染结果；全部通过。
- [scripts/build_r1_tables.py](./scripts/build_r1_tables.py)：生成机器表。
- [scripts/build_workbook.js](./scripts/build_workbook.js)：用 `@oai/artifact-tool` 创建并渲染工作簿。

## 未闭环事项

1. 50 个 PCF 证券的逐证券官方公司行动/停牌核验仍不完整；工作簿 `Events` 明确标出 `REVIEW_NOT_COMPLETE`，不隐瞒这一点。
2. 02800/02828 只作为港股 ETF 补充候选，当前为 22/24 日；纯 PCF 篮子不依赖这两只 ETF，因此不影响 520600 的主面板。
3. 真实成交费用、借券、资金成本、取整持仓和 live quote 未核实；`Execution_Scenarios` 全部为 `CONDITIONAL`，成本字段不填 0。
4. 其余 80 只基金尚未在本 B 任务中完成独立 PCF/成分面板和新期研究；不得用 520600 的结果替代。

独立远端逐笔归档只覆盖 15 个已复制日期，08-26 以后没有对应本地逐股票归档；它仅用于交叉核验，未用来补齐研究数据。

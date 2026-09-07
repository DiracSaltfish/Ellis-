# C组 R1 重做交付报告

更新时间：2026-09-06  
运行：`C-R1-BASKET-20260906T133938Z`  
标准：`USER_RHO_060_FUTURES_ETF`

## 状态

- `repair_status=COMPLETE`：已修复并验证旧的 `includeExpired` 参数问题。HBI 期货使用 `includeExpired=true`，03069/03174 作为 STK 使用 `false`；全区间 402 次工具/日期尝试中 401 次成功、1 次 HBI 2026-04-29 `NOT_FOUND`、0 次 321 参数错误。原始条和逐次 attempts 均已保存。
- `research_status=PARTIAL`：R1 研究已实际运行，但新确认期有效日不足 20 日，且逐 PCF 成分事件清单仍未完成，因此没有把探索性结果升级为确认方案。

## 决策计数

| decision | count |
|---|---:|
| SUITABLE_PRICE_PROXY | 0 |
| NONE_IN_TESTED_SET | 0 |
| INSUFFICIENT_EVIDENCE | 55 |
| OUT_OF_SCOPE | 0 |

## 研究结果

C 组分配 55 只基金均已生成结论行；22 只基金形成了至少一个可构造的 PCF×港股价格篮子日。研究引擎共保存 13,519 条 OOS 端点残差、677 条策略比较/指标记录和 546 条滚动权重记录。候选池和策略在研究前已写入 `selection_lock_R1.json`，主池仅含港股期货和港股 ETF，不含港股单股现货。

门槛定义为：同样本外端点的正向工具代理与篮子收益 Pearson `rho>=0.60`，同时残差方差相对无对冲目标方差降低；`rho` 与 VR 分开记录。新确认仍需至少 20 个有效 OOS 日。

520760 代表性重算的 30 分钟 OOS 探索结果为：03069 单腿，Pearson rho=`0.9388`，残差方差降低=`0.8797`，OOS 日=`28`；新确认期有效日只有 `5`，所以该结果仍是 `REUSED_OR_UNPROVEN/EXPLORATORY`，不是 `NEW_LOCKED`。

## 数据与口径

- PCF 篮子按“数量股 × 同日港股 1 分钟收盘价”构造；不把固定现金替代金额当价格，不对缺失价格静默填 0。
- 使用 13:01—15:00（Asia/Hong_Kong）分钟端点，保留 5/15/30/60 分钟标签；1 分钟、现金替代、结算后 FX 口径保持不变。
- CN ETF 二级市场分钟价不是纯 PCF 篮子模型的必要输入；如需分析 CN ETF 折溢价/基差，应作为单独问题处理。
- 事件表为逐基金 `EVENT_MANIFEST_PENDING` 待核验行，没有把未核验误写成 `REVIEWED_NO_EVENT`。官方范围证据已从交易所 ETF 名录保存为 C 组永久副本，但 PCF 资格和逐证券事件仍标记为 `PARTIAL`。
- 执行成本、借券和资金成本未充分核实，相关字段保持 `null`；没有访问账户、持仓、实时订阅，也没有下单。

## 交付物

- `RESULTS.xlsx`：16 张表，首张为阅读说明；包含 11 张基础明细表、`修复检查`、`抓取尝试`、`研究门槛`、`探索策略`。
- `fund_decisions.json/csv`、`model_metrics.json/csv`、`exploratory_policies.json/csv` 等机器明细。
- `data/industry_history_bars.jsonl.gz` 与 `data/fetch_attempts_industry.jsonl`：原始 TWS 条和逐请求审计。
- `selection_lock_R1.json`、`assets.json`、`SHARED_FINDINGS.md`、脚本及预览图。

后续若要形成可执行确认结论，需要补齐新确认期至少 20 个有效 PCF/HK OOS 日，并完成逐 PCF 成分的官方公司行动/暂停复牌核验；在此之前，所有候选仅可作为探索性参考。

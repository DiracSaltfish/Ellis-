# C 组全量覆盖交付报告

运行：`C-FULL-20260906T151113Z`。本报告只描述 C 组交付，不代表主 agent 验收。

## 覆盖与结论

- 分配基金：55/55，55 个唯一 `fund_id`，无 `UNPROCESSED`。
- 终态：`MATCH` 7、`NO_MATCH_IN_TESTED_SET` 2、`INSUFFICIENT_DATA` 46、`OUT_OF_SCOPE` 0。
- 实际回测基金数：9 个唯一基金（定义为真实生成 OOS 残差的基金），不是按周期、候选或残差行数重复计数。
- 映射主路径：`PCF_BASKET` 2、`ETF_MARKET_PRICE` 7、`INDEX_STRUCTURAL` 46。
- 495 条分目标记录：PCF 220 条（55×4 周期）、ETF 市场价 220 条（55×4 周期）、结构候选 55 条。
- 3,699 条候选比较、32,238 条 OOS 残差、386 条逐日权重已落盘。

## 实际 OOS 回测基金

主周期为 30 分钟。数值门槛为 OOS Pearson rho≥0.60 且残差方差降低；单腿在验证阶段达标时优先。ETF_MARKET_PRICE 是基金自身市场价格，不能解释为 PCF 篮子。

| 基金 | 主路径 | 结论 | 工具 | rho | 方差降低 | OOS日/标签 |
|---|---|---|---|---:|---:|---:|
| 513070.SH | ETF_MARKET_PRICE | MATCH | 02828 | 0.717020 | 0.509536 | 39/3510 |
| 513230.SH | ETF_MARKET_PRICE | MATCH | 02828 | 0.678028 | 0.458998 | 39/3510 |
| 513590.SH | ETF_MARKET_PRICE | NO_MATCH_IN_TESTED_SET | 02828 | 0.598169 | 0.355249 | 39/3510 |
| 513780.SH | ETF_MARKET_PRICE | MATCH | 03069 | 0.940553 | 0.884602 | 40/3599 |
| 520620.SH | ETF_MARKET_PRICE | NO_MATCH_IN_TESTED_SET | 02800 | 0.592445 | 0.343381 | 39/3510 |
| 520700.SH | ETF_MARKET_PRICE | MATCH | 03069 | 0.944078 | 0.891010 | 40/3599 |
| 520760.SH | PCF_BASKET | MATCH | 03069 | 0.978780 | 0.957369 | 15/110 |
| 520930.SH | PCF_BASKET | MATCH | 03069 | 0.978768 | 0.957340 | 15/110 |
| 520970.SH | ETF_MARKET_PRICE | MATCH | 03069 | 0.927284 | 0.859347 | 40/3599 |

其中 520760.SH 与 520930.SH 的 OOS 只有 15 个有效交易日，已标记 `SHORT_SAMPLE`，不能视为 20 日以上的新确认；其余 7 个市场价结果为 `SEEN_EXPLORATORY`。其余基金的 PCF、ETF 市场价或二者均未达到完整滚动 OOS 条件，最终映射保留为结构候选或不足数据。

## 方法与数据边界

- PCF 路径严格使用“本基金 PCF 数量 × 同日港股 1 分钟标记价”；最近可用价最多滞后 2 分钟，不做第二次填充，不把固定现金替代金额当价格。
- ETF 市场价路径独立尝试本基金 CN ETF 的 1 分钟 `etf_price`，并明确 premium/basis 局限，绝不把它标为 PCF_BASKET。
- 结构路径逐基金列出具体工具与经济理由，rho、VR 保持 null。
- 交易时点使用 Asia/Hong_Kong 的分钟结束标签，只允许 09:30–11:30 与 13:00–15:00 连续窗口，不跨午休、不跨日。
- 主周期采用先前 60 个有效日：前 50 日拟合、后 10 日验证；冻结验证选出的系数用于下一日 OOS，并在 OOS 前用完整 60 日重拟合。5/15/60 分钟记录为描述性比较，未冒充滚动确认。
- beta 为非负约束，每腿≤2，总和≤2；没有订单、账户、持仓或实时订阅。

## 缺口与后续动作

46 只基金仍是 `INSUFFICIENT_DATA`，缺口已按基金写入 `remaining_gaps`、`fetch_attempts.jsonl` 与“缺口与尝试”sheet；主要是没有基金自身 1 分钟市场价面板、PCF 记录不足或严格 2 分钟对齐后有效篮子日不足。所有基金均实际尝试了 PCF、ETF_MARKET_PRICE 与结构路径，未用“PCF不足”跳过 ETF 市场价路径。

范围身份在提供的分配清单中保持 `UNVERIFIED`，事件证据为 `PARTIAL`；这些限制没有被写成零值或“无事件”。行业历史取数审计为 401 次 SUCCESS、1 次明确 NOT_FOUND、0 次参数错误。

## 交付物

机器文件、结果、残差、权重、脚本、输入指纹、`selection_lock_full_C.json`、证据索引和 8-sheet `RESULTS.xlsx` 均位于本目录。B 公共引擎版本为 `FULL237_RHO060_V1`，执行与锁定 hash：`fe73e1368e8b48b276646d0130c662c9de785d6ab306d7f2780d1416d31d8661`。

# Agent A 独立研究报告

## 结论

本交付覆盖 A 的 74 只分配种子，以及从上交所/深交所官方 ETF 目录发现、且不在完整 210 只 manifest 中的 27 只补漏候选；A 所有权总分母为 101 只。逐只机器结论为：INSUFFICIENT_EVIDENCE 80 只、OUT_OF_SCOPE 21 只、SUITABLE_PRICE_PROXY 0 只、NONE_IN_TESTED_SET 0 只。当前不使用 NONE_IN_TESTED_SET，因为新确认证据不足，不能把“未测试”解释为“测试后不适合”。

## 为什么没有新确认

目标新确认窗口为 2026-08-04 至 2026-09-04。当前可审计目录只有 5 个 PCF 明细日，港股交易文件覆盖到 2026-08-25，未形成完整共同 PCF、港股分钟和 ETF 分钟样本；有效新 OOS 记为 0。按合同要求，任何候选都未被升级为价格对冲确认政策，coverage_complete 保持 false。成本表只登记 0/1/2.5/5/10 bp 单边情景，不把未核实的借券、资金、容量或固定费用当作已知事实。

## 旧结果的使用边界

已有 23 只基金的旧批次技术结果写入 model_metrics、weights、sensitivity 和 evidence，但全部标记 REUSED_OR_UNPROVEN / exploration_only。它们只用于记录研究轨迹和模型接口，不构成 2026-08-04 至 2026-09-04 的新确认，也不直接生成当前 recommended_policy_id。候选工具池包括 HSI、国企、恒生科技指数期货和港股 ETF；证券/非银风险单独标记，未用银行期货作为证券行业精确替代。

## 事件处理

513090 的 01788 已保留官方停牌/复牌事件：停牌区间为 2026-07-23 至 2026-08-10。PCF 数量不删除，停牌期间使用停牌前最后收盘价冻结估值，并将冻结权重与严格样本影响单列；没有用未来复牌价格填补停牌价格。由于新确认期证据仍不完整，该事件处理也不会单独产生当前可执行政策。

## 官方目录与审计文件

官方目录来源：SSE [ETF 基金列表](https://etf.sse.com.cn/fundlist/)，SZSE [ETF 行情/基金列表](https://fund.szse.cn/marketdata/etf/)，HKEX [ETP Overview](https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en)。目录抓取共登记 SSE 1116 条、SZSE 728 条；目录身份字段不等同于逐只投资通道认证。

机器明细、来源证据、QA、锁定文件、覆盖快照和 RESULTS.xlsx 均位于本目录。后续重跑前需补齐完整 PCF/共同分钟样本、逐只官方投资范围、全证券事件 manifest，并重新执行缓存指纹检查、selection lock 和确认门槛。

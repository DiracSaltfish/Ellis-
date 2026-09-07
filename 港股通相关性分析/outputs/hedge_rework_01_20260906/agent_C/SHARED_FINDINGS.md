# C组返工可复用资产与发现

更新时间：2026-09-06T13:52:17.179051+00:00

## 已修复并验证

- `fetch_industry_history_C.py` 已永久保存，使用 clientId 参数、共享历史锁、断点续传、逐请求 attempts 记录和错误分类。
- `includeExpired` 只对 `FUT` 设置为 `True`；`03069`/`03174` 的 `STK` 请求显式保持 `False`。
- 全区间 2026-03-03—2026-09-04：402 次工具/日期尝试，401 SUCCESS、1 个 HBI 2026-04-29 NOT_FOUND、0 个321参数错误；STK 的 `includeExpired=false` 已在全区间审计。
- TWS只调用合约详情与历史行情接口；没有订单、持仓、账户或实时订阅请求。

- R1 520760 30分钟代表性 OOS：03069 单腿 rho≈0.939、残差方差降低≈88.0%；但新确认日只有5日，不能升级为确认。
- 55只基金统一输出均把 `HEDGE_RETURN_CORRELATION_GE_060` 与 `RESIDUAL_VARIANCE_REDUCED` 作为研究门槛，并和QA分离。

## 共享路径

- `assets.json`：/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_rework_01_20260906/agent_C/assets.json
- STK/期货取数器：/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_rework_01_20260906/agent_C/scripts/fetch_industry_history_C.py
- 样本K线：/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_rework_01_20260906/agent_C/data/industry_history_sample_20260825.jsonl.gz
- 样本取数尝试：/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_rework_01_20260906/agent_C/data/fetch_attempts_sample_20260825.jsonl
- 全区间原始条：/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_rework_01_20260906/agent_C/data/industry_history_bars.jsonl.gz
- 全区间取数审计：/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_rework_01_20260906/agent_C/data/fetch_attempts_industry.jsonl
- R1 锁定文件：/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_rework_01_20260906/agent_C/selection_lock_R1.json
- R1 结果Excel：/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_rework_01_20260906/agent_C/RESULTS.xlsx

## 尚未完成

- 新确认期可用PCF/HK日仍不足20日；因此本轮 research_status 保持 PARTIAL，55只均未升级为确认方案。
- 需要补全每只基金的官方范围与PCF证券事件证据；当前不能把行业工具样本外表现写成确认结论。

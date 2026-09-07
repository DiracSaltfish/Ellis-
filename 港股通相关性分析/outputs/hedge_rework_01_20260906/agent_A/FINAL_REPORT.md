# Agent A R1返工报告

## 状态

- repair_status: PARTIAL
- research_status: PARTIAL
- decision_counts: {"INSUFFICIENT_EVIDENCE":90,"NONE_IN_TESTED_SET":0,"OUT_OF_SCOPE":11,"SUITABLE_PRICE_PROXY":0}
- 旧21条范围复核：官方资料成功取得21/21；重新判为OUT_OF_SCOPE=11；仍待范围核实=10。
- 分母：101只，所有权固定为A。

## 已完成

1. 上轮21条OUT_OF_SCOPE不再直接沿用旧classification。本轮逐只尝试官方产品页面/文件；21/21份资料成功缓存并进入 evidence.json，其中11只由本轮资料重新支持范围外，10只仍保留待核实。
2. 全局交易所目录使用单一 A-DIRECTORY-GLOBAL-20260906；逐只产品资料使用唯一 A-SCOPE-DOC-<fund_id>，清理了重复证据ID。
3. 513090已完成真实PCF数量→1分钟篮子→60日滚动训练（50日拟合+10日验证）→候选选择→5/15/30/60分钟OOS残差探索。
4. 01788数量保留；停牌期间只对缺失的01788价格使用停牌前收盘冻结，其他证券缺价不填。

## 研究口径

默认工具池为HSI_FUT、HHI_FUT、HTI_FUT、02800、02828，优先期货/香港ETF；证券/非银风险不把银行期货作为精确替代。rho>=0.60是收益相关性门槛，不代表方差降低60%；残差方差、尾部风险和区间独立报告。成本/借券/资金/容量没有可靠证据时保留null。

513090数值结果是旧窗口探索（截至2026-08-03），不属于新确认政策。新确认期2026-08-04至2026-09-04有效新OOS=0，任何探索优选未写入recommended_policy_id。其他基金旧模型若存在，单列为REUSED_OR_UNPROVEN。

## 尚未完成

- 除上述旧21条外，其他基金的官方范围证据仍未全部补齐，相关基金继续留在A分母。
- 新确认期PCF/共同分钟样本不足；ETF二级市场分钟只作为折溢价/基差研究输入，不再阻塞纯PCF篮子模型。
- 513090之外未在本轮逐只重跑PCF篮子；全PCF证券事件manifest、缓存失效测试、完整费用/容量验证尚待完成。

详见 assets.json、SHARED_FINDINGS.md、research/ 和四张返工新增表。

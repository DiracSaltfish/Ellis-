# Excel及机器明细字段规范

每组11张明细表；另加一张自动汇总的“阅读说明”。Excel由对应CSV/JSON生成，每阶段更新任务流水。不得手工填造数值。

## 基金结论 / fund_decisions.csv

|列名|类型|定义|
|---|---|---|
|fund_id|string|基金代码，唯一；保留交易所后缀|
|fund_name|string|官方全称|
|owner|string|A/B/C|
|index_id|string|官方指数代码，未知null|
|index_name|string|官方指数名|
|scope|enum|CONNECT_PURE/CONNECT_MIXED/QDII_ONLY/OTHER/UNVERIFIED|
|scope_evidence_id|string|关联官方证据|
|listing_date|date|上市日|
|primary_horizon_min|integer|30|
|decision|enum|SUITABLE_PRICE_PROXY/NONE_IN_TESTED_SET/INSUFFICIENT_EVIDENCE/OUT_OF_SCOPE|
|reason_codes|json|逐项门槛失败/通过代码|
|reason_detail|string|含具体数值，不用低相关概括|
|recommended_policy_id|string|未通过时null|
|primary_tools|json|工具ID列表，未通过时null|
|backup_policy_id|string|备选，未通过则null|
|exploratory_best_policy_id|string|仅探索，不能混作主方案|
|execution_status|enum|SUPPORTED/CONDITIONAL/NOT_AVAILABLE/UNKNOWN|
|confirmation_status|enum|NEW_LOCKED/REUSED_OR_UNPROVEN/UNAVAILABLE|
|confirmation_start|date|有效确认期首日|
|confirmation_end|date|有效确认期末日|
|oos_days|integer|有效日数|
|target_std_bp|number|未对冲标准差bp|
|residual_std_bp|number|主政策残差标准差bp|
|variance_reduction|ratio|小数|
|ci_low|ratio|对应主政策95%CI|
|ci_high|ratio|对应主政策95%CI|
|up_es95_bp|number|上侧尾部均值bp|
|down_es95_bp|number|下侧尾部均值bp|
|positive_block_fraction|ratio|5日块正改善比例|
|strict_refit_vr|ratio|严格场景真正重拟合值|
|effective_quote_coverage|ratio|共同预期端点有效率|
|latest_beta_date|date|最新权重生效日期|
|latest_beta|json|名义权重和工具映射|
|candidate_coverage_complete|boolean|合理工具池是否充分检验|
|event_coverage_complete|boolean|关键估值事件是否足够核验|
|remaining_gaps|json|数据/研究缺口|
|invalidation_triggers|json|模型应停用/重估条件|
|source_run_id|string|来源运行|
|result_path|path|自己永久目录文件|
|updated_at_utc|datetime|真实时间|
## 模型指标 / model_metrics.csv

|列名|类型|定义|
|---|---|---|
|run_id|string|运行ID|
|fund_id|string|基金|
|horizon_min|integer|5/15/30/60|
|policy_id|string|预先锁定政策|
|model_id|string|模型或候选|
|scenario_id|string|主场景/敏感性|
|sample_hash|string|端点及输入hash|
|confirmation_status|enum|是否新确认|
|oos_start|date|首日|
|oos_end|date|末日|
|oos_days|integer|有效日数|
|sample_count|integer|重叠标签数，非独立样本数|
|target_std_bp|number|目标标准差|
|residual_std_bp|number|残差标准差|
|variance_reduction|ratio|1-var(residual)/var(target)|
|ci_low|ratio|没有计算则null|
|ci_high|ratio|没有计算则null|
|bootstrap_method|string|日/多日block和次数|
|bootstrap_seed|integer|种子|
|target_up_es95_bp|number|未对冲上侧尾部|
|target_down_es95_bp|number|未对冲下侧尾部|
|up_es95_bp|number|对冲上侧尾部|
|down_es95_bp|number|对冲下侧尾部|
|positive_block_fraction|ratio|5日块改善率|
|residual_mean_bp|number|残差均值|
|beta_turnover|number|名义换手定义附配置|
|decision_gate_results|json|每个门槛数值和通过状态|
|residual_path|path|可独立复算文件|
## 工具候选 / tool_candidates.csv

|列名|类型|定义|
|---|---|---|
|fund_id|string|基金|
|tool_id|string|合约/ETF真实ID|
|risk_family|string|对应指数/行业|
|rationale|string|经济映射原因|
|asset_type|string|期货/ETF/单股期货等|
|official_url|url|产品证据|
|listed_from|date|历史可用起日|
|listed_to|date|终日或null|
|currency|string|币种|
|multiplier|number|合约乘数|
|lot_size|number|最小交易单位|
|session|string|适用交易时段|
|quote_coverage|ratio|分钟可用率|
|data_start|date|数据起日|
|data_end|date|数据止日|
|short_status|enum|SUPPORTED/UNKNOWN/NOT_AVAILABLE|
|included|boolean|是否进入确认候选池|
|exclusion_reason|string|排除/缺数据原因|
|evidence_id|string|来源证据|
|selection_lock_hash|string|锁定文件hash|
## 逐日权重 / weights.csv

|列名|类型|定义|
|---|---|---|
|fund_id|string|基金|
|horizon_min|integer|周期|
|policy_id|string|政策|
|effective_date|date|测试日|
|train_start|date|训练起日|
|train_end|date|训练末日，必须早于测试|
|validation_start|date|内层验证起日|
|validation_end|date|内层验证末日|
|tool_id|string|工具|
|beta|number|还原后的名义系数|
|currency_conversion|number|适用汇率|
|selection_reason|string|少腿/成本/tie规则|
|config_hash|string|配置hash|
## 方向规模成本 / execution_scenarios.csv

|列名|类型|定义|
|---|---|---|
|fund_id|string|基金|
|horizon_min|integer|周期|
|direction|enum|LONG_BASKET_SHORT_HEDGE/SHORT_BASKET_LONG_HEDGE|
|notional_cny|number|100万/1000万/5000万|
|policy_id|string|内层选择政策|
|cost_scenario_id|string|假设标记|
|per_side_variable_cost_bp|number|每腿名义单边bp|
|known_fixed_fees_cny|number|可证实固定费，未知null|
|borrow_cost_bp|number|未核实null，不能0|
|funding_cost_bp|number|未核实null|
|basket_total_cost_bp|number|名义与换手折算|
|rounded_positions|json|整数张/手以及报价时点|
|rounded_residual_std_bp|number|取整实施残差|
|rounded_vr|ratio|取整方差降低|
|risk_cost_score_bp|number|max双侧ES+成本|
|pareto_optimal|boolean|是否在前沿|
|execution_status|enum|SUPPORTED/CONDITIONAL/NOT_AVAILABLE/UNKNOWN|
|assumptions|json|费用/借券/时段条件|
|fee_evidence_id|string|费用证据|
|run_id|string|来源|
## 数据覆盖 / data_coverage.csv

|列名|类型|定义|
|---|---|---|
|fund_id|string|基金|
|security_id|string|成分/工具/FX/PCF|
|data_type|string|minute/PCF/FX/event|
|source|string|系统或URL|
|path|path|永久小包|
|sha256|string|文件hash|
|start_date|date|起日|
|end_date|date|末日|
|expected_rows|integer|可比较分母|
|actual_rows|integer|实际值|
|missing_dates|json|缺日期|
|duplicates|integer|重复数|
|timezone|string|时区|
|timestamp_semantics|string|K线起/止|
|adjustment|string|未复权/事件调整|
|retrieved_at_utc|datetime|获取实际时间|
|quality_status|enum|PASS/FAIL/UNKNOWN|
|gap_action|string|补全动作或具体障碍|
## 事件核验 / events.csv

|列名|类型|定义|
|---|---|---|
|fund_id|string|基金|
|security_id|string|PCF原始代码|
|event_type|string|HALT/RESUME/DIVIDEND/SPLIT/CODE_CHANGE/REVIEWED_NO_EVENT等|
|effective_from|datetime|有效起点|
|effective_to|datetime|有效终点|
|published_at|datetime|公布时点|
|official_url|url|官方证据|
|evidence_path|path|本地原文|
|evidence_sha256|string|原文hash|
|treatment|string|估值处理|
|affected_dates|json|影响日期|
|max_weight|ratio|最大篮子权重|
|verified|boolean|足够证据|
|numerical_check_path|path|独立核对|
|remaining_risk|string|复牌/潜在公司行动等未覆盖风险|
## 稳健性 / sensitivity.csv

|列名|类型|定义|
|---|---|---|
|fund_id|string|基金|
|horizon_min|integer|周期|
|policy_id|string|模型政策|
|base_run_id|string|对应真实主场景|
|scenario_id|string|场景|
|refit|boolean|是否真正重新拟合|
|sample_hash|string|端点hash|
|oos_days|integer|场景日数|
|variance_reduction|ratio|场景VR|
|residual_std_bp|number|场景残差|
|up_es95_bp|number|上侧ES|
|down_es95_bp|number|下侧ES|
|pass|boolean|门槛结果|
|explanation|string|日期变化/不可执行提前等解释|
|source_path|path|实际结果|
## 任务流水 / tasks.csv

|列名|类型|定义|
|---|---|---|
|task_id|string|唯一阶段任务ID|
|owner|string|A/B/C|
|fund_id|string|基金或组级GLOBAL|
|phase|enum|P0/P1/P2/P3/P4/P5/P6/P7|
|status|enum|TODO/RUNNING/DONE/BLOCKED/NOT_APPLICABLE|
|started_at_utc|datetime|实际开始时间|
|finished_at_utc|datetime|实际结束时间|
|action|string|具体执行动作|
|inputs|json|路径及hash|
|outputs|json|文件链接|
|detailed_result|string|每项任务详细输出|
|validation|string|核验方式和数值|
|blocker_type|enum|NONE/DATA/SOURCE/ACCESS/COMPUTE/METHODOLOGY|
|blocker_evidence|string|实际错误/尝试来源，不含秘密|
|next_action|string|下一步|
|run_command|string|复现命令，无密钥|
## 来源证据 / evidence.csv

|列名|类型|定义|
|---|---|---|
|evidence_id|string|唯一ID|
|fund_id|string|基金或GLOBAL|
|purpose|string|分类/事件/工具/费用/分母|
|publisher|string|发行人/交易所|
|url|url|原文链接|
|published_at|datetime|原文时间，未知null|
|retrieved_at_utc|datetime|抓取实际时间|
|local_path|path|归档原文|
|sha256|string|hash|
|locator|string|页码/段落|
|finding|string|短证据或自述，遵守引用限制|
|sufficiency|enum|SUFFICIENT/PARTIAL/UNAVAILABLE|
## 独立检查 / qa_checks.csv

|列名|类型|定义|
|---|---|---|
|check_id|string|唯一ID|
|fund_id|string|基金|
|check_type|string|指标复算/泄漏/冻结等|
|run_id|string|对应运行|
|actual|json|实际值|
|expected|json|预期|
|tolerance|number|容差或null|
|passed|boolean|实际运行才有真值|
|source_path|path|检查脚本与结果|
|checked_at_utc|datetime|真实时间|
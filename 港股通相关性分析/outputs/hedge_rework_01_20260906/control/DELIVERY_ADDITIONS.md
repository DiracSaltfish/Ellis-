# R1交付字段与核验规范

保留schema_base.json的11张明细及字段，JSON使用真正布尔值/null/数组，不把JSON嵌套成字符串。标识符为文本，前导零保留。原11张表按基金保存细节，不用大量空成本情景占位冒充已跑场景。Excel第一张为中文阅读说明，其余原11张表加返工记录、取数尝试、研究门槛、探索优选，共16张。

## repair_checks.csv/json — 返工记录

每一已确认问题一行，不与模型合格混为一谈。列：repair_id、owner、fund_id（组级GLOBAL）、finding、required_fix、status（TODO/RUNNING/VERIFIED/BLOCKED）、started_at_utc、finished_at_utc、changed_files（数组）、test_command、actual_result、evidence_paths（数组）、remaining_issue。

每组至少包含：已确认专属问题、逐只范围证据、永久代码归档、任务真实时间、缺口实证、依赖修正、模型接口读取候选、缓存失效、QA分层。VERIFIED必须有实际验证输出；不能只写“已添加检查”。

## fetch_attempts.csv/json — 取数尝试

列：attempt_id（唯一）、owner、fund_id、security_id、data_type、source_name、source_url_or_method、request_parameters（剔除秘密）、requested_start、requested_end、bar_size、attempted_at_utc、completed_at_utc、status（SUCCESS/PARTIAL/PARAMETER_ERROR/ACCESS_DENIED/NOT_FOUND/NETWORK_ERROR/PACING/OTHER_ERROR）、error_code、error_summary、returned_rows、returned_start、returned_end、raw_path、sha256、next_action。

请求失败为null/明确状态，不写成真实0成交或0报价。处理了真实零行也必须同时保留API错误，不得抹掉参数错误。每条关键缺口至少能关联实际尝试或权威来源证明；没有尝试不得写已证明不可得。

## research_gates.csv/json — 研究门槛

列：gate_id（唯一）、owner、fund_id、run_id、horizon_min、scenario_id、gate_type（DATA/METHOD/RISK/EXECUTION）、gate_name、threshold、actual、status（PASS/FAIL/NOT_RUN/NOT_APPLICABLE）、sample_hash、evidence_path、evaluated_at_utc。

qa_checks只记录“检查执行是否正确”，不得拿“正确阻止了错误确认”当研究天数/风险门槛通过。研究条件不满足在本表写FAIL；根本未跑写NOT_RUN。整体通过只能从真实研究门槛判断。

## exploratory_policies.csv/json — 探索优选

有可用探索样本的基金应提供真实比较，而不是所有字段为空。列：fund_id、owner、horizon_min、sample_start、sample_end、sample_status（EXPLORATORY/REUSED_OR_UNPROVEN）、policy_id、selection_rule、tools、last_beta_date、last_beta、residual_std_bp、variance_reduction、up_es95_bp、down_es95_bp、single_leg_comparator、complexity_benefit_bp、candidate_scope、missing_confirmation_requirements、run_id、residual_path。

探索优选可以用于后续候选排序，不能填进未获确认基金的recommended_policy_id。事后最佳单腿需在selection_rule明确事后参照，不能冒充预先政策。

## assets.json与SHARED_FINDINGS.md — 跨组只读复用

assets.json为行数组，列：asset_id、owner、kind（PCF/MINUTE/FX/EVENT/SCOPE/CODE）、security_or_fund_ids、date_start、date_end、path、sha256、schema_version、timestamp_semantics、quality_status、known_gaps、created_at_utc。

每个资产只归一个组写。消费方记录引用hash，不改变生产者文件。PCF抓取方法和STK修正接口应尽早发布，不等最终Excel。

## tasks、证据与实际验收

tasks逐基金阶段初始化为TODO，实际开始才写开始时间，结束才写结束时间；不拿生成表格的时间同时填满所有开始/结束。事件代码示例不能替代完整证券范围检查。evidence_id唯一、引用必须存在，local_path若非空应存在并核验hash；目录资料与产品通道证据分开。

交付时额外保存：运行环境版本、requirements、配置、真实逐端点残差、逐日权重、模型选择与bootstrap种子、selection_lock、数据指纹、原始官方材料、独立复算结果、面板标量核对。至少每只已跑基金3个实际端点（覆盖事件日若有）。做过的代码测试与尚未运行的测试明确分开。

保存FINAL_REPORT.md、STATUS.md、RESULTS.xlsx与机器文件。STATUS显示分母、已确认数、已测不合适数、范围外数、尚待证据数、实际完成回测数及最新工作，不把填表数量当完成回测数量。

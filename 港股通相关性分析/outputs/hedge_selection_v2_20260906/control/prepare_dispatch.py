"""Build immutable ownership and machine-readable deliverable contracts."""
from pathlib import Path
import json, csv, hashlib
from datetime import datetime, timezone

CONTROL = Path(__file__).resolve().parent
ROOT = CONTROL.parent
PROJECT = ROOT.parents[1]
source = PROJECT / 'outputs/final_review_20260906/candidate_review.json'
records = json.loads(source.read_text())

def owner(row):
    s = row['index_name'] or row['fund_name']
    # Medical precedes generic 科技 so 生物科技 stays in the medical group.
    if any(k in s for k in ('医药', '医疗', '创新药', '生物', '消费')):
        return 'C'
    if any(k in s for k in ('科技', '互联网', '信息', '汽车', '新经济')):
        return 'B'
    return 'A'

def writecsv(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows({k:r.get(k, '') for k in fields} for r in rows)

manifest = [{**r, 'owner': owner(r)} for r in records]
assert len(manifest) == 210 and len({r['fund_id'] for r in manifest}) == 210
ixowners = {}
for r in manifest:
    if r['index_name']:
        ixowners.setdefault(r['index_name'], set()).add(r['owner'])
assert all(len(v) == 1 for v in ixowners.values())
fields = ['owner', 'fund_id', 'fund_name', 'index_name', 'classification', 'luna_status', 'has_technical_result', 'reason', 'official_url']
writecsv(CONTROL/'manifest.csv', manifest, fields)
(CONTROL/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

# Descriptions below are column-level contracts; raw CSV/JSON are the merge source.
tables = {
'fund_decisions': ('基金结论', [
('fund_id','string','基金代码，唯一；保留交易所后缀'),('fund_name','string','官方全称'),('owner','string','A/B/C'),('index_id','string','官方指数代码，未知null'),('index_name','string','官方指数名'),('scope','enum','CONNECT_PURE/CONNECT_MIXED/QDII_ONLY/OTHER/UNVERIFIED'),('scope_evidence_id','string','关联官方证据'),('listing_date','date','上市日'),('primary_horizon_min','integer','30'),('decision','enum','SUITABLE_PRICE_PROXY/NONE_IN_TESTED_SET/INSUFFICIENT_EVIDENCE/OUT_OF_SCOPE'),('reason_codes','json','逐项门槛失败/通过代码'),('reason_detail','string','含具体数值，不用低相关概括'),('recommended_policy_id','string','未通过时null'),('primary_tools','json','工具ID列表，未通过时null'),('backup_policy_id','string','备选，未通过则null'),('exploratory_best_policy_id','string','仅探索，不能混作主方案'),('execution_status','enum','SUPPORTED/CONDITIONAL/NOT_AVAILABLE/UNKNOWN'),('confirmation_status','enum','NEW_LOCKED/REUSED_OR_UNPROVEN/UNAVAILABLE'),('confirmation_start','date','有效确认期首日'),('confirmation_end','date','有效确认期末日'),('oos_days','integer','有效日数'),('target_std_bp','number','未对冲标准差bp'),('residual_std_bp','number','主政策残差标准差bp'),('variance_reduction','ratio','小数'),('ci_low','ratio','对应主政策95%CI'),('ci_high','ratio','对应主政策95%CI'),('up_es95_bp','number','上侧尾部均值bp'),('down_es95_bp','number','下侧尾部均值bp'),('positive_block_fraction','ratio','5日块正改善比例'),('strict_refit_vr','ratio','严格场景真正重拟合值'),('effective_quote_coverage','ratio','共同预期端点有效率'),('latest_beta_date','date','最新权重生效日期'),('latest_beta','json','名义权重和工具映射'),('candidate_coverage_complete','boolean','合理工具池是否充分检验'),('event_coverage_complete','boolean','关键估值事件是否足够核验'),('remaining_gaps','json','数据/研究缺口'),('invalidation_triggers','json','模型应停用/重估条件'),('source_run_id','string','来源运行'),('result_path','path','自己永久目录文件'),('updated_at_utc','datetime','真实时间')]),
'model_metrics': ('模型指标', [
('run_id','string','运行ID'),('fund_id','string','基金'),('horizon_min','integer','5/15/30/60'),('policy_id','string','预先锁定政策'),('model_id','string','模型或候选'),('scenario_id','string','主场景/敏感性'),('sample_hash','string','端点及输入hash'),('confirmation_status','enum','是否新确认'),('oos_start','date','首日'),('oos_end','date','末日'),('oos_days','integer','有效日数'),('sample_count','integer','重叠标签数，非独立样本数'),('target_std_bp','number','目标标准差'),('residual_std_bp','number','残差标准差'),('variance_reduction','ratio','1-var(residual)/var(target)'),('ci_low','ratio','没有计算则null'),('ci_high','ratio','没有计算则null'),('bootstrap_method','string','日/多日block和次数'),('bootstrap_seed','integer','种子'),('target_up_es95_bp','number','未对冲上侧尾部'),('target_down_es95_bp','number','未对冲下侧尾部'),('up_es95_bp','number','对冲上侧尾部'),('down_es95_bp','number','对冲下侧尾部'),('positive_block_fraction','ratio','5日块改善率'),('residual_mean_bp','number','残差均值'),('beta_turnover','number','名义换手定义附配置'),('decision_gate_results','json','每个门槛数值和通过状态'),('residual_path','path','可独立复算文件')]),
'tool_candidates': ('工具候选', [
('fund_id','string','基金'),('tool_id','string','合约/ETF真实ID'),('risk_family','string','对应指数/行业'),('rationale','string','经济映射原因'),('asset_type','string','期货/ETF/单股期货等'),('official_url','url','产品证据'),('listed_from','date','历史可用起日'),('listed_to','date','终日或null'),('currency','string','币种'),('multiplier','number','合约乘数'),('lot_size','number','最小交易单位'),('session','string','适用交易时段'),('quote_coverage','ratio','分钟可用率'),('data_start','date','数据起日'),('data_end','date','数据止日'),('short_status','enum','SUPPORTED/UNKNOWN/NOT_AVAILABLE'),('included','boolean','是否进入确认候选池'),('exclusion_reason','string','排除/缺数据原因'),('evidence_id','string','来源证据'),('selection_lock_hash','string','锁定文件hash')]),
'weights': ('逐日权重', [
('fund_id','string','基金'),('horizon_min','integer','周期'),('policy_id','string','政策'),('effective_date','date','测试日'),('train_start','date','训练起日'),('train_end','date','训练末日，必须早于测试'),('validation_start','date','内层验证起日'),('validation_end','date','内层验证末日'),('tool_id','string','工具'),('beta','number','还原后的名义系数'),('currency_conversion','number','适用汇率'),('selection_reason','string','少腿/成本/tie规则'),('config_hash','string','配置hash')]),
'execution_scenarios': ('方向规模成本', [
('fund_id','string','基金'),('horizon_min','integer','周期'),('direction','enum','LONG_BASKET_SHORT_HEDGE/SHORT_BASKET_LONG_HEDGE'),('notional_cny','number','100万/1000万/5000万'),('policy_id','string','内层选择政策'),('cost_scenario_id','string','假设标记'),('per_side_variable_cost_bp','number','每腿名义单边bp'),('known_fixed_fees_cny','number','可证实固定费，未知null'),('borrow_cost_bp','number','未核实null，不能0'),('funding_cost_bp','number','未核实null'),('basket_total_cost_bp','number','名义与换手折算'),('rounded_positions','json','整数张/手以及报价时点'),('rounded_residual_std_bp','number','取整实施残差'),('rounded_vr','ratio','取整方差降低'),('risk_cost_score_bp','number','max双侧ES+成本'),('pareto_optimal','boolean','是否在前沿'),('execution_status','enum','SUPPORTED/CONDITIONAL/NOT_AVAILABLE/UNKNOWN'),('assumptions','json','费用/借券/时段条件'),('fee_evidence_id','string','费用证据'),('run_id','string','来源')]),
'data_coverage': ('数据覆盖', [
('fund_id','string','基金'),('security_id','string','成分/工具/FX/PCF'),('data_type','string','minute/PCF/FX/event'),('source','string','系统或URL'),('path','path','永久小包'),('sha256','string','文件hash'),('start_date','date','起日'),('end_date','date','末日'),('expected_rows','integer','可比较分母'),('actual_rows','integer','实际值'),('missing_dates','json','缺日期'),('duplicates','integer','重复数'),('timezone','string','时区'),('timestamp_semantics','string','K线起/止'),('adjustment','string','未复权/事件调整'),('retrieved_at_utc','datetime','获取实际时间'),('quality_status','enum','PASS/FAIL/UNKNOWN'),('gap_action','string','补全动作或具体障碍')]),
'events': ('事件核验', [
('fund_id','string','基金'),('security_id','string','PCF原始代码'),('event_type','string','HALT/RESUME/DIVIDEND/SPLIT/CODE_CHANGE/REVIEWED_NO_EVENT等'),('effective_from','datetime','有效起点'),('effective_to','datetime','有效终点'),('published_at','datetime','公布时点'),('official_url','url','官方证据'),('evidence_path','path','本地原文'),('evidence_sha256','string','原文hash'),('treatment','string','估值处理'),('affected_dates','json','影响日期'),('max_weight','ratio','最大篮子权重'),('verified','boolean','足够证据'),('numerical_check_path','path','独立核对'),('remaining_risk','string','复牌/潜在公司行动等未覆盖风险')]),
'sensitivity': ('稳健性', [
('fund_id','string','基金'),('horizon_min','integer','周期'),('policy_id','string','模型政策'),('base_run_id','string','对应真实主场景'),('scenario_id','string','场景'),('refit','boolean','是否真正重新拟合'),('sample_hash','string','端点hash'),('oos_days','integer','场景日数'),('variance_reduction','ratio','场景VR'),('residual_std_bp','number','场景残差'),('up_es95_bp','number','上侧ES'),('down_es95_bp','number','下侧ES'),('pass','boolean','门槛结果'),('explanation','string','日期变化/不可执行提前等解释'),('source_path','path','实际结果')]),
'tasks': ('任务流水', [
('task_id','string','唯一阶段任务ID'),('owner','string','A/B/C'),('fund_id','string','基金或组级GLOBAL'),('phase','enum','P0/P1/P2/P3/P4/P5/P6/P7'),('status','enum','TODO/RUNNING/DONE/BLOCKED/NOT_APPLICABLE'),('started_at_utc','datetime','实际开始时间'),('finished_at_utc','datetime','实际结束时间'),('action','string','具体执行动作'),('inputs','json','路径及hash'),('outputs','json','文件链接'),('detailed_result','string','每项任务详细输出'),('validation','string','核验方式和数值'),('blocker_type','enum','NONE/DATA/SOURCE/ACCESS/COMPUTE/METHODOLOGY'),('blocker_evidence','string','实际错误/尝试来源，不含秘密'),('next_action','string','下一步'),('run_command','string','复现命令，无密钥')]),
'evidence': ('来源证据', [
('evidence_id','string','唯一ID'),('fund_id','string','基金或GLOBAL'),('purpose','string','分类/事件/工具/费用/分母'),('publisher','string','发行人/交易所'),('url','url','原文链接'),('published_at','datetime','原文时间，未知null'),('retrieved_at_utc','datetime','抓取实际时间'),('local_path','path','归档原文'),('sha256','string','hash'),('locator','string','页码/段落'),('finding','string','短证据或自述，遵守引用限制'),('sufficiency','enum','SUFFICIENT/PARTIAL/UNAVAILABLE')]),
'qa_checks': ('独立检查', [
('check_id','string','唯一ID'),('fund_id','string','基金'),('check_type','string','指标复算/泄漏/冻结等'),('run_id','string','对应运行'),('actual','json','实际值'),('expected','json','预期'),('tolerance','number','容差或null'),('passed','boolean','实际运行才有真值'),('source_path','path','检查脚本与结果'),('checked_at_utc','datetime','真实时间')]),
}
schema = {'version':'2.0','null_rule':'JSON null; CSV empty. Unknown is not zero.','decision_enums':['SUITABLE_PRICE_PROXY','NONE_IN_TESTED_SET','INSUFFICIENT_EVIDENCE','OUT_OF_SCOPE'], 'tables':{k:{'sheet':name,'columns':[{'key':a,'type':b,'description':c} for a,b,c in cols]} for k,(name,cols) in tables.items()}}
(CONTROL/'schema.json').write_text(json.dumps(schema, ensure_ascii=False, indent=2))
schema_md = ['# Excel及机器明细字段规范\n','每组11张明细表；另加一张自动汇总的“阅读说明”。Excel由对应CSV/JSON生成，每阶段更新任务流水。不得手工填造数值。\n']
for key,(name,cols) in tables.items():
    schema_md += [f'## {name} / {key}.csv\n','|列名|类型|定义|','|---|---|---|']
    schema_md += [f'|{a}|{b}|{c}|' for a,b,c in cols]
(CONTROL/'EXCEL_COLUMNS.md').write_text('\n'.join(schema_md))
titles = {'A':'港股通对冲 A｜宽基红利金融与全量核验','B':'港股通对冲 B｜科技互联网汽车','C':'港股通对冲 C｜医药生物消费'}
for a in 'ABC':
    out=ROOT/f'agent_{a}';out.mkdir(exist_ok=True)
    rows=[r for r in manifest if r['owner']==a]
    writecsv(CONTROL/f'assignment_{a}.csv',rows,fields)
    (CONTROL/f'assignment_{a}.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    (out/'templates').mkdir(exist_ok=True)
    for k,(_,cols) in tables.items():writecsv(out/'templates'/f'{k}.csv',[],[c[0] for c in cols])
    (out/'STATUS.md').write_text(f'# {titles[a]}\n\n待启动；已分配{len(rows)}只。此状态由主Agent初始化，后续由本组独占更新。\n')
    special = {'A':'除自己74只初始标的外，负责全市场官方目录补漏与coverage.json。所有新增不在210清单的基金本轮归A；不改B/C既有结果。优先513090，使用前轮验收中01788正式停复牌证据，扩展事件处理，不能重新忽略。另重点区分银行与非银/券商；不要把银行期货当证券行业精确替代。', 'B':'优先520600复现与新期确认，再科技/互联网/汽车。同指数基金可以复用经济候选但不能复制PCF结果。恒生生物科技归C，不在你的标的内。HTI与跟踪恒科ETF为替代路线。', 'C':'生物科技、医药、创新药、医疗、消费均在本组。优先查实际行业ETF/行业期货候选，核心宽基不足时不能直接全标没有；关键行业候选缺行情则写证据不足。'}[a]
    prompt=f'''你是独立研究任务{a}，模型必须保持GPT-5.6 Luna，推理xhigh。用户授权你实际执行逐港股通ETF最优价格对冲研究，不是只写计划。读写项目永久根目录：{PROJECT}。

先读 {CONTROL}/WORKFLOW.md、schema.json、EXCEL_COLUMNS.md 和 assignment_{a}.json；它们是主Agent为本次委派编写的工作合同，按它们执行。也读前轮最终验收报告。当前你拥有{len(rows)}只种子基金，必须每只落结论，先初始化完整分母，再推进实际研究。你的唯一共享项目写入范围为 {out}；基础项目、旧归档、control和其他组输出只读。隔离worktree可以写自己代码，交付前复制到上述永久目录。不要修改其他业务仓库、删源数据或改远端服务。

{special}

任务流程是P0台账→P1官方范围→P2分钟/PCF/结算汇率增量数据→P3真实事件→P4锁定候选与政策→P5修复引擎并训练→P6新期及稳健性→P7逐只判定和Excel。不等主Agent继续在线，不等别组结束；某只缺口不阻塞其余标的。可以自动用ssh machome、现有数据库获取方法、newnavnav和只读TWS；不要求tick或精确成交量。TWS clientId为{7311+'ABC'.index(a)}，必须用统一文件 {ROOT}/resource_locks/ibkr_historical.lock 串行请求历史，实际遵守pacing。每组一个重计算进程、最多2CPU线程和1个远端重I/O，避免三组拖垮数据盘。

新确认目标2026-08-04至09-04；旧结果只能探索。缺PCF不倒推、不伪造；缺新期不少写限制而冒充确认。主结论四态，数据不足绝不能写没有对冲。方法的门槛为自定研究标准，不得放宽以获得全绿；合理候选池和新期足够但全部风险门槛失败才可“已测试工具池内没有合适方案”。30分钟主结论和5/15/60分钟分开；每个方向/规模/成本假设另列。价格对冲合格不等于可执行套利收益。

按照schema.json生成11张机器明细CSV/JSON和RESULTS.xlsx（另加阅读说明），使用可用Spreadsheets技能，逐阶段更新tasks及STATUS，最终渲染核对Excel。保存代码/环境/配置/输入hash、运行命令、端点残差、逐日权重、bootstrap与测试结果。旧Excel置信区间和refit错配必须避免。若工具失败，尝试其他官方或现有本地路径；真实不可解的缺口详细记录，继续其余项。不能只生成表格或批量套理由就结束。

不要新增子Agent，不发Slack/邮件、不下单、不读无关账户、不要发布或推送仓库。不要消费usage reset。完成后输出FINAL_REPORT.md、完整机器表、RESULTS.xlsx、QA结果；STATUS.md写真实完成数/不足数/范围外数，保留未完成边界。你的任务需自行完成，用户会之后让主Agent验收。
'''
    (CONTROL/f'prompt_{a}.txt').write_text(prompt)
(ROOT/'resource_locks').mkdir(exist_ok=True)
lock={'version':'2.0','created_at_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'counts':{a:sum(r['owner']==a for r in manifest) for a in 'ABC'},'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in CONTROL.iterdir() if p.is_file() and p.name!='dispatch_lock.json'}}
(CONTROL/'dispatch_lock.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2))
print(json.dumps({'counts':lock['counts'],'unique_funds':len(manifest),'split_index_groups':0,'root':str(ROOT)},ensure_ascii=False))

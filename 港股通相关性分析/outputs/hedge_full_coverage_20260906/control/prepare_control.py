from pathlib import Path
import json, shutil, datetime

C = Path(__file__).resolve().parent
R = C.parent
OLD = R.parent / 'hedge_rework_01_20260906' / 'control'
if (C / 'dispatch_receipts.json').exists():
    raise SystemExit('Dispatch exists; do not overwrite active control.')
for name in ['assignments.json', 'threads.json']:
    shutil.copy2(OLD / name, C / name)

def cols(spec):
    return [dict(zip(['field', 'label', 'type', 'definition'], line.split('|', 3))) for line in spec.strip().splitlines()]

sheets = {}
sheets['全量对冲映射'] = cols('''fund_id|基金代码|text|唯一主键，含交易所后缀，保留前导零
fund_name|基金名称|text|官方全称
owner|负责组|enum|A/B/C
index_id|跟踪指数代码|text|null时不得猜造
index_name|跟踪指数|text|官方名称
scope_status|港股通范围|enum|VERIFIED/UNVERIFIED/OUT_OF_SCOPE
scope_evidence_ids|范围证据|array|对应证据索引
processing_status|处理进度|enum|UNPROCESSED/RUNNING/PROCESSED
actual_backtest_run|已实际回测|boolean|真实生成本基金目标OOS残差才true
decision|匹配结论|enum|MATCH/NO_MATCH_IN_TESTED_SET/INSUFFICIENT_DATA/OUT_OF_SCOPE
target_type|被对冲目标|enum|PCF_BASKET/ETF_MARKET_PRICE/INDEX_RETURN_PROXY/INDEX_STRUCTURAL
evidence_level|证据等级|enum|UNSEEN_CONFIRMATION/SEEN_EXPLORATORY/SHORT_SAMPLE/DESCRIPTIVE/STRUCTURAL/UNAVAILABLE
primary_policy_id|首选政策ID|text|无实测政策时null
primary_tools|首选对冲工具|array|具体代码与名称，无实测推荐时null
backup_policy_id|备选政策ID|text|对应候选明细
backup_tools|备选工具|array|具体代码与名称
structural_candidates|结构候选工具|array|未实测候选及经济理由，不能称已验证
selection_reason|选择理由|text|单腿优先、成本场景与验证排序
primary_horizon_min|主周期分钟|integer|30
hedge_return_correlation|样本外收益相关|number|null或[-1,1]，不取绝对值
correlation_ci_low|相关区间下限|number|交易日block bootstrap
correlation_ci_high|相关区间上限|number|与主政策样本完全对应
correlation_threshold|达标门槛|number|0.60
target_std_bp|原风险标准差bp|number|同一样本目标收益
residual_std_bp|对冲后标准差bp|number|实际系数后的残差
variance_reduction|方差降低比例|number|1-var(residual)/var(target)
up_es95_bp|上行尾部ES95bp|number|residual最差5%平均值
down_es95_bp|下行尾部ES95bp|number|-residual最差5%平均值
oos_start|OOS开始|date|实际首日
oos_end|OOS结束|date|实际末日
oos_days|有效OOS天数|integer|真实有效交易日去重
new_unseen_oos_days|未使用的新OOS天数|integer|不能用总天数代替
oos_rows|OOS标签数|integer|重叠标签不是独立样本量
sample_group_id|共同样本组|text|候选比较的可比组
sample_hash|样本指纹|text|可复现端点集合hash
candidate_tools_tested|实际测试工具|array|真实进入引擎并产出结果
candidate_tools_missing|尚缺数据工具|array|具体工具与缺口
latest_beta_date|系数估计时点|date|系数可得时点
latest_beta|最新名义系数|object|工具代码到数值，不是权重百分比强制和1
hedge_direction|实际仓位方向|text|相对目标敞口的对冲方向
cost_status|费用证据状态|enum|VERIFIED/SCENARIO_ONLY/UNKNOWN
cost_assumptions|费用及规模假设|object|币种、规模、费用来源，未知null不填0
execution_status|交易可执行性|enum|VERIFIED/CONDITIONAL/UNKNOWN
event_status|事件处理状态|enum|VERIFIED/PARTIAL/UNKNOWN
remaining_gaps|具体剩余缺口|array|逐项输入缺口，不许泛化PCF不足
fetch_attempt_ids|取数记录引用|array|实际请求或远端分区调查记录
source_run_id|实际运行ID|text|关联分目标及候选明细
result_path|残差与权重路径|text|存在的归档路径
updated_at_utc|实际更新时刻|datetime|真实生成时间，不伪造任务时刻''')
sheets['分目标结果'] = cols('''fund_id|基金代码|text|不得用同指数其他基金替换
target_type|目标类型|enum|篮子或ETF市场价或指数代理，分开记录
run_id|运行ID|text|唯一运行标识
decision|目标结论|enum|四态结论
evidence_level|证据等级|enum|同主表
horizon_min|周期分钟|integer|5/15/30/60
policy_id|选择政策|text|完整滚动政策而非仅被选中某工具的天数子集
metrics|样本指标|object|rho/CI/VR/std/ES/days/rows/start/end
data_manifest_path|输入清单|text|实际文件hash与时点口径
weights_path|逐日权重|text|实际文件
residual_path|逐端点残差|text|实际文件
selection_lock_path|选择规则锁|text|先验规则与看过的数据披露
checks_ids|核验引用|array|实际检查ID
limitations|适用边界|array|市场价不能替代IOPV，跨日FX等''')
sheets['候选比较'] = cols('''fund_id|基金代码|text|自身目标
run_id|运行ID|text|关联分目标
target_type|目标类型|text|与目标表一致
horizon_min|周期|integer|5/15/30/60
candidate_id|候选政策|text|具体单腿或两腿
tools|工具代码|array|真实合约或ETF
economic_reason|经济理由|text|指数/行业/成分敞口证据
selection_stage|评价阶段|enum|FIT/VALIDATION/OOS/DESCRIPTIVE
sample_group_id|共同样本组|text|不可跨组直接排名
sample_hash|端点指纹|text|公平比较用
metrics|指标|object|rho/CI/VR/std/ES/days/rows/start/end
eligible|价格标准达标|boolean|依据该阶段数值，不冒充新确认
rank_basis|排序依据|text|费用已知程度、腿数、验证残差
exclusion_reason|排除或未测试原因|text|null表示已正常测试
residual_path|实际残差|text|路径必须存在''')
sheets['数据覆盖'] = cols('''fund_id|基金代码|text|GLOBAL允许工具共用
instrument_id|证券或工具|text|准确代码与合约
data_type|数据类型|enum|PCF/MINUTE/FX/EVENT/SCOPE
target_pathway|对应目标路径|text|PCF/ETF_MARKET_PRICE/INDEX
source_id|来源ID|text|证据引用
requested_start|需要起始|date|实际需求
requested_end|需要结束|date|实际需求
actual_start|已有起始|date|null表示无数据
actual_end|已有结束|date|null表示无数据
rows|有效行数|integer|实际值
days|有效日数|integer|实际交易日
timestamp_semantics|分钟时点口径|text|原始起始或结束及转换
quality_status|质量状态|enum|PASS/PARTIAL/UNAVAILABLE
path|本地小包路径|text|有效文件
sha256|数据hash|text|内容hash
gap_detail|缺口|array|日期/字段/原因
attempt_ids|实际调查引用|array|禁止未尝试称不可得''')
sheets['逐项任务'] = cols('''task_id|任务ID|text|唯一
fund_id|基金代码|text|各基金阶段不能合并冒充完成
phase|阶段|enum|SCOPE/CANDIDATES/INVENTORY/COMPUTE/EVENTS/QA/DELIVER
status|状态|enum|TODO/RUNNING/DONE/BLOCKED
started_at_utc|实际开始|datetime|未执行为null
finished_at_utc|实际结束|datetime|未完成为null，不能sleep伪造
input_paths|输入|array|真实文件
command|实际命令|text|脱敏
output_paths|详细产物|array|每项任务输出可核对
result_summary|实际结果|text|含数量、指标、失败原因
next_action|下一动作|text|可执行补数或复算动作''')
sheets['缺口与尝试'] = cols('''attempt_id|尝试ID|text|唯一
fund_id|基金代码|text|全局去重请求需明确受影响基金引用
instrument_id|标的|text|真实代码
data_type|类型|text|数据或本地清单调查
source|来源与方法|text|允许真实远端库存检查作为尝试
request|请求参数|object|脱敏，日期和频率
started_at_utc|开始|datetime|真实
finished_at_utc|结束|datetime|真实
status|返回状态|enum|SUCCESS/PARTIAL/PARAMETER_ERROR/ACCESS_DENIED/NOT_FOUND/NETWORK_ERROR/PACING/OTHER_ERROR
error_summary|错误与原因|text|缺数不能当零报价
rows|返回行数|integer|实际
raw_path|证据路径|text|原始结果或日志
sha256|证据hash|text|实际
next_action|后续动作|text|修正参数/换源/明确缺口''')
sheets['方法与核验'] = cols('''check_id|核验ID|text|唯一
fund_id|基金代码|text|共享引擎检查允许GLOBAL
run_id|运行ID|text|真实运行
check_name|检查|text|无未来泄漏/午休边界/ES/标量核对等
check_type|核验类型|enum|CODE/DATA/RESEARCH
status|核验状态|enum|PASS/FAIL/NOT_RUN
expected|期望|text|具体规则
actual|实际结果|text|执行结果而非静态声明
command|实际命令|text|可重复
evidence_path|证据|text|实际输出
engine_hash|引擎hash|text|确定执行版本
evaluated_at_utc|核验时间|datetime|真实''')
sheets['证据索引'] = cols('''evidence_id|证据ID|text|全局唯一，目录不复写每只
subject_ids|关联基金证券|array|具体关联
evidence_type|用途|enum|IDENTITY/SCOPE/INDEX/EVENT/DATA/COST/CONTRACT
source_title|来源标题|text|官方来源优先
source_url|来源网址或方法|text|准确定位
published_at|发布时间|datetime|未知null
effective_at|生效时间|datetime|事件与通道时点
retrieved_at_utc|取得时间|datetime|真实
page_or_section|页码段落|text|逐只范围不能仅搜全文词
supporting_excerpt|支持内容|text|精确与本基金相关的必要短摘录
local_path|原始材料|text|实际存在
sha256|原始hash|text|实际内容
limitations|局限|text|目录身份不等于投资通道证明''')

schema = {'version':'FULL237_RHO060_V1','created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sheets':sheets,'rules':['Null is unavailable, never a fabricated zero.','Main sheet has one row per assigned fund.','Fund and instrument IDs are text.','Nested machine fields remain arrays/objects; serialize for Excel display only.','All numerical claims point to real data and residual files.']}
(C/'workbook_schema.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2))
assign = json.loads((C/'assignments.json').read_text())
threads = json.loads((C/'threads.json').read_text())
for t in threads:
    owner=t['owner']; d=R/f'agent_{owner}';d.mkdir(exist_ok=True)
    (C/f'fund_ids_{owner}.json').write_text(json.dumps([x['fund_id'] for x in assign[owner]],indent=2))
    prompt=f'''请继续用户已授权的全量研究；用户最新强调：“对这200多个港股通ETF标的都找到合适的对冲标的，而不是只有两个”。本轮必须覆盖你负责的全部{len(assign[owner])}只，不能再次停在代表基金或空表交付。保持GPT-5.6 Luna、xhigh。

项目根 /Users/ellis/工具程序开发/港股通相关性分析。先读 outputs/hedge_full_coverage_20260906/control/FULL_COVERAGE_CONTRACT.md、workbook_schema.json、assignments.json中{owner}组，以及 outputs/hedge_rework_01_20260906/acceptance/验收结论.md。本次只写新目录 outputs/hedge_full_coverage_20260906/agent_{owner}，旧R1只读。你的线程标题保留不变。

先修已核实的方法问题，同时立即清点全组每只目标数据和期货/香港ETF候选，再成批真实计算。PCF不足必须尝试该基金ETF市场价1分钟收益的独立初步匹配，标明target_type和折溢价基差，不能冒充篮子结果。全部基金有具体候选；有数据必须算，确实无数据须真实调查证据。不再以独立新期不足让所有探索值为空。主标准是正向OOS收益相关>=0.60、正确系数降低残差方差、达标时单腿优先。

合同指定B先发布共用标准选择引擎，A/C与此同时推进各自全量数据和候选；三组之后用同一通过测试的引擎（固定hash）批算。各自有数值结果仍须逐基金数据核验。读其他组资产，不覆盖其他组目录；TWS使用既有公共锁。每10只更新STATUS。不要自定耗时截止，不要仅生成Excel就结束；逐只满足合同终态后才收尾，数据确实不可得可实证不足，未尝试不可称完成。

最终输出本组一只一行映射总表、8张Excel明细、机器数据、实际残差/权重及可复跑代码，运行 control/check_delivery.py --owner {owner}。写清逐只处理覆盖和实际回测覆盖。回复完成时给准确路径和真实未决项，不声称主Agent已验收。请现在开始完整推进。'''
    (C/f'prompt_{owner}.txt').write_text(prompt)
print(json.dumps({'assignments':{k:len(v) for k,v in assign.items()},'sheets':{k:len(v) for k,v in sheets.items()}},ensure_ascii=False))

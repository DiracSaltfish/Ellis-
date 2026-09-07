from pathlib import Path
import json, shutil, hashlib
from datetime import datetime, timezone

CTRL=Path(__file__).resolve().parent
BASE=CTRL.parent
PROJECT=BASE.parents[1]
OLD=PROJECT/'outputs/hedge_selection_v2_20260906'
if (CTRL/'dispatch_receipts.json').exists():
    raise SystemExit('Already dispatched; do not overwrite live task initialization.')
threads=json.loads((OLD/'control/threads.json').read_text())['threads']
assignments={a:json.loads((OLD/f'agent_{a}/fund_decisions.json').read_text()) for a in 'ABC'}
ids=[r['fund_id'] for rows in assignments.values() for r in rows]
assert len(ids)==len(set(ids))==237
assert {a:len(x) for a,x in assignments.items()}=={'A':101,'B':81,'C':55}
(CTRL/'assignments.json').write_text(json.dumps(assignments,ensure_ascii=False,indent=2))
(CTRL/'threads.json').write_text(json.dumps(threads,ensure_ascii=False,indent=2))
shutil.copy2(OLD/'control/schema.json',CTRL/'schema_base.json')
schema=json.loads((CTRL/'schema_base.json').read_text())
for table in ['fund_decisions','model_metrics']:
    schema['tables'][table]['columns'] += [
      {'key':'hedge_return_correlation','type':'number','description':'同样本外端点正向工具代理与篮子收益Pearson相关'},
      {'key':'correlation_ci_low','type':'number','description':'相关系数95%区间下限，未算null'},
      {'key':'correlation_ci_high','type':'number','description':'相关系数95%区间上限，未算null'},
      {'key':'correlation_method','type':'string','description':'Pearson OOS'},
      {'key':'correlation_threshold','type':'number','description':'0.60'},
      {'key':'criteria_version','type':'string','description':'USER_RHO_060_FUTURES_ETF'}]
schema['version']='R1_USER_RHO_060_FUTURES_ETF'
(CTRL/'schema_base.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2))
special={'A':'修复21条排除证据和重复ID，先513090，继续官方分母补漏；不再用旧classification直接排除。',
         'B':'修复失效产品证据，先520600，公开可复用PCF获取/解析脚本和期货小包索引，实际继续取得成分股1分钟行情。',
         'C':'先修复includeExpired错误，取得03069/03174/HBI完整所需历史并保存真实K线；先520760，恢复永久scripts，发布可复用STK获取器。'}
for t in threads:
    a=t['owner'];out=BASE/f'agent_{a}';out.mkdir(exist_ok=True)
    (out/'scripts').mkdir(exist_ok=True)
    (out/'STATUS.md').write_text(f'# R1返工 {a}\n\n待派发，初始分母{len(assignments[a])}只；此初始化不代表研究开始。\n')
    prompt=f'''用户已明确要求按验收意见返工。请在当前独立线程继续实际执行，保持GPT-5.6 Luna、xhigh，不再派生Agent。

你的返工工作合同是 {CTRL}/USER_CRITERIA.md（用户最新要求，最高优先）、REWORK_CONTRACT.md 和 DELIVERY_ADDITIONS.md，优先于上轮冲突要求。先完整读这三份文件、上轮acceptance/验收报告.md，再读本轮assignments.json中{a}组清单。原始237只分母中你独占{len(assignments[a])}只。旧交付只读；本轮只写 {out}。control和其他组目录只读；真实代码从开始便保存自己的永久scripts。

{special[a]}

这次不接受只生成新台账、复用旧表然后全部标证据不足的交付。先修验收已证实问题，尽早发布assets.json/SHARED_FINDINGS.md，跑通代表基金实际篮子面板→训练/选模→端点残差→新期或明确分列探索→敏感性→独立检查，再按指数扩展。即使行情缺口，也继续逐只范围/事件文档工作和所有可做的分析。不要自己设置20/40/60分钟停止期限。

关键方法改动：用户希望尽量不用港股个股，优先港股期货/香港ETF，60%以上相关性即可。因此默认主工具池不含港股个股现货，以收益的样本外Pearson相关>=0.60判相关性达标，取消旧VR50%/CI下限30%/严格VR40%硬门槛，实际名义权重仍须降低残差方差，其他尾部稳健性作诊断。不得把相关性60%误当方差降低60%。纯PCF篮子对冲不需要境内ETF二级市场报价作前置。继续1分钟、强制现金替代研究假设、事后结算汇率口径。真实数据不足不得填造，也不能无依据宣布无合适。原2112行旧指标已复算一致，直接复用作基准，不浪费时间重复包装。新期中只读报价探针不等于按策略绩效选模；已看过的策略绩效必须披露，候选/政策锁定后再确认。schema_base已加入相关系数和对应区间字段。

全组分别保留11张原明细并新增返工记录、取数尝试、研究门槛、探索优选4表，Excel加阅读说明共16表。qa_checks不要混同研究门槛。逐只任务时间真实写入，证据ID唯一且文件/hash能访问，原工作簿不覆盖。本轮交付FINAL_REPORT.md、RESULTS.xlsx、STATUS.md、机器文件、代码和真实检查输出，分别报告repair_status和research_status，不以任务结束代替研究完成。

共享TWS历史锁仍为 {OLD}/resource_locks/ibkr_historical.lock。每组1个重计算进程、最多2计算线程、1个远端重I/O。C发布STK脚本、B发布PCF方法与期货索引、A发布范围/事件证据；其他组按hash只读复用，不互相修改，也不等待别组做完。可通过原线程消息协调必要事项：A=01a07681-ce75-77f1-925f-4fa20a8ced0e，B=01a07681-e6f0-7180-8dcc-629234b1e1d0，C=01a07682-0818-7301-8a17-e6244830cc8b。

你无需等待主Agent再次发消息，按合同持续完成返工。真实外部不可解缺口列逐证券/日期及尝试记录，并继续其他已授权事项。不下单、不访问无关账户、不改业务服务、不消费usage reset。
'''
    (CTRL/f'prompt_{a}.txt').write_text(prompt)
lock={'created_at_utc':datetime.now(timezone.utc).isoformat(),'counts':{a:len(x) for a,x in assignments.items()},'source_acceptance_sha256':hashlib.sha256((OLD/'acceptance/验收报告.md').read_bytes()).hexdigest(),'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in CTRL.iterdir() if p.is_file() and p.name!='method_lock.json'}}
(CTRL/'method_lock.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2))
print(json.dumps(lock['counts'],ensure_ascii=False))

from pathlib import Path
import json,gzip,collections,math,hashlib
import numpy as np
import pandas as pd

OUT=Path(__file__).resolve().parent; BASE=OUT.parent
OUT.mkdir(exist_ok=True)
def read_rows(p):
    with (gzip.open(p,'rt') if str(p).endswith('.gz') else p.open()) as f:
        return [json.loads(s) for s in f if s.strip()]
def resolve(p,d):
    q=Path(p)
    if q.is_absolute():return q
    for root in [d,BASE,BASE.parents[1]]:
        if (root/q).is_file():return root/q
    return d/q
def jsave(name,x):
    (OUT/name).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False))
def metrics(y,h):
    e=y-h;k=max(1,math.ceil(len(y)*.05))
    return dict(rho=float(np.corrcoef(y,h)[0,1]) if np.std(h)>0 and np.std(y)>0 else None,vr=float(1-np.var(e,ddof=1)/np.var(y,ddof=1)),target_std_bp=float(np.std(y,ddof=1)*1e4),residual_std_bp=float(np.std(e,ddof=1)*1e4),up_es95_bp=float(np.sort(e)[-k:].mean()*1e4),down_es95_bp=float(np.sort(-e)[-k:].mean()*1e4))
allmap=[];audit=[];errors=[];source=[];cached={}
for owner in 'ABC':
    d=BASE/f'agent_{owner}'; mapping=json.loads((d/'mapping.json').read_text());targets=read_rows(d/'target_results.jsonl')
    source.append({'owner':owner,'mapping_sha256':hashlib.sha256((d/'mapping.json').read_bytes()).hexdigest(),'funds':len(mapping),'decision_counts':dict(collections.Counter(x['decision'] for x in mapping))})
    for m in mapping:
        a={'fund_id':m['fund_id'],'owner':owner,'raw_decision':m['decision'],'target_type':m['target_type'],'numeric_checked':False,'switches_tools':None,'primary_beta_keys_mismatch':bool(m.get('latest_beta')) and set(m.get('primary_tools') or [])!=set(m['latest_beta'])}
        if m.get('actual_backtest_run'):
            ts=[t for t in targets if t['fund_id']==m['fund_id'] and t.get('horizon_min')==30 and t.get('target_type')==m['target_type']]
            if len(ts)!=1:errors.append({'fund_id':m['fund_id'],'error':'target lookup','matches':len(ts)})
            else:
                t=ts[0]; f=resolve(t['residual_path'],d)
                if owner=='C':
                    if str(f) not in cached:cached[str(f)]=read_rows(f)
                    rows=[r for r in cached[str(f)] if r.get('fund_id')==m['fund_id'] and r.get('target_type')==m['target_type'] and r.get('horizon_min')==30]
                else:rows=[r for r in read_rows(f) if r.get('horizon_min',30)==30]
                if not rows:errors.append({'fund_id':m['fund_id'],'error':'empty main residuals'})
                else:
                    if 'y' in rows[0]:
                        y=np.array([r['y'] for r in rows]);h=np.array([r['locked_proxy'] for r in rows]);pids=collections.Counter({'HHI_FUT+HTI_FUT':len(rows)})
                    else:
                        y=np.array([r['target'] for r in rows]);h=np.array([r['proxy_return'] for r in rows]);pids=collections.Counter(r['policy_id'] for r in rows)
                    s=metrics(y,h);days=len({r['date'] for r in rows});err={}
                    for k,mk in [('rho','hedge_return_correlation'),('vr','variance_reduction'),('target_std_bp','target_std_bp'),('residual_std_bp','residual_std_bp'),('up_es95_bp','up_es95_bp'),('down_es95_bp','down_es95_bp')]:
                        if m.get(mk) is not None and s[k] is not None:err[k]=abs(m[mk]-s[k])
                    a.update({'numeric_checked':True,'actual_rows':len(rows),'actual_days':days,'metrics':s,'max_metric_error':max(err.values()) if err else None,'row_count_match':len(rows)==m.get('oos_rows'),'day_count_match':days==m.get('oos_days'),'policy_counts':dict(pids),'switches_tools':len(pids)>1,'source_residual_path':str(f)})
                    if a['max_metric_error'] is not None and a['max_metric_error']>1e-6:errors.append({'fund_id':m['fund_id'],'error':'metric mismatch','details':err})
        if m['decision']=='NO_MATCH_IN_TESTED_SET':label='滚动方案未达标；尚不能排除固定工具'
        elif m['decision']=='MATCH':label='历史滚动方案初步达标' if a.get('switches_tools') else '历史样本初步达标'
        elif m['decision']=='INSUFFICIENT_DATA':label='证据不足'
        else:label='Agent标记范围外'
        a['summary_label']=label;audit.append(a)
        allmap.append({'fund_id':m['fund_id'],'fund_name':m['fund_name'],'owner':owner,'agent_decision':m['decision'],'summary_label':label,'target_type':m['target_type'],'evidence_level':m['evidence_level'],'reported_tools':m.get('primary_tools'),'reported_beta':m.get('latest_beta'),'rho':m.get('hedge_return_correlation'),'vr':m.get('variance_reduction'),'oos_days_reported':m.get('oos_days'),'actual_backtest_run':m['actual_backtest_run'],'numeric_checked':a['numeric_checked'],'multiple_policies_in_oos':a.get('switches_tools'),'primary_beta_keys_mismatch':a['primary_beta_keys_mismatch'],'source_mapping':str(d/'mapping.json'),'remaining_gaps':m.get('remaining_gaps',[])})
assert len(allmap)==237 and len({m['fund_id'] for m in allmap})==237
matches=[a for a in audit if a['raw_decision']=='MATCH']; nonout=[a for a in audit if a['raw_decision']!='OUT_OF_SCOPE' and a['numeric_checked']]
summary={'candidate_funds':237,'decision_counts':dict(collections.Counter(m['agent_decision'] for m in allmap)),'group_counts':source,'actual_oos_including_out_of_scope':sum(a['numeric_checked'] for a in audit),'actual_oos_excluding_out_of_scope':len(nonout),'match_targets':dict(collections.Counter(a['target_type'] for a in matches)),'matches_with_multiple_oos_policies':sum(a.get('switches_tools') is True for a in matches),'matches_with_primary_beta_keys_mismatch':sum(a['primary_beta_keys_mismatch'] for a in matches),'reported_match_tools':dict(collections.Counter('+'.join(m['reported_tools'] or []) for m in allmap if m['agent_decision']=='MATCH')),'descriptive_numeric_rows_not_real_oos':sum(not m['actual_backtest_run'] and m['rho'] is not None for m in allmap),'max_metric_error':max(a.get('max_metric_error') or 0 for a in audit),'errors':errors,'claim_boundary':'Aggregated historical research results, not final fixed-instrument or executable hedge approval.'}
jsave('summary.json',summary);jsave('numeric_audit.json',audit);jsave('all_237_summary.json',allmap)
print(json.dumps(summary,ensure_ascii=False,indent=2))

decision_titles={'MATCH':'初步达标','NO_MATCH_IN_TESTED_SET':'当前滚动方案未达标','INSUFFICIENT_DATA':'数据或样本不足','OUT_OF_SCOPE':'Agent标记范围外'}
target_names={'ETF_MARKET_PRICE':'ETF市场价','PCF_BASKET':'PCF篮子','INDEX_STRUCTURAL':'结构候选','INDEX_RETURN_PROXY':'指数代理'}
def percent(x):return '—' if x is None else f'{x:.2%}'
def safe(s):return str(s).replace('|','/').replace('\n',' ')
lines=['# 港股通ETF对冲研究汇总','',
'整理日期：2026-09-06。237条候选已全部有行级处理记录。当前报告115只历史样本初步达标、39只当前滚动方案未达标、72只数据或样本不足、11只由Agent标为范围外。不能将此解释为237只均已确定最终固定对冲工具。','',
'## 总体覆盖','', '| 组别 | 候选 | 初步达标 | 当前滚动未达标 | 数据不足 | 范围外 |','|---|---:|---:|---:|---:|---:|']
for g in source:
    c=g['decision_counts'];lines.append(f"| {g['owner']} | {g['funds']} | {c.get('MATCH',0)} | {c.get('NO_MATCH_IN_TESTED_SET',0)} | {c.get('INSUFFICIENT_DATA',0)} | {c.get('OUT_OF_SCOPE',0)} |")
lines+=['| 合计 | 237 | 115 | 39 | 72 | 11 |','',
f"共独立复算{summary['actual_oos_including_out_of_scope']}只主表目标的实际OOS残差，其中10只同时被原表列为范围外；排除这些后，研究范围内有{summary['actual_oos_excluding_out_of_scope']}只具备真实OOS结果。收益相关、方差降低、标准差与尾部ES的最大绝对复算误差为{summary['max_metric_error']:.3g}。复算验证归档指标一致，不等于重新确认输入行情、事件、交易成本和全工具最优。",'',
'## 结果应该怎么用','',
'115只达标中，112只的目标是境内ETF二级市场价格，只有520600、520760、520930三只是PCF篮子。市场价格包含折溢价与时点基差，适合初步风险匹配，不能直接推断申赎补券风险已得到同等对冲。', '',
'主周期为30分钟，标准为正向样本外收益相关至少0.60且正确系数下残差方差降低。多数使用已观察历史样本的滚动探索。520760和520930各只有15个有效OOS日、110条标签，约97.88%的相关仅限短样本。520600的74.00%为上一轮固定HHI+HTI组合的24日结果，沿用旧方案，尚不足以证明它优于所有单腿与香港ETF。', '',
f"{summary['matches_with_multiple_oos_policies']}只达标基金的OOS记录中实际使用了多种政策。表中“主工具”通常是历史最常出现的政策，而指标是整条每日选模路径的成绩；二者不能直接等同。{summary['matches_with_primary_beta_keys_mismatch']}只达标基金还存在主工具代码与latest_beta代码不一致。下表以“历史主工具标签”呈现并保留换工具提示，暂不将这些系数作为固定工具的下单参数。", '',
'39只的“NO_MATCH_IN_TESTED_SET”应暂时理解为所运行的滚动选择政策未达标。公共引擎没有为每个固定候选在全部相同OOS端点都生成独立残差，而是只汇总该候选被选中时的结果，因此现有证据不足以证明工具池中的所有固定工具均不合适。', '',
'三组主要可比行情集中在2026-03-03至08-03的下午时段，具体基金实际OOS区间不同；不能直接等同完整上午／下午的交易表现。C组5/15/60分钟多数是描述性比较，标准滚动主结果只在30分钟。费用、借券、资金占用、合约换月及公司行动证据仍不完整，不能称已经确认最低总成本或实盘可执行。', '',
'## 工具分布','',
'以下为115条达标记录的历史主工具标签数量，可能对应每日切换工具的滚动政策，不是固定工具全OOS优胜数量。','',
'| 标签 | 条数 |','|---|---:|']
for k,v in sorted(summary['reported_match_tools'].items(),key=lambda x:-x[1]):lines.append(f'| {k} | {v} |')
lines+=['','科技方向的主标签集中于03032／03033；宽基方向集中于02828／02800；生物科技方向出现03069。这里仅总结已归档研究分布，不构成现价成交或费用建议。','',
'## 逐只完整清单','',
'相关数值对应原表声明的目标及样本。数据不足且没有真实OOS的行不展示其描述性相关，避免误认为样本外。纯结构候选的工具代码详见各组原表，不充作已验证主工具。']
for decision in decision_titles:
    lines+=['',f'### {decision_titles[decision]}','', '| 基金代码 | 基金名称 | 组 | 目标 | 历史主工具标签 | 相关 | 方差降低 | OOS天数 | 期间换工具 |','|---|---|---|---|---|---:|---:|---:|---|']
    for m in sorted((m for m in allmap if m['agent_decision']==decision),key=lambda x:x['fund_id']):
        valid=m['actual_backtest_run'];tools='+'.join(m['reported_tools'] or []) or '—';sw='是' if m['multiple_policies_in_oos'] else ('否' if m['numeric_checked'] else '—')
        lines.append(f"| {m['fund_id']} | {safe(m['fund_name'])} | {m['owner']} | {target_names.get(m['target_type'],m['target_type'])} | {tools} | {percent(m['rho']) if valid else '—'} | {percent(m['vr']) if valid else '—'} | {m['oos_days_reported'] if valid else '—'} | {sw} |")
lines+=['','## 原始交付与复算','',
'三组原始交付没有被本次总结改写。', '',
f'- [A组Excel]({BASE}/agent_A/RESULTS.xlsx)',f'- [B组Excel]({BASE}/agent_B/RESULTS.xlsx)',f'- [C组Excel]({BASE}/agent_C/RESULTS.xlsx)',
'- 本目录summary.json保存统计与输入文件hash；numeric_audit.json逐基金记录独立复算；all_237_summary.json保存统一清单。','',
'下一步应先补齐39只未达标基金的固定候选同样本OOS比较，以及72只缺口；再把115只初步达标政策的固定工具、每日选择规则和相应系数分别列清。最终申赎用途仍需逐基金PCF篮子验证。']
(OUT/'总结与237只完整清单.md').write_text('\n'.join(lines)+'\n')

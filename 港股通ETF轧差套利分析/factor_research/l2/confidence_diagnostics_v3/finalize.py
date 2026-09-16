from pathlib import Path
import csv,json,os
from signal_gate import Evidence,evaluate
R=Path(__file__).resolve().parent;SH_ONLY=os.environ.get('HK_ETF_SH_ONLY')=='1';O=R/('outputs_sh' if SH_ONLY else 'outputs');M=R.parent/('sh_only_v3' if SH_ONLY else 'merged_years_v3');base=json.loads((M/'outputs/data.json').read_text());rows=list(csv.DictReader((O/'库存假设_每日证据.csv').open(encoding='utf-8-sig')));lookup={r['date']:r for r in rows};seq=[];checks=0
def boolean(v):return v.lower()=='true'
for b in base['main']:
 if not (b['date'].startswith('2026') and b['date']>'2026-03-13'):continue
 r=lookup.get(b['date']);out={'交易日':b['date'],'样本阶段':b['phase'],'原第一名':b['symbol'],'基金名称':b['name'],'原模型分数':b['score'],'候选证据通过':False,'原因':'原规则无候选','整U卖出U':None,'整U买入U':None,'整U净供给U':None,'上日正净申购U':None,'历史供给覆盖后余量U（非净申购预测）':None,'前日份额日期':'','实际同日净申赎份额':b['net_shares'],'实际同日净申赎U':b['net_U'],'事后满额代理排除':b['excluded'],'事后保留':False,'数据质量状态':b['data_quality_status']}
 if r:
  unit=int(float(r['unit']));e=Evidence(b['date'],r['previous_share_date'],r['previous_share_date'],float(r['score']),unit,int(round(float(r['v2_sell_unit_U'])*unit)),int(round(float(r['v2_buy_unit_U'])*unit)),int(round(float(r['prior_net_U'])*unit)),boolean(r['candidate_complete']),True);g=evaluate(e)
  expected=float(r['score'])>=.95 and boolean(r['candidate_complete']) and float(r['inventory_adjusted_U'])>0
  assert g['pass_evidence_gate']==expected,(b['date'],g,r['inventory_adjusted_U']);checks+=1
  out.update({'候选证据通过':g['pass_evidence_gate'],'原因':'；'.join(g['reasons']),'整U卖出U':float(r['v2_sell_unit_U']),'整U买入U':float(r['v2_buy_unit_U']),'整U净供给U':g['net_unit_supply_U'],'上日正净申购U':g['previous_positive_creation_U'],'历史供给覆盖后余量U（非净申购预测）':g['inventory_hypothesis_excess_U'],'前日份额日期':r['previous_share_date'],'事后保留':bool(g['pass_evidence_gate'] and not b['excluded'])})
 seq.append(out)
assert len(seq)==73
expected=[r for r in json.loads((O/'inventory_rules.json').read_text()) if r['cohort']=='后续73日' and r['quality']=='完整候选日期' and r['rule']=='分数≥0.95且净供给超过上日正净申购']
assert sum(r['候选证据通过'] for r in seq)==next(r['n'] for r in expected if r['cap_policy']=='未事后剔除')
assert sum(r['事后保留'] for r in seq)==next(r['n'] for r in expected if r['cap_policy']=='事后剔除满额')
if SH_ONLY:assert all(not r['原第一名'] or r['原第一名'].startswith('5') for r in seq)
for filename,data in [('高置信度候选_后续73日完整序列.csv',seq),('高置信度候选_保留记录.csv',[r for r in seq if r['事后保留']])]:
 with (O/filename).open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(seq[0]));w.writeheader();w.writerows(data)
(O/'gate_validation.json').write_text(json.dumps(dict(prototype_matches_vectorized_rule=True,selected_days_checked=checks,total_sequence_days=73,pre_cap_signals=sum(r['候选证据通过'] for r in seq),post_cap_signals=sum(r['事后保留'] for r in seq),no_same_day_outcome_in_gate_signature=True),ensure_ascii=False,indent=2))
print('GATE VERIFIED',checks,'original choice days; exported 73-day sequence')

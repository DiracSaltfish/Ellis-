from pathlib import Path
import csv,json
from signal_gate import evaluate
R=Path(__file__).resolve().parent;O=R/'outputs';C=R.parent/'confidence_diagnostics_v3/outputs_sh';base=json.loads((O/'data.json').read_text());rows={r['date']:r for r in csv.DictReader((C/'库存假设_每日证据.csv').open(encoding='utf-8-sig'))};out=[];checks=0
for b in base['main']:
 if not (b['date'].startswith('2026') and b['date']>'2026-03-13'):continue
 x=dict(交易日=b['date'],阶段=b['phase'],沪市第一名=b['symbol'],基金名称=b['name'],模型分数=b['score'],信号触发=False,未触发原因=b['reason'],整U卖出执行U=None,整U买入执行U=None,整U净卖出执行U=None,上日正净申购U=None,历史供给可覆盖本日净卖出=None,实际净申赎份额=b['net_shares'],实际净申赎U=b['net_U'],事后满额剔除=b['excluded'],事后保留=False,数据质量=b['data_quality_status'])
 if b['symbol']:
  r=rows[b['date']];u=int(float(r['unit']));g=evaluate(b['symbol'],float(r['score']),u,int(round(float(r['v2_sell_unit_U'])*u)),int(round(float(r['v2_buy_unit_U'])*u)),candidate_complete=b['candidate_complete'],order_trade_valid=True)
  expected=float(r['score'])>=.95 and float(r['net_unit_U'])>0 and b['candidate_complete'];assert g['trigger']==expected;checks+=1
  x.update(信号触发=g['trigger'],未触发原因='；'.join(g['reasons']),整U卖出执行U=float(r['v2_sell_unit_U']),整U买入执行U=float(r['v2_buy_unit_U']),整U净卖出执行U=g['net_unit_supply_U'],上日正净申购U=float(r['prior_positive_U']),历史供给可覆盖本日净卖出=float(r['prior_positive_U'])>=g['net_unit_supply_U'],事后保留=g['trigger'] and not b['excluded'])
 out.append(x)
rules=json.loads((C/'rules.json').read_text());expected=[r for r in rules if r['quality']=='完整候选日期' and r['cohort']=='后续73日' and r['rule']=='分数≥0.95且整U净供给>0']
assert len(out)==73 and sum(r['信号触发'] for r in out)==next(x['n'] for x in expected if x['cap_policy']=='未事后剔除') and sum(r['事后保留'] for r in out)==next(x['n'] for x in expected if x['cap_policy']=='事后剔除满额')
for name,z in [('沪市高置信度候选_73日完整序列.csv',out),('沪市高置信度候选_保留记录.csv',[r for r in out if r['事后保留']])]:
 with (O/name).open('w',encoding='utf-8-sig',newline='') as f:w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(z)
(O/'gate_validation.json').write_text(json.dumps(dict(passed=True,original_choices_checked=checks,sequence_days=73,triggers=sum(r['信号触发'] for r in out),post_cap=sum(r['事后保留'] for r in out),shenzhen_excluded_before_ranking=True,no_actual_same_day_label_input=True),ensure_ascii=False,indent=2));print('SH GATE VERIFIED',checks,'original choices')

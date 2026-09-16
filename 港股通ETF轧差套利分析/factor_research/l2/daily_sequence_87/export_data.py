from pathlib import Path
import json,gzip,hashlib
import pandas as pd
import numpy as np
import joblib

R=Path(__file__).resolve().parent; L=R.parent; V=L/'premium_l2_gate_v3'; E=L/'expanded_dates_v3'
plan=json.loads((V/'plan.json').read_text()); selection=json.loads((V/'selection.json').read_text())
hist=pd.read_parquet(V/'historical_candidates.parquet')
prem=pd.concat([pd.read_parquet(V/'premium.parquet'),pd.read_parquet(E/'data/premium.parquet')],ignore_index=True)
existing=pd.read_csv(E/'all_daily_choices.csv'); existing=existing[existing.policy.eq('rank_only')&existing['rank'].eq('probability')]
records=existing.to_dict('records')
for fx in ['lag','final']:
 for cut in ['14:30','14:45']:
  key=f'{fx}_{cut.replace(":","")}_premium_l2'; path=V/'models'/f'{key}.joblib'
  assert hashlib.sha256(path.read_bytes()).hexdigest()==selection[key]['model_sha256']
  m=joblib.load(path); h=hist[hist.fx_basis.eq(fx)&hist.cutoff.eq(cut)&hist.l2_available&hist.split.isin(['train','validation'])].copy()
  h['score']=m['classifier'].predict_proba(h[m['features']])[:,1]
  for e in plan['entries']:
   if e['split'] not in ['train','validation']:continue
   g=h[h.date.eq(e['date'])]
   for quality in ['original_frozen','isolate_513130']:
    z=g if quality=='original_frozen' else g[g.symbol.ne('513130.SH')]
    r=dict(date=e['date'],scope=e['split'],fx_basis=fx,cutoff=cut,quality=quality,status='no_eligible_candidate',eligible_before_quality=len(g))
    if len(z):
     w=z.sort_values(['score','symbol'],ascending=[False,True],kind='stable').iloc[0]
     r.update({k:w[k] for k in ['symbol','score','net_shares','net_baskets']});r['status']='selected'
    records.append(r)
phases={'train':'训练期回放','validation':'验证期回放','prior_19_dates':'历史压力检验','prior_12_dates':'上轮检验','fresh_expansion':'本轮新增检验'}
baskets={}; shares={}; out=[]; checks=0
for r in records:
 day=r['date']; chosen=r['status']=='selected'; symbol=r['symbol'] if chosen else ''
 o=dict(date=day,phase=phases[r['scope']],fx='盘中汇率代理' if r['fx_basis']=='lag' else '最终汇率（事后）',cutoff=r['cutoff'],quality='隔离513130' if r['quality']=='isolate_513130' else '原样规则',symbol=symbol,name='',score=None,net_shares=None,net_wan=None,unit=None,net_U=None,result='空仓',reason='')
 if chosen:
  if day not in baskets:baskets[day]=json.load(gzip.open(L.parent/'inputs/baskets'/f'{day.replace("-","")}.json.gz'))
  b=baskets[day][symbol]
  if symbol not in shares:shares[symbol]={x['share_date']:x for x in json.loads((L.parent/'inputs/share_history'/f'{symbol}.json').read_text())['rows']}
  actual=shares[symbol][day]['share_change_10k']*10000
  assert np.isclose(actual,r['net_shares'],rtol=0,atol=.01),(day,symbol,actual,r['net_shares'])
  assert np.isclose(actual/b['unit'],r['net_baskets'],rtol=0,atol=1e-6)
  checks+=1
  o.update(name=b['name'],score=float(r['score']),net_shares=float(actual),net_wan=float(actual/10000),unit=float(b['unit']),net_U=float(actual/b['unit']),result='命中净申购' if actual>0 else ('未命中：净赎回' if actual<0 else '未命中：净量不变'))
 else:
  p=prem[prem.date.eq(day)&prem.fx_basis.eq(r['fx_basis'])&prem.cutoff.eq(r['cutoff'])]
  o['reason']='溢价初筛无候选' if not p.premium_gate.any() else ('隔离异常标的后无候选' if r['eligible_before_quality']>0 else '候选L2数据未通过校验')
 out.append(o)
d=pd.DataFrame(out).sort_values(['date','fx','cutoff','quality']).reset_index(drop=True)
assert len(d)==87*8 and not d.duplicated(['date','fx','cutoff','quality']).any()
main=d[d.fx.eq('盘中汇率代理')&d.cutoff.eq('14:45')&d.quality.eq('隔离513130')].copy()
assert len(main)==87 and main.date.nunique()==87
evaluation=main[~main.phase.isin(['训练期回放','验证期回放'])]; assert len(evaluation)==44
assert evaluation.symbol.ne('').sum()==32 and evaluation.net_shares.gt(0).sum()==30
cn={'date':'交易日','phase':'样本阶段','fx':'汇率口径','cutoff':'观察截止','quality':'标的范围','symbol':'选中标的','name':'基金名称','score':'模型分数（非校准概率）','net_shares':'实际净申赎份额','net_wan':'实际净申赎万份','unit':'申赎单位份额','net_U':'实际净申赎篮子U','result':'判断结果','reason':'空仓原因'}
O=R/'outputs/01a0915e-f9e1-76e3-bbaa-2329e4b4b7e8';O.mkdir(parents=True,exist_ok=True)
main.rename(columns=cn).to_csv(O/'每日选标与实际净申赎_87日.csv',index=False,encoding='utf-8-sig')
d.rename(columns=cn).to_csv(O/'规则对照每日序列_696行.csv',index=False,encoding='utf-8-sig')
(O/'data.json').write_text(json.dumps({'main':json.loads(main.to_json(orient='records',force_ascii=False)),'comparison':json.loads(d.to_json(orient='records',force_ascii=False))},ensure_ascii=False))
validation={'days':len(main),'first':main.date.min(),'last':main.date.max(),'selected':int(main.symbol.ne('').sum()),'actual_positive':int(main.net_shares.gt(0).sum()),'actual_negative':int(main.net_shares.lt(0).sum()),'actual_zero':int(main.net_shares.eq(0).sum()),'blank_days':int(main.symbol.eq('').sum()),'share_history_and_basket_checks':checks,'evaluation_days':44,'evaluation_selected':32,'evaluation_positive':30,'phase_days':main.groupby('phase').size().to_dict()}
(O/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2));print(json.dumps(validation,ensure_ascii=False,indent=2))

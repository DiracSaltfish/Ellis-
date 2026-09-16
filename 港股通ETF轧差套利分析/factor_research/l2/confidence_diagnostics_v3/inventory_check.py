"""Post-diagnostic hypothesis: prior creation inventory may imitate fresh ETF supply.

Prior shares are observable; beneficial ownership and remaining inventory are not.
All reported comparisons are exploratory after examining failures, never a holdout claim.
"""
from pathlib import Path
import json,gzip,sys,os
import pandas as pd,numpy as np
R=Path(__file__).resolve().parent;L=R.parent;O=R/('outputs_sh' if os.environ.get('HK_ETF_SH_ONLY')=='1' else 'outputs');d=pd.read_parquet(O/'daily_top1.parquet')
plans=[L/'premium_l2_gate_v3/plan.json',L/'expanded_dates_v3/data/plan.json',L/'merged_years_v3/plan.json'];es={e['date']:e for p in plans for e in json.loads(p.read_text())['entries']};cache={};values=[]
for r in d.itertuples():
 if r.symbol not in cache:cache[r.symbol]=sorted(json.loads((L.parent/'inputs/share_history'/f'{r.symbol}.json').read_text())['rows'],key=lambda x:x['share_date'])
 prev=json.load(gzip.open(es[r.date]['sources']['basket']['path']))[r.symbol]['prev_date'];history=[x for x in cache[r.symbol] if x['share_date']<=prev]
 assert history and history[-1]['share_date']==prev and prev<r.date
 last=history[-1];net=float(last['share_change_10k']*10000)
 values.append(dict(previous_share_date=prev,prior_net_U=net/r.unit,prior_positive_U=max(0,net/r.unit),prior5_positive_U=sum(max(0,float(x['share_change_10k'])*10000/r.unit) for x in history[-5:])))
for k in values[0]:d[k]=[v[k] for v in values]
d['inventory_adjusted_U']=d.net_unit_U-d.prior_positive_U
d.to_csv(O/'库存假设_每日证据.csv',index=False,encoding='utf-8-sig')
def stats(z):
 n=len(z);k=int(z.success.sum());a=k/n if n else None
 if n:
  den=1+1.96**2/n;c=(a+1.96**2/(2*n))/den;h=1.96*np.sqrt(a*(1-a)/n+1.96**2/(4*n*n))/den;ci=[max(0,c-h),min(1,c+h)]
 else:ci=[None,None]
 return dict(n=n,positive=k,zero=int(z.net_shares.eq(0).sum()),negative=int(z.net_shares.lt(0).sum()),accuracy=a,wilson95=ci,median_net_U=float(z.net_U.median()) if n else None,dates=z.date.tolist(),symbols=z.symbol.tolist(),actual_U=z.net_U.tolist())
rules={
 '分数≥0.95且整U净供给>0':lambda z:z.score.ge(.95)&z.net_unit_U.gt(0),
 '分数≥0.95且净供给超过上日正净申购':lambda z:z.score.ge(.95)&z.inventory_adjusted_U.gt(0),
 '分数≥0.90且净供给超过上日正净申购':lambda z:z.score.ge(.90)&z.inventory_adjusted_U.gt(0),
 '分数≥0.95且净供给超过过去5日正净申购':lambda z:z.score.ge(.95)&z.net_unit_U.gt(z.prior5_positive_U),
}
records=[];seq=[]
for scope in ['验证期','此前44日','新增29日','后续73日']:
 z=d[d['set'].eq('validation')] if scope=='验证期' else d[d['set'].eq('test')]
 if scope=='此前44日':z=z[z.phase.ne('2026新增检验')]
 if scope=='新增29日':z=z[z.phase.eq('2026新增检验')]
 for quality in ['完整候选日期','沿用全部日期']:
  a=z[z.candidate_complete] if quality=='完整候选日期' else z
  for name,fn in rules.items():
   b=a[fn(a)]
   for cap in ['未事后剔除','事后剔除满额']:
    c=b[~b.full] if cap=='事后剔除满额' else b
    records.append(dict(cohort=scope,quality=quality,rule=name,cap_policy=cap,**stats(c)))
for r in d.to_dict('records'):
 one=d[d.date.eq(r['date'])]
 for name,fn in rules.items():seq.append({k:r[k] for k in ['date','phase','symbol','name','score','net_shares','net_U','full','candidate_complete','v2_sell_unit_U','v2_buy_unit_U','net_unit_U','prior_net_U','inventory_adjusted_U']}|dict(rule=name,trigger=bool(fn(one).iloc[0])))
pd.DataFrame(seq).to_csv(O/'库存假设_触发序列.csv',index=False,encoding='utf-8-sig')
(O/'inventory_rules.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
print(pd.DataFrame(records).query('quality=="完整候选日期" and cap_policy=="事后剔除满额"')[['cohort','rule','n','positive','accuracy','wilson95']].to_string(index=False));print(d[d.date.eq('2026-07-02')][['date','symbol','score','net_U','net_unit_U','previous_share_date','prior_net_U','prior5_positive_U','inventory_adjusted_U']].to_string(index=False),flush=True)

from pathlib import Path
import json,joblib,pandas as pd,numpy as np
R=Path(__file__).resolve().parents[1];L=R/'factor_research/l2';O=Path(__file__).resolve().parent
paths=[L/'premium_l2_gate_v3/premium.parquet',L/'expanded_dates_v3/data/premium.parquet',L/'merged_years_v3/premium.parquet']
d=pd.concat([pd.read_parquet(p) for p in paths],ignore_index=True);d=d[(d.cutoff=='14:45')&d.symbol.str.startswith('5')&(d.symbol!='513130.SH')].copy();assert not d.duplicated(['date','symbol','fx_basis']).any()
old=json.loads((L/'sh_only_v3/outputs/data.json').read_text());meta={r['date']:r for r in old['main']};flags={(r['date'],r['symbol']) for r in old['price_flags']};entries={e['date']:e for p in [L/'premium_l2_gate_v3/plan.json',L/'expanded_dates_v3/data/plan.json',L/'merged_years_v3/plan.json'] for e in json.loads(p.read_text())['entries']}
cache={};result=[]
for fx in ['lag','final']:
 m=joblib.load(L/f'premium_l2_gate_v3/models/{fx}_1445_premium.joblib');g=d[(d.fx_basis==fx)&d.premium_gate].copy();g['price_flag']=[(a,b) in flags for a,b in zip(g.date,g.symbol)];g['score']=m['classifier'].predict_proba(g[m['features']])[:,1]
 for day in sorted(meta):
  allg=g[g.date==day];q=allg[~allg.price_flag];r=dict(date=day,phase=meta[day]['phase'],fx=fx,symbol='',score=None,net_shares=None,net_U=None,cap_full=False,cap_known=False,candidate_price_complete=not bool(allg.price_flag.any()))
  if len(q):
   x=q.sort_values(['score','symbol'],ascending=[False,True]).iloc[0]
   if day not in cache:cache[day]=pd.read_csv(entries[day]['sources']['pcf']['path'],encoding='utf-8-sig',dtype={'基金代码':str}).set_index('基金代码')
   cap=pd.to_numeric(cache[day].loc[x.symbol[:6],'当日累计申购上限份'],errors='coerce');known=bool(np.isfinite(cap) and 0<cap<1e14)
   r.update(symbol=x.symbol,score=float(x.score),net_shares=float(x.net_shares),net_U=float(x.net_shares/x.unit),cap_known=known,cap_full=bool(known and x.net_shares>=cap-.01))
  result.append(r)
 z=g.replace([np.inf,-np.inf],np.nan);z.to_parquet(O/f'{fx}_premium_candidates.parquet',index=False)
a=pd.DataFrame(result);a.to_csv(O/'T日最终汇率_纯溢价模型_每日序列与旧口径对照.csv',index=False,encoding='utf-8-sig')
summary=[]
for fx in ['lag','final']:
 for cohort in ['全部353日混合回放','2026后续73日','2026新增29日']:
  p=a[a.fx==fx];p=p if cohort.startswith('全部') else p[(p.date.str.startswith('2026'))&~p.phase.isin(['训练期回放','验证期回放','2026补齐训练期回放'])] if cohort=='2026后续73日' else p[p.phase=='2026新增检验']
  for capfilter in [False,True]:
   z=p[p.symbol!=''];z=z[~z.cap_full] if capfilter else z
   for threshold in [0,.90,.95,.97]:
    k=z[z.score>=threshold];summary.append(dict(fx=fx,cohort=cohort,days=len(p),expost_cap_removed=capfilter,threshold=threshold,n=len(k),positive=int(k.net_shares.gt(0).sum()),zero=int(k.net_shares.eq(0).sum()),negative=int(k.net_shares.lt(0).sum()),hit_rate=float(k.net_shares.gt(0).mean()) if len(k) else None))
(O/'recompute_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(pd.DataFrame(summary).query('threshold==0').to_string(index=False))

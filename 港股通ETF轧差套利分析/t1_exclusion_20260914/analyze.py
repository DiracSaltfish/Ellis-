from pathlib import Path
import json,hashlib,math
import pandas as pd,numpy as np,joblib
O=Path(__file__).resolve().parent;R=O.parent;L=R/'factor_research/l2'
ledger=json.loads((O/'ledger.json').read_text())['records']
d=pd.read_parquet(R/'live_model_v2/final_premium_candidates.parquet')
a=pd.read_csv(R/'live_model_v2/T日最终汇率_纯溢价模型_每日序列与旧口径对照.csv');a=a[a.fx=='final'].fillna({'symbol':''}).copy()
assert len(a)==353 and not a.date.duplicated().any()
assert not d.duplicated(['date','symbol']).any()
m=joblib.load(L/'premium_l2_gate_v3/models/final_1445_premium.joblib')
score=m['classifier'].predict_proba(d[m['features']])[:,1];assert np.max(np.abs(score-d.score))<1e-12;d['score']=score
assert d.net_shares.notna().all() and (d.unit>0).all() and d.premium_gate.all()
d['deadline']=d.symbol.map(lambda s:ledger.get(s,{}).get('creation_buy_deadline','UNKNOWN'))
d['name']=d.symbol.map(lambda s:ledger.get(s,{}).get('fund_name',s));d['net_U']=d.net_shares/d.unit
q=d[~d.price_flag].copy()
# Retain all original quality filters. Unknown rules are retained by literal T+1 exclusion, and excluded in strict T0 sensitivity.
strategies={'baseline':q,'remove_T1_reselect':q[q.deadline!='T+1'],'T0_only_reselect':q[q.deadline.isin(['T日内','T日','T 日'])]}
entries={e['date']:e for p in [L/'premium_l2_gate_v3/plan.json',L/'expanded_dates_v3/data/plan.json',L/'merged_years_v3/plan.json'] for e in json.loads(p.read_text())['entries']}
cache={}
def cap(row):
 day=row['date']
 if day not in cache:cache[day]=pd.read_csv(entries[day]['sources']['pcf']['path'],encoding='utf-8-sig',dtype={'基金代码':str}).set_index('基金代码')
 c=pd.to_numeric(cache[day].loc[row['symbol'][:6],'当日累计申购上限份'],errors='coerce');known=bool(np.isfinite(c) and 0<c<1e14)
 return known,bool(known and row['net_shares']>=c-.01)
seq=[]
for strategy,g in strategies.items():
 picks=g.sort_values(['date','score','symbol'],ascending=[True,False,True]).drop_duplicates('date').set_index('date')
 for old in a.to_dict('records'):
  out={'date':old['date'],'phase':old['phase'],'strategy':strategy,'symbol':'','score':np.nan,'net_shares':np.nan,'net_U':np.nan,'deadline':'','name':'','cap_full':False,'cap_known':False}
  if old['date'] in picks.index:
   row=picks.loc[old['date']];out.update({k:row[k] for k in ['symbol','score','net_shares','net_U','deadline','name']});out['cap_known'],out['cap_full']=cap(out)
  if strategy=='baseline':
   assert out['symbol']==old['symbol'],(out,old)
   if out['symbol']:assert abs(out['net_shares']-old['net_shares'])<.01 and abs(out['score']-old['score'])<1e-12 and out['cap_full']==old['cap_full']
  seq.append(out)
s=pd.DataFrame(seq);skip=s[s.strategy=='baseline'].copy();skip['strategy']='remove_T1_no_reselect';bad=skip.deadline.eq('T+1');skip.loc[bad,['symbol','deadline','name']]='';skip.loc[bad,['score','net_shares','net_U']]=np.nan;skip.loc[bad,['cap_full','cap_known']]=False;s=pd.concat([s,skip],ignore_index=True)
cohorts={'全部353日':a.date.tolist(),'2026后续73日':a[(a.date.str.startswith('2026'))&~a.phase.isin(['训练期回放','验证期回放','2026补齐训练期回放'])].date.tolist(),'新增29日':a[a.phase=='2026新增检验'].date.tolist(),'2025回放':a[a.phase=='2025跨期回放'].date.tolist()}
def stats(z):
 n=len(z);k=int(z.net_shares.gt(0).sum());p=k/n if n else None
 if n:
  den=1+1.96**2/n;mid=(p+1.96**2/(2*n))/den;half=1.96*math.sqrt(p*(1-p)/n+1.96**2/(4*n*n))/den;ci=[mid-half,mid+half]
 else:ci=[None,None]
 return dict(n=n,positive=k,zero=int(z.net_shares.eq(0).sum()),negative=int(z.net_shares.lt(0).sum()),hit_rate=p,ci_low=ci[0],ci_high=ci[1],median_net_U=float(z.net_U.median()) if n else None)
summary=[]
for cohort,dates in cohorts.items():
 for strategy in s.strategy.unique():
  for remove_cap in [False,True]:
   z=s[s.date.isin(dates)&s.strategy.eq(strategy)&s.symbol.ne('')];z=z[~z.cap_full] if remove_cap else z
   summary.append(dict(cohort=cohort,strategy=strategy,calendar_days=len(dates),expost_cap_removed=remove_cap,**stats(z)))
segments=[]
for cohort,dates in cohorts.items():
 for kind,g in s[s.strategy.eq('baseline')&s.date.isin(dates)&s.symbol.ne('')].groupby('deadline'):segments.append(dict(cohort=cohort,deadline=kind,**stats(g)))
b=s[s.strategy.eq('baseline')].set_index('date');t=s[s.strategy.eq('remove_T1_reselect')].set_index('date');c=b.add_prefix('before_').join(t.add_prefix('after_'));c['changed']=c.before_symbol!=c.after_symbol;c['transition']=np.where(c.before_net_shares>0,'命中',np.where(c.before_symbol=='','未选','失误'))+'→'+np.where(c.after_net_shares>0,'命中',np.where(c.after_symbol=='','未选','失误'))
s.to_csv(O/'每日序列_四种方案.csv',index=False,encoding='utf-8-sig');c.reset_index().to_csv(O/'逐日对照.csv',index=False,encoding='utf-8-sig');c[c.changed].reset_index().to_csv(O/'更换标的日期.csv',index=False,encoding='utf-8-sig');pd.DataFrame(summary).to_csv(O/'命中率汇总.csv',index=False,encoding='utf-8-sig');pd.DataFrame(segments).to_csv(O/'原第一名按补券时点分组.csv',index=False,encoding='utf-8-sig');d.to_parquet(O/'重算候选分数.parquet',index=False)
meta={'rows':len(d),'quality_rows':len(q),'symbols':d.symbol.nunique(),'unmatched':d[d.deadline=='UNKNOWN'].symbol.unique().tolist(),'dates':[a.date.min(),a.date.max()],'changed':int(c.changed.sum()),'transitions':c[c.changed].transition.value_counts().to_dict(),'summary':summary,'segments':segments,'source_sha256':{str(p.relative_to(R)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [O/'ledger.json',R/'live_model_v2/final_premium_candidates.parquet',L/'premium_l2_gate_v3/models/final_1445_premium.joblib']}}
(O/'summary.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print(json.dumps(meta,ensure_ascii=False,indent=2))

"""After-cutoff diagnostics and same-model final-FX counterfactual, never live inputs."""
from pathlib import Path
import json,gzip,hashlib,os
import numpy as np,pandas as pd,joblib
R=Path(__file__).resolve().parent;SH_ONLY=os.environ.get('HK_ETF_SH_ONLY')=='1';O=R/('outputs_sh' if SH_ONLY else 'outputs');L=R.parent;V=L/'premium_l2_gate_v3';M=L/'merged_years_v3';B=L/'sh_only_v3' if SH_ONLY else M;d=pd.read_parquet(O/'daily_top1.parquet');base=json.loads((B/'outputs/data.json').read_text());dm={r['date']:r for r in base['main']};fm={r['date']:r for r in base['final']}
es={e['date']:e for f in [V/'plan.json',L/'expanded_dates_v3/data/plan.json',M/'plan.json'] for e in json.loads(f.read_text())['entries']}
late=[]
for r in d[d['set'].eq('test')].itertuples():
 s=pd.read_parquet(es[r.date]['sources']['series']['path'],filters=[('symbol','==',r.symbol)]);s=s[(s.minute>'14:45')&(s.minute<='15:00')].dropna(subset=['etf','lag_settlement','actual_settlement_buy'])
 if len(s):
  a=(s.etf/s.lag_settlement-1)*10000;b=(s.etf/s.actual_settlement_buy-1)*10000
  late.append(dict(date=r.date,symbol=r.symbol,net_U=r.net_U,score=r.score,pre_current_premium=r.settlement_premium_bp,late_mean_lag=float(a.mean()),late_last_lag=float(a.iloc[-1]),late_negative_fraction=float((a<0).mean()),late_mean_final=float(b.mean()),late_last_final=float(b.iloc[-1]),after_cutoff_diagnostic_only=True))
pd.DataFrame(late).to_csv(O/'尾盘溢价事后核对.csv',index=False,encoding='utf-8-sig')
hist=pd.read_parquet(V/'historical_candidates.parquet');hist=hist[hist.split.isin(['train','validation'])&hist.l2_available]
pool=pd.concat([hist,pd.read_parquet(L/'top_one_v3/scored.parquet'),pd.read_parquet(L/'expanded_dates_v3/scored.parquet'),pd.read_parquet(M/'scored.parquet')],ignore_index=True)
pool=pool[pool.fx_basis.eq('final')&pool.cutoff.eq('14:45')&pool.symbol.ne('513130.SH')&pool.date.ge('2026-03-16')].copy();assert not pool.duplicated(['date','symbol']).any()
if SH_ONLY:pool=pool[pool.symbol.str.startswith('5')&pool.symbol.str.endswith('.SH')]
model=joblib.load(V/'models/lag_1445_premium_l2.joblib');pool['counterfactual_score']=model['classifier'].predict_proba(pool[model['features']])[:,1]
chosen=pool.sort_values(['date','counterfactual_score','symbol'],ascending=[True,False,True],kind='stable').groupby('date').head(1);out=[]
for r in chosen.itertuples():
 orig=dm[r.date];pcf=pd.read_csv(es[r.date]['sources']['pcf']['path'],encoding='utf-8-sig',dtype={'基金代码':str}).set_index('基金代码');cap=pd.to_numeric(pcf.loc[r.symbol[:6],'当日累计申购上限份'],errors='coerce');full=bool(np.isfinite(cap) and 0<cap<1e14 and r.net_shares>=cap-.01)
 out.append(dict(date=r.date,phase=orig['phase'],symbol=r.symbol,score=r.counterfactual_score,net_U=r.net_shares/r.unit,full=full,original_symbol=orig['symbol'],original_U=orig['net_U'],original_full=orig['excluded'],candidate_complete=orig['candidate_complete'] and fm[r.date]['candidate_complete']))
pd.DataFrame(out).to_csv(O/'同模型最终汇率反事实.csv',index=False,encoding='utf-8-sig')
summary=[]
for name in ['全部后续','新增日期']:
 for quality in ['全部','完整候选']:
  z=pd.DataFrame(out);z=z[z.phase.eq('2026新增检验')] if name=='新增日期' else z
  if quality=='完整候选':z=z[z.candidate_complete]
  for cap in [False,True]:
   w=z[~z.full] if cap else z
   summary.append(dict(cohort=name,quality=quality,remove_full=cap,n=len(w),positive=int(w.net_U.gt(0).sum()),zero=int(w.net_U.eq(0).sum()),negative=int(w.net_U.lt(0).sum()),accuracy=float(w.net_U.gt(0).mean())))
(O/'fx_counterfactual_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=2));print(pd.DataFrame(late).query('net_U<=0').round(3).to_string(index=False))

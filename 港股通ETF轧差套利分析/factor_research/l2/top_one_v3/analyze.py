"""Daily one-fund policy audit on existing frozen v3 cohorts. No new holdout claim.

Probability ranks reuse frozen scores. Quantity ranks are a new descriptive control:
fixed asinh net-flow regression fitted ONLY on the original training interval.
"""
from pathlib import Path
import json,hashlib
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingRegressor

R=Path(__file__).resolve().parent; V=R.parent/'premium_l2_gate_v3'
RANKS={'probability':'score','amount':'pred_net_amount_cny','shares':'pred_net_shares','baskets':'pred_net_U'}
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def serial(x):
 if isinstance(x,np.generic):return x.item()
 raise TypeError(type(x).__name__)
def save(name,data):(R/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=serial))
def wilson(k,n):
 if n==0:return [None,None]
 z=1.96;r=k/n;den=1+z*z/n;c=(r+z*z/(2*n))/den;w=z*np.sqrt(r*(1-r)/n+z*z/(4*n*n))/den;return [float(c-w),float(c+w)]

def choose(d,metric,threshold,policy,quality):
 z=d.copy()
 if quality=='isolate_513130':z=z[z.symbol.ne('513130.SH')]
 if z.empty:return None,'no_eligible_candidate'
 if policy=='threshold':z=z[z.score.ge(threshold)] if threshold is not None else z.iloc[:0]
 if z.empty:return None,'below_frozen_threshold'
 # No use of net_shares / target in ranking or tie-breaks; do not demand positive quantity after seeing data.
 z=z.sort_values([metric,'symbol'],ascending=[False,True],kind='stable')
 return z.iloc[0],'selected'

def main():
 R.mkdir(exist_ok=True);(R/'models').mkdir(exist_ok=True)
 plan=json.loads((V/'plan.json').read_text());selection=json.loads((V/'selection.json').read_text());pred=pd.read_parquet(V/'predictions.parquet');pred=pred[pred.variant.eq('premium_l2')].copy();hist=pd.read_parquet(V/'historical_candidates.parquet');hist=hist[hist.l2_available];features=plan['premium_features']+plan['order_features']
 spec=dict(question='Pick at most one screened fund per day: greatest creation score or predicted net creation amount',data_status='The same 12 dates have already been examined in v3. This is a retrospective policy analysis, not 12 fresh trials.',source_sha256={name:digest(V/name) for name in ['predictions.parquet','historical_candidates.parquet','premium.parquet','plan.json','selection.json']},primary='probability rank; original frozen fund-level threshold or rank alone',quantity_control='New fixed asinh(net_flow_pct) regression using original Jan5-Feb27 training only, same HGB parameters; no test/validation model selection. Amount=predicted shares times previous NAV, not executed cash cost.',quantity_parameters=plan['parameters'],ranks=RANKS,policies=['rank_only','threshold'],qualities=['original_frozen','isolate_513130'],quality_note='513130 is isolated as an entire fund owing to the previously observed input-price problem; this is a current known-data-quality sensitivity, not a new validated profit strategy.',cutoffs=['14:30','14:45'],budget='No budget cap provided, so no affordability filter. Previous-NAV basket estimate is exported; actual order cash requirement is unknown.',threshold_note='Reuses prior per-fund validation threshold, not tuned for daily top1. Threshold filters candidate pool before ranking; no fund chosen if empty.',definition='Error: actual same-day net shares <=0. Separately net redemption<0 and flat=0; no profit labels.',tie_break='six-digit canonical symbol ascending',new_test_dates=[e['date'] for e in plan['entries'] if e['split']=='new_test'])
 save('plan.json',spec)
 frames=[];modelmeta=[]
 for (fx,cut),z in pred.groupby(['fx_basis','cutoff']):
  tr=hist[hist.fx_basis.eq(fx)&hist.cutoff.eq(cut)&hist.split.eq('train')];assert tr.date.max()<'2026-03-01';assert tr.date.max()<z.date.min()
  clfkey=f'{fx}_{cut.replace(":","")}_premium_l2';assert digest(V/'models'/(clfkey+'.joblib'))==selection[clfkey]['model_sha256']
  reg=HistGradientBoostingRegressor(loss='squared_error',**plan['parameters']).fit(tr[features],np.arcsinh(tr.net_flow_pct))
  path=R/'models'/(clfkey+'_quantity.joblib');joblib.dump(dict(regressor=reg,features=features,fit_through=tr.date.max(),fx_basis=fx,cutoff=cut,target='asinh_net_flow_pct',research_only=True),path)
  modelmeta.append(dict(key=clfkey,n_train=len(tr),fit_through=tr.date.max(),sha256=digest(path)))
  # Freeze each regression before scoring any old test row.
  z=z.copy();z['pred_net_flow_pct']=np.maximum(-100,np.sinh(reg.predict(z[features])));z['pred_net_shares']=z.pred_net_flow_pct*z.prev_shares/100;z['prev_nav']=np.exp(z.log_prev_assets)/z.prev_shares;z['pred_net_amount_cny']=z.pred_net_shares*z.prev_nav;z['pred_net_U']=z.pred_net_shares/z.unit;z['basket_capital_proxy_cny']=z.prev_nav*z.unit;z['frozen_threshold']=selection[clfkey]['threshold'];frames.append(z)
 save('quantity_models.json',modelmeta);scored=pd.concat(frames,ignore_index=True);scored.to_parquet(R/'scored.parquet',index=False)
 daily=[];checks=[]
 for scope in ['new_test','seen_stress']:
  dates=[e['date'] for e in plan['entries'] if e['split']==scope]
  for fx in ['final','lag']:
   for cut in spec['cutoffs']:
    d=scored[scored.split.eq(scope)&scored.fx_basis.eq(fx)&scored.cutoff.eq(cut)];threshold=selection[f'{fx}_{cut.replace(":","")}_premium_l2']['threshold']
    for quality in spec['qualities']:
     for policy in spec['policies']:
      for rank,metric in RANKS.items():
       for day in dates:
        g=d[d.date.eq(day)];row,status=choose(g,metric,threshold,policy,quality)
        base=dict(scope=scope,date=day,fx_basis=fx,cutoff=cut,quality=quality,policy=policy,rank=rank,eligible_before_quality=len(g),threshold=threshold,status=status)
        if row is not None:
         for f in ['symbol','score','net_shares','net_baskets','pred_net_shares','pred_net_U','pred_net_amount_cny','basket_capital_proxy_cny','settlement_mean_bp','mid_mean_bp','v2_sell_unit_U','v2_buy_unit_U']:base[f]=row[f]
        daily.append(base)
        # Permuting the realized labels must never change chosen symbol or abstention.
        altered=g.copy();altered['net_shares']=-altered.net_shares;altered['net_baskets']=0;r2,s2=choose(altered,metric,threshold,policy,quality)
        assert s2==status and (row is None or r2.symbol==row.symbol)
 daily=pd.DataFrame(daily);daily.to_csv(R/'daily_choices.csv',index=False);summaries=[]
 keys=['scope','fx_basis','cutoff','quality','policy','rank']
 for group,g in daily.groupby(keys):
  z=g[g.status.eq('selected')];n=len(z);bad=int(z.net_shares.le(0).sum());red=int(z.net_shares.lt(0).sum());flat=int(z.net_shares.eq(0).sum());total=len(g);assert n==z.date.nunique();assert bad==red+flat
  summaries.append(dict(**dict(zip(keys,group)),calendar_days=total,traded_days=n,abstain_days=total-n,create_days=n-bad,redemption_days=red,flat_days=flat,error_rate=bad/n if n else None,redemption_rate=red/n if n else None,coverage=n/total,wilson_error95=wilson(bad,n),max_losing_streak=longest(g),median_actual_U=float(z.net_baskets.median()) if n else None,quantity_MAE_U=float((z.pred_net_U-z.net_baskets).abs().mean()) if n else None,median_basket_capital_proxy=float(z.basket_capital_proxy_cny.median()) if n else None))
 summary=pd.DataFrame(summaries);summary.to_csv(R/'summary.csv',index=False);save('summary.json',summaries)
 # Independently verify selected maximum against input pool for every non-empty decision.
 for x in daily[daily.status.eq('selected')].itertuples():
  pool=scored[scored.split.eq(x.scope)&scored.fx_basis.eq(x.fx_basis)&scored.cutoff.eq(x.cutoff)&scored.date.eq(x.date)]
  if x.quality=='isolate_513130':pool=pool[pool.symbol.ne('513130.SH')]
  if x.policy=='threshold':pool=pool[pool.score.ge(x.threshold)]
  metric=RANKS[x.rank];winner=pool[pool.symbol.eq(x.symbol)].iloc[0];assert winner[metric]==pool[metric].max()
 save('validation.json',dict(status='passed',ranking_label_invariance_decisions=len(daily),selected_maximum_verified=int(daily.status.eq('selected').sum()),frozen_classifier_hashes_verified=4,quantity_fit_dates_verified=4,one_choice_per_day=True,counts_reconciled_groups=len(summary),new_holdout=False))
 print(summary[(summary.scope=='new_test')&(summary.cutoff=='14:45')&summary['rank'].isin(['probability','amount'])][['fx_basis','quality','policy','rank','traded_days','create_days','redemption_days','flat_days','error_rate','abstain_days']].to_string(index=False))

def longest(g):
 streak=best=0
 for row in g.sort_values('date').itertuples():
  if row.status!='selected':continue
  streak=streak+1 if row.net_shares<=0 else 0;best=max(best,streak)
 return best

if __name__=='__main__':main()

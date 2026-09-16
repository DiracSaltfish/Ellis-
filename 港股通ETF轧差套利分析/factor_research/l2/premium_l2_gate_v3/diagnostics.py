"""Held-out descriptive diagnostics only. Never changes models or thresholds."""
import json
import numpy as np
import pandas as pd
import joblib
from sklearn.metrics import average_precision_score
import pipeline as p

def main():
 pred=pd.read_parquet(p.R/'predictions.parquet');hist=pd.read_parquet(p.R/'historical_candidates.parquet');test=pred[pred.split.eq('new_test')&pred.variant.eq('premium_l2')];out=[];buckets=[];levels=[];markets=[];cal=[]
 groups={
  'sell_unit_execution':[x for x in p.ORDER if x.startswith('v2_sell_') and 'large_executed' not in x and 'unknown_quantity_fraction' not in x],
  'buy_unit_execution':[x for x in p.ORDER if x.startswith('v2_buy_') and 'large_executed' not in x and 'unknown_quantity_fraction' not in x],
  'unit_net_supply':['v2_unit_net_supply_pct'],
  'active_flow_large_orders':['l2_active_imbalance','l2_buy_large_parent_frac','l2_sell_large_parent_frac','v2_buy_large_executed_pct','v2_sell_large_executed_pct','v2_traded_pct'],
  'active_parent_and_bursts':[x for x in p.ORDER if 'parent_excess' in x or 'burst_excess' in x]+['l2_passive_sell_unit_frac'],
 }
 factors=['settlement_mean_bp','settlement_above30_fraction','l2_active_imbalance','v2_sell_unit_pct','v2_sell_unit_passive_pct','v2_buy_unit_pct','v2_unit_net_supply_pct','v2_sell_unit_fill_ratio','v2_sell_unit_cancel_ratio']
 rng=np.random.default_rng(829)
 for (fx,cut),z in test.groupby(['fx_basis','cutoff']):
  z=z.reset_index(drop=True);tr=hist[(hist.fx_basis==fx)&(hist.cutoff==cut)&hist.split.eq('train')&hist.l2_available];model=joblib.load(p.R/'models'/f'{fx}_{cut.replace(":","")}_premium_l2.joblib');clf=model['classifier'];base=average_precision_score(z.net_shares.gt(0),z.score);names=model['features']
  for name,cols in groups.items():
   drops=[]
   for _ in range(12):
    x=z[names].copy()
    for _,idx in z.groupby('date').groups.items():
     perm=rng.permutation(idx);x.loc[idx,cols]=x.loc[perm,cols].to_numpy()
    drops.append(base-average_precision_score(z.net_shares.gt(0),clf.predict_proba(x)[:,1]))
   out.append(dict(fx_basis=fx,cutoff=cut,group=name,ap_base=base,mean_ap_drop=float(np.mean(drops)),sd=float(np.std(drops)),note='within-date group permutation; correlated features and artificial combinations limit causal interpretation'))
  for f in factors:
   edges=np.unique(np.quantile(tr[f].dropna(),[0,.25,.5,.75,1]));edges[0]=-np.inf;edges[-1]=np.inf
   if len(edges)<2:continue
   for bucket,g in z.groupby(pd.cut(z[f],edges,include_lowest=True),observed=True):buckets.append(dict(fx_basis=fx,cutoff=cut,factor=f,bucket=str(bucket),**p.stats(g,np.ones(len(g),bool))))
  for stage,mask in [('gate',np.ones(len(z),bool)),('selected',z.selected),('score90',z.score.ge(.9))]:
   a=z[mask]
   levels.append(dict(fx_basis=fx,cutoff=cut,stage=stage,n=len(a),**{f'actual_at_least_{u}U':int(a.net_baskets.ge(u-1e-6).sum()) for u in [1,5,10,20,50]},median_net_U=float(a.net_baskets.median()) if len(a) else None,mean_net_U=float(a.net_baskets.mean()) if len(a) else None))
   for m,g in a.groupby('market'):markets.append(dict(fx_basis=fx,cutoff=cut,stage=stage,market=m,**p.stats(g,np.ones(len(g),bool))))
  for bucket,g in z.groupby(pd.cut(z.score,[0,.5,.7,.8,.9,.95,1],include_lowest=True),observed=True):cal.append(dict(fx_basis=fx,cutoff=cut,score_bin=str(bucket),mean_score=float(g.score.mean()),**p.stats(g,np.ones(len(g),bool))))
 for name,rows in [('permutation_groups.csv',out),('factor_buckets.csv',buckets),('net_quantity_levels.csv',levels),('by_market.csv',markets),('score_reliability.csv',cal)]:pd.DataFrame(rows).to_csv(p.R/name,index=False)
 print('diagnostics saved')

if __name__=='__main__':main()

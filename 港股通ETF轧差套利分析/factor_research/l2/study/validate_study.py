"""Integrity checks for chronological research outputs; not predictive-performance tests."""
from pathlib import Path
import json,sys
import numpy as np,pandas as pd,joblib
from score import score
R=Path(__file__).resolve().parent;F=R.parent.parent
p=pd.read_parquet(R/'panel.parquet');pred=pd.read_parquet(R/'predictions.parquet');split=json.loads((R/'split.json').read_text());metrics=json.loads((R/'metrics.json').read_text());checks=[]
assert max(split['train'])<min(split['validation'])<=max(split['validation'])<min(split['test']);checks.append('chronological_dates_disjoint')
assert not p.duplicated(['date','symbol','cutoff']).any();assert len(p)==2*len(p[['date','symbol']].drop_duplicates());assert p.premium_coverage.ge(.95).all();checks.append('unique_complete_cutoffs_quality')
# Check raw website source values, independently of model/label loader.
for sym,g in p.groupby('symbol'):
 rows={x['share_date']:x for x in json.loads((F/'inputs/share_history'/f'{sym}.json').read_text())['rows']}
 for _,v in g[g.cutoff=='14:45'].iterrows():
  raw=rows[v.date];assert abs(raw['share_change_10k']*10000-v.net_shares)<1e-4;assert abs(raw['shares_10k']*10000-v.prev_shares-v.net_shares)<220
assert np.allclose(p.net_shares/p.unit,p.net_baskets);assert np.allclose(p.net_baskets,np.rint(p.net_baskets),atol=.01);checks.append('all_raw_share_labels_and_pcf_units_reconciled')
for cutoff in ['14:30','14:45']:
 for variant in ['premium','premium_l2']:
  m=joblib.load(R/'models'/f'{cutoff.replace(":","")}_{variant}.joblib');d=p[(p.cutoff==cutoff)&p.date.isin(split['test'])].sort_values(['date','symbol']);s=score(d,m);ref=pred[(pred.cutoff==cutoff)&(pred.variant==variant)&(pred.split=='test')].sort_values(['date','symbol'])
  assert list(zip(s.date,s.symbol))==list(zip(ref.date,ref.symbol))
  for c in ['p_create','p_flat','p_redeem','pred_shares','lo_shares','hi_shares','pred_baskets']:assert np.allclose(s[c],ref[c])
  altered=d.copy();altered['net_shares']=1e18;altered['net_baskets']=-1e18;altered['net_flow_pct']=np.nan;assert score(altered,m).equals(s)
  for bad in ['date','cutoff','premium_coverage','l2_quality_valid']:
   changed=d.iloc[:1].copy();changed[bad]={'date':m['trained_through'],'cutoff':'13:00','premium_coverage':.5,'l2_quality_valid':False}[bad]
   try:score(changed,m)
   except ValueError:pass
   else:raise AssertionError('missing guard: '+bad)
  truth=np.sign(ref.net_shares).to_numpy();decision=np.array([-1,0,1])[ref[['p_redeem','p_flat','p_create']].to_numpy().argmax(axis=1)];assert abs(np.mean(truth==decision)-metrics[cutoff+'_'+variant]['test']['accuracy'])<1e-12
checks+=['all_saved_models_reproduce_holdout_predictions','current_labels_do_not_affect_scoring','date_cutoff_and_quality_guards','direction_metrics_recomputed']
z=pred[(pred.cutoff=='14:45')&(pred.variant=='premium_l2')&(pred.split=='test')].sort_values(['date','p_create'],ascending=[True,False]);cols=['date','symbol','unit','p_create','p_flat','p_redeem','pred_shares','net_shares','pred_baskets','net_baskets','lo_shares','hi_shares','lo_baskets','hi_baskets'];z[cols].to_csv(R/'净申赎预测明细_1445_测试集.csv',index=False);z[z.date==z.date.max()][cols].to_csv(R/'最近历史日_20260427_1445.csv',index=False)
(R/'validation.json').write_text(json.dumps(dict(passed=True,checks=checks,fund_days=len(p)//2,holdout_per_cutoff=len(z),note='checks verify implementation and labels; do not establish 90% forecast precision'),ensure_ascii=False,indent=2));print('PASS',len(checks),'research integrity checks')

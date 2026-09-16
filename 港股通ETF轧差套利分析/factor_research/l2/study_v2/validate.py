"""Audit time split, native parity, frozen-v1 preservation, scoring guards and selected-model reproducibility."""
from pathlib import Path
import sys,json,hashlib
import numpy as np,pandas as pd,joblib
R=Path(__file__).resolve().parent;L=R.parent
from features import V2_FEATURES
from model import score_v2

def main():
 p=pd.read_parquet(R/'panel.parquet');pred=pd.read_parquet(R/'predictions.parquet');plan=json.loads((R/'plan.json').read_text());selection=json.loads((R/'selection.json').read_text());checks=[]
 def check(name,value):
  if not value:raise AssertionError(name)
  checks.append(name)
 check('unique date/fund/cutoff rows',not p.duplicated(['date','symbol','cutoff']).any());check('September design case entirely excluded',not p.date.eq('2026-09-02').any());check('all source dates within 2026',p.date.str.startswith('2026-').all())
 dates={s:set(p.loc[p.split.eq(s),'date']) for s in ['train','validation','test']};check('no temporal split overlap',not dates['train']&dates['validation'] and not dates['train']&dates['test'] and not dates['validation']&dates['test']);check('strict chronological ordering',max(dates['train'])<min(dates['validation']) and max(dates['validation'])<min(dates['test']))
 check('same-day label shares = baskets x PCF unit',np.allclose(p.net_shares,p.net_baskets*p.unit));check('all rows pass cutoff and source coverage',p.premium_coverage.ge(.95).all() and p.l2_quality_valid.eq(True).all())
 check('known unknown PCF cap remains distinguishable',p.loc[p.v2_creation_limit_known.eq(0),'v2_creation_limit_pct'].isna().all() and p.loc[p.v2_redemption_limit_known.eq(0),'v2_redemption_limit_pct'].isna().all())
 check('new features contain no target fields',not set(V2_FEATURES)&{'net_shares','net_baskets','net_flow_pct','actual_settlement_buy','actual_buy_fx'})
 for side in ['buy','sell']:
  check('unit active passive reconcile '+side,np.allclose(p[f'v2_{side}_unit_pct'],p[f'v2_{side}_unit_active_pct']+p[f'v2_{side}_unit_passive_pct']));check('continuous executions do not exceed originals '+side,p[f'v2_{side}_unit_fill_ratio'].between(0,1+1e-10).all());check('unit count/pct scaling '+side,np.allclose(p[f'v2_{side}_unit_U']*p.unit,p[f'v2_{side}_unit_pct']*p.prev_shares/100))
 # Old feature implementation remains equal on overlapping old-study rows.
 old=pd.read_parquet(L/'study/panel.parquet');joined=p.merge(old,on=['date','symbol','cutoff'],suffixes=('_v2','_v1'));cols=['net_baskets','settlement_mean_bp','l2_active_imbalance','l2_sell_unit_parent_excess','l2_passive_sell_unit_frac'];check('old baseline matched overlap exists',len(joined)>1000)
 for col in cols:check('v1 feature parity '+col,np.allclose(joined[col+'_v2'],joined[col+'_v1'],equal_nan=True))
 for cut,sel in selection.items():
  m=joblib.load(R/'models'/f'{cut.replace(":","")}_selected.joblib');check('chosen model agrees with validation selection '+cut,m['variant']==sel['variant']);z=p[p.cutoff.eq(cut)&p.split.eq('test')].sort_values(['date','symbol']);r=score_v2(z,m);saved=pred[pred.cutoff.eq(cut)&pred.split.eq('test')&pred.variant.eq(sel['variant'])].sort_values(['date','symbol']);check('saved/test recomputation exact '+cut,np.allclose(r.pred_baskets,saved.pred_baskets) and np.allclose(r.p_create,saved.p_create));check('probabilities sum to one '+cut,np.allclose(r[['p_create','p_redeem','p_flat']].sum(axis=1),1));check('shares U inverse conversion '+cut,np.allclose(r.pred_shares,r.pred_baskets*r.unit));check('interval ordered '+cut,r.lo_baskets.le(r.hi_baskets).all());check('out of training range suppresses candidate '+cut,not r.loc[r.outside_training_range,'candidate'].any())
  for bad,reason in [(z.assign(date=m['trained_through']),'future/model date'),(z.assign(cutoff='09:30'),'wrong cutoff'),(z.assign(unit=0),'zero unit'),(z.assign(l2_quality_valid=False),'bad L2')]:
   rejected=False
   try:score_v2(bad,m)
   except ValueError:rejected=True
   check('reject '+reason+' '+cut,rejected)
  r.head(10).to_csv(R/f'score_reproduction_{cut.replace(":","")}.csv',index=False)
 prior=json.loads((L/'cases/20260902_520600/results/artifact_hashes.json').read_text())
 for file in (L/'study/models').glob('*.joblib'):check('v1 remains frozen '+file.name,hashlib.sha256(file.read_bytes()).hexdigest()==prior['frozen_model/'+file.name])
 result=dict(checks_passed=len(checks),checks=checks,old_overlap_rows=len(joined),dataset_rows=len(p),source_assumptions=['Historical exchange timestamps available; original receive timestamps unavailable','Final same-day FX excluded; prior settlement availability assumed','Share-history cache labels, not gross primary-market orders','Validation gate precision is not guaranteed future precision']);(R/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()

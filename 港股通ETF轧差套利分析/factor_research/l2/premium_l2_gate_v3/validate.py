"""Reconcile the two-stage experiment, including independent minute/label checks."""
import json, unittest
from pathlib import Path
import numpy as np
import pandas as pd
import pipeline as p

class GateContract(unittest.TestCase):
 def test_weak_persistent_and_spike(self):
  row=dict(settlement_mean_bp=10,settlement_positive_fraction=.70,settlement_premium_bp=.01,fx_gap_bp=1,creation_allowed=1,redemption_allowed=1,premium_coverage=.95)
  self.assertTrue(p.gate(pd.DataFrame([row])).iloc[0])
  for k,v in [('settlement_mean_bp',9.99),('settlement_positive_fraction',.69),('settlement_premium_bp',0),('fx_gap_bp',0),('creation_allowed',0),('redemption_allowed',0),('premium_coverage',.949)]:
   self.assertFalse(p.gate(pd.DataFrame([{**row,k:v}])).iloc[0],k)
 def test_label_cannot_change_screen(self):
  d=pd.read_parquet(p.R/'premium.parquet');before=p.gate(d);d['net_shares']=-d.net_shares*1000;d['net_baskets']=np.nan;np.testing.assert_array_equal(before,p.gate(d))
 def test_minutes_and_features(self):
  for cut,count in [('14:30',210),('14:45',225)]:
   mins=p.expected_minutes(cut);self.assertEqual(len(mins),count);self.assertEqual(len(mins),len(set(mins)));self.assertLessEqual(max(mins),885);self.assertFalse(any(691<=m<=780 for m in mins))
  self.assertFalse(any(x in p.PREMIUM+p.ORDER for x in ['net_shares','net_baskets','net_flow_pct','date','symbol']))

def audit():
 checks=[]
 def check(name,ok):
  assert bool(ok),name
  checks.append(name)
 plan=json.loads((p.R/'plan.json').read_text());d=pd.read_parquet(p.R/'premium.parquet');new=pd.read_parquet(p.R/'new_candidates.parquet');pred=pd.read_parquet(p.R/'predictions.parquet');summary=pd.read_csv(p.R/'summary.csv');old=pd.read_parquet(p.V/'panel.parquet')
 check('premium key unique',not d.duplicated(p.KEY).any());check('new L2 key unique',not new.duplicated(p.KEY).any());check('prediction key unique',not pred.duplicated(p.KEY+['variant']).any())
 check('new dates disjoint from v2',not set(d[d.split.eq('new_test')].date)&set(old.date));check('12 newly held out dates',d[d.split.eq('new_test')].date.nunique()==12)
 check('strict fit/calibration chronology',d[d.split.eq('train')].date.max()<d[d.split.eq('validation')].date.min()<=d[d.split.eq('validation')].date.max()<d[d.split.eq('new_test')].date.min())
 intended=d[d.split.eq('new_test')&d.premium_gate];check('L2 called only for screened rows',set(map(tuple,new[p.KEY].to_numpy()))==set(map(tuple,intended[p.KEY].to_numpy())))
 count=json.loads((p.R/'compute_counts.json').read_text());check('native call count matches union of both FX screens',sum(x['native_calls'] for x in count)==len(intended[['date','symbol','cutoff']].drop_duplicates()))
 # v2 labels and shared lag context must match where available.
 join=d[d.fx_basis.eq('lag')].merge(old,on=['date','symbol','cutoff'],suffixes=('_new','_old'),validate='one_to_one')
 for col in ['net_shares','net_baskets',*p.BASE]:
  a=join[col+'_new'].to_numpy(float);b=join[col+'_old'].to_numpy(float);check('v2 parity '+col,np.allclose(a,b,equal_nan=True,rtol=1e-8,atol=1e-8))
 # Independent direct arithmetic from saved minute series and raw share history.
 sample=d[d.split.eq('new_test')].sample(48,random_state=7)
 for row in sample.itertuples():
  raw=pd.read_parquet(p.F/'results/series'/(row.date.replace('-','')+'.parquet'),filters=[('symbol','==',row.symbol)])
  minute=raw.minute.str.slice(0,2).astype(int)*60+raw.minute.str.slice(3,5).astype(int);end=int(row.cutoff[:2])*60+int(row.cutoff[3:]);mask=((minute>=571)&(minute<=690))|((minute>=781)&(minute<=end));s=raw[mask];col='lag_settlement' if row.fx_basis=='lag' else 'actual_settlement_buy';s=s.dropna(subset=['etf','mid',col]);v=(s.etf.to_numpy()/s[col].to_numpy()-1)*10000
  check(f'minute mean {row.date} {row.symbol} {row.cutoff} {row.fx_basis}',abs(v.sum()/len(v)-row.settlement_mean_bp)<1e-8)
  history=json.loads((p.F/'inputs/share_history'/(row.symbol+'.json')).read_text())['rows'];r=next(x for x in history if x['share_date']==row.date)
  check(f'raw share change {row.date} {row.symbol}',abs(r['share_change_10k']*10000-row.net_shares)<1e-5)
 # Independently reconstruct every summary count from exported rows, not pipeline.stats.
 for row in summary.itertuples():
  universe=d[(d.fx_basis==row.fx_basis)&(d.cutoff==row.cutoff)&(d.split==row.split)]
  if row.stage=='all':z=universe
  elif row.stage=='premium_gate':z=universe[universe.premium_gate]
  elif row.stage=='gate_l2_valid':z=pred[(pred.fx_basis==row.fx_basis)&(pred.cutoff==row.cutoff)&(pred.split==row.split)&pred.variant.eq('premium')]
  elif row.stage=='premium_equal_daily_count':continue
  else:
   variant='premium_l2' if row.stage.startswith('premium_l2') else 'premium';z=pred[(pred.fx_basis==row.fx_basis)&(pred.cutoff==row.cutoff)&(pred.split==row.split)&pred.variant.eq(variant)]
   z=z[z.selected] if row.stage.endswith('selected') else z[z.score>=float(row.stage[-2:])/100]
  check('summary '+row.fx_basis+row.cutoff+row.split+row.stage,len(z)==row.n and (z.net_shares>0).sum()==row.create and (z.net_shares<0).sum()==row.redeem and (z.net_shares==0).sum()==row.flat and abs(row.coverage-len(z)/len(universe))<1e-12)
 p.save('validation.json',dict(status='passed',checks=len(checks),items=checks,independent_minute_samples=len(sample),limits='No account identities, no gross creation/redemption, no realized settlement P&L, no original receipt timestamps; row Wilson intervals do not address correlated trades.'))
 print('AUDIT PASSED',len(checks),'checks')

if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(GateContract);result=unittest.TextTestRunner(verbosity=2).run(suite)
 if not result.wasSuccessful():raise SystemExit(1)
 audit()

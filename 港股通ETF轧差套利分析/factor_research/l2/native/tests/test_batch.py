import sys,unittest
from pathlib import Path
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'batch'))
import intraday
import test_binding

class BatchTests(unittest.TestCase):
 def setUp(self):
  self.f=test_binding.BindingTests();self.f.setUp();kw=self.f.fixture()
  self.manifest=dict(schema_version=1,date='2026-01-05',snapshot_until='2026-01-05T14:45:00+08:00',complete_from_open=True,items=[dict(symbol='159636.SZ',unit=100,pcf_known_at='2026-01-05T08:00:00+08:00',trade_file=kw['trade_file'],order_file=kw['order_file'],quote_file=kw['quote_file'])])
  self.context=pd.DataFrame([dict(date='2026-01-05',symbol='159636.SZ',minute=f'{m//60:02}:{m%60:02}',known_at=f'2026-01-05T{m//60:02}:{m%60:02}:00+08:00',etf=1.005,iopv_mid=1.02,iopv_estimate=1.,fx_basis='intraday_estimate') for m in intraday.expected_minutes('14:45')])
  self.model=dict(schema_version=1,asof_compatible=True,cutoffs=['14:30','14:45'],trained_through='2026-01-02',features=['settlement_mean_bp','l2_active_imbalance'],center=[0,0],scale=[1,1],coefficients=[[0,0],[0,0],[0,0]],intercepts=[4,0,0],classes=['net_create','flat','net_redeem'],min_p_create=.9,cost_buffer_bp=30,calibration_validated=False)
 def tearDown(self):self.f.tearDown()
 def run_rows(self,model=None,cutoff='14:45'):return intraday.run_batch(self.manifest,self.context,cutoff,model)['rows']
 def test_without_model_never_invents_probability(self):
  row=self.run_rows()[0];self.assertEqual(row['status'],'features_ready');self.assertIsNone(row['probabilities']);self.assertFalse(row['candidate']);self.assertAlmostEqual(row['premium_coverage'],1)
 def test_model_probabilities_and_research_only(self):
  result=intraday.run_batch(self.manifest,self.context,'14:45',self.model);row=result['rows'][0]
  self.assertEqual(result['mode'],'research_only');self.assertEqual(row['direction'],'net_create');self.assertTrue(row['candidate']);self.assertEqual(row['status'],'research_candidate');self.assertAlmostEqual(sum(row['probabilities'].values()),1);self.assertFalse(row['calibration_validated'])
 def test_1430_uses_all_210_completed_minutes(self):
  d=intraday.prepare_context(self.context,'2026-01-05','14:30');self.assertEqual(len(d),210);self.assertAlmostEqual(self.run_rows(cutoff='14:30')[0]['premium_coverage'],1)
 def test_final_fx_forbidden(self):
  self.context['fx_basis']='final_settlement'
  with self.assertRaises(ValueError):self.run_rows()
 def test_future_price_rows_do_not_change_features(self):
  old=self.run_rows()[0];extra=self.context.iloc[-1].copy();extra['minute']='14:46';extra['known_at']='2026-01-05T14:46:00+08:00';extra['etf']=10000;extra['fx_basis']='final_settlement';self.context=pd.concat([self.context,pd.DataFrame([extra])],ignore_index=True);new=self.run_rows()[0]
  for name in intraday.FEATURES:self.assertAlmostEqual(old[name],new[name])
 def test_future_availability_removed_even_if_old_event_time(self):
  self.context.loc[0,'known_at']='2026-01-05T14:46:00+08:00';d=intraday.prepare_context(self.context,'2026-01-05','14:45');self.assertEqual(len(d),224)
 def test_model_training_future_rejected(self):
  self.model['trained_through']='2026-01-05';r=self.run_rows(self.model)[0];self.assertEqual(r['status'],'error');self.assertFalse(r['candidate'])
 def test_incomplete_snapshot_rejected(self):
  self.manifest['snapshot_until']='2026-01-05T14:30:00+08:00'
  with self.assertRaises(ValueError):self.run_rows()
 def test_future_pcf_blocks_symbol(self):
  self.manifest['items'][0]['pcf_known_at']='2026-01-05T15:00:00+08:00';self.assertEqual(self.run_rows()[0]['status'],'error')
 def test_duplicate_minute_rejected(self):
  self.context=pd.concat([self.context,self.context.iloc[:1]],ignore_index=True)
  with self.assertRaises(ValueError):self.run_rows()
 def test_naive_available_timestamp_rejected(self):
  self.context.loc[0,'known_at']='2026-01-05T09:31:00'
  with self.assertRaises(ValueError):self.run_rows()
 def test_low_price_coverage_blocks_candidate(self):
  self.context=self.context.iloc[:100];row=self.run_rows(self.model)[0];self.assertEqual(row['status'],'quality_blocked');self.assertFalse(row['candidate'])
 def test_one_bad_fund_does_not_abort_batch(self):
  second=dict(self.manifest['items'][0]);second['symbol']='159920.SZ';self.manifest['items'].append(second);rows=self.run_rows();self.assertEqual(rows[0]['status'],'features_ready');self.assertEqual(rows[1]['status'],'error')
if __name__=='__main__':unittest.main()

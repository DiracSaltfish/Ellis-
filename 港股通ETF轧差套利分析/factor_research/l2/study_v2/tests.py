"""Meaningful contract tests: separate sides, no cancel-as-execution, cutoff and source invariants."""
from pathlib import Path
import sys,unittest
import numpy as np,pandas as pd
R=Path(__file__).resolve().parent;L=R.parent;sys.path[:0]=[str(L/'native/build'),str(L/'native/batch')]
import etf_l2
from features import execution_features,V2_FEATURES
from intraday import near
class FeatureTests(unittest.TestCase):
 def test_native_september_reconciles_reference_without_labels(self):
  p=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/单标的研究提取/520600_20260902/20260902/520600.SZ');n=etf_l2.process_files(str(p/'逐笔成交.csv'),str(p/'逐笔委托.csv'),str(p/'行情.csv'),520600,20260902,500000,cutoff='14:45',session_profile='sh_etf_20260706');f=execution_features(n,500000,235868000,'SH',dict(creation=2e8,redemption=1e7))
  self.assertAlmostEqual(f['v2_sell_unit_U'],58.4322);self.assertAlmostEqual(f['v2_buy_unit_U'],14.222);self.assertAlmostEqual(n['execution']['sell']['unit_passive']/500000,18.1086);self.assertEqual(n['execution']['sell']['unit_filled_orders'],65)
  o=pd.DataFrame(n['orders'])
  for side,key in [(1,'buy'),(2,'sell')]:
   z=o[o.side.eq(side)];known=z.quantity_evidence.isin([1,2]);mask=(known&near(z.original_quantity,500000))|(~known&near(z.active_filled,500000));q=z[mask];self.assertEqual(n['execution'][key]['unit_active'],q.active_filled.sum());self.assertEqual(n['execution'][key]['unit_passive'],q.passive_filled.sum());self.assertEqual(n['execution'][key]['known_unit_cancelled'],z.loc[known&near(z.original_quantity,500000),'cancelled'].sum())
  self.assertTrue(set(V2_FEATURES).issubset(f));self.assertGreater(f['v2_buy_unit_cancel_ratio'],f['v2_buy_unit_fill_ratio'])
  other=execution_features(n,500000,235868000*2,'SH',{});self.assertAlmostEqual(other['v2_sell_unit_pct'],f['v2_sell_unit_pct']/2);self.assertTrue(np.isnan(other['v2_creation_limit_pct']));self.assertEqual(other['v2_creation_limit_known'],0)
 def test_invalid_native_gate(self):
  with self.assertRaises(ValueError):execution_features({'audit':{'order_features_valid':False,'trade_features_valid':True}},500000,1000000,'SH')
 def test_nonpositive_denominator(self):
  with self.assertRaises(ValueError):execution_features({},500000,0,'SH')
if __name__=='__main__':unittest.main()

import unittest
from signal_gate import evaluate
class Tests(unittest.TestCase):
 def run_gate(self,**kw):
  args=dict(symbol='513120.SH',score=.95,unit_shares=1000000,sell_unit_executed_shares=2000000,buy_unit_executed_shares=1000000,candidate_complete=True,order_trade_valid=True);args.update(kw);return evaluate(**args)
 def test_boundary(self):
  self.assertTrue(self.run_gate()['trigger']);self.assertFalse(self.run_gate(score=.949999)['trigger']);self.assertFalse(self.run_gate(buy_unit_executed_shares=2000000)['trigger'])
 def test_shenzhen_always_excluded(self):
  for s in ['159297.SZ','159297.SH','513120.SZ','513130.SH']:
   self.assertFalse(self.run_gate(symbol=s)['trigger'])
 def test_quality_and_nan(self):
  for kw in [dict(candidate_complete=False),dict(order_trade_valid=False),dict(score=float('nan'))]:self.assertFalse(self.run_gate(**kw)['trigger'])
 def test_exact_shares(self):
  with self.assertRaises(TypeError):self.run_gate(sell_unit_executed_shares=1.2)
  with self.assertRaises(ValueError):self.run_gate(unit_shares=0)
  self.assertTrue(self.run_gate(sell_unit_executed_shares=1000001)['trigger'])
if __name__=='__main__':unittest.main()

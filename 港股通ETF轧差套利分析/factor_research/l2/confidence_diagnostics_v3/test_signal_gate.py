import unittest
from dataclasses import replace
from signal_gate import Evidence,evaluate

class GateTests(unittest.TestCase):
    def setUp(self):
        self.e=Evidence('2026-07-02','2026-07-01','2026-07-01',.96,1000000,200000000,20000000,100000000,True,True)
    def test_excess_pass_and_boundary(self):
        self.assertTrue(evaluate(self.e)['pass_evidence_gate'])
        self.assertFalse(evaluate(replace(self.e,previous_net_creation_shares=180000000))['pass_evidence_gate'])
    def test_no_same_day_or_stale_history(self):
        for value in ['2026-07-02','2026-07-03','2026-06-30']:
            self.assertFalse(evaluate(replace(self.e,history_share_date=value))['pass_evidence_gate'])
    def test_reject_incomplete_and_weak_score(self):
        for kwargs in [dict(score=.949999),dict(score=float('nan')),dict(all_candidates_complete=False),dict(trade_and_order_valid=False)]:
            self.assertFalse(evaluate(replace(self.e,**kwargs))['pass_evidence_gate'])
        self.assertTrue(evaluate(replace(self.e,score=.95))['pass_evidence_gate'])
    def test_prior_redemption_is_not_negative_inventory(self):
        r=evaluate(replace(self.e,previous_net_creation_shares=-90000000))
        self.assertEqual(r['previous_positive_creation_U'],0)
        self.assertEqual(r['inventory_hypothesis_excess_U'],180)
    def test_invalid_quantities(self):
        with self.assertRaises(TypeError):evaluate(replace(self.e,unit_shares=1000000.0))
        with self.assertRaises(ValueError):evaluate(replace(self.e,unit_shares=0))
    def test_inventory_dominated_supply(self):
        r=evaluate(replace(self.e,sell_unit_executed_shares=248457600,buy_unit_executed_shares=98250000,previous_net_creation_shares=275000000,score=.975023))
        self.assertFalse(r['pass_evidence_gate']);self.assertAlmostEqual(r['inventory_hypothesis_excess_U'],-124.7924)

if __name__=='__main__':unittest.main()

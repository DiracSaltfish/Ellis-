import unittest
from model import round_trip, expected_profit


class ModelTest(unittest.TestCase):
    def test_user_example_and_own_dilution(self):
        r = round_trip(90, 10, 1, 1010000, 1000000, 1000000)
        self.assertAlmostEqual(r.gross_total, 80/91*10000)
        self.assertEqual(r.total_create, 91)
        self.assertEqual(r.total_redeem, 11)

    def test_cash_conservation(self):
        for S,R in [(90,10),(10,90),(0,10),(10,0),(0,0),(10,10)]:
            r=round_trip(S,R,1,1010000,1000300,999800)
            residual=r.total_create*r.create_price-r.total_redeem*r.redeem_price
            self.assertAlmostEqual(residual, max(S-R,0)*1000300-max(R-S,0)*999800)

    def test_roundtrip_does_not_generate_external_flow(self):
        self.assertEqual(round_trip(0,0,7,1010000,1000000,1000000).gross_total, 0)
        self.assertLess(round_trip(10,90,1,1010000,1000000,1000000).gross_total, 0)

    def test_identical_net_flow_different_return(self):
        a=round_trip(90,10,1,1010000,1000000,1000000)
        b=round_trip(900,820,1,1010000,1000000,1000000)
        self.assertEqual(a.net_create,b.net_create)
        self.assertGreater(a.gross_total,b.gross_total*9)

    def test_common_cash_cancels(self):
        a=round_trip(90,10,1,1010000,1000000,999800)
        b=round_trip(90,10,1,1015000,1005000,1004800)
        self.assertAlmostEqual(a.gross_total,b.gross_total)

    def test_expectation_and_cost(self):
        r=expected_profit([{'probability':0.75,'S':90,'R':10},
                           {'probability':0.25,'S':10,'R':90}],
                          x=1,M=1010000,B=1000000,D=1000000,fee=500)
        self.assertAlmostEqual(r['expected_net_total'],0.5*80/91*10000-500)
        self.assertEqual(r['probability_of_loss'],0.25)

    def test_invalid_input(self):
        for x in [0, -1, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                round_trip(90,10,x,1010000,1000000,1000000)


if __name__ == '__main__':
    unittest.main()

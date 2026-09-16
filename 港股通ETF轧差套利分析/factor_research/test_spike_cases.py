import unittest
import numpy as np
import pandas as pd
from spike_cases import detect

class Shapes(unittest.TestCase):
    def frame(self,p):
        n=len(p);return pd.DataFrame({'date':['2026-06-01']*n,'minute':pd.date_range('2026-06-01 10:01',periods=n,freq='min').strftime('%H:%M'),'etf':1+np.array(p)/10000,'actual_settlement_buy':np.ones(n),'amount':np.ones(n)*1000})
    def test_detects_sustained_spike_then_fall(self):
        d=self.frame([0]*15+[80]*5+[0]*25);r=detect(d,30)
        self.assertIsNotNone(r);self.assertGreaterEqual(r['fall_bp'],30)
    def test_rejects_single_minute_spike(self):
        self.assertIsNone(detect(self.frame([0]*15+[80]+[0]*25),30))
    def test_does_not_bridge_lunch(self):
        d=self.frame([0]*15+[80]*5+[0]*25)
        d.loc[20:,'minute']=pd.date_range('2026-06-01 13:01',periods=25,freq='min').strftime('%H:%M')
        self.assertIsNone(detect(d,30))
    def test_rejects_persistent_premium_without_drop(self):
        self.assertIsNone(detect(self.frame([0]*15+[80]*30),30))

if __name__=='__main__':unittest.main()

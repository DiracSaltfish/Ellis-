import sys,unittest
from pathlib import Path
import numpy as np,pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model import round_trip
from profit_research import pnl
class Formula(unittest.TestCase):
    def test_matches_original_cashflow_model(self):
        for n in [-100,-1,0,1,100]:
            for r in [0,1,4,9]:
                for x in [1,5,50]:
                    M,B,S=1010000,1000000,999900
                    d=pd.DataFrame([dict(net_baskets=n,M=M,B=B,Sell=S)])
                    got=pnl(d,r,10,x).net_yuan.iloc[0]
                    create=(1+r)*n if n>0 else r*abs(n)
                    redeem=r*n if n>0 else (1+r)*abs(n)
                    expected=round_trip(create,redeem,x,M,B,S,fee=B*.001).net_total
                    self.assertAlmostEqual(got,expected,places=6)
    def test_flat_loses_cost_and_size_dilutes(self):
        d=pd.DataFrame([dict(net_baskets=0,M=1010000,B=1000000,Sell=999900)])
        self.assertAlmostEqual(pnl(d).net_yuan.iloc[0],-1000)
        d.net_baskets=10
        self.assertLess(pnl(d,x=50).return_bp.iloc[0],pnl(d,x=1).return_bp.iloc[0])
if __name__=='__main__':unittest.main()

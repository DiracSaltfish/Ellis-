import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import minimize

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_pilot import ridge_fit, minute_series, labels, TOOLS, walkforward


class ResearchTests(unittest.TestCase):
    def test_constrained_fit_matches_independent_optimizer(self):
        rng=np.random.default_rng(7)
        for _ in range(15):
            x=rng.normal(size=(100,2));y=x@rng.uniform(-1,4,2)+rng.normal(size=100)*.2
            alpha=.1;b=ridge_fit(x,y,alpha)
            penalty=alpha*np.trace(x.T@x/len(y))/2
            objective=lambda v:np.mean((y-x@v)**2)+penalty*(v@v)
            ref=minimize(objective,[.5,.5],method='SLSQP',bounds=[(0,2)]*2,
                constraints=[{'type':'ineq','fun':lambda v:2.5-v.sum()}],options={'ftol':1e-12})
            self.assertTrue(ref.success)
            self.assertAlmostEqual(objective(b),ref.fun,places=8)
            self.assertTrue((b>=0).all() and (b<=2).all() and b.sum()<=2.5+1e-12)

    def test_last_marks_do_not_backfill(self):
        px,stamp=minute_series([[800,10,10,10,10],[805,12,12,12,12]])
        self.assertTrue(np.isnan(px.loc[799]));self.assertEqual(px.loc[804],10)
        self.assertEqual(stamp.loc[804],800)

    def make_points(self):
        rows=[]
        for day in ['20260601','20260602']:
            for minute in range(780,790):
                row={'date':day,'minute':minute,'basket_hkd':float(100+minute-780),'stale_weight':0.,'frozen_weight':0.}
                row.update({n:200+minute-780 for n in TOOLS})
                row.update({n+'_contract':'SAME' for n in TOOLS[:3]});rows.append(row)
        return pd.DataFrame(rows)

    def test_labels_no_day_or_roll_crossing(self):
        points=self.make_points();a=labels(points,5)
        self.assertEqual(len(a),10);self.assertTrue((a.end_minute<=789).all())
        points.loc[(points.date=='20260601')&(points.minute>=785),'HSI_FUT_contract']='OTHER'
        b=labels(points,5)
        self.assertEqual(len(b[b.date=='20260601']),0)

    def test_constant_daily_fx_cancels_in_return(self):
        points=self.make_points();a=labels(points,5)
        points.loc[points.date=='20260601','basket_hkd']*=.87654
        b=labels(points,5);np.testing.assert_allclose(a.y,b.y,atol=1e-14)

    def test_future_test_changes_cannot_change_prior_folds(self):
        rng=np.random.default_rng(12);dates=pd.bdate_range('2026-01-01',periods=63).strftime('%Y%m%d')
        records=[]
        for d in dates:
            for m in [780,795,810]:
                x=rng.normal(0,.001,len(TOOLS))
                records.append({'date':d,'minute':m,'end_minute':m+15,'y':x[0]*.7+x[2]*.3,
                    'stale_weight':0.,'frozen_weight':0.,**dict(zip(TOOLS,x))})
        data=pd.DataFrame(records);first,folds=walkforward(data,15)
        data.loc[data.date==dates[-1],'y']+=.1
        second,other=walkforward(data,15)
        pd.testing.assert_frame_equal(folds,other)
        pd.testing.assert_frame_equal(first[first.date!=dates[-1]],second[second.date!=dates[-1]])


if __name__=='__main__':unittest.main()

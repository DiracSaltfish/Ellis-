#!/usr/bin/env python3
import json
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd

from selection_core import fit_beta_constrained, make_returns, rolling_oos

ROOT=Path(__file__).resolve().parents[1]

def panel(days=140):
    start=pd.Timestamp("2026-01-02",tz="Asia/Hong_Kong")
    rows=[]; rng=np.random.default_rng(7)
    for d in range(days):
        day=start+pd.Timedelta(days=d)
        if day.weekday()>=5: continue
        for minute in list(range(570,691))+list(range(780,901)):
            ts=day.normalize()+pd.Timedelta(minutes=minute)
            x1=100+0.02*(d*331+minute)+rng.normal(0,0.01)
            x2=100+0.01*(d*331+minute)+rng.normal(0,0.01)
            rows.append({"timestamp_end":ts,"target_price":100+0.5*(x1-100)+rng.normal(0,0.005),"HSI_FUT":x1,"HHI_FUT":x2,"HTI_FUT":100+0.015*(d*331+minute)})
    return pd.DataFrame(rows)

def main():
    checks=[]
    beta=fit_beta_constrained(np.array([[1.,0.],[0.,1.],[1.,1.]]),np.array([.5,.2,.7]))
    checks.append({"check":"nonnegative_sum_constraint","passed":bool(np.all(beta>=-1e-9) and np.all(beta<=2+1e-9) and beta.sum()<=2+1e-9),"actual":beta.tolist()})
    p=panel(); r=make_returns(p,60)
    def sess(m):
        return "AM" if 570 <= int(m) <= 690 else ("PM" if 780 <= int(m) <= 900 else None)
    direct_ok=all(sess(m)==sess(int(m)+60) and ((sess(m)=="AM" and int(m)+60<=690) or (sess(m)=="PM" and int(m)+60<=900)) for m in r["minute_end"])
    # Explicit lunch, day-end, and next-day fixtures ensure no cross-session or
    # cross-day endpoint is silently accepted.
    fixture=[]
    for day in ["20260102","20260105"]:
        minutes=list(range(685,701))+list(range(895,906))+list(range(570,576))
        for minute in minutes:
            ts=pd.Timestamp(day,tz="Asia/Hong_Kong")+pd.Timedelta(minutes=minute)
            fixture.append({"timestamp_end":ts,"target_price":100.0+minute/1000,"HSI_FUT":200.0+minute/1000})
    fr=make_returns(pd.DataFrame(fixture),5)
    fixture_ok=(len(fr)>0 and all(sess(m)==sess(int(m)+5) for m in fr["minute_end"]) and not any(int(m) in {686,687,688,689,690,691,696,697,698,699,700,896,897,898,899,900} for m in fr["minute_end"]))
    checks.append({"check":"same_continuous_session","passed":bool(direct_ok and fixture_ok),"actual":{"rows":len(r),"fixture_rows":len(fr),"bad_labels":int(sum(not (sess(m)==sess(int(m)+60)) for m in r["minute_end"]))}})
    out, res, w=rolling_oos(p,["HSI_FUT","HHI_FUT"],horizons=[30],policies=[["HSI_FUT"],["HHI_FUT"],["HSI_FUT","HHI_FUT"]])
    checks.append({"check":"real_engine_run","passed":bool(len(res[30])>3 and len(w[30])>0),"actual":{"oos_rows":len(res[30]),"weight_days":len(w[30]),"primary":out[30]["primary"]}})
    # Future mutation: observations after a cutoff may not alter earlier weights.
    weight_dates=sorted(str(x["date"]) for x in w[30])
    cutoff_day=weight_dates[10]
    cutoff=max(x for x in p["timestamp_end"] if pd.Timestamp(x).strftime("%Y%m%d")==cutoff_day)
    p2=p.copy(); p2.loc[p2["timestamp_end"]>cutoff,"HSI_FUT"]*=10
    _,_,w2=rolling_oos(p2,["HSI_FUT","HHI_FUT"],horizons=[30],policies=[["HSI_FUT"],["HHI_FUT"],["HSI_FUT","HHI_FUT"]])
    before=lambda ws:[x for x in ws if str(x["date"]) < cutoff_day]
    after=lambda ws:[x for x in ws if str(x["date"]) > cutoff_day]
    def comparable(ws): return [(x["date"],x["policy_id"],x["beta"]) for x in ws]
    late_changed=comparable(after(w[30])) != comparable(after(w2[30]))
    checks.append({"check":"future_mutation_does_not_change_earlier_weights","passed":len(before(w[30]))>=5 and before(w[30])==before(w2[30]) and late_changed,"actual":{"earlier_weights":len(before(w[30])),"late_weights_changed":late_changed,"cutoff_day":cutoff_day}})
    outp=ROOT/"checks"; outp.mkdir(exist_ok=True); (outp/"selection_core_tests.json").write_text(json.dumps({"engine_version":"FULL237_RHO060_V1","checks":checks,"passed":all(x["passed"] for x in checks)},ensure_ascii=False,indent=2,default=str))
    print(json.dumps({"engine_version":"FULL237_RHO060_V1","checks":checks,"passed":all(x["passed"] for x in checks)},ensure_ascii=False,indent=2,default=str))
    raise SystemExit(0 if all(x["passed"] for x in checks) else 1)
if __name__=="__main__": main()

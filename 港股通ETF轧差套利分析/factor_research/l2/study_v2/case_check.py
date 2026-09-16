"""Case used to design features, therefore explicitly not independent test evidence."""
from pathlib import Path
import sys,json
import pandas as pd,joblib
R=Path(__file__).resolve().parent;L=R.parent;C=L/'cases/20260902_520600';sys.path.insert(0,str(L/'native/build'));import etf_l2
from features import execution_features
from model import score_v2

def main():
 rows=[];raw=Path('/Volumes/EllisFiles/Stocksdata/A股逐笔/单标的研究提取/520600_20260902/20260902/520600.SZ');case=pd.read_csv(C/'results/features.csv');sel=json.loads((R/'selection.json').read_text())
 for cut in ['14:30','14:45']:
  d=case[case.cutoff.eq(cut)].copy();n=etf_l2.process_files(str(raw/'逐笔成交.csv'),str(raw/'逐笔委托.csv'),str(raw/'行情.csv'),520600,20260902,500000,cutoff=cut,session_profile='sh_etf_20260706');f=execution_features(n,500000,float(d.prev_shares.iloc[0]),'SH',dict(creation=2e8,redemption=1e7))
  for k,v in f.items():d[k]=v
  d.to_csv(R/f'case_features_{cut.replace(":","")}.csv',index=False)
  for name in ['old_features_asinh','enriched_asinh','enriched_hurdle']:
   m=joblib.load(R/'models'/f'{cut.replace(":","")}_{name}.joblib');out=score_v2(d,m);out['variant']=name;out['selected']=name==sel[cut]['variant'];out['actual_baskets']=61;out['role']='feature_design_case_not_independent_validation';rows.append(out)
 pd.concat(rows,ignore_index=True).to_csv(R/'case_predictions.csv',index=False);print(pd.concat(rows)[['cutoff','variant','p_create','pred_baskets','lo_baskets','hi_baskets','outside_training_range','candidate']].to_string(index=False))
if __name__=='__main__':main()

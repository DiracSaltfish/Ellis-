from pathlib import Path
import json,hashlib,joblib,numpy as np,pandas as pd
root=Path(__file__).resolve().parents[1];src=root/'factor_research/l2/premium_l2_gate_v3';out=Path(__file__).resolve().parent
for cut in ['1430','1445']:
 path=src/'models'/f'final_{cut}_premium.joblib';b=joblib.load(path);c=b['classifier'];trees=[]
 for predictors in c._predictors:
  assert len(predictors)==1
  rows=[]
  for n in predictors[0].nodes:
   assert not n['is_categorical']
   rows.append(dict(value=float(n['value']),feature=int(n['feature_idx']),threshold=float(n['num_threshold']),missing_left=bool(n['missing_go_to_left']),left=int(n['left']),right=int(n['right']),leaf=bool(n['is_leaf'])))
  trees.append(rows)
 model=dict(version=f'final_{cut}_premium',features=b['features'],baseline=float(c._baseline_prediction[0,0]),trees=trees,source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),fit_through=b['fit_through'],trained_through=b['trained_through'],cutoff=b['cutoff'],calibrated=False,fx_training='T-day realized settlement',fx_live='T-day website predicted settlement')
 (out/f'model_{cut}.json').write_text(json.dumps(model,ensure_ascii=False,separators=(',',':')))
 d=pd.read_parquet(src/'premium.parquet');d=d[(d.fx_basis=='final')&(d.cutoff==b['cutoff'])];x=d[b['features']].sample(min(250,len(d)),random_state=123).copy()
 # Missing branches are part of the trained tree contract; test them, not just dense vectors.
 extra=x.iloc[:25].copy();extra.iloc[:,::4]=np.nan;x=pd.concat([x,extra],ignore_index=True)
 y=c.predict_proba(x)[:,1];fixtures=[dict(x=[None if not np.isfinite(v) else float(v) for v in row],score=float(y[i])) for i,row in enumerate(x.to_numpy())]
 (out/f'golden_{cut}.json').write_text(json.dumps(fixtures,separators=(',',':')))
 print(cut,len(trees),len(fixtures),path.name)

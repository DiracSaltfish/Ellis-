"""将固定模型应用到已按同口径计算的全天特征表，不需要当日份额标签。"""
from pathlib import Path
import argparse,joblib,pandas as pd
R=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__);p.add_argument('input');p.add_argument('output');p.add_argument('--family',default='enriched',choices=['history_only','simple_levels','prior_full','enriched']);args=p.parse_args()
source=Path(args.input);d=pd.read_parquet(source) if source.suffix=='.parquet' else pd.read_csv(source)
models=joblib.load(R/'reverse/frozen_models.joblib');out=d[['date','symbol']].copy()
for target in ['flow_positive','large_positive','large_negative']:
    m,cols,threshold=models[(target,args.family)]
    missing=set(cols)-set(d.columns)
    if missing:raise ValueError('Missing features: '+', '.join(sorted(missing)))
    out[target+'_score']=m.predict_proba(d[cols])[:,1]
    out[target+'_selected']=out[target+'_score']>=threshold
    out[target+'_threshold']=threshold
out.to_csv(args.output,index=False)
print(f'Scored {len(out)} rows; full-day fixed-FX research scores, not calibrated execution probabilities.')

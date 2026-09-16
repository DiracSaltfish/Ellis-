from pathlib import Path
import pandas as pd,numpy as np,json,joblib
from reverse_analysis import enrich
r=Path(__file__).resolve().parent/'reverse'
base=pd.concat([pd.read_parquet(r/'base_features.parquet'),pd.read_parquet(r/'extension_features.parquet')])
a=enrich(base);b=enrich(base[base.date<='2026-05-29'])
cols=['premium_vs_20d_bp','relative_turnover_20d','within_day_premium_rank','basket_excess_market_bp','pressure_balance']
x=a[a.date<='2026-05-29'].sort_values(['date','symbol']);y=b.sort_values(['date','symbol'])
np.testing.assert_allclose(x[cols].to_numpy(),y[cols].to_numpy(),equal_nan=True)
changed=base.copy();changed['net_baskets']=999;changed['net_flow_pct']=-99;c=enrich(changed)
models=joblib.load(r/'frozen_models.joblib');fs=models[('flow_positive','enriched')][1]
np.testing.assert_allclose(a[fs].to_numpy(),c[fs].to_numpy(),equal_nan=True)
sample=a[a.date>'2026-06-30'].head(20)
feature_union=sorted(set(v for m,f,t in models.values() for v in f))
sample[['date','symbol']+feature_union].to_parquet(r/'results/scorer_input_example.parquet',index=False)
(r/'results/feature_integrity_checks.json').write_text(json.dumps(dict(prefix_history_invariant=True,current_labels_not_features=True,example_rows=20),indent=2))
print('Feature temporal and label isolation checks passed')

from pathlib import Path
import json
import numpy as np,pandas as pd
from sklearn.metrics import average_precision_score
R=Path(__file__).resolve().parent;D=R/'reverse';O=D/'results'
p=pd.read_parquet(O/'fresh_predictions.parquet');rng=np.random.default_rng(20260912);results=[]
for target in ['flow_positive','large_positive','large_negative']:
    sub=p[p.target==target]
    a=sub[sub.model=='enriched'];b=sub[sub.model=='prior_full']
    j=a.merge(b,on=['date','symbol'],suffixes=('_new','_old'),validate='one_to_one')
    assert (j.actual_new==j.actual_old).all()
    blocks=[g.index.to_numpy() for _,g in j.groupby('date')];diff=[]
    for _ in range(1000):
        ix=np.concatenate([blocks[k] for k in rng.integers(len(blocks),size=len(blocks))]);z=j.loc[ix]
        diff.append(average_precision_score(z.actual_new,z.score_new)-average_precision_score(z.actual_old,z.score_old))
    results.append(dict(target=target,ap_difference=float(average_precision_score(j.actual_new,j.score_new)-average_precision_score(j.actual_old,j.score_old)),
                        clustered_95_interval=np.quantile(diff,[.025,.975]).tolist()))
(O/'incremental_ap_bootstrap.json').write_text(json.dumps(results,indent=2))
# 质量异常敏感性：不改模型，分别剔除抽核异常日、整只基金。
quality=[]
for scope,mask in [('all',np.ones(len(p),dtype=bool)),('exclude_520660_0715',~((p.symbol=='520660.SH')&(p.date=='2026-07-15'))),('exclude_520660_all',p.symbol!='520660.SH')]:
    for (target,name),g in p[mask].groupby(['target','model']):
        if name not in ['enriched','prior_full','simple_levels']:continue
        s=g[g.selected];quality.append(dict(scope=scope,target=target,model=name,n=len(g),selected_n=len(s),precision=s.actual.mean(),ap=average_precision_score(g.actual,g.score)))
pd.DataFrame(quality).to_csv(O/'quality_sensitivity.csv',index=False)
print(json.dumps(results,indent=2))

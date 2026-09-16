"""抽样核对PCF数量/现金/汇率口径与上一日净值；不修改筛选条件。"""
from pathlib import Path
from zipfile import ZipFile
import csv, gzip, io, json
import numpy as np
import pandas as pd

R=Path(__file__).resolve().parent;I=R/'inputs';O=R/'results'
mid=json.loads((I/'midpoint.json').read_text())
rows=[]
for day in ['20250702','20250812','20250923','20251215','20260316','20260615']:
    path=I/'baskets'/(day+'.json.gz')
    if not path.exists():continue
    with gzip.open(path,'rt') as h:baskets=json.load(h)
    caches={}
    for symbol,b in baskets.items():
        date=b['prev_date']; prevkey=date.replace('-','')
        hk=Path('/Volumes/Upan/港股/港股_1分钟')/date[:7]/(prevkey+'_1min.zip')
        if date not in mid or not hk.exists():continue
        if date not in caches:caches[date]=(ZipFile(hk),{})
        z,cache=caches[date]
        valid=True;value=0
        for s,q in b['components']:
            if s not in cache:
                try:
                    df=pd.read_csv(io.BytesIO(z.read(s+'.csv')))
                    cache[s]=float(df['收盘价'].iloc[-1])
                except Exception:cache[s]=np.nan
            value+=q*cache[s]
        implied=(value*mid[date]+b['cash'])/b['unit']
        if not b['prev_nav']:continue
        rows.append(dict(symbol=symbol,pcf_date=b['date'],price_date=date,
            implied_prior_nav=implied,pcf_prior_nav=b['prev_nav'],
            difference_bp=(implied/b['prev_nav']-1)*10000))
    for z,_ in caches.values():z.close()
d=pd.DataFrame(rows);d.to_csv(O/'pcf_nav_reconciliation.csv',index=False)
valid=d.dropna();print('sample_count',len(d),'valid',len(valid))
print(valid.difference_bp.abs().quantile([.5,.9,.95,.99]).to_string())
print(valid.loc[valid.difference_bp.abs()>100].sort_values('difference_bp').to_string(index=False))

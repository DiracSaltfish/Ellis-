"""新验证段的数据质量和标签对齐独立检查。"""
from pathlib import Path
from zipfile import ZipFile
import io,json,gzip,urllib.request,hashlib
import pandas as pd,numpy as np
R=Path(__file__).resolve().parent;D=R/'reverse';I=D/'extension/inputs';O=D/'results';O.mkdir(exist_ok=True)
# 在线抽核三个网站份额历史，完整原始响应保存在本目录。
checks=[];sources=D/'website_checks';sources.mkdir(exist_ok=True)
for sym in ['513120.SH','159015.SZ','159570.SZ']:
    code=sym[-2:]+sym[:6];url='https://1navs.com/api/v1/funds/'+code+'/share-history?days=3660'
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=30) as h:raw=h.read()
        (sources/(sym+'.json')).write_bytes(raw);fresh=json.loads(raw)
        old=json.loads((R/'inputs/share_history'/(sym+'.json')).read_text())
        f={r['share_date']:r for r in fresh['rows']};dif=[];count=0
        for row in old['rows']:
            date=row['share_date']
            if not '2025-07-01'<=date<='2026-08-03' or date not in f:continue
            count+=1
            if abs(row['shares_10k']-f[date]['shares_10k'])>.001:dif.append(date)
        checks.append(dict(symbol=sym,url=url,compared_dates=count,revised_dates=dif,sha256=hashlib.sha256(raw).hexdigest()))
    except Exception as e:checks.append(dict(symbol=sym,error=str(e)))
(O/'website_label_checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
# 未分析过日期的PCF与上一日净值核对。
mid=json.loads((I/'midpoint.json').read_text());result=[]
for day in ['20260702','20260715','20260729','20260803']:
    path=I/'baskets'/(day+'.json.gz')
    if not path.exists():continue
    with gzip.open(path,'rt') as h:bs=json.load(h)
    caches={}
    for sym,b in bs.items():
        date=b['prev_date'];hk=Path('/Volumes/Upan/港股/港股_1分钟')/date[:7]/(date.replace('-','')+'_1min.zip')
        if date not in mid or not hk.exists():continue
        if date not in caches:caches[date]=(ZipFile(hk),{})
        z,cache=caches[date];value=0
        for s,q in b['components']:
            if s not in cache:
                try:cache[s]=float(pd.read_csv(io.BytesIO(z.read(s+'.csv')))['收盘价'].iloc[-1])
                except Exception:cache[s]=np.nan
            value+=q*cache[s]
        implied=(value*mid[date]+b['cash'])/b['unit']
        result.append(dict(date=b['date'],symbol=sym,difference_bp=(implied/b['prev_nav']-1)*10000))
    for z,_ in caches.values():z.close()
d=pd.DataFrame(result);d.to_csv(O/'extension_nav_checks.csv',index=False)
summary=dict(label_checks=checks,nav_n=len(d),nav_valid=int(d.difference_bp.notna().sum()),abs_nav_bp_quantiles=d.difference_bp.abs().quantile([.5,.9,.95,.99]).to_dict(),nav_over100=int((d.difference_bp.abs()>100).sum()))
(O/'quality_checks.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False),flush=True)

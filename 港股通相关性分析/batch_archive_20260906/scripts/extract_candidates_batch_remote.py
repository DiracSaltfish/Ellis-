#!/usr/bin/env python3
"""Stream multiple candidate PCF/minute bundles from machome.

The source archives are opened once per date.  Only the requested candidate
ETF member, its PCF components and the fixed hedge set are emitted; source
archives remain read-only.
"""
import csv
import gzip
import io
import json
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT=Path('/Volumes/EllisFiles/Stocksdata')
START,END=os.environ.get('BATCH_START','20260303'),os.environ.get('BATCH_END','20260803')
HEDGES=['02800','02828','03032','03033','02845']
SPECS=[]
for token in os.environ.get('FUND_SPECS','').split(','):
    if token.strip():
        code,exchange=token.strip().split(':',1);SPECS.append((code.zfill(6),exchange.upper()))


def read_rows(path):
    with path.open(encoding='utf-8-sig',newline='') as f: yield from csv.DictReader(f)


def prices(archive,name,date,hk):
    result={}
    with archive.open(name) as stream:
        for row in csv.DictReader(io.TextIOWrapper(stream,encoding='utf-8-sig')):
            stamp=row['时间'];dt=datetime.fromisoformat(stamp.replace('/','-'))
            if dt.strftime('%Y%m%d')!=date: raise ValueError('wrong date in '+name)
            minute=dt.hour*60+dt.minute
            if not (570<=minute<=960): continue
            px=float(row['价格' if hk else '收盘价'])
            if px<=0: continue
            if hk:
                if minute not in result: result[minute]=[px,px,px,px]
                bar=result[minute];bar[1]=max(bar[1],px);bar[2]=min(bar[2],px);bar[3]=px
            else:
                if minute in result: raise ValueError('duplicate CN minute '+stamp)
                result[minute]=[float(row['开盘价']),float(row['最高价']),float(row['最低价']),px]
    return [[m,*v] for m,v in sorted(result.items())]


def main():
    cnroot=ROOT/'基金_分钟数据/ETF_分钟数据/1分钟_按月归档'
    hkroot=ROOT/'港股_分笔成交/港股_分笔成交_按月归档'
    detailroot=ROOT/'PCF导出CSV'
    paths=sorted(p for p in cnroot.glob('*/*.zip') if START<=p.name[:8]<=END)
    specs=dict(SPECS)
    with gzip.GzipFile(fileobj=sys.stdout.buffer,mode='wb',mtime=0) as output:
        for index,cnpath in enumerate(paths):
            d=cnpath.name[:8];month=d[:4]+'-'+d[4:6]
            hkpath=hkroot/month/(d+'.zip');detail=detailroot/(d+'_明细.csv');headerpath=detailroot/(d+'_主表.csv')
            if not hkpath.exists() or not detail.exists() or not headerpath.exists(): continue
            detailrows=list(read_rows(detail));headers={str(r.get('基金代码','')).strip().zfill(6):r for r in read_rows(headerpath)}
            grouped={code:[] for code in specs}
            for row in detailrows:
                code=str(row.get('基金代码','')).strip().zfill(6)
                if code in grouped: grouped[code].append(row)
            with zipfile.ZipFile(hkpath) as hkz,zipfile.ZipFile(cnpath) as cnz:
                names=set(hkz.namelist());cnnames=set(cnz.namelist())
                hk_cache={};cn_cache={}
                for code,rows in grouped.items():
                    if not rows: continue
                    exchange=specs[code];h=headers.get(code,{})
                    codes=[f"{int(r['成分股代码']):05d}" for r in rows]
                    item={'date':d,'target':code,'exchange':exchange,'header':h,'components':rows,
                          'bar_columns':['minute_label','open','high','low','close'],'hk':{},'missing_members':[],'sources':[]}
                    for p in [cnpath,hkpath,headerpath,detail]:
                        stat=p.stat();item['sources'].append({'path':str(p),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns})
                    for security in sorted(set(codes+HEDGES)):
                        name=security+'.HK.csv'
                        if name in names:
                            if name not in hk_cache: hk_cache[name]=prices(hkz,name,d,True)
                            item['hk'][security]=hk_cache[name]
                        else:item['missing_members'].append(security)
                    cnname=code+'.'+exchange+'.csv'
                    if cnname in cnnames:
                        if cnname not in cn_cache: cn_cache[cnname]=prices(cnz,cnname,d,False)
                        item['cn']=cn_cache[cnname]
                    else:item['cn']=[];item['missing_cn']=True
                    output.write((json.dumps(item,ensure_ascii=False,separators=(',',':'))+'\n').encode())
            if index%10==0: print('batch_extracted',d,'funds',sum(bool(v) for v in grouped.values()),file=sys.stderr,flush=True)


if __name__=='__main__':main()

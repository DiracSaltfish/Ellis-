#!/usr/bin/env python3
"""Stream a single ETF's daily PCF and minute LAST prices as gzip JSONL.
Run on machome via stdin. Source archives are read only, never extracted.
HK trades are reduced immediately to minute OHLC; no tick/volume output.
"""
import csv
import gzip
import io
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path('/Volumes/EllisFiles/Stocksdata')
TARGET = '520600'
START, END = '20260303', '20260803'
if len(sys.argv)==3:START,END=sys.argv[1:]
HEDGES = ['02800','02828','03032','03033','02845']


def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        yield from csv.DictReader(f)


def prices(archive, name, date, hk):
    result = {}
    with archive.open(name) as stream:
        for row in csv.DictReader(io.TextIOWrapper(stream, encoding='utf-8-sig')):
            stamp = row['时间']
            dt = datetime.fromisoformat(stamp.replace('/','-'))
            if dt.strftime('%Y%m%d') != date: raise ValueError('wrong date in '+name)
            minute = dt.hour * 60 + dt.minute
            if not (570 <= minute <= 960): continue
            px = float(row['价格' if hk else '收盘价'])
            if px <= 0: continue
            if hk:
                if minute not in result: result[minute] = [px,px,px,px]
                bar = result[minute]; bar[1] = max(bar[1],px); bar[2] = min(bar[2],px); bar[3] = px
            else:
                if minute in result: raise ValueError('duplicate CN minute '+stamp)
                result[minute] = [float(row['开盘价']),float(row['最高价']),float(row['最低价']),px]
    return [[m,*v] for m,v in sorted(result.items())]


def main():
    cnroot = ROOT/'基金_分钟数据/ETF_分钟数据/1分钟_按月归档'
    hkroot = ROOT/'港股_分笔成交/港股_分笔成交_按月归档'
    paths = sorted(p for p in cnroot.glob('*/*.zip') if START <= p.name[:8] <= END)
    with gzip.GzipFile(fileobj=sys.stdout.buffer,mode='wb',mtime=0) as output:
        for index, cnpath in enumerate(paths):
            d=cnpath.name[:8]; month=d[:4]+'-'+d[4:6]
            hkpath=hkroot/month/(d+'.zip'); detail=ROOT/'PCF导出CSV'/(d+'_明细.csv')
            if not hkpath.exists() or not detail.exists():continue
            headerpath=detail.with_name(d+'_主表.csv')
            hh=[r for r in read_rows(headerpath) if r['基金代码']==TARGET]
            cc=[r for r in read_rows(detail) if r['基金代码']==TARGET]
            if len(hh)!=1 or not cc:raise ValueError('PCF absent/ambiguous '+d)
            codes=[f"{int(r['成分股代码']):05d}" for r in cc]
            if len(set(codes))!=len(codes):raise ValueError('duplicate component '+d)
            if len(cc)!=int(hh[0]['成分股数量只']):raise ValueError('PCF count mismatch '+d)
            item={'date':d,'target':TARGET,'header':hh[0], 'components':cc,
                  'bar_columns':['minute_label','open','high','low','close'],
                  'hk':{}, 'missing_members':[], 'sources':[]}
            for p in [cnpath,hkpath,headerpath,detail]:
                stat=p.stat();item['sources'].append({'path':str(p),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns})
            with zipfile.ZipFile(hkpath) as z:
                names=set(z.namelist())
                for code in sorted(set(codes+HEDGES)):
                    name=code+'.HK.csv'
                    if name in names:item['hk'][code]=prices(z,name,d,True)
                    else:item['missing_members'].append(code)
            with zipfile.ZipFile(cnpath) as z:
                item['cn']=prices(z,TARGET+'.SH.csv',d,False)
            output.write((json.dumps(item,ensure_ascii=False,separators=(',',':'))+'\n').encode())
            if index%10==0:print('extracted',d,'components',len(cc),'missing',len(item['missing_members']),file=sys.stderr,flush=True)


if __name__=='__main__':main()

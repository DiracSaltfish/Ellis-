#!/usr/bin/env python3
"""Read-only batch PCF coverage audit for the locked candidate universe.

The remote source is opened once per date; only header/detail counts and source
metadata are returned. No minute bars or account data are read.
"""
import csv
import json
import os
import re
import sys
from pathlib import Path

ROOT=Path('/Volumes/EllisFiles/Stocksdata')
START=os.environ.get('PCF_START','20260303')
END=os.environ.get('PCF_END','20260803')
CODES={x.strip().zfill(6) for x in os.environ.get('FUND_CODES','').split(',') if x.strip()}


def read_rows(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        yield from csv.DictReader(f)


def main():
    detail_root=ROOT/'PCF导出CSV'
    cn_root=ROOT/'基金_分钟数据/ETF_分钟数据/1分钟_按月归档'
    hk_root=ROOT/'港股_分笔成交/港股_分笔成交_按月归档'
    dates=sorted(p.name[:8] for p in cn_root.glob('*/*.zip') if START<=p.name[:8]<=END)
    out={c:{'pcf_dates':[],'row_counts':{},'component_counts':{},'duplicate_component_dates':[],
            'missing_quantity_dates':[],'header_mismatch_dates':[],'fund_names':set()} for c in CODES}
    source_dates=[]
    for index,d in enumerate(dates):
        detail=detail_root/(d+'_明细.csv');header=detail_root/(d+'_主表.csv')
        hkpath=hk_root/(d[:4]+'-'+d[4:6])/(d+'.zip')
        if not detail.exists() or not header.exists() or not hkpath.exists(): continue
        detail_rows=list(read_rows(detail));header_rows=list(read_rows(header))
        grouped={c:[] for c in CODES}
        for row in detail_rows:
            code=str(row.get('基金代码','')).strip().zfill(6)
            if code in grouped: grouped[code].append(row)
        headers={str(row.get('基金代码','')).strip().zfill(6):row for row in header_rows}
        source_dates.append({'date':d,'detail_path':str(detail),'detail_bytes':detail.stat().st_size,
                             'header_path':str(header),'header_bytes':header.stat().st_size,
                             'hk_path':str(hkpath),'hk_bytes':hkpath.stat().st_size})
        for code,rows in grouped.items():
            if not rows: continue
            comps=[str(r.get('成分股代码','')).strip().zfill(6) for r in rows]
            h=headers.get(code,{})
            out[code]['pcf_dates'].append(d);out[code]['row_counts'][d]=len(rows)
            out[code]['component_counts'][d]=len(set(comps));out[code]['fund_names'].add(h.get('基金名称',''))
            if len(comps)!=len(set(comps)): out[code]['duplicate_component_dates'].append(d)
            if any(not str(r.get('数量股','')).strip() for r in rows): out[code]['missing_quantity_dates'].append(d)
            expected=str(h.get('成分股数量只','')).strip()
            if expected and expected.isdigit() and int(expected)!=len(rows): out[code]['header_mismatch_dates'].append(d)
        if index%10==0: print('audited',d,'funds_with_pcf',sum(bool(v['pcf_dates']) for v in out.values()),file=sys.stderr,flush=True)
    for value in out.values(): value['fund_names']=sorted(value['fund_names'])
    print(json.dumps({'generated_on_remote':True,'root':str(ROOT),'start':START,'end':END,
                      'candidate_count':len(CODES),'date_file_count':len(dates),
                      'source_dates':source_dates,'funds':out},ensure_ascii=False,separators=(',',':')))


if __name__=='__main__': main()

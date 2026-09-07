#!/usr/bin/env python3
"""Cache public Yahoo daily/actions responses for the pilot component union.
This is a secondary-source event screen. Verified HKEX events are kept separately.
"""
import csv
import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]


def main():
    with gzip.open(ROOT/'data/raw/pilot_520600.jsonl.gz','rt') as f:days=[json.loads(x) for x in f]
    codes=sorted({f"{int(c['成分股代码']):05d}" for d in days for c in d['components']})
    cache=ROOT/'data/raw/actions_yahoo';cache.mkdir(exist_ok=True)
    start=int(datetime(2026,3,1,tzinfo=timezone.utc).timestamp());end=int(datetime(2026,8,4,tzinfo=timezone.utc).timestamp())
    def fetch(code):
        symbol=f'{int(code):04d}.HK';url='https://query1.finance.yahoo.com/v8/finance/chart/'+symbol
        path=cache/(code+'.json')
        try:
            if path.exists():payload=json.loads(path.read_text())
            else:
                r=requests.get(url,params={'period1':start,'period2':end,'interval':'1d','events':'div,splits'},headers={'User-Agent':'Mozilla/5.0'},timeout=20)
                r.raise_for_status();payload=r.json();path.write_text(json.dumps(payload))
            result=payload['chart']['result'][0]
            events=[]
            for typ,values in result.get('events',{}).items():
                for ev in values.values():events.append({'code':code,'event_type':typ,'date':datetime.fromtimestamp(ev['date'],timezone.utc).date().isoformat(),
                    'amount':ev.get('amount',''),'split_ratio':ev.get('splitRatio',''),'source':url,'verification':'secondary_source_screen'})
            return {'code':code,'status':'ok','events':events,'source':url}
        except Exception as e:return {'code':code,'status':'failed','error_type':type(e).__name__,'source':url}
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(fetch,codes))
    (ROOT/'data/inventory/corporate_actions_fetch.json').write_text(json.dumps(results,indent=2))
    events=[event for r in results for event in r.get('events',[])]
    with (ROOT/'data/inventory/corporate_actions_screen.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['code','event_type','date','amount','split_ratio','source','verification']);w.writeheader();w.writerows(events)
    print('symbols',len(codes),'ok',sum(r['status']=='ok' for r in results),'events',len(events),'failed',[r['code'] for r in results if r['status']!='ok'])


if __name__=='__main__':main()

import json,urllib.request,concurrent.futures,collections
from pathlib import Path
O=Path(__file__).resolve().parent
base='http://192.168.1.113:18680/api/v1/'
def get(p):return json.load(urllib.request.urlopen(base+p,timeout=30))
sn=get('snapshots')['snapshots']
def read(r):return r['symbol'],get('minutes?symbol='+r['symbol']+'&date=2026-09-14')
allrows=dict(concurrent.futures.ThreadPoolExecutor(max_workers=6).map(read,sn));(O/'minutes.json').write_text(json.dumps(allrows,ensure_ascii=False));results=[];counter=collections.Counter()
for sym,rows in allrows.items():
 mid=[r for r in rows if r.get('midpoint_iopv') is None];settle=[r for r in rows if r.get('settlement_buy_iopv') is None]
 reasons=collections.Counter(x for r in mid for x in r.get('reasons',[]));missing=collections.Counter(x for r in mid for x in r.get('missing',[]));counter.update(missing)
 results.append(dict(symbol=sym,rows=len(rows),mid_gaps=len(mid),settlement_gaps=len(settle),reasons=dict(reasons),missing=dict(missing)))
(O/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
r=allrows['513890.SH'];print('TARGET',next(x for x in results if x['symbol']=='513890.SH'))
for x in r:
 if x.get('midpoint_iopv') is None or x.get('settlement_buy_iopv') is None:print(x['minute'],x.get('missing'),x.get('stale'),x.get('reasons'))
print('OTHER',sorted([x for x in results if 0<x['mid_gaps']<x['rows']],key=lambda x:-x['mid_gaps'])[:12]);print('COMMON',counter.most_common(12));print('ANY',sum(x['mid_gaps']>0 for x in results),'INTERMITTENT',sum(0<x['mid_gaps']<x['rows'] for x in results))

import json,urllib.request,time,sqlite3
from pathlib import Path
base='http://127.0.0.1:18680/api/v1/'
def get(p):return json.load(urllib.request.urlopen(base+p,timeout=10))
h=get('health');points=get('snapshots')['snapshots'];checked=0;settled=0
for p in points:
 if p.get('midpoint_iopv') is not None:
  assert abs((p['hkd_assets']*p['midpoint_fx']+p['cny_assets'])/p['unit']-p['midpoint_iopv'])<1e-12;checked+=1
 for side in ['buy','sell']:
  k='settlement_'+side+'_iopv';fx='settlement_'+side+'_fx'
  if p.get(k) is not None and p.get(fx) is not None:
   assert abs((p['hkd_assets']*p[fx]+p['cny_assets'])/p['unit']-p[k])<1e-12;settled+=1
paths=['dates','suspensions','components?symbol=513090.SH','minutes?symbol=513090.SH&date=2026-09-09']
responses={p:get(p)for p in paths}
with urllib.request.urlopen(base+'export.csv?symbol=513090.SH&date=2026-09-09',timeout=10)as r:csv_header=r.readline().decode().strip();assert 'iopv' in csv_header
with urllib.request.urlopen(base+'stream',timeout=10)as r:
 events=[]
 while len(events)<2:
  l=r.readline()
  if l.startswith(b'data: '):events.append(json.loads(l[6:]))
assert events[1]['health']['now']>events[0]['health']['now']
db=Path.home()/'Library/Application Support/MachomeHub/data/premium/local-iopv/data/iopv.sqlite'
c=sqlite3.connect(f'file:{db}?mode=ro',uri=True);integrity=c.execute('pragma quick_check').fetchone()[0];assert integrity=='ok';tables=[x[0]for x in c.execute("select name from sqlite_master where type='table'")];c.close()
r={'health':h,'point_count':len(points),'math_checked':checked,'settlement_math_checked':settled,'sample':next((p for p in points if p['symbol']=='513090.SH'),{}),'endpoint_types':{k:type(v).__name__ for k,v in responses.items()},'csv_header':csv_header,'sse_two_events':True,'sqlite_integrity':integrity,'tables':tables}
p=Path('/tmp/machome-production-iopv.json');p.write_text(json.dumps(r,ensure_ascii=False,indent=2));p.chmod(0o600);print(json.dumps({k:v for k,v in r.items()if k!='sample'},ensure_ascii=False,indent=2))

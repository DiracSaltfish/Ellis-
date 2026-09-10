import concurrent.futures,json,urllib.request,datetime,hashlib,time,xml.etree.ElementTree as ET
from pathlib import Path
root=Path(__file__).parent/'backfill-20260909/raw';symbols=set()
for p in (root/'pcf').glob('*.xml'):
 symbols.add(p.stem)
 # Subscription union is taken from audited live plan, independent of XML field spellings.
import sqlite3
c=sqlite3.connect('file:/Users/ellis/工具程序开发/内网IOPV计算/data/iopv.sqlite?mode=ro',uri=True)
# Existing live snapshot component endpoint supplies normalized symbols.
points=json.load(open(root/'pcf/snapshots.json'))
for point in points:
 u='http://192.168.1.113:18680/api/v1/components?symbol='+point['symbol']
 components=json.load(urllib.request.urlopen(u,timeout=8))
 for item in components:
  comp=item['component']
  if comp.get('mode')!=2:symbols.add(comp['symbol'])
(root/'quote-plan.json').write_text(json.dumps(sorted(symbols)))
(root/'quotes').mkdir(exist_ok=True)
def fetch(symbol):
 code,market=symbol.split('.');key=market.lower()+code;path=root/'quotes'/(symbol+'.json');url='https://web.ifzq.gtimg.cn/appstock/app/minute/query?code='+key
 for attempt in range(3):
  try:
   raw=urllib.request.urlopen(url,timeout=15).read();obj=json.loads(raw);series=obj.get('data',{}).get(key,{}).get('data',{});path.write_bytes(raw)
   return {'symbol':symbol,'url':url,'fetched_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sha256':hashlib.sha256(raw).hexdigest(),'date':series.get('date'),'rows':len(series.get('data',[]))}
  except Exception as e:
   if attempt==2:return {'symbol':symbol,'error':str(e)}
   time.sleep(.5*(attempt+1))
results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=6)as ex:
 for r in ex.map(fetch,sorted(symbols)):results.append(r)
(root/'quote-manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print('Downloaded',len(results),'valid today',sum(r.get('date')=='20260909' for r in results),'errors',sum('error'in r for r in results),flush=True)

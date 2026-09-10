from pathlib import Path
import urllib.request,json,concurrent.futures
ROOT=Path(__file__).resolve().parents[1];RAW=ROOT/'raw'
codes=['hk'+r['C_STOCKCODE'] for r in json.loads((RAW/'pcf_2026-09-08_stocklist.json').read_text())['data']]+['sh513090']
def fetch(task):
 code,kind=task
 url=f'https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}' if kind=='minute' else f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,2026-08-27,2026-09-08,20,'
 data=urllib.request.urlopen(url,timeout=30).read();(RAW/f'tencent_{code}_{kind}.json').write_bytes(data)
 obj=json.loads(data)['data'].get(code,{})
 return code,kind,len(obj.get('day',[])) if kind=='day' else (obj.get('data',{}).get('date'),len(obj.get('data',{}).get('data',[])))
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
 for r in ex.map(fetch,[(c,k) for c in codes for k in ['day','minute']]):print(r)

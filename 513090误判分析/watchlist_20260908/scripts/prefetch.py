from pathlib import Path
import csv,json,shutil,urllib.request,concurrent.futures,hashlib,datetime,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
for d in ['inputs','raw/nav','raw/pcf','baseline_513090']:(ROOT/d).mkdir(parents=True,exist_ok=True)
shutil.copy2('/Volumes/Upan/premiumWatchlistSymbols.csv',ROOT/'inputs/premiumWatchlistSymbols.csv')
rows=list(csv.DictReader((ROOT/'inputs/premiumWatchlistSymbols.csv').open(encoding='utf-8-sig')))
symbols=[r['标的代码'].strip() for r in rows]
assert len(symbols)==len(set(symbols))==197
(ROOT/'inputs/symbols.json').write_text(json.dumps(symbols,ensure_ascii=False,indent=2))
# Preserve the already captured historical quotes: live minute endpoints roll dates.
for p in (ROOT.parent/'raw').iterdir():
    if p.is_file() and (p.name.startswith(('pcf_','tencent_','safe'))):shutil.copy2(p,ROOT/'baseline_513090'/p.name)
for name in ['minute_iopv_summary.json','minute_iopv_20260908.csv','minute_constituents_20260908.csv','daily_reconciliation.csv','constituents.csv','source_notes.md']:
    shutil.copy2(ROOT.parent/name,ROOT/'baseline_513090'/name)
tasks=[]
for symbol in symbols:
    code,market=symbol.split('.')
    tasks.append(('nav',symbol,f'https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=30&startDate=2026-08-28&endDate=2026-09-08',ROOT/f'raw/nav/{code}.json','https://fundf10.eastmoney.com/'))
    if market=='SZ':
        tasks.append(('pcf',symbol,f'https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_{code}_20260908.xml',ROOT/f'raw/pcf/{code}_20260908.xml','https://www.szse.cn/'))
def fetch(t):
    kind,symbol,url,path,ref=t
    out={'kind':kind,'symbol':symbol,'url':url,'path':str(path.relative_to(ROOT)),'fetched_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        if path.exists():body=path.read_bytes();out['cached']=True
        else:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':ref}),timeout=12) as r:body=r.read(8*1024*1024)
            temp=path.with_suffix(path.suffix+'.tmp');temp.write_bytes(body);temp.replace(path)
        out.update(bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
        if kind=='nav':
            data=json.loads(body);rr=(data.get('Data') or {}).get('LSJZList') or []
            out.update(status='FETCHED' if rr else 'EMPTY',dates=[r.get('FSRQ') for r in rr],target_nav=next((r.get('DWJZ') for r in rr if r.get('FSRQ')=='2026-09-08'),None))
        else:
            tree=ET.fromstring(body);fields={n.tag.split('}')[-1]:(n.text or '').strip() for n in tree.iter() if len(n)==0}
            out.update(status='RAW_XML_REQUIRES_SCHEMA_VALIDATION',trading_day=fields.get('TradingDay'),fund_code=fields.get('SecurityID'))
    except Exception as e:out.update(status='ERROR',error_type=type(e).__name__,error=str(e)[:180])
    return out
results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
    for i,out in enumerate(ex.map(fetch,tasks),1):
        results.append(out)
        if i%50==0:print('completed',i,'/',len(tasks),flush=True)
(ROOT/'prefetch_manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
from collections import Counter
summary={'symbols':len(symbols),'requests':len(tasks),'status_counts':dict(Counter(r['kind']+':'+r['status'] for r in results)),'nav_with_target_day':sum(r.get('target_nav') is not None for r in results),'baseline_files':len(list((ROOT/'baseline_513090').iterdir()))}
(ROOT/'prefetch_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False),flush=True)

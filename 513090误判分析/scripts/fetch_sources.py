from pathlib import Path
import urllib.request,urllib.parse,json,re,concurrent.futures
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1]; RAW=ROOT/'raw'
def get(url,path):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    data=urllib.request.urlopen(req,timeout=45).read();path.write_bytes(data);return data
html=(RAW/'fund.html').read_text()
links=re.findall(r'href="([^"]+\.pdf)"[^>]*>([^<]+)',html)
for url,title in links:
    if any(t in title for t in ['招募说明书','2026年中期报告','清算交收相关事项']):
        name='prospectus' if '招募' in title else 'halfyear' if '中期' in title else 'settlement_notice'
        get(url,RAW/(name+'.pdf'))
        reader=PdfReader(RAW/(name+'.pdf'))
        (RAW/(name+'.txt')).write_text('\n'.join(f'\n--- PDF PAGE {i+1} ---\n'+p.extract_text() for i,p in enumerate(reader.pages)))
        (RAW/(name+'_source.json')).write_text(json.dumps({'url':url,'title':title},ensure_ascii=False))
        print(name,len(reader.pages))
dates=['2026-08-28','2026-08-31','2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-07','2026-09-08','2026-09-09']
def pcf(task):
    date,endpoint=task
    url='https://api.efunds.com.cn/xcowch/front/etffund/'+endpoint+'?'+urllib.parse.urlencode({'fundCode':'513090','tDate':date,'listType':'1'})
    data=get(url,RAW/f'pcf_{date}_{endpoint}.json')
    return date,endpoint,json.loads(data).get('status')
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
    for row in ex.map(pcf,[(d,e) for d in dates for e in ['baseinfo','stocklist']]): print(row)

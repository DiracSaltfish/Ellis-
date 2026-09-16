import json,urllib.request,concurrent.futures,datetime,time,re
from pathlib import Path
O=Path(__file__).resolve().parent;D=O/'sina_minutes';D.mkdir(exist_ok=True)
symbols=json.loads((O/'needed.json').read_text());headers={'Referer':'https://stock.finance.sina.com.cn/','User-Agent':'Mozilla/5.0'}
# Check each symbol's real-time date. The minline endpoint serves today's data and has no per-row date.
hq={}
for i in range(0,len(symbols),60):
 raw=urllib.request.urlopen(urllib.request.Request('https://hq.sinajs.cn/?list='+','.join('rt_hk'+x[:5] for x in symbols[i:i+60]),headers=headers),timeout=20).read().decode('gbk','replace')
 for code,body in re.findall(r'var hq_str_rt_hk(\d{5})="([^"]*)";',raw):hq[code+'.HK']=body.split(',')
(O/'hq_validation.json').write_text(json.dumps(hq,ensure_ascii=False))
def fetch(s):
 p=D/(s+'.json')
 if p.exists():return s,'cached'
 f=hq.get(s,[])
 if len(f)<19 or f[17]!='2026/09/14':return s,'no_same_day_hq'
 url='https://stock.finance.sina.com.cn/hkstock/api/openapi.php/HK_StockService.getHKMinline?symbol='+s[:5]+'&random='+str(time.time())
 for attempt in range(2):
  try:
   raw=urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=18).read().decode();data=json.loads(raw)['result'];assert data['status']['code']==0
   rows=data['data'];assert isinstance(rows,list)
   obj={'symbol':s,'trade_date':'2026-09-14','fetched_at':datetime.datetime.now().astimezone().isoformat(),'url':url,'hq_date':f[17],'hq_time':f[18],'rows':rows};p.write_text(json.dumps(obj,ensure_ascii=False));return s,len(rows)
  except Exception as e:err=str(e)
 return s,err
out=dict(concurrent.futures.ThreadPoolExecutor(max_workers=6).map(fetch,symbols));(O/'fetch_results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print('Downloaded',sum(isinstance(x,int) for x in out.values()),'of',len(out));print({k:v for k,v in out.items() if not isinstance(v,int)})

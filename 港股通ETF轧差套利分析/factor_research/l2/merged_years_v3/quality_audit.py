"""Read-only price precision check; never change scores or infer missing prices."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,pandas as pd,numpy as np
R=Path(__file__).resolve().parent;plan=json.loads((R/'plan.json').read_text());p=pd.read_parquet(R/'premium.parquet');p=p[p.premium_gate&p.symbol.ne('513130.SH')]
tasks=[]
for e in plan['entries']:
 m=json.loads(Path(e['sources']['manifest']['path']).read_text());lookup={(f['symbol'],Path(f['path']).name):f for f in m['files']}
 for sym in sorted(set(p[p.date.eq(e['date'])].symbol)):
  if (sym,'逐笔成交.csv') in lookup:tasks.append((e,sym,lookup[(sym,'逐笔成交.csv')]['path']))
def check(task):
 e,sym,path=task
 try:
  t=pd.read_csv(path,encoding='gb18030',usecols=['时间','成交价格']);t=t[(t['时间']>=93000000)&(t['时间']<144500000)&(t['成交价格']>0)]
  if len(t)<100 or (t['成交价格']%100!=0).any():return None
  s=pd.read_parquet(e['sources']['series']['path'],filters=[('symbol','==',sym)]);s=s[(s.minute>='09:31')&(s.minute<'14:45')].dropna(subset=['etf']);fine=(np.rint(s.etf*1000).astype('int64')%10!=0)
  if fine.sum()<20 or fine.mean()<.2:return None
  return dict(date=e['date'],symbol=sym,reason='全部连续成交价格仅精确到0.01元，独立ETF分钟价有更细价格',trades=len(t),fine_minute_count=int(fine.sum()),minute_count=len(s))
 except Exception as exc:return dict(date=e['date'],symbol=sym,reason='price_audit_failed',error=str(exc))
with ThreadPoolExecutor(max_workers=4) as pool:flags=[r for r in pool.map(check,tasks) if r]
(R/'price_quality.json').write_text(json.dumps(flags,ensure_ascii=False,indent=2));print('PRICE FLAGS',len(flags),'of',len(tasks),'candidate funddays',flush=True);print(pd.Series([x['date'] for x in flags]).value_counts().to_dict())

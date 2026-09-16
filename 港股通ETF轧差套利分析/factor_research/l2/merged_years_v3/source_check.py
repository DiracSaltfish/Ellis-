from pathlib import Path
import json,pandas as pd,numpy as np
R=Path(__file__).resolve().parent;es={e['day']:e for e in json.loads((R/'plan.json').read_text())['entries']}
checks=[('20250919','159202.SZ'),('20250922','159202.SZ'),('20251215','513010.SH'),('20250529','159331.SZ'),('20251217','159202.SZ'),('20260611','159202.SZ')]
out=[]
for day,sym in checks:
 m=json.loads(Path(es[day]['sources']['manifest']['path']).read_text());files={Path(f['path']).name:f['path'] for f in m['files'] if f['symbol']==sym}
 if not Path(files['逐笔成交.csv']).exists():continue
 t=pd.read_csv(files['逐笔成交.csv'],encoding='gb18030');o=pd.read_csv(files['逐笔委托.csv'],encoding='gb18030')
 s=pd.read_parquet(es[day]['sources']['series']['path'],filters=[('symbol','==',sym)])
 real=t[t['成交价格']>0];minute=t['时间']//100000;t['minute']=minute.map(lambda v:f'{v//100:02d}:{v%100:02d}')
 # Minute price data uses completed-minute convention; compare close of same source minute.
 v=t[t['成交价格']>0].groupby('minute')['成交价格'].last().rename('raw_px')/10000
 s=s[['minute','etf']].drop_duplicates('minute');s['source_minute']=s.minute.map(lambda v:f'{(int(v[:2])*60+int(v[3:])-1)//60:02d}:{(int(v[:2])*60+int(v[3:])-1)%60:02d}')
 joined=s.merge(v,left_on='source_minute',right_index=True);joined=joined[(joined.minute>='09:32')&(joined.minute<'14:45')]
 r=dict(day=day,symbol=sym,trade_min=int(t['时间'].min()),order_min=int(o['时间'].min()),trade_max=int(t['时间'].max()),order_max=int(o['时间'].max()),price_mod100_nonzero=int((real['成交价格']%100!=0).sum()),trade_rows=len(real),median_abs_price_difference=float((joined.raw_px-joined.etf).abs().median()),median_signed_bp=float(((joined.raw_px/joined.etf-1)*10000).median()),samples=joined.tail(4).to_dict('records'),first_orders=o.head(2).to_dict('records'))
 out.append(r)
(R/'source_check.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False,indent=2))

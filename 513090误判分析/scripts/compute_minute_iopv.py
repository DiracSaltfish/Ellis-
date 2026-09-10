"""Reconstruct 2026-09-08 minute IOPV at fixed central parity; no exchange IOPV input."""
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
import json,csv
ROOT=Path(__file__).resolve().parents[1];RAW=ROOT/'raw'
def read(n):return json.loads((RAW/n).read_text())
q=read('pcf_2026-09-08_stocklist.json')['data'];b=read('pcf_2026-09-08_baseinfo.json')['data']['map']
assert len(q)==17 and len({x['C_STOCKCODE'] for x in q})==17
assert all(x['BUSI_DATE']=='20260908' and x['C_TDBZ']=='1' for x in q)
f=Decimal('0.86482');cash=Decimal(str(b['ESTIMATECASHCOMPONENT']));unit=Decimal(str(b['CREATIONREDEMPTIONUNIT']))
assert cash==Decimal('9547.19') and unit==500000
prices={}
for x in q:
 c=x['C_STOCKCODE'];m=read(f'tencent_hk{c}_minute.json')['data']['hk'+c]['data'];assert m['date']=='20260908'
 prices[c]={r.split()[0]:Decimal(r.split()[1]) for r in m['data']}
 assert len(prices[c])==len(m['data'])
times=sorted(set.union(*(set(x) for x in prices.values())))
assert all(set(x)==set(times) for x in prices.values())
em=read('tencent_sh513090_minute.json')['data']['sh513090']['data'];assert em['date']=='20260908'
etf={r.split()[0]:Decimal(r.split()[1]) for r in em['data']}
rows=[];detail=[]
for t in times:
 values=[]
 for x in q:
  c=x['C_STOCKCODE'];p=prices[c][t];h=Decimal(x['L_NUMBER'])*p;v=h*f;values.append(h)
  detail.append({'date':'2026-09-08','time':t[:2]+':'+t[2:],'code':c,'name':x['C_STOCKSHORT'],'quantity':x['L_NUMBER'],'price_HKD':str(p),'stock_HKD':str(h),'stock_CNY':str(v),'fx':str(f)})
 h=sum(values);i=(h*f+cash)/unit;market=etf.get(t) if ('0930'<=t<='1130' or '1300'<=t<='1500') else None
 rows.append({'date':'2026-09-08','time':t[:2]+':'+t[2:],'stock_count':17,'stock_HKD':str(h),'fx_CNY_per_HKD':str(f),'stock_CNY':str(h*f),'estimated_cash_CNY':str(cash),'basket_value_CNY':str(h*f+cash),'unit_shares':500000,'IOPV_exact':str(i),'IOPV_4dp':str(i.quantize(Decimal('.0001'),rounding=ROUND_HALF_UP)),'ETF_price':str(market) if market else '', 'ETF_premium':str(market/i-1) if market else ''})
def save(n,rs):
 with (ROOT/n).open('w',encoding='utf-8-sig',newline='') as out:
  w=csv.DictWriter(out,fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
save('minute_iopv_20260908.csv',rows);save('minute_constituents_20260908.csv',detail)
matched=[r for r in rows if r['ETF_premium']]
result={'date':'2026-09-08','minute_rows':len(rows),'constituent_rows':len(detail),'stocks':17,'timestamps_identical_across_stocks':True,'formula':'(sum(q_T * P_minute_HKD)*0.86482 + 9547.19)/500000','quote_timestamp_policy':'Use exact provider minute labels; do not forward-fill or fabricate 16:00. Closing auction final is 16:08.','first':rows[0],'last':rows[-1],'key_minutes':[r for r in rows if r['time'] in ['09:30','10:00','11:00','11:30','12:00','13:00','14:00','14:30','15:00','15:59','16:00','16:08']],'ETF_matched_minutes':len(matched),'positive_premium_minutes':sum(Decimal(r['ETF_premium'])>0 for r in matched),'min_premium':min(Decimal(r['ETF_premium']) for r in matched),'max_premium':max(Decimal(r['ETF_premium']) for r in matched)}
(ROOT/'minute_iopv_summary.json').write_text(json.dumps(result,default=str,ensure_ascii=False,indent=2))
print(json.dumps(result,default=str,ensure_ascii=False,indent=2))

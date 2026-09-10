from pathlib import Path
import json,csv
ROOT=Path(__file__).resolve().parents[1]; RAW=ROOT/'raw'
def read(n):return json.loads((RAW/n).read_text())
def stocks(d):return read(f'pcf_{d}_stocklist.json')['data']
def base(d):return read(f'pcf_{d}_baseinfo.json')['data']['map']
def daily(c):return {r[0]:float(r[2]) for r in read(f'tencent_hk{c}_day.json')['data']['hk'+c]['day']}
def minutes(c):
 obj=read(f'tencent_{c}_minute.json')['data'][c]['data'];assert obj['date']=='20260908'
 return {r.split()[0]:float(r.split()[1]) for r in obj['data']}
def save(name,rows):
 with (ROOT/name).open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
q=stocks('2026-09-08');qnext={r['C_STOCKCODE']:r for r in stocks('2026-09-09')};b=base('2026-09-08');bn=base('2026-09-09');fx=.86482
rows=[]
for r in q:
 c=r['C_STOCKCODE'];m=minutes('hk'+c);p=daily(c)['2026-09-08'];n=qnext[c]
 rows.append(dict(code=c,name=r['C_STOCKSHORT'],q_T=r['L_NUMBER'],q_next=n['L_NUMBER'],price_1500=m['1500'],close_HKD=p,value_T_CNY=r['L_NUMBER']*p*fx,next_pcf_CNY=n['F_TDJE'],next_pcf_error_CNY=n['F_TDJE']-n['L_NUMBER']*p*fx))
save('constituents.csv',rows)
K=sum(r['q_T']*r['close_HKD'] for r in rows);K15=sum(r['q_T']*r['price_1500'] for r in rows)
summary={'K_close_HKD':K,'K_1500_HKD':K15,'estimated_cash':b['ESTIMATECASHCOMPONENT'],'final_cash':bn['CASHCOMPONENT'],'NAV_precise':bn['NAVPERCU']/500000,'calc_close_mid_est':(K*fx+b['ESTIMATECASHCOMPONENT'])/500000,'calc_close_mid_final':(K*fx+bn['CASHCOMPONENT'])/500000,'calc_1500_mid_est':(K15*fx+b['ESTIMATECASHCOMPONENT'])/500000,'fx_implied_1500':(1.8252*500000-b['ESTIMATECASHCOMPONENT'])/K15,'calc_1500_fx08555':(K15*.8555+b['ESTIMATECASHCOMPONENT'])/500000,'calc_close_fx08555':(K*.8555+b['ESTIMATECASHCOMPONENT'])/500000,'max_pcf_rounding_error':max(abs(r['next_pcf_error_CNY']) for r in rows)}
(ROOT/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
ms={r['C_STOCKCODE']:minutes('hk'+r['C_STOCKCODE']) for r in q};em=minutes('sh513090');intraday=[]
for tm in sorted(set.intersection(*(set(m) for m in ms.values()))):
 k=sum(r['L_NUMBER']*ms[r['C_STOCKCODE']][tm] for r in q)
 intraday.append(dict(time=tm,stock_HKD=k,estimate_mid=(k*fx+b['ESTIMATECASHCOMPONENT'])/500000,estimate_08555=(k*.8555+b['ESTIMATECASHCOMPONENT'])/500000,ETF=em.get(tm)))
save('intraday.csv',intraday)
for d in ['2026-08-28','2026-08-31','2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-07','2026-09-08','2026-09-09']:
 bb=base(d);print(d,bb['NAV'],bb['NAVPERCU'],bb['CASHCOMPONENT'],bb['ESTIMATECASHCOMPONENT'])

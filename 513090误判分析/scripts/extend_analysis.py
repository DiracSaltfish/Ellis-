from analyze import *
import urllib.request,urllib.parse,concurrent.futures,re
fxs={'2026-08-27':.86535,'2026-08-28':.86498,'2026-08-31':.86510,'2026-09-01':.86498,'2026-09-02':.86497,'2026-09-03':.86476,'2026-09-04':.86458,'2026-09-07':.86465,'2026-09-08':.86482}
dates=list(fxs)[1:]+['2026-09-09']
def shares(d):
 u='https://query.sse.com.cn/commonQuery.do?'+urllib.parse.urlencode({'sqlId':'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L','STAT_DATE':d,'pageHelp.pageSize':'2000','isPagination':'false'})
 data=(RAW/f'shares_{d}.json').read_bytes() if (RAW/f'shares_{d}.json').exists() else urllib.request.urlopen(urllib.request.Request(u,headers={'Referer':'https://www.sse.com.cn/','User-Agent':'Mozilla/5.0'}),timeout=25).read();(RAW/f'shares_{d}.json').write_bytes(data)
 match=[r for r in json.loads(data)['result'] if r['SEC_CODE']=='513090'];assert len(match)==1 and match[0]['STAT_DATE']==d
 return d,float(match[0]['TOT_VOL'])
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:smap=dict(ex.map(shares,dates[:-1]))
navs=[];etfd={r[0]:float(r[2]) for r in read('tencent_sh513090_day.json')['data']['sh513090']['day']}
for i,d in enumerate(dates[:-1]):
 qq=stocks(d);nb=base(dates[i+1]);bb=base(d);k=sum(r['L_NUMBER']*daily(r['C_STOCKCODE'])[d] for r in qq);calc=(k*fxs[d]+nb['CASHCOMPONENT'])/500000
 navs.append(dict(date=d,stock_count=len(qq),stock_HKD=k,fx=fxs[d],est_cash_CNY=bb['ESTIMATECASHCOMPONENT'],final_cash_CNY=nb['CASHCOMPONENT'],reconstructed_NAV=calc,official_NAV=nb['NAVPERCU']/500000,error_CNY_per_basket=calc*500000-nb['NAVPERCU'],ETF_close=etfd[d],expost_premium=etfd[d]/(nb['NAVPERCU']/500000)-1,shares_wan=smap[d],net_baskets=(smap[d]-smap[dates[i-1]])/50 if i else None))
save('daily_reconciliation.csv',navs);print(json.dumps(navs,indent=2))
# Half-year disclosed holdings: compare composition, no claim these are today's quantities.
text=(RAW/'halfyear.txt').read_text();section=text[text.index('1 06030 中信证券'):text.index('7.4  报告期内股票投资组合的重大变动',text.index('1 06030 中信证券'))]
hold=[]
for line in section.splitlines():
 line=line.strip()
 mm=re.match(r'(\d+) (\d{5}) (.+?) ([\d,]+) ([\d,.]+) ([\d.]+)$',line)
 if mm:
  rank,c,name,qty,value,weight=mm.groups();hold.append(dict(code=c,name=name,halfyear_qty=int(qty.replace(',','')),halfyear_weight=float(weight),pcf_weight_NAV=next(r['value_T_CNY'] for r in rows if r['code']==c)/bn['NAVPERCU']*100))
assert len(hold)==17;save('holdings_comparison.csv',hold)
scenario=[]
for C in [1,10,50,100,200,500]:
 R=C+17;a=17/R;buy=K*.855559;sell=K*.855441;V=K*fx
 scenario.append(dict(creations=C,redemptions=R,net_redemptions=17,actual_sell_fraction=a,assumed_buy_fx=.855559,assumed_sell_fx=.855441,roundtrip_CNY=a*(sell-V),full_stock_fx_gap_CNY=sell-V))
save('settlement_scenarios.csv',scenario)

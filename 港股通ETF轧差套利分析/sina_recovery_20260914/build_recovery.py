import json,math,datetime,urllib.request,concurrent.futures,bisect,collections
from pathlib import Path
O=Path(__file__).resolve().parent;date='2026-09-14'
# Freeze an input cut at the earliest downloaded minline response; never fill an unfinished minute.
files=[json.loads(p.read_text()) for p in (O/'sina_minutes').glob('*.json')]
cut=min(x['fetched_at'][11:16] for x in files)
prices={};times={}
for f in files:
 vals={}
 assert f['trade_date']==date and f['hq_date']=='2026/09/14'
 for r in f['rows']:
  m=r['m'][:5]
  try:p=float(r['p'])
  except (KeyError,ValueError):continue
  if float(r.get('v',0))>0 and math.isfinite(p) and p>0 and '09:30'<=m<cut and (m<='12:00' or m>='13:00'):vals[m]=p
 prices[f['symbol']]=vals;times[f['symbol']]=sorted(vals)
def get(path):return json.load(urllib.request.urlopen('http://192.168.1.113:18680/api/v1/'+path,timeout=30))
sn=get('snapshots')['snapshots']
def read(s):return s['symbol'],get('minutes?symbol='+s['symbol']+'&date='+date)
hist=dict(concurrent.futures.ThreadPoolExecutor(max_workers=6).map(read,sn));(O/'before_minutes.json').write_text(json.dumps(hist,ensure_ascii=False))
bs=json.loads((O/'baskets.json').read_text());updates=[];remaining=collections.Counter();comparison=[]
def exposure(b,minute):
 hk=[];cn=[b['estimated_cash_cny']];carry=[];stale=[];used=[]
 for c in b['components']:
  if c['mode']==2:cn.append(c['cash_cny']);continue
  sym=c['symbol'];ts=times.get(sym,[]);i=bisect.bisect_right(ts,minute)-1
  if i<0:return None,sym
  tm=ts[i];value=prices[sym][tm];used.append(tm)
  if tm!=minute:carry.append(sym)
  delta=(int(minute[:2])*60+int(minute[3:]))-(int(tm[:2])*60+int(tm[3:]))
  if delta>2:stale.append(sym)
  (hk if c['mode']==0 else cn).append(c['quantity']*value)
 return (math.fsum(hk),math.fsum(cn),carry,stale,min(used) if used else minute),None
for sym,rows in hist.items():
 for r in rows:
  if r['minute']>=cut:continue
  b=bs.get(r['pcf_sha256']);missing=r.get('midpoint_iopv') is None
  if not b or b['trade_date']!=date or not r.get('midpoint_fx'):
   if missing:remaining['pcf_or_fx_unavailable']+=1
   continue
  assert b['pcf_sha256']==r['pcf_sha256'] and b['creation_unit']==r['unit']
  ex,err=exposure(b,r['minute'])
  if err:
   if missing:remaining[err]+=1
   continue
  hk,cn,carry,stale,oldest=ex;nav=(hk*r['midpoint_fx']+cn)/r['unit']
  if not missing:
   comparison.append({'symbol':sym,'minute':r['minute'],'difference_bp':(nav/r['midpoint_iopv']-1)*10000});continue
  if r.get('suspended'):
   remaining['confirmed_suspension']+=1;continue
  n=json.loads(json.dumps(r));n['hkd_assets']=hk;n['cny_assets']=cn;n['midpoint_iopv']=nav
  for side in ['buy','sell']:
   rate=r.get('settlement_'+side+'_fx');n['settlement_'+side+'_iopv']=(hk*rate+cn)/r['unit'] if rate else None
  n['priced']=r['components'];n['missing']=[];n['stale']=stale;n['suspension_pending']=[]
  n['reasons']=[x for x in r['reasons'] if x not in ['QUOTE_MISSING','STALE_COMPONENT_MARKS']]+['HISTORICAL_SINA_BACKFILL']
  if stale:n['reasons'].append('STALE_COMPONENT_MARKS')
  n['eligible_for_signal']=False;n['mode']='historical_sina_backfill';n['recovered_at']=datetime.datetime.now().astimezone().isoformat();n['recovery_source']='Sina HK_StockService.getHKMinline: minute close; same-day last trade carried across no-trade minutes';n['recovery_carry_components']=carry;n['oldest_quote_at']=date+'T'+oldest+':00+08:00'
  n['component_issues']=[{'symbol':c,'name':next(x.get('name','') for x in b['components'] if x['symbol']==c),'quote_status':'stale','suspension_status':'','source':'sina_rt_hk','observed_at':date+'T'+times[c][bisect.bisect_right(times[c],r['minute'])-1]+':00+08:00','note':'历史分钟回补：沿用同日最后成交价'} for c in stale]
  for key,navkey in [('midpoint_premium_pct','midpoint_iopv'),('settlement_buy_premium_pct','settlement_buy_iopv'),('settlement_sell_premium_pct','settlement_sell_iopv')]:n[key]=(r['etf_price']/n[navkey]-1)*100 if r.get('etf_price') and n.get(navkey) else None
  updates.append({'symbol':sym,'minute':r['minute'],'before':r,'after':n})
(O/'updates.json').write_text(json.dumps(updates,ensure_ascii=False));(O/'valid_comparison.json').write_text(json.dumps(comparison,ensure_ascii=False));xs=sorted(abs(x['difference_bp']) for x in comparison);target=[x for x in updates if x['symbol']=='513890.SH'];summary={'cutoff_exclusive':cut,'updated_minutes':len(updates),'funds':len(set(x['symbol'] for x in updates)),'target_minutes':len(target),'target_stale':sum(bool(x['after']['stale']) for x in target),'remaining_by_reason':dict(remaining),'valid_comparison_n':len(xs),'median_abs_difference_bp':xs[len(xs)//2],'p95_abs_difference_bp':xs[int(len(xs)*.95)]};(O/'recovery_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=2))

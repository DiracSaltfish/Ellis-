import sqlite3,json,urllib.request,math,pathlib,time
r=pathlib.Path(__file__).resolve().parents[1];c=sqlite3.connect(r/'runtime-storage-audit/source.sqlite');n=0;start=time.monotonic();mx=0;groups=0
for sym,day in c.execute('select distinct symbol,trade_date from minutes'):
 groups+=1;old={json.loads(x[0])['minute']:json.loads(x[0]) for x in c.execute('select payload from minutes where symbol=? and trade_date=?',(sym,day))};new=json.load(urllib.request.urlopen('http://192.168.1.113/api/v1/minutes?symbol='+sym+'&date='+day,timeout=10));assert len(old)==len(new)
 for p in new:
  q=old[p['minute']];n+=1
  for k,scale in [('etf_price',1000),('midpoint_iopv',10000),('settlement_buy_iopv',10000),('settlement_sell_iopv',10000)]:
   a,b=q.get(k),p.get(k);assert (a is None)==(b is None),(sym,k)
   if a is not None:assert abs(a-b)<=.50001/scale;mx=max(mx,abs(a-b))
  for k in ['midpoint_premium_pct','settlement_buy_premium_pct','settlement_sell_premium_pct']:assert (q.get(k)is None)==(p.get(k)is None),(sym,k)
  for k in ['reasons','missing','stale','suspended','suspension_pending','fx_status','fx_actionable','mode']:
   default=False if k=='fx_actionable' else '' if k in ['fx_status','mode'] else None
   assert q.get(k,default)==p.get(k),(sym,k)
  assert p['book'] is None and p['book_premiums'] is None
out={'checked_minutes':n,'checked_groups':groups,'max_price_quantization_error':mx,'total_read_seconds':time.monotonic()-start,'health':json.load(urllib.request.urlopen('http://192.168.1.113/api/v1/health'))};(r/'docs/v1.1.0/compact-acceptance.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False))

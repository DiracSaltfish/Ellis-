import sqlite3,json,zlib,pathlib,collections,decimal
root=pathlib.Path(__file__).parent
src=sqlite3.connect(root/'source.sqlite')
rows=[(a,b,c,d,json.loads(d)) for a,b,c,d in src.execute('select symbol,trade_date,minute,payload from minutes')]
def dump(v):return json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()
def fixed(v,n):return None if v is None else int(decimal.Decimal(str(v)).scaleb(n).quantize(decimal.Decimal('1'),rounding=decimal.ROUND_HALF_UP))
def rounded(p):
 q=dict(p)
 for k,n in [('etf_price',3),('midpoint_iopv',4),('settlement_buy_iopv',4),('settlement_sell_iopv',4)]:
  if q.get(k) is not None:q[k]=fixed(q[k],n)/(10**n)
 return q
results={}
for variant in ['original','compact_json','round_json','lossless_zlib','chart_fixed']:
 path=root/(variant+'.sqlite')
 if path.exists():path.unlink()
 c=sqlite3.connect(path);c.execute('pragma journal_mode=OFF')
 if variant=='chart_fixed':
  c.execute('create table minutes(symbol text,day integer,minute integer,etf integer,mid integer,buy integer,sell integer,quality blob,primary key(symbol,day,minute)) without rowid')
 else:c.execute('create table minutes(symbol text,trade_date text,minute text,payload blob,primary key(symbol,trade_date,minute))')
 stat=collections.defaultdict(list);err=0;premerr=0
 for sym,day,m,raw,p in rows:
  if variant=='original':payload=raw.encode();v=(sym,day,m,payload)
  elif variant=='compact_json':payload=dump(p);v=(sym,day,m,payload)
  elif variant=='round_json':payload=dump(rounded(p));v=(sym,day,m,payload)
  elif variant=='lossless_zlib':
   payload=zlib.compress(raw.encode(),6);assert zlib.decompress(payload)==raw.encode();v=(sym,day,m,payload)
  else:
   quality={k:v for k,v in p.items() if k in ['mode','reasons','missing','stale','suspended','suspension_pending','eligible_for_signal','fx_status','fx_actionable','pcf_sha256','calculated_at','etf_quote_at','oldest_quote_at','fx_at','fx_generated_at','fx_model','run_id','components','priced']}
   payload=zlib.compress(dump(quality),6)
   nums=[fixed(p.get(k),n) for k,n in [('etf_price',3),('midpoint_iopv',4),('settlement_buy_iopv',4),('settlement_sell_iopv',4)]]
   v=(sym,int(day.replace('-','')),int(m[:2])*60+int(m[3:]),*nums,payload)
   for key,q in zip(['midpoint_iopv','settlement_buy_iopv','settlement_sell_iopv'],nums[1:]):
    orig=p.get(key)
    if orig and q:
     err=max(err,abs(orig-q/10000))
     if p.get('etf_price') is not None:premerr=max(premerr,abs((p['etf_price']/orig-1)*100-(nums[0]/1000/(q/10000)-1)*100))
  c.execute('insert into minutes values('+','.join('?'*len(v))+')',v);stat[p.get('mode')].append(len(payload))
 c.commit();c.execute('vacuum');assert c.execute('select count(*) from minutes').fetchone()[0]==len(rows)
 results[variant]={'bytes':path.stat().st_size,'rows':len(rows),'payload_by_mode':{k:{'rows':len(x),'average_bytes':sum(x)/len(x)} for k,x in stat.items()},'max_iopv_rounding_error':err,'max_premium_error_percentage_points':premerr};c.close()
# Auxiliary raw-source payload compression; not a production schema.
aux={}
for table,col in [('pcf','raw'),('fx','payload')]:
 arr=[x.encode() if isinstance(x,str) else x for x, in src.execute('select '+col+' from '+table)]
 aux[table]={'rows':len(arr),'raw_bytes':sum(map(len,arr)),'zlib_bytes':sum(len(zlib.compress(x,6)) for x in arr)}
results['auxiliary']=aux
(root/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,indent=2))

import json,sqlite3,zlib,datetime,hashlib
from pathlib import Path
O=Path(__file__).resolve().parent;db=Path('/Users/ellis/Library/Application Support/MachomeHub/data/premium/local-iopv/data/iopv.sqlite');updates=json.loads((O/'updates.json').read_text());date='2026-09-14'
con=sqlite3.connect(str(db),timeout=30);backup=O/('before-recovery-'+datetime.datetime.now().strftime('%H%M%S')+'.sqlite');bc=sqlite3.connect(str(backup));con.backup(bc);bc.close()
def decode(raw):return json.loads(zlib.decompress(raw[5:]) if raw.startswith(b'IOPZ1') else raw)
def pack(p):
 q=p.copy();prices=[None if p.get(k) is None else int(p[k]*scale+0.5) for k,scale in [('etf_price',1000),('midpoint_iopv',10000),('settlement_buy_iopv',10000),('settlement_sell_iopv',10000)]]
 for k in ['etf_price','midpoint_iopv','settlement_buy_iopv','settlement_sell_iopv','midpoint_premium_pct','settlement_buy_premium_pct','settlement_sell_premium_pct','book','book_premiums']:q[k]=None
 return {'q':q,'p':prices,'v':[p.get(k) is not None for k in ['midpoint_premium_pct','settlement_buy_premium_pct','settlement_sell_premium_pct']]}
groups={}
for u in updates:groups.setdefault(u['symbol'],{})[u['minute']]=u
changed=[];skipped=[]
try:
 con.execute('BEGIN IMMEDIATE')
 for sym,byminute in groups.items():
  raw,n=con.execute('SELECT payload,point_count FROM minute_blocks WHERE symbol=? AND trade_date=?',(sym,date)).fetchone();rows=decode(raw);assert n==len(rows);new=[]
  for old in rows:
   u=byminute.get(old['q']['minute'])
   if not u:new.append(old);continue
   if old['p'][1] is not None or old['q']['pcf_sha256']!=u['before']['pcf_sha256'] or old['q']['calculated_at']!=u['before']['calculated_at']:skipped.append([sym,old['q']['minute']]);new.append(old);continue
   r=pack(u['after']);assert r['p'][0]==old['p'][0] and r['q']['pcf_sha256']==old['q']['pcf_sha256'];new.append(r);changed.append([sym,old['q']['minute']])
  assert len(new)==len(rows)
  # Every record outside the requested missing minutes remains identical.
  for old,r in zip(rows,new):
   if [sym,old['q']['minute']] not in changed:assert old==r
  con.execute('UPDATE minute_blocks SET payload=? WHERE symbol=? AND trade_date=?',(b'IOPZ1'+zlib.compress(json.dumps(new,ensure_ascii=False,separators=(',',':')).encode(),9),sym,date))
 con.commit()
except BaseException:con.rollback();raise
result={'backup':str(backup),'updated':len(changed),'funds':len(set(x[0] for x in changed)),'skipped_concurrent_change':skipped,'changed':changed};(O/'applied.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='changed'},ensure_ascii=False));con.close()

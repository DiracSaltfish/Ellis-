"""Import only absent historical minutes, after an online SQLite backup."""
import json,sqlite3,pathlib,datetime,hashlib,sys,math
src=pathlib.Path(sys.argv[1]);expected=sys.argv[2];assert hashlib.sha256(src.read_bytes()).hexdigest()==expected
root=pathlib.Path.home()/'Library/Application Support/MachomeHub/data/premium/local-iopv/data';db=root/'iopv.sqlite';c=sqlite3.connect(db,timeout=15);c.execute('pragma busy_timeout=15000');backup=root/('before-history-backfill-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite');b=sqlite3.connect(backup);c.backup(b);b.close();backup.chmod(0o600)
rows=[json.loads(l)for l in src.open()];keys=set();now=datetime.datetime.now(datetime.timezone.utc)
for p in rows:
 key=(p['symbol'],p['trade_date'],p['minute']);assert key not in keys;keys.add(key)
 assert p['mode']=='historical_reconstruction' and p['eligible_for_signal'] is False
 assert p['trade_date']=='2026-09-09' and '09:30'<=p['minute']<='12:00'
 assert datetime.datetime.fromisoformat(p['trade_date']+'T'+p['minute']+':00+08:00')<now
 assert c.execute('select 1 from pcf where symbol=? and trade_date=? and hash=?',(p['symbol'],p['trade_date'],p['pcf_sha256'])).fetchone()
 for field in ['midpoint_iopv','settlement_buy_iopv','settlement_sell_iopv','etf_price']:
  assert p[field] is None or (math.isfinite(p[field])and p[field]>0)
 assert not any(p.get(k)for k in ['book','book_premiums'])
 if p['minute']>'11:30':assert p['etf_price'] is None
 json.dumps(p,allow_nan=False)
pre=c.execute('select count(*) from minutes').fetchone()[0];changed=c.total_changes
with c:
 c.executemany('insert or ignore into minutes(symbol,trade_date,minute,payload) values(?,?,?,?)',[(p['symbol'],p['trade_date'],p['minute'],json.dumps(p,ensure_ascii=False,allow_nan=False))for p in rows]);inserted=c.total_changes-changed
 c.execute('insert into events(at,kind,message)values(?,?,?)',(datetime.datetime.now(datetime.timezone.utc).isoformat(),'historical_backfill',json.dumps(dict(artifact_sha256=expected,inserted=inserted,existing_kept=len(rows)-inserted,backup=str(backup)))))
assert c.execute('pragma quick_check').fetchone()[0]=='ok';post=c.execute('select count(*) from minutes').fetchone()[0];assert post-pre==inserted
r=dict(before=pre,after=post,inserted=inserted,existing_kept=len(rows)-inserted,backup=str(backup),artifact_sha256=expected,integrity='ok');out=root/'history-backfill-20260909-receipt.json';out.write_text(json.dumps(r,indent=2));out.chmod(0o600);print(json.dumps(r,ensure_ascii=False,indent=2))

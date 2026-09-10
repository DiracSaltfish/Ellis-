import sqlite3,pathlib,json,urllib.request,zipfile
root=pathlib.Path.home()/'Library/Application Support/MachomeHub/data/premium/local-iopv/data';c=sqlite3.connect(f'file:{root}/iopv.sqlite?mode=ro',uri=True)
points=json.load(urllib.request.urlopen('http://127.0.0.1:18680/api/v1/snapshots'))['snapshots']
with zipfile.ZipFile('/tmp/machome-pcf-20260909.zip','w',zipfile.ZIP_DEFLATED)as z:
 z.writestr('snapshots.json',json.dumps(points))
 for p in points:
  row=c.execute('select raw from pcf where symbol=? and trade_date=? and hash=?',(p['symbol'],'2026-09-09',p['pcf_sha256'])).fetchone();assert row,p['symbol'];z.writestr(p['symbol']+'.xml',row[0])
print('PCF archived',len(points))

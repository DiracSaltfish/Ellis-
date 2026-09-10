import pathlib,sqlite3,subprocess,shutil,os,signal,time,json,urllib.request,gzip
h=pathlib.Path.home();root=h/'Library/Application Support/MachomeHub';app=h/'Applications/Machome 四合一运行中心.app';db=root/'data/premium/local-iopv/data/iopv.sqlite';helper=app/'Contents/Helpers/machome-iopv-server';manifest=app/'Contents/Resources/iopv-webfix-manifest.json'
bak=root/'backups'/('compact-'+time.strftime('%Y%m%d-%H%M%S'));bak.mkdir()
def health():return json.load(urllib.request.urlopen('http://127.0.0.1:18680/api/v1/health',timeout=2))
old=health();assert old['quote_window_open']==False,'only offline cutover supported'
def stop():
 for line in subprocess.check_output(['ps','-axo','pid=,comm='],text=True).splitlines():
  parts=line.strip().split(None,1)
  if len(parts)==2 and parts[1] in [str(helper),str(app/'Contents/MacOS/../Helpers/machome-iopv-server')]:
   pid=int(parts[0]);os.kill(pid,signal.SIGTERM)
   for _ in range(50):
    try:os.kill(pid,0)
    except ProcessLookupError:break
    time.sleep(.1)
   else:raise RuntimeError('helper did not stop')
def replace_db(src):
 shutil.copy2(src,str(db)+'.next')
 for suffix in ['-wal','-shm']:
  p=pathlib.Path(str(db)+suffix)
  if p.exists():p.unlink()
 os.replace(str(db)+'.next',db)
# Online consistent rollback copy. Trading window is closed and archive rows are stable.
s=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);b=sqlite3.connect(bak/'iopv.sqlite');s.backup(b);counts=s.execute('select count(*) from minutes').fetchone()[0];s.close();b.close()
c=sqlite3.connect('/tmp/iopv-compact.sqlite');assert c.execute('select sum(point_count) from minute_blocks').fetchone()[0]==counts;assert c.execute('pragma integrity_check').fetchone()[0]=='ok';c.close()
shutil.copy2(helper,bak/helper.name);shutil.copy2(manifest,bak/manifest.name)
try:
 stop();replace_db('/tmp/iopv-compact.sqlite');shutil.copy2('/tmp/machome-iopv-server-compact',helper);shutil.copy2('/tmp/compact-manifest.json',manifest)
 subprocess.run(['codesign','--force','--sign','-',str(app)],check=True)
 for _ in range(30):
  try:
   new=health()
   if new['run_id']!=old['run_id'] and new['pcf_ready']==old['pcf_ready']:break
  except Exception:pass
  time.sleep(1)
 else:raise RuntimeError('health not ready')
 rows=json.load(urllib.request.urlopen('http://127.0.0.1:18680/api/v1/minutes?symbol=513090.SH&date=2026-09-09',timeout=5));assert len(rows)==340
 assert all(x['book'] is None for x in rows)
except BaseException:
 stop();replace_db(bak/'iopv.sqlite');shutil.copy2(bak/helper.name,helper);shutil.copy2(bak/manifest.name,manifest);subprocess.run(['codesign','--force','--sign','-',str(app)]);raise
with open(bak/'iopv.sqlite','rb') as src,gzip.open(bak/'iopv.sqlite.gz','wb',compresslevel=9) as dst:shutil.copyfileobj(src,dst)
(bak/'iopv.sqlite').unlink()
print(json.dumps({'backup':str(bak),'db_bytes':db.stat().st_size,'history_rows':len(rows),'health':new},ensure_ascii=False))

import pathlib,shutil,subprocess,os,signal,time,json,urllib.request,hashlib
h=pathlib.Path.home();a=h/'Applications/Machome 四合一运行中心.app';p=a/'Contents/Helpers/machome-iopv-server';f=a/'Contents/Resources/iopv-webfix-manifest.json';b=h/'Library/Application Support/MachomeHub/backups'/('sina-'+time.strftime('%Y%m%d-%H%M%S'));b.mkdir();shutil.copy2(p,b/p.name);shutil.copy2(f,b/f.name)
def get(path):return json.load(urllib.request.urlopen('http://127.0.0.1:18680'+path,timeout=3))
def stop():
 for line in subprocess.check_output(['ps','-axo','pid=,comm='],text=True).splitlines():
  x=line.strip().split(None,1)
  if len(x)==2 and x[1] in [str(p),str(a/'Contents/MacOS/../Helpers/machome-iopv-server')]:os.kill(int(x[0]),signal.SIGTERM)
old=get('/api/v1/health');m=json.load(open('/tmp/iopv-sina-manifest.json'));assert hashlib.sha256(pathlib.Path('/tmp/iopv-sina-helper').read_bytes()).hexdigest()==m['helper_sha256']
try:
 shutil.copy2('/tmp/iopv-sina-helper',p);shutil.copy2('/tmp/iopv-sina-manifest.json',f);subprocess.run(['codesign','--force','--sign','-',str(a)],check=True);stop()
 for _ in range(35):
  try:
   new=get('/api/v1/health');data=get('/api/v1/snapshots')
   if new['run_id']!=old['run_id'] and new.get('sina_quote_count')==6 and any(p['symbol']=='520920.SH' and p['priced']==30 and p['midpoint_iopv'] is not None for p in data['snapshots']):break
  except Exception:pass
  time.sleep(1)
 else:raise RuntimeError('ledger deployment failed')
 assert len(get('/api/v1/minutes?symbol=513090.SH&date=2026-09-09'))==340
except BaseException:
 shutil.copy2(b/p.name,p);shutil.copy2(b/f.name,f);subprocess.run(['codesign','--force','--sign','-',str(a)]);stop();raise
print(json.dumps({'backup':str(b),'sina_quote_count':new['sina_quote_count'],'health_errors':new['errors']},ensure_ascii=False))

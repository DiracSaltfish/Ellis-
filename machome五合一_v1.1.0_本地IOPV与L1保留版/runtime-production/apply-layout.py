import pathlib,json,hashlib,subprocess,os,signal,time,socket,urllib.request,shutil
home=pathlib.Path.home();app=home/'Applications/Machome 四合一运行中心.app';helper=app/'Contents/Helpers/machome-iopv-server';manifest=json.load(open('/tmp/iopv-webfix-manifest.json'));src=pathlib.Path('/tmp/machome-iopv-server-webfix');assert hashlib.sha256(src.read_bytes()).hexdigest()==manifest['helper_sha256'];subprocess.run([str(src),'-h'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
bak=home/'Library/Application Support/MachomeHub/backups'/('iopv-webfix-'+time.strftime('%Y%m%d-%H%M%S'));bak.mkdir(mode=0o700);shutil.copy2(helper,bak/helper.name)
get=lambda:json.load(urllib.request.urlopen('http://127.0.0.1:18680/api/v1/health',timeout=5));before=get();pids=[]
for line in subprocess.check_output(['ps','-axo','pid=,command='],text=True).splitlines():
 a=line.strip().split(None,1)
 if len(a)==2 and a[1].startswith(str(app/'Contents/MacOS/../Helpers/machome-iopv-server')+' '):pids.append(int(a[0]))
assert len(pids)==1,pids
shutil.copy2(src,helper.with_suffix('.next'));os.replace(helper.with_suffix('.next'),helper);shutil.copy2('/tmp/iopv-webfix-manifest.json',app/'Contents/Resources/iopv-webfix-manifest.json');subprocess.run(['codesign','--force','--sign','-',str(app)],check=True);subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
os.kill(pids[0],signal.SIGTERM)
with socket.create_connection(('127.0.0.1',19195),3)as s:
 f=s.makefile('rb');assert json.loads(f.readline())['service']=='qmt_l1';s.sendall(b'{"v":1,"t":"ping"}\n');assert json.loads(f.readline())['t']=='pong'
for _ in range(40):
 time.sleep(1)
 try:
  h=get()
  if h['run_id']!=before['run_id']:break
 except Exception:pass
else:raise RuntimeError('IOPV did not restart')
history=json.load(urllib.request.urlopen('http://127.0.0.1:18680/api/v1/minutes?symbol=513090.SH&date=2026-09-09'));assert len(history)>=151
print(json.dumps(dict(patch=manifest['patch'],backup=str(bak),new_run=h['run_id'],history_rows=len(history),l1_during_restart='pong'),ensure_ascii=False))

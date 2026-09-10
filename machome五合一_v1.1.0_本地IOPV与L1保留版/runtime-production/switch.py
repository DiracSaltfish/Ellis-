import os,json,time,socket,struct,subprocess,hashlib,shutil,signal
from pathlib import Path
home=Path.home(); root=home/'Library/Application Support/MachomeHub'; app=home/'Applications/Machome 四合一运行中心.app'; stage=home/'Applications/MachomeCandidates/1.1.0-production-20260909'; config=root/'config/modules.json'; plist=home/'Library/LaunchAgents/com.ellis.machome-hub-agent.plist'; domain=f'gui/{os.getuid()}'
backup=root/'backups'/('production-v1.1.0-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,mode=0o700)
def run(*args):subprocess.run([str(x) for x in args],check=True)
def capture():
 s=socket.socket(socket.AF_UNIX);s.settimeout(8);s.connect(str(root/'runtime/agent.sock'));p=json.dumps({'schema_version':1,'protocol':'module.control.v1','type':'hello','client_instance_id':'deployment-readonly'}).encode();s.sendall(struct.pack('>I',len(p))+p)
 def recv(n):
  b=b''
  while len(b)<n:
   q=s.recv(n-len(b))
   if not q:raise RuntimeError('IPC closed')
   b+=q
  return b
 result=[];mods=set()
 while len(mods)<5:
  n=struct.unpack('>I',recv(4))[0];assert n<1048577;m=json.loads(recv(n));result.append(m)
  if m.get('type')=='snapshot':mods.add(m.get('module_id'))
 s.close();return result
before=capture();(backup/'before.json').write_text(json.dumps(before,ensure_ascii=False,indent=2))
assert hashlib.sha256((stage/'Machome-Operations-Hub-macOS.zip').read_bytes()).hexdigest()=='da691a34a256358f5d3bffb1e71468eda1e5ee9b3d08a0f7c7178565d927352a'
run('/usr/bin/codesign','--verify','--deep','--strict',stage/app.name)
shutil.copy2(config,backup/'modules.json');os.chmod(backup/'modules.json',0o600);shutil.copy2(plist,backup/plist.name)
d=json.loads(config.read_text());old=json.loads(config.read_text())
for m in d['modules']:
 if m['id']=='premium':
  assert not m['settings'].get('test_mode');m['settings'].update(local_iopv_enabled=True,local_iopv_basis='midpoint',iopv_port=18680,iopv_listen='0.0.0.0:18680',iopv_web_url='http://127.0.0.1:18680/');m['settings'].pop('test_iopv_l1_address',None)
assert [m for m in old['modules']if m['id']!='premium']==[m for m in d['modules']if m['id']!='premium']
prepared=backup/'prepared.json';prepared.write_text(json.dumps(d,ensure_ascii=False,indent=2));os.chmod(prepared,0o600)
new=app.with_name(app.name+'.next');assert not new.exists();run('/usr/bin/ditto',stage/app.name,new);run('/usr/bin/codesign','--verify','--deep','--strict',new)
print('PREPARED BACKUP',backup,flush=True)
# Stop only the verified candidate Agent, then the single production LaunchAgent.
for line in subprocess.check_output(['ps','-axo','pid=,command='],text=True).splitlines():
 a=line.strip().split(None,1)
 if len(a)==2 and a[1].startswith(str(stage/app.name/'Contents/MacOS/machome-hub-agent')+' '):os.kill(int(a[0]),signal.SIGTERM)
run('launchctl','bootout',domain,str(plist));time.sleep(3)
for port in [19195,8421,6787,18765,18976]:
 s=socket.socket();s.settimeout(1);assert s.connect_ex(('127.0.0.1',port))!=0,f'old listener still active {port}';s.close()
os.rename(app,backup/app.name);os.rename(new,app);shutil.copy2(prepared,config.with_suffix('.new'));os.replace(config.with_suffix('.new'),config)
try:
 run('launchctl','bootstrap',domain,plist)
 for attempt in range(30):
  time.sleep(1)
  try:
   after=capture()
   hello=next(m for m in after if m.get('type')=='hello');assert hello['version']=='1.1.0';assert hello['control_ready'];break
  except Exception:
   if attempt==29:raise
 (backup/'after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2))
 print('SWITCHED',hello['version'],'PID',hello['pid'],'BACKUP',backup,flush=True)
except Exception:
 subprocess.run(['launchctl','bootout',domain,str(plist)]);time.sleep(3);os.rename(app,backup/'failed-new.app');os.rename(backup/app.name,app);shutil.copy2(backup/'modules.json',config);run('launchctl','bootstrap',domain,plist);print('ROLLED BACK',flush=True);raise

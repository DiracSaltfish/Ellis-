from pathlib import Path
import os,sys,json,time,hashlib,shutil,subprocess,socket,urllib.request,sqlite3
home=Path.home();root=home/'Library/Application Support/MachomeHub';app=home/'Applications/Machome 四合一运行中心.app';stage=Path(sys.argv[1]);tag='a-v32-'+time.strftime('%Y%m%d-%H%M%S');backup=root/'backups'/tag;backup.mkdir(mode=0o700)
plist=home/'Library/LaunchAgents/com.ellis.machome-hub-agent.plist';domain='gui/'+str(os.getuid());config=root/'config/modules.json'
def run(*a):subprocess.run([str(v)for v in a],check=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def get(path):return json.load(urllib.request.urlopen('http://127.0.0.1:18680'+path,timeout=5))
before=get('/api/v1/health');config_hash=sha(config);shutil.copy2(config,backup/'modules.json');shutil.copy2(plist,backup/plist.name);shutil.copy2('/tmp/pre-v32-acceptance.json',backup/'before.json')
db=root/'data/premium/local-iopv/data/iopv.sqlite'
with sqlite3.connect(db) as src,sqlite3.connect(backup/'iopv.sqlite') as dst:src.backup(dst)
new=app.with_name(tag+'.app');assert not new.exists();run('/usr/bin/ditto',app,new)
manifest=json.loads((stage/'manifest.json').read_text())
for relative,expected in manifest['files'].items():
 src=stage/Path(relative).name;assert sha(src)==expected
 shutil.copy2(src,new/relative);run('codesign','--force','--sign','-',new/relative)
manifest['installed_files']={p:sha(new/p)for p in manifest['files']};manifest['tag']=tag
(new/'Contents/Resources/a-v32-deployment.json').write_text(json.dumps(manifest,indent=2))
# Everything outside these two executables and the new patch manifest is retained.
for p in app.rglob('*'):
 if not p.is_file() or p.is_symlink():continue
 rel=str(p.relative_to(app))
 if rel in manifest['files'] or '_CodeSignature/' in rel:continue
 assert sha(p)==sha(new/rel),rel
run('codesign','--force','--sign','-',new);run('codesign','--verify','--deep','--strict',new)
dependencies=subprocess.check_output(['otool','-L',str(new/'Contents/MacOS/machome-hub-agent')],text=True)
assert '/opt/homebrew/opt/qt' not in dependencies, 'Agent must use the bundled Qt runtime'
for line in dependencies.splitlines()[1:]:
 dep=line.strip().split(' (')[0]
 if dep.startswith('@loader_path/'):
  assert (new/'Contents/MacOS'/dep.removeprefix('@loader_path/')).exists(),dep
print('PREPARED '+str(backup),flush=True)
swapped=False;stopped=False
try:
 run('launchctl','bootout',domain,plist);stopped=True
 for _ in range(30):
  live=[]
  for port in [8421,19195,18680]:
   with socket.socket() as s:s.settimeout(.3);live.append(s.connect_ex(('127.0.0.1',port))==0)
  if not any(live):break
  time.sleep(1)
 else:raise RuntimeError('old listeners did not stop')
 assert sha(config)==config_hash,'config changed during preparation'
 os.rename(app,backup/app.name);os.rename(new,app);swapped=True
 run('launchctl','bootstrap',domain,plist)
 for _ in range(60):
  time.sleep(1)
  try:
   health=get('/api/v1/health');settings=get('/api/v1/signal-settings')
   if health.get('run_id')!=before.get('run_id') and health.get('pcf_ready',0)>=before.get('pcf_ready',0):break
  except Exception:pass
 else:raise RuntimeError('new IOPV did not become ready')
 assert sha(config)==config_hash
 result={'backup':str(backup),'tag':tag,'run_id':health.get('run_id'),'pcf_ready':health.get('pcf_ready'),'settings':settings,'config_unchanged':True}
 (backup/'switch-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
except Exception:
 if stopped:
  subprocess.run(['launchctl','bootout',domain,str(plist)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);time.sleep(3)
  if swapped:os.rename(app,backup/'failed-new.app');os.rename(backup/app.name,app)
  run('launchctl','bootstrap',domain,plist)
 print('ROLLED BACK '+str(backup),flush=True);raise

#!/usr/bin/env python3
"""Run on machome: prepare (no restart), then apply. Preserves existing components."""
from pathlib import Path
import json,hashlib,subprocess,shutil,sqlite3,os,time,socket,signal,sys,datetime
h=Path.home();root=h/'Library/Application Support/MachomeHub';app=h/'Applications/Machome 四合一运行中心.app';incoming=Path('/tmp/machome-batch-release');cfg=root/'config/modules.json';plist=h/'Library/LaunchAgents/com.ellis.machome-hub-agent.plist';domain=f'gui/{os.getuid()}'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(*args):return subprocess.run([str(x) for x in args],check=True,capture_output=True,text=True)
def processes():
 out=[]
 for line in run('ps','-axo','pid=,comm=').stdout.splitlines():
  a=line.strip().split(None,1)
  if len(a)==2:out.append((int(a[0]),a[1]))
 return out
def open_port(port):
 with socket.socket() as s:s.settimeout(.2);return s.connect_ex(('127.0.0.1',port))==0
def launch_gui():subprocess.Popen([str(app/'Contents/MacOS/Machome 四合一运行中心'),'--config',str(cfg)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
def health():
 import urllib.request
 with urllib.request.urlopen('http://127.0.0.1:6787/api/v1/snapshot',timeout=3) as f:old=json.load(f)
 with urllib.request.urlopen('http://127.0.0.1/page-data/hk-connect-redemption',timeout=3) as f:hk=json.load(f)
 assert old['watchlist']==['159513','159518','159561','159632','159660','159866','159941'],old['watchlist']
 assert 'hk_connect' not in old and len(hk['items'])==97 and not hk['feed_stale'] and not hk['error']
 assert sum(bool(x['values']) for x in old['items'])==7
 assert sum(bool(x['values']) for x in hk['items'])==97
 assert not hk['alert_policy']['enabled'] and not hk['external_api_enabled']
 return {'legacy_count':len(old['items']),'hk_count':len(hk['items']),'hk_sequence':hk['sequence'],'monitoring':old['monitoring'],'wind':old['wind']['state'],'legacy_frames':sum(bool(x['values']) for x in old['items']),'hk_frames':sum(bool(x['values']) for x in hk['items'])}
if sys.argv[1]=='prepare':
 manifest=json.loads((incoming/'manifest.json').read_text())
 for n,digest in manifest['files'].items():assert sha(incoming/n)==digest,n
 backup=root/'backups'/('wind-batch-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
 stage=backup/'candidate'/app.name;stage.parent.mkdir()
 run('ditto',app,stage);shutil.copy2(cfg,backup/'modules.before.json');shutil.copy2(plist,backup/plist.name)
 db=root/'data/redemption/redemption.sqlite3'
 with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as source,sqlite3.connect(backup/'redemption.before.sqlite3') as target:source.backup(target)
 run('ditto',app,backup/'original.app')
 abi=root/'data/redemption/abi-manifest.json';shutil.copy2(abi,backup/'abi-manifest.before.json')
 mapping={'machome-wind-probe-helper':'Contents/Helpers/machome-wind-probe-helper','libmachome_wind_batch_probe.dylib':'Contents/Helpers/libmachome_wind_batch_probe.dylib'}
 for n,rel in mapping.items():shutil.copy2(incoming/n,stage/rel)
 # Verify every replaced binary resolves its bundled framework dependencies.
 for rel in mapping.values():
  binary=stage/rel
  for line in run('otool','-L',binary).stdout.splitlines()[1:]:
   dep=line.strip().split(' (')[0]
   assert not dep.startswith('/opt/homebrew/'),dep
   if dep.startswith('@loader_path/'):
    assert (binary.parent/dep[len('@loader_path/'):]).exists(),dep
  if binary.name!='Machome 四合一运行中心':run('codesign','--verify','--strict',binary)
 old_probe=app/'Contents/Helpers/libmachome_wind_tbapi_probe.dylib'
 assert sha(old_probe)==sha(stage/'Contents/Helpers/libmachome_wind_tbapi_probe.dylib')
 d=json.loads(cfg.read_text())
 for m in d['modules']:
  if m['id']=='redemption':
   assert len(m['settings']['hk_connect_pool']['symbols'])==97
   m['settings']['hk_connect_pool']['enabled']=True
   m['settings']['hk_connect_pool']['alerts_enabled']=False
   m['settings']['hk_connect_pool']['external_api_enabled']=False
 newabi=json.loads(abi.read_text())
 assert sha(Path('/Applications/WindPersonFree.app/Contents/MacOS/WindPersonFree'))==manifest['wind_executable_sha256']
 assert sha(Path('/Applications/WindPersonFree.app/Contents/Frameworks/libWind.Cosmos.TBAPI2.dylib'))==manifest['tbapi_sha256']
 newabi.update(batch_probe_sha256=manifest['files']['libmachome_wind_batch_probe.dylib'],tbapi_sha256=manifest['tbapi_sha256'])
 (backup/'abi-manifest.prepared.json').write_text(json.dumps(newabi,indent=2))
 check=subprocess.run([str(stage/'Contents/Helpers/machome-wind-probe-helper'),'--stdio','--mode','fixture','--data-root',str(backup/'fixture')],input='{"action":"quit"}\n',capture_output=True,text=True,timeout=15)
 assert check.returncode==0,check.stderr
 prepared=backup/'modules.prepared.json';prepared.write_text(json.dumps(d,ensure_ascii=False,indent=2));prepared.chmod(0o600)
 run(stage/'Contents/MacOS/machome-hub-agent','--config',prepared,'--check-config')
 resources=stage/'Contents/Resources';build=json.loads((resources/'build_manifest.json').read_text())
 build.update(base_build_id=build['build_id'],build_id=manifest['release'],built_at=manifest['built_at'],release_kind='incremental-preserve-other-components')
 build.setdefault('features',{})['wind_in_process_batch']=True
 (resources/'build_manifest.json').write_text(json.dumps(build,ensure_ascii=False,indent=2))
 sm=json.loads((resources/'source_manifest.json').read_text());sm.update(manifest['source_updates']);(resources/'source_manifest.json').write_text(json.dumps(sm,ensure_ascii=False,indent=2))
 shutil.copy2(incoming/'manifest.json',resources/'wind-batch-release.json')
 run('codesign','--force','--sign','-',stage);run('codesign','--verify','--deep','--strict',stage)
 receipt={'backup':str(backup),'stage':str(stage),'config_before_sha256':sha(cfg),'probe_sha256':sha(old_probe),'abi_sha256':sha(abi),'build_id':build['build_id'],'baseline_binaries':{rel:(sha(app/rel) if (app/rel).exists() else None) for rel in mapping.values()}}
 (incoming/'prepared.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(json.dumps({'prepared':str(backup),'build_id':build['build_id']},ensure_ascii=False))
elif sys.argv[1]=='apply':
 r=json.loads((incoming/'prepared.json').read_text());backup=Path(r['backup']);stage=Path(r['stage'])
 assert sha(cfg)==r['config_before_sha256'],'Config changed since prepare'
 for rel,digest in r['baseline_binaries'].items():assert (sha(app/rel) if (app/rel).exists() else None)==digest,'Production binary changed since prepare'
 assert sha(root/'data/redemption/abi-manifest.json')==r['abi_sha256']
 run('codesign','--verify','--deep','--strict',stage)
 run(sys.executable,incoming/'control.py','redemption_monitor_stop')
 ui=[pid for pid,cmd in processes() if cmd==str(app/'Contents/MacOS/Machome 四合一运行中心')]
 for pid in ui:os.kill(pid,signal.SIGTERM)
 time.sleep(1)
 assert not [pid for pid,cmd in processes() if cmd==str(app/'Contents/MacOS/Machome 四合一运行中心')]
 stopped=False;swapped=False
 try:
  run('launchctl','bootout',domain,plist);stopped=True
  for _ in range(90):
   if not any(open_port(p) for p in [6787,8421,19195,18765,18976,18680]):break
   time.sleep(1)
  else:raise RuntimeError('Old listeners failed to stop; deployment not swapped')
  os.rename(app,backup/'previous-installed.app');os.rename(stage,app);swapped=True
  shutil.copy2(backup/'modules.prepared.json',cfg.with_suffix('.next'));os.replace(cfg.with_suffix('.next'),cfg)
  shutil.copy2(backup/'abi-manifest.prepared.json',root/'data/redemption/abi-manifest.json')
  run('launchctl','bootstrap',domain,plist)
  started_monitor=False
  for _ in range(120):
   time.sleep(1)
   try:result=health();break
   except Exception:pass
   if not started_monitor and _>=2 and open_port(6787):
    run(sys.executable,incoming/'control.py','redemption_monitor_start');started_monitor=True
  else:raise RuntimeError('New pool/legacy/web checks failed')
  if ui:launch_gui()
  result.update(backup=str(backup),build_id=r['build_id'],applied_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
  (backup/'deployed.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
 except BaseException:
  if swapped:
   subprocess.run(['launchctl','bootout',domain,str(plist)],capture_output=True)
   for _ in range(90):
    if not any(open_port(p) for p in [6787,8421,19195,18765,18976,18680]):break
    time.sleep(1)
   else:raise RuntimeError('Rollback waiting for listeners; original bundle retained in backup')
   for _ in range(50):
    active=[]
    for f in (h/'Library/Containers/com.windin.mac.free/Data/tmp/machome-hub-probe').glob('batch-v1-*/state.json'):
     try:
      actor=json.loads(f.read_text())
      if actor['pid'] in [pid for pid,_cmd in processes()] and actor.get('items'):active.append(f)
     except (ValueError,OSError,KeyError):pass
    if not active:break
    time.sleep(1)
   else:raise RuntimeError('Rollback awaiting native subscription cleanup; original bundle retained')
   os.rename(app,backup/'failed-new.app');os.rename(backup/'previous-installed.app',app);shutil.copy2(backup/'modules.before.json',cfg);shutil.copy2(backup/'abi-manifest.before.json',root/'data/redemption/abi-manifest.json')
  if stopped:run('launchctl','bootstrap',domain,plist)
  if ui:launch_gui()
  raise

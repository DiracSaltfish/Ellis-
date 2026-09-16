#!/usr/bin/env python3
"""Atomic web-entry-only upgrade; run on machome with incoming binary path."""
from pathlib import Path
import os,sys,json,time,shutil,hashlib,subprocess,urllib.request
root=Path.home()/'Library/Application Support/MachomeHub'
target=root/'web-entry/machome-iopv-web-entry'
incoming=Path(sys.argv[1])
expected_new=sys.argv[2]
expected_old='28a301657e82b924b06df5f68e31e73ceee3a046cce0a3401cf2eff9e70778a5'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def processes():
 result={}
 for line in subprocess.check_output(['ps','-axo','pid=,comm='],text=True).splitlines():
  parts=line.strip().split(None,1)
  if len(parts)==2 and Path(parts[1]).name in ['machome-iopv-server','machome-wind-probe-helper','machome-hub-agent']:
   result[parts[1]]=int(parts[0])
 return result
def get(path):
 with urllib.request.urlopen('http://127.0.0.1'+path,timeout=15) as response:return response.read()
def restart():subprocess.run(['launchctl','kickstart','-k',f'gui/{os.getuid()}/com.ellis.machome-iopv-web-entry'],check=True)
assert digest(incoming)==expected_new,'Incoming checksum mismatch'
assert digest(target)==expected_old,'Web-entry has changed; do not overwrite'
subprocess.run(['codesign','--verify','--strict',str(incoming)],check=True)
prior=processes();original_minutes=digest_bytes=hashlib.sha256(get('/api/v1/minutes?symbol=513090.SH&date=2026-09-15')).hexdigest()
backup=root/'backups'/time.strftime('netting-layout-20260916-%H%M%S');backup.mkdir(parents=True)
shutil.copy2(target,backup/'web-entry')
receipt={'backup':str(backup),'before_sha256':expected_old,'after_sha256':expected_new,'processes_before':prior}
try:
 stage=target.with_name(target.name+'.netting-next');shutil.copy2(incoming,stage);stage.chmod(0o755);os.replace(stage,target);restart()
 for attempt in range(15):
  try:
   page=get('/')
   assert page.index(b'id="valuation-chart"') < page.index(b'class="panel bookpanel"') < page.index(b'id="netting-panel"')
   assert b'function inverse(' in get('/netting.js')
   assert b'NettingUI' in get('/netting-ui.js')
   break
  except Exception:
   if attempt==14:raise
   time.sleep(.5)
 assert hashlib.sha256(get('/api/v1/minutes?symbol=513090.SH&date=2026-09-15')).hexdigest()==original_minutes,'Historical API changed'
 assert processes()==prior,'Collector or Wind helper process changed'
 receipt['processes_after']=processes();receipt['status']='deployed';receipt['verified_at']=time.strftime('%Y-%m-%d %H:%M:%S')
except Exception:
 restore=target.with_name(target.name+'.netting-rollback');shutil.copy2(backup/'web-entry',restore);os.replace(restore,target);restart();receipt['status']='rolled_back';raise
finally:
 (backup/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
print(json.dumps(receipt,ensure_ascii=False,indent=2))

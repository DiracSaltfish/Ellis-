import hashlib,json,os,shutil,signal,subprocess,time,urllib.request
from pathlib import Path
stage=Path(__file__).resolve().parent
app=Path('/Users/ellis/Applications/Machome 四合一运行中心.app')
helper=app/'Contents/Helpers/machome-iopv-server';uni=app/'Contents/Resources/iopv/universe.json';manifest=app/'Contents/Resources/iopv-webfix-manifest.json'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
expected=json.loads((stage/'expected.json').read_text())
assert sha(helper)==expected['old_helper'],'Live helper changed'
assert sha(stage/'updated-server')==expected['new_helper']
assert json.loads(uni.read_text())==json.loads((stage/'before-universe.json').read_text()),'Universe changed'
get=lambda p:json.load(urllib.request.urlopen('http://127.0.0.1:18680'+p,timeout=15))
health=get('/api/v1/health');ledger=get('/ledger.json');hist=get('/api/v1/minutes?symbol=513090.SH&date=2026-09-09')
backup=Path('/Users/ellis/Library/Application Support/MachomeHub/backups')/time.strftime('opening-fx-fix-%Y%m%d-%H%M%S');backup.mkdir(parents=True)
for p in [helper,uni,manifest]:shutil.copy2(p,backup/p.name)
def stop():
 allowed={str(helper),str(app/'Contents/MacOS/../Helpers/machome-iopv-server')}
 for line in subprocess.check_output(['ps','-axo','pid=,comm='],text=True).splitlines():
  p=line.strip().split(None,1)
  if len(p)==2 and p[1] in allowed:os.kill(int(p[0]),signal.SIGTERM)
try:
 shutil.copy2(stage/'updated-server',helper);shutil.copy2(stage/'universe.json',uni)
 m=json.loads(manifest.read_text());m['assets']=expected['assets'];m['web_assets']=expected['assets'];m.update(patch='opening-fx-coverage-fix-20260914',helper_sha256=sha(helper),universe_sha256=sha(uni),changes='Revalue recorded assets despite historical FX gaps; first usable minute with unchanged 95 percent coverage; frozen model unchanged',ranking_source=expected['source_hashes'])
 manifest.write_text(json.dumps(m,ensure_ascii=False,indent=2));subprocess.run(['codesign','--force','--sign','-',str(app)],check=True);stop()
 for _ in range(45):
  try:
   h=get('/api/v1/health')
   if h['run_id']!=health['run_id'] and h['candidates']==197:break
  except Exception:pass
  time.sleep(1)
 else:raise RuntimeError('Updated service did not start')
 info=get('/api/v1/ranking-model');assert len(info['features'])==29 and info['trees']==150 and not info['lag_fx_fallback'];rank=get('/api/v1/ranking');assert rank['version']=='premium-model.v2'
 assert get('/api/v1/minutes?symbol=513090.SH&date=2026-09-09')==hist
 newledger=get('/ledger.json');assert newledger['matched']==197
 for key,val in ledger['records'].items():assert newledger['records'][key]==val
 rows=get('/api/v1/snapshots')['snapshots'];assert len(rows)==197
 assert sum('估值待核验' in r['name'] for r in rows)==76
 page=urllib.request.urlopen('http://127.0.0.1/ranking').read().decode();assert '每日模型排名' in page
 result={'backup':str(backup),'run_id':h['run_id'],'candidates':197,'added':76,'sh':sum(r['symbol'].startswith('5') for r in rows),'history_unchanged':len(hist),'ranking':rank,'model':info,'helper_sha256':sha(helper)}
 (stage/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False))
except BaseException:
 for p in [helper,uni,manifest]:shutil.copy2(backup/p.name,p)
 subprocess.run(['codesign','--force','--sign','-',str(app)],check=True);stop();raise

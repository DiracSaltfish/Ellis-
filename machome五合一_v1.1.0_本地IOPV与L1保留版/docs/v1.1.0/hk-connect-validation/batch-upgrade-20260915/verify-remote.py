from pathlib import Path
import subprocess,json,time,statistics,urllib.request,hashlib,tempfile,shutil
h=Path.home();root=h/'Library/Application Support/MachomeHub';app=h/'Applications/Machome 四合一运行中心.app';pid=int(subprocess.check_output(['pgrep','-x','WindPersonFree'],text=True).strip());raw=h/f'Library/Containers/com.windin.mac.free/Data/tmp/machome-hub-probe/batch-v1-{pid}'
a=[]
for i in range(45):
 v=subprocess.check_output(['ps','-p',str(pid),'-o','pcpu=,rss=,state='],text=True).split();s=json.loads((raw/'state.json').read_text());a.append({'time':time.time(),'cpu':float(v[0]),'rss_kb':int(v[1]),'state':v[2],'subscriptions':len(s['items']),'callbacks':sum(x['callback_seq'] for x in s['items']),'lag_ms':s['max_main_queue_lag_ms'],'error':s['error']});time.sleep(1)
get=lambda url:json.load(urllib.request.urlopen(url,timeout=5));old=get('http://127.0.0.1:6787/api/v1/snapshot');hk=get('http://127.0.0.1/page-data/hk-connect-redemption')
assert len(old['items'])==7 and sum(bool(x['values']) for x in old['items'])==7
assert len(hk['items'])==97 and sum(bool(x['values']) for x in hk['items'])==97 and not hk['feed_stale']
assert all(x['subscriptions']==104 and not x['error'] and 'T' not in x['state'] for x in a)
assert not hk['alert_policy']['enabled'] and not hk['external_api_enabled']
s=json.loads((raw/'state.json').read_text());oldcodes={x+'.SZ' for x in old['watchlist']};assert all(x['latency_ms']==(500 if x['symbol'] in oldcodes else 5000) for x in s['items'])
prepared=json.loads(Path('/tmp/machome-batch-release/prepared.json').read_text());before=Path(prepared['backup'])/'original.app';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();unchanged=['Contents/MacOS/machome-hub-agent','Contents/MacOS/Machome 四合一运行中心','Contents/Helpers/machome-iopv-server','Contents/Helpers/libmachome_wind_tbapi_probe.dylib'];
for n in unchanged:
 if sha(before/n)==sha(app/n):continue
 assert n=='Contents/MacOS/Machome 四合一运行中心'
 with tempfile.TemporaryDirectory() as directory:
  x=Path(directory)/'old';y=Path(directory)/'new';shutil.copy2(before/n,x);shutil.copy2(app/n,y)
  for f in [x,y]:subprocess.run(['codesign','--remove-signature',str(f)],check=True,capture_output=True)
  assert sha(x)==sha(y)

d={'wind_pid':pid,'subscriptions':104,'old_frames':7,'hk_frames':97,'cpu_median':statistics.median(x['cpu'] for x in a),'cpu_max':max(x['cpu'] for x in a),'rss_mib_max':max(x['rss_kb'] for x in a)/1024,'max_main_queue_lag_ms':max(x['lag_ms'] for x in a),'stopped_samples':0,'callbacks_during_observation':a[-1]['callbacks']-a[0]['callbacks'],'unchanged_binaries':unchanged,'new_alerts_enabled':False,'new_external_api_enabled':False,'instance':s['instance']}
out=Path('/tmp/machome-batch-release');(out/'verification.json').write_text(json.dumps(d,ensure_ascii=False,indent=2));(out/'samples.json').write_text(json.dumps(a));(out/'final-state.json').write_text(json.dumps(s,indent=2));print(json.dumps(d,ensure_ascii=False))

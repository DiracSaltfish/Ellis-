from pathlib import Path
import json,subprocess,threading,queue,time,hashlib,statistics,os
root=Path(__file__).parent;build=Path('/tmp/machome-hk-build');data=root/'data';data.mkdir(exist_ok=True)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
wind=Path('/Applications/WindPersonFree.app/Contents/MacOS/WindPersonFree');tb=wind.parent.parent/'Frameworks/libWind.Cosmos.TBAPI2.dylib'
manifest={'schema_version':1,'approved':True,'wind_executable_sha256':sha(wind),'probe_sha256':sha(build/'libmachome_wind_tbapi_probe.dylib'),'batch_probe_sha256':sha(build/'libmachome_wind_batch_probe.dylib'),'tbapi_sha256':sha(tb)};(data/'abi-manifest.json').write_text(json.dumps(manifest))
old=[x+'.SZ' for x in ['159513','159518','159561','159632','159660','159866','159941']];seed=Path('/Users/ellis/工具程序开发/machome五合一_v1.1.0_本地IOPV与L1保留版/config/hk-connect-redemption.json');hk=[x['symbol'] for x in json.loads(seed.read_text())['symbols']];additional=[x for x in hk if x not in old];union=set(old+hk)
pid=int(subprocess.check_output(['pgrep','-x','WindPersonFree'],text=True).strip());raw=Path.home()/f'Library/Containers/com.windin.mac.free/Data/tmp/machome-hub-probe/batch-v1-{pid}'
samples=[];phase='init';done=False;messages=[]
def log(x):print(json.dumps(x,ensure_ascii=False),flush=True)
def state():return json.loads((raw/'state.json').read_text())
def sampler():
 while not done:
  try:
   a=subprocess.check_output(['ps','-p',str(pid),'-o','pcpu=,rss=,state='],text=True).split();v={'time':time.time(),'phase':phase,'cpu':float(a[0]),'rss_kb':int(a[1]),'state':a[2]};samples.append(v)
   with (root/'live-samples.jsonl').open('a') as f:f.write(json.dumps(v)+'\n')
  except Exception:pass
  time.sleep(1)
threading.Thread(target=sampler,daemon=True).start()
def start():
 p=subprocess.Popen([str(build/'machome-wind-probe-helper'),'--stdio','--mode','live','--data-root',str(data)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);q=queue.Queue()
 def read():
  for l in p.stdout:
   m=json.loads(l);q.put(m);messages.append(m)
   with (root/'live-messages.jsonl').open('a') as f:f.write(l)
 threading.Thread(target=read,daemon=True).start();return p,q
p,q=start()
def send(action,**kw):p.stdin.write(json.dumps({'action':action,**kw})+'\n');p.stdin.flush()
def until(predicate,timeout=60):
 t=time.monotonic()
 while time.monotonic()-t<timeout:
  m=q.get(timeout=max(.1,timeout-(time.monotonic()-t)))
  if m.get('type')=='error':raise RuntimeError(m)
  if predicate(m):return m
 raise TimeoutError()
def observe(n):
 end=time.monotonic()+n
 while time.monotonic()<end:
  if p.poll() is not None:raise RuntimeError('helper exited')
  if len(samples)>10 and all(x['cpu']>180 or x['rss_kb']>1500000 for x in samples[-10:]):raise RuntimeError('resource limit')
  time.sleep(1)
try:
 until(lambda m:m.get('type')=='status');t=time.monotonic();send('subscribe',symbols=old);until(lambda m:m.get('state')=='subscribed');instance=state()['instance'];log({'old_ready_seconds':time.monotonic()-t,'instance':instance})
 phase='add-hk';t=time.monotonic();send('subscribe_add',symbols=additional);ok=set();captures={}
 until(lambda m:(ok.add(m['symbol']) if m.get('type')=='pool_subscription' and m.get('ok') else None) or len(ok)==len(additional));log({'hk_subscriptions':len(ok),'add_seconds':time.monotonic()-t})
 phase='steady';observe(60)
 captures={m['payload']['windcode']:m['payload'] for m in messages if m.get('type')=='capture'}
 assert set(captures)==union,(len(captures),len(union),union-set(captures));assert not [m for m in messages if m.get('type') in ['error','pool_capture_error']]
 s=state();assert all(x['latency_ms']==(500 if x['symbol'] in old else 5000) for x in s['items']);(root/'live-full-state.json').write_text(json.dumps(s,indent=2));log({'decoded_symbols':len(captures),'hk':len(hk),'main_queue_max_ms':s['max_main_queue_lag_ms']})
 phase='duplicate-add';before={x['symbol']:x['sub_id'] for x in s['items']};send('subscribe_add',symbols=additional);ok=set();until(lambda m:(ok.add(m['symbol']) if m.get('type')=='pool_subscription' and m.get('ok') else None) or len(ok)==len(additional));assert before=={x['symbol']:x['sub_id'] for x in state()['items']};log({'duplicate_add_idempotent':True})
 phase='stop';send('unsubscribe',request_id='test-stop');until(lambda m:m.get('type')=='command_result' and m.get('request_id')=='test-stop');assert not state()['items']
 phase='resume';send('subscribe',symbols=old);until(lambda m:m.get('state')=='subscribed');assert state()['instance']==instance;log({'resume_reused_instance':True})
 phase='controller-loss';p.kill();p.wait(timeout=3);t=time.monotonic()
 while time.monotonic()-t<45:
  if not state()['items']:break
  time.sleep(1)
 else:raise RuntimeError('watchdog cleanup failed')
 log({'watchdog_seconds':time.monotonic()-t});p,q=start();until(lambda m:m.get('type')=='status');send('subscribe',symbols=old);until(lambda m:m.get('state')=='subscribed');assert state()['instance']==instance;log({'new_helper_reused_instance':True})
except BaseException as e:
 (root/'live-error.txt').write_text(str(e));log({'error':str(e)})
finally:
 phase='final-cleanup'
 if p.poll() is None:
  try:send('unsubscribe',request_id='final');until(lambda m:m.get('type')=='command_result' and m.get('request_id')=='final');send('quit');p.wait(timeout=5)
  except Exception as e:log({'cleanup_error':str(e)});p.kill();p.wait()
 done=True
 (root/'live-final-state.json').write_text(json.dumps(state(),indent=2))
 result={}
 for ph in dict.fromkeys(x['phase'] for x in samples):
  a=[x for x in samples if x['phase']==ph];result[ph]={'samples':len(a),'cpu_median':statistics.median(x['cpu'] for x in a),'cpu_max':max(x['cpu'] for x in a),'rss_mib_max':max(x['rss_kb'] for x in a)/1024,'stopped_samples':sum('T' in x['state'] for x in a)}
 (root/'live-summary.json').write_text(json.dumps(result,indent=2));log(result)

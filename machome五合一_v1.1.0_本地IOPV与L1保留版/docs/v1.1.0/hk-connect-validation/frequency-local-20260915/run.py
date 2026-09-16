from pathlib import Path
import subprocess,json,time,os,hashlib,shutil,threading,statistics,uuid,signal
root=Path('/tmp/machome-frequency-20260915');exe=Path('/Applications/WindPersonFree.app/Contents/MacOS/WindPersonFree');assert hashlib.sha256(exe.read_bytes()).hexdigest()=='c272d3ec569473ed0d9b7f79942546b263f9301046219b818497daee7c79b419'
pids=[int(l.split(None,1)[0]) for l in subprocess.check_output(['ps','-axo','pid=,comm='],text=True).splitlines() if l.strip().endswith(str(exe))];assert len(pids)==1;p=pids[0]
raw=Path.home()/'Library/Containers/com.windin.mac.free/Data/tmp/machome-frequency-20260915';raw.mkdir(parents=True,exist_ok=True);raw.chmod(0o700);shutil.copy2(root/'probe.dylib',raw/'probe.dylib')
seed=json.loads(Path('/Users/ellis/工具程序开发/machome五合一_v1.1.0_本地IOPV与L1保留版/config/hk-connect-redemption.json').read_text());codes=[r['symbol'] for r in seed['symbols']]
latency=60000;phase='baseline';done=False;renew=True;samples=[];states=[];initialized=False;attach_count=0

def out(d):print(json.dumps(d,ensure_ascii=False),flush=True)
def snapshot():
 try:return json.loads((raw/'state.json').read_text())
 except Exception:return {}
def sample():
 while not done:
  if renew:(raw/'lease').touch()
  try:
   v=subprocess.check_output(['ps','-p',str(p),'-o','pcpu=,rss=,state='],text=True).split();s={'t':time.time(),'phase':phase,'cpu':float(v[0]),'rss_kb':int(v[1]),'state':v[2]};samples.append(s)
   with (root/'samples.jsonl').open('a') as f:f.write(json.dumps(s)+'\n')
   s=snapshot()
   if s:
    states.append(s)
    with (root/'states.jsonl').open('a') as f:f.write(json.dumps(s)+'\n')
  except Exception:pass
  time.sleep(1)
threading.Thread(target=sample,daemon=True).start()
def observe(name,n):
 global phase
 phase=name;out({'phase':name,'seconds':n})
 for i in range(n):
  time.sleep(1)
  if len(samples)>10 and all(x['rss_kb']>1500000 or x['cpu']>180 for x in samples[-10:]):raise RuntimeError('resource guard')
def command(target):
 cid=str(uuid.uuid4());tmp=raw/'command.next';tmp.write_text(json.dumps({'id':cid,'symbols':target,'latency_ms':latency}));os.replace(tmp,raw/'command.json');return cid
def wait(target,cid,timeout=60,frames=True):
 start=time.monotonic()
 while time.monotonic()-start<timeout:
  d=snapshot()
  if d.get('error'):raise RuntimeError(d['error'])
  rows=d.get('items',[])
  if d.get('command')==cid and {r['symbol'] for r in rows}==set(target) and (not frames or all(r['callback_seq']>0 and r['error']==0 for r in rows)):
   out({'phase':phase,'count':len(rows),'ready_seconds':round(time.monotonic()-start,3),'max_main_queue_lag_ms':d['max_main_queue_lag_ms']});return d
  time.sleep(.2)
 raise RuntimeError('command timeout '+str(d)[:500])
try:
 observe('baseline',25);phase='initialize';attach_count+=1;start=time.monotonic()
 cmds=['thread select 1',f'expr -- void *$once = (void *)dlopen("{raw}/probe.dylib", 0x6)',f'expr -- int $ready = ((int (*)(const char *))dlsym($once,"once_start"))("{raw}")','expr -- $ready','process detach'];a=['/usr/bin/lldb','--batch','-p',str(p)]
 for c in cmds:a+=['-o',c]
 try:r=subprocess.run(a,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=30)
 except subprocess.TimeoutExpired:os.kill(p,signal.SIGCONT);raise
 (root/'initialize.log').write_text(r.stdout);elapsed=time.monotonic()-start
 if r.returncode or f'Process {p} detached' not in r.stdout or '$ready = 0' not in r.stdout:os.kill(p,signal.SIGCONT);raise RuntimeError('initialization failed '+r.stdout[-800:])
 initialized=True;out({'attach_count':attach_count,'initialize_seconds':elapsed})
 for interval in [60000,5000,1000]:
  latency=interval;phase=f'add-{interval}';s=wait(codes,command(codes));(root/f'ready-{interval}.json').write_text(json.dumps(s,indent=2))
  observe(f'stable-{interval}',90)
  s=snapshot();(root/f'end-{interval}.json').write_text(json.dumps(s,indent=2))
  dest=root/f'captures-{interval}';dest.mkdir(exist_ok=True)
  for f in raw.glob('capture_*.json'):shutil.copy2(f,dest/f.name)
  phase=f'stop-{interval}';s=wait([],command([]));(root/f'stopped-{interval}.json').write_text(json.dumps(s,indent=2));observe(f'idle-{interval}',10)
except BaseException as e:(root/'error.txt').write_text(str(e));out({'error':str(e)})
finally:
 if initialized:
  try:renew=True;phase='final-cleanup';s=wait([],command([]));(root/'final-cleanup.json').write_text(json.dumps(s,indent=2));observe('after-cleanup',25)
  except Exception as e:out({'cleanup_error':str(e)});renew=False
 done=True
 summary={}
 for ph in dict.fromkeys(x['phase'] for x in samples):
  a=[x for x in samples if x['phase']==ph];summary[ph]={'n':len(a),'cpu_median':statistics.median(x['cpu'] for x in a),'cpu_max':max(x['cpu'] for x in a),'rss_mb_max':max(x['rss_kb'] for x in a)/1024,'stopped_samples':sum('T' in x['state'] for x in a)}
 summary['attach_count']=attach_count;(root/'summary.json').write_text(json.dumps(summary,indent=2));out(summary)

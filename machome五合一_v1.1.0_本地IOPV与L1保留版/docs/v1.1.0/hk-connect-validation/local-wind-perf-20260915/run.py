import os,sys,json,time,subprocess,shutil,hashlib,signal,threading,re,statistics
from pathlib import Path
root=Path('/tmp/machome-hk-perf-v2-20260915');exe='/Applications/WindPersonFree.app/Contents/MacOS/WindPersonFree'
assert hashlib.sha256(Path(exe).read_bytes()).hexdigest()=='c272d3ec569473ed0d9b7f79942546b263f9301046219b818497daee7c79b419'
pids=[int(l.split(None,1)[0]) for l in subprocess.check_output(['ps','-axo','pid=,comm='],text=True).splitlines() if l.strip().endswith(exe)];assert len(pids)==1;p=pids[0]
raw=Path.home()/'Library/Containers/com.windin.mac.free/Data/tmp/machome-hk-perf-v2-20260915';raw.mkdir(parents=True,exist_ok=True);raw.chmod(0o700)
seed=json.loads(Path('/Users/ellis/工具程序开发/machome五合一_v1.1.0_本地IOPV与L1保留版/config/hk-connect-redemption.json').read_text());symbols=[r['symbol'] for r in seed['symbols']]
phase='baseline';samples=[];operations=[];active=[];done=False

def record(msg):print(json.dumps(msg,ensure_ascii=False),flush=True)
def monitor():
 while not done:
  try:
   vals=subprocess.check_output(['ps','-p',str(p),'-o','pcpu=,rss=,state='],text=True).split();s={'t':time.time(),'phase':phase,'cpu':float(vals[0]),'rss_kb':int(vals[1]),'state':vals[2]};samples.append(s)
   with (root/'samples.jsonl').open('a') as f:f.write(json.dumps(s)+'\n')
  except Exception:pass
  time.sleep(1)
threading.Thread(target=monitor,daemon=True).start()
def lldb(commands,label):
 start=time.monotonic();a=['/usr/bin/lldb','--batch','-p',str(p)]
 for c in commands+['process detach']:a+=['-o',c]
 try:r=subprocess.run(a,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=40)
 except subprocess.TimeoutExpired:
  os.kill(p,signal.SIGCONT);raise RuntimeError('LLDB timeout')
 elapsed=time.monotonic()-start;(root/(label+'.log')).write_text(r.stdout);operations.append({'label':label,'seconds':elapsed,'exit':r.returncode})
 if r.returncode or f'Process {p} detached' not in r.stdout:
  os.kill(p,signal.SIGCONT);raise RuntimeError('LLDB failed: '+r.stdout[-1200:])
 return elapsed

def add(code,latency):
 i=len(active);path=raw/f'perf_{latency}_{i}.dylib';shutil.copy2(root/'probe.dylib',path);active.append((code,path));(root/'active.json').write_text(json.dumps([(a,str(b)) for a,b in active]))
 commands=[f'expr -- void *$hp{i} = (void *)dlopen("{path}", 0x6)',f'expr -- long long $out{i} = ((long long (*)(const char *))dlsym($hp{i}, "wind_tbapi_set_output_dir"))("{raw}")',f'expr -- long long $sub{i} = ((long long (*)(const char *, int))dlsym($hp{i}, "wind_tbapi_subscribe"))("{code}", {latency})',f'expr -- long long $id{i} = ((long long (*)(void))dlsym($hp{i}, "wind_tbapi_subscription_id"))()',f'expr -- $sub{i}',f'expr -- $id{i}']
 elapsed=lldb(commands,f'add-{latency}-{i}');status=json.loads((raw/f"wind_tbapi_live_{code.replace('.','_')}_status.json").read_text());assert status['status']=='modify_target' and status['code']>=0,status;assert f'LATENCY({latency} MS)' in status['message'];record({'phase':phase,'added':len(active),'latency_ms':latency,'attach_seconds':round(elapsed,3)})

def counters():
 out={}
 for code,_ in active:
  f=raw/f"wind_tbapi_live_{code.replace('.','_')}.json"
  try:
   j=json.loads(f.read_text());out[code]={k:v for k,v in j.items() if k not in ['frame_hex','frame','payload_hex','raw_hex'] and not isinstance(v,(dict,list)) and len(str(v))<200}
  except Exception:pass
 return out

def observe(name,seconds):
 global phase
 phase=name;before=counters();record({'phase':name,'observe_seconds':seconds,'active':len(active)})
 for _ in range(seconds):
  time.sleep(1)
  if len(samples)>15 and all(s['cpu']>180 for s in samples[-15:]):raise RuntimeError('Wind CPU guard exceeded')
 after=counters();(root/(name+'-callbacks.json')).write_text(json.dumps({'before':before,'after':after},indent=2));record({'phase':name,'frames':len(after),'done':True})
def cleanup():
 global active,phase
 phase='cleanup'
 for off in range(0,len(active),8):
  cmds=[]
  for n,(code,path) in enumerate(active[off:off+8]):
   cmds += [f'expr -- void *$cp{n} = (void *)dlopen("{path}", 0x6)',f'expr -- long long $stop{n} = ((long long (*)(void))dlsym($cp{n},"wind_tbapi_stop"))()',f'expr -- $stop{n}']
  lldb(cmds,f'cleanup-{time.time_ns()}')
 for code,path in active:
  st=json.loads((raw/f"wind_tbapi_live_{code.replace('.','_')}_status.json").read_text());assert st['status']=='stopped' and st['code']>=0,st
 active=[];(root/'active.json').write_text('[]');record({'cleanup_confirmed':True})
try:
 observe('baseline',30)
 phase='subscribe-500';add(symbols[0],500);observe('one-500',75);cleanup()
 phase='subscribe-60000';add(symbols[0],60000);observe('one-60000',75)
 for target in [10,30,97]:
  phase=f'subscribe-{target}'
  for code in symbols[len(active):target]:add(code,60000)
  observe(f'{target}-60000',75)
except BaseException as e:
 record({'error':str(e)});(root/'error.txt').write_text(str(e))
finally:
 try:cleanup();observe('after-cleanup',30)
 except BaseException as e:record({'cleanup_error':str(e)})
 done=True;(root/'operations.json').write_text(json.dumps(operations,indent=2))
 summary={}
 for ph in dict.fromkeys(s['phase'] for s in samples):
  ss=[s for s in samples if s['phase']==ph];summary[ph]={'n':len(ss),'cpu_median':statistics.median(s['cpu'] for s in ss),'cpu_max':max(s['cpu'] for s in ss),'rss_mb_max':max(s['rss_kb'] for s in ss)/1024,'stopped_samples':sum('T' in s['state'] for s in ss)}
 (root/'summary.json').write_text(json.dumps(summary,indent=2));record(summary)

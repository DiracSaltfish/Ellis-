#!/usr/bin/env python3
"""Private Upload component. Only declarative, bundled workers can be executed.

A closed parent pipe stops the complete process group of every owned worker.
Business scripts run unchanged from an isolated copy, retaining relative paths.
The IPC stream contains lifecycle facts, never credentials or raw child logs.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import runpy
import shutil
import socket
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo, reset_tzpath
import urllib.request
import urllib.error

BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
# Conda-built CPython bakes its build prefix into TZPATH and OpenSSL defaults.
# Target macOS supplies IANA time zones; HTTPS uses the CA bundle we ship.
reset_tzpath(['/usr/share/zoneinfo'])
os.environ['PYTHONTZPATH']='/usr/share/zoneinfo'
if (BASE/'ca-bundle.pem').is_file():
    os.environ['SSL_CERT_FILE']=str(BASE/'ca-bundle.pem')
ALLOW = {
    'sina': ('sina_ws_uploader.py', []),
    'xop-family': ('private_xop_family_uploader.py', []),
    'xop-smart': ('private_valuation_uploader.py', []),
    'xop-overnight': ('private_513350_valuation_uploader.py', []),
    'lof-162411': ('private_162411_valuation_uploader.py', []),
    'basket-159605': ('private_159605_valuation_uploader.py', []),
    'silver': ('private_161226_silver_uploader.py', []),
    'china-internet': ('private_china_internet_valuation_uploader.py', []),
    'india': ('private_164824_valuation_uploader.py', []),
    'rebuild': ('private_intraday_rebuild_agent.py', []),
    'health-monitor': ('upload_health_monitor.py', []),
    'anchor-backfill': ('ibkr_backfill_valuation_anchors.py', []),
    'india-final-nav': ('private_164824_final_nav_backfill.py', ['--max-days','4']),
    'china-history': ('private_china_internet_history_backfill.py', []),
    'month-history': ('private_month_history_backfill.py', []),
}
for family in ('nasdaq','sp500','nikkei225','germany'):
    ALLOW[family] = ('private_nasdaq_valuation_uploader.py', ['--family', family])


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def worker(path, args):
    # Also handles subprocesses launched by baseline backfill scripts using
    # sys.executable. Frozen executable is a narrowly scoped Python runner.
    root = Path(os.environ['MACHOME_UPLOAD_RUN_ROOT']).resolve()
    script = Path(path).resolve()
    if script.parent != root / 'scripts' or script.suffix != '.py':
        raise ValueError('worker must be an installed business script')
    expected = json.loads((root / 'script-hashes.json').read_text()).get(script.name)
    if not expected or hashlib.sha256(script.read_bytes()).hexdigest() != expected:
        raise ValueError('worker source checksum mismatch')
    os.chdir(root)
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script), *args]
    runpy.run_path(str(script), run_name='__main__')


def stage(root, source):
    target = root / 'business-run'
    scripts = target / 'scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for src in (source / 'scripts').glob('*.py'):
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        dest = scripts / src.name
        if dest.is_symlink():
            raise ValueError('script symlinks are forbidden')
        if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            shutil.copyfile(src, dest)
        hashes[src.name] = digest
    if (source / 'scripts' / 'data').exists():
        shutil.copytree(source / 'scripts' / 'data', scripts / 'data', dirs_exist_ok=True)
    atomic_json(target / 'script-hashes.json', hashes)
    return target


def read_config(path, root):
    root = Path(root).resolve()
    path = Path(path).resolve()
    if not path.is_relative_to(root) or path.stat().st_mode & 0o077:
        raise ValueError('private configuration must be inside data root with mode 0600')
    cfg = json.loads(path.read_text())
    if cfg.get('schema_version') != 1:
        raise ValueError('unsupported component configuration')
    if cfg.get('mode') != 'live':
        raise ValueError('component mode must explicitly be live')
    env = cfg.get('environment', {})
    if not isinstance(env,dict): raise ValueError('environment must be an object')
    for name in ('NNN_SERVER_URL','NNN_UPLOAD_TOKEN'):
        if not str(env.get(name, '')).strip():
            raise ValueError('required configuration missing: ' + name)
    workers = cfg.get('workers', [])
    if not isinstance(workers,list) or not all(isinstance(x,dict) for x in workers): raise ValueError('workers must be a list')
    ids = [x.get('id') for x in workers]
    if len(ids) != len(set(ids)) or any(x not in ALLOW and x != 'website' for x in ids):
        raise ValueError('invalid or duplicate worker id')
    if any(not isinstance(x.get('args', []), list) or
           not all(isinstance(a,str) for a in x.get('args',[])) for x in workers):
        raise ValueError('worker arguments must be strings')
    if cfg.get('website', {}).get('enabled', True):
        persistence=cfg.get('website',{}).get('persistence','mysql')
        if persistence not in ('mysql','file_only'):
            raise ValueError('unsupported website persistence mode')
        if persistence=='file_only' and (not str(env.get('HTTP_ADDR','')).startswith('127.0.0.1:') or str(env.get('ENABLE_UPLOAD_QUOTES','')).lower()!='false'):
            raise ValueError('file-only mirror requires loopback and quote uploads disabled')
        required=('UPLOAD_TOKEN','DATA_VIEW_TOKEN','NAV_SETTINGS_TOKEN')
        if persistence=='mysql': required=('MYSQL_DSN',)+required
        for name in required:
            if not str(env.get(name, '')).strip():
                raise ValueError('website requires: ' + name)
        if 'website' not in ids or not next(x for x in workers if x['id']=='website').get('enabled',True):
            raise ValueError('website worker missing or disabled')
    # Consolidated SMART XOP replaces these, never run duplicate upload writers.
    active = {x['id'] for x in workers if x.get('enabled', True)}
    if 'xop-family' in active and active & {'xop-smart','xop-overnight','lof-162411'}:
        raise ValueError('overlapping XOP writers; select the active baseline chain')
    return cfg


class Supervisor:
    def __init__(self, root, config, source=BASE/'business'):
        self.root, self.cfg = root, config
        self.run_root = stage(root, source)
        self.events = queue.Queue(maxsize=128)
        self.stopping = threading.Event()
        self.procs, self.states, self.restarts, self.next_start = {}, {}, {}, {}
        self.website_ok = False
        self.next_probe = 0.0
        self.schedule_path = root/'component-schedule.json'
        self.completed = json.loads(self.schedule_path.read_text()) if self.schedule_path.exists() else {}
        self.workers = {w['id']: w for w in config['workers']}
        self.logs = root/'logs'/'components'
        self.logs.mkdir(parents=True, exist_ok=True)
        self.env = {'PATH':'/usr/bin:/bin:/usr/sbin:/sbin', 'HOME':str(root),
                    'LANG':'en_US.UTF-8','TZ':'Asia/Shanghai','PYTHONUNBUFFERED':'1',
                    'MACHOME_UPLOAD_RUN_ROOT':str(self.run_root),'PYTHONTZPATH':'/usr/share/zoneinfo'}
        if (BASE/'ca-bundle.pem').is_file():self.env['SSL_CERT_FILE']=str(BASE/'ca-bundle.pem')
        for key, value in config.get('environment', {}).items():
            if key in ('PATH','HOME','PYTHONPATH','PYTHONHOME','DYLD_LIBRARY_PATH'):
                raise ValueError('runtime override forbidden: '+key)
            self.env[str(key)] = str(value).replace('{data_root}',str(root)).replace('{run_root}',str(self.run_root))
        # All baseline defaults that point outside the run directory are pinned.
        self.env.update({
            'NNN_QUOTE_STORE_DIR':str(self.run_root/'scripts'/'quote_store'),
            'NNN_INTRADAY_MINUTE_STORE_DIR':str(self.run_root/'scripts'/'intraday_minute_store'),
            'NNN_PRIVATE_PCF_PACER_STATE':str(root/'pcf-pacer.json'),
            'NNN_UPLOAD_MONITOR_REPO_DIR':str(self.run_root),
            'NNN_REPO_ROOT':str(self.run_root),
            'MACHOME_REQUIRE_MYSQL':'1',
            'MACHOME_UPLOAD_PROCESS_STATE':str(root/'component-processes.json'),
            'NNN_UPLOAD_HEALTH_DIR':str(self.run_root/'scripts'/'.runtime'/'upload_health'),
            'SNAPSHOT_DATA_DIR':str(root/'website'/'snapshots'/'data'),
            'SNAPSHOT_HTML_DIR':str(root/'website'/'snapshots'/'html'),
            'MINUTE_HISTORY_DIR':str(root/'website'/'snapshots'/'minute_history'),
            'SHARE_HISTORY_DIR':str(root/'website'/'snapshots'/'share_history'),
            'FRONTEND_DIST':str(source/'frontend'/'dist'),
        })
        # The index/India collectors historically use a different server key.
        # Default them to this component's configured site, never a baked-in host.
        self.env.setdefault('NNN_PRIVATE_SERVER', self.env['NNN_SERVER_URL'])
        if config.get('website',{}).get('persistence')=='file_only':
            self.env['MYSQL_DSN']=''
            self.env['MACHOME_REQUIRE_MYSQL']='0'
        self.secrets = [str(v) for k,v in self.env.items() if any(x in k for x in ('TOKEN','PASSWORD','DSN')) and len(str(v))>3]

    def log_reader(self, name, stream):
        path = self.logs/(name+'.log')
        try:
            while True:
                data = stream.readline()
                if not data: break
                text = data.decode('utf-8', errors='replace')
                for secret in self.secrets: text = text.replace(secret,'[redacted]')
                if path.exists() and path.stat().st_size>8*1024*1024:
                    os.replace(path,path.with_suffix('.previous.log'))
                with open(path,'a',encoding='utf-8') as out: out.write(text)
                os.chmod(path,0o600)
        finally:
            stream.close()

    def launch(self, name):
        spec = self.workers[name]
        environment = self.env.copy()
        for key,value in spec.get('environment',{}).items():
            if not key.startswith(('NNN_','IBKR_')): raise ValueError('worker environment key not allowed')
            environment[key] = str(value).replace('{data_root}',str(self.root)).replace('{run_root}',str(self.run_root))
        if name=='website':
            program = BASE/'machome-upload-web'
            if not program.is_file(): raise ValueError('bundled website binary missing')
            command=[str(program)]
        else:
            script, defaults = ALLOW[name]
            command=[sys.executable]
            if not getattr(sys,'frozen',False): command += [str(Path(__file__).resolve())]
            command += [str(self.run_root/'scripts'/script), *defaults, *spec.get('args',[])]
        process=subprocess.Popen(command,cwd=self.run_root,env=environment,stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
        self.procs[name]=process
        self.states[name]={'id':name,'state':'running','pid':process.pid,'started_at':datetime.now().astimezone().isoformat(),
                           'ready':False,'business_success':False}
        threading.Thread(target=self.log_reader,args=(name,process.stdout),daemon=True).start()

    def stop_worker(self,name):
        process=self.procs.pop(name,None)
        if process is None:return
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait(timeout=3)
        self.states[name]={'id':name,'state':'stopped','ready':False}

    def shutdown(self):
        # One global deadline avoids N workers multiplying the shutdown time.
        processes=list(self.procs.items())
        for _,proc in processes:
            try:os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError:pass
        deadline=time.monotonic()+8
        while any(proc.poll() is None for _,proc in processes) and time.monotonic()<deadline:
            time.sleep(.025)
        for name,proc in processes:
            # Terminate descendants even if their direct parent exited first.
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            proc.wait(timeout=2)
            self.states[name]={'id':name,'state':'stopped','ready':False}
        self.procs.clear()

    def due(self,name,now):
        spec=self.workers[name]
        times=spec.get('daily_at',[])
        if not times:return not spec.get('once') or name not in self.completed
        if now.weekday()>4:return False
        for clock in times:
            key=f'{name}:{now:%Y-%m-%d}:{clock}'
            if now.strftime('%H:%M')>=clock and key not in self.completed:
                return key
        return False

    def collection_expected(self, name, now):
        data_ids={'sina','xop-family','xop-smart','xop-overnight','lof-162411','basket-159605',
                  'silver','china-internet','india','nasdaq','sp500','nikkei225','germany'}
        if name not in data_ids or not self.workers.get(name,{}).get('enabled',True):return False
        is_day=now.weekday()<5 and now.date().isoformat() not in self.env.get('NNN_UPLOAD_MONITOR_SKIP_DATES','').split(',')
        clock=now.strftime('%H:%M:%S')
        begin=self.env.get('NNN_UPLOAD_MONITOR_PUBLIC_START','09:20:00') if name=='sina' else self.env.get('NNN_UPLOAD_MONITOR_PRIVATE_START','09:37:00')
        end='14:57:00' if name=='sina' else '14:40:00' if name=='nikkei225' else '15:00:00'
        return is_day and begin<=clock<end and (name!='silver' or
            '09:15:00'<=clock<'10:15:00' or '10:30:00'<=clock<'11:30:00' or '13:30:00'<=clock<'15:00:00')

    def business_readiness(self, now, health):
        data_ids={'sina','xop-family','xop-smart','xop-overnight','lof-162411','basket-159605',
                  'silver','china-internet','india','nasdaq','sp500','nikkei225','germany'}
        active={name for name,spec in self.workers.items() if spec.get('enabled',True)}
        required=active & data_ids
        if not required:return False, ['no business workers enabled']
        problems=[]
        for name in active:
            if self.states.get(name,{}).get('state') not in ('running','scheduled_idle','completed'):
                problems.append(name+':process_not_ready')
        if 'website' in active and not self.website_ok:problems.append('website:not_ready')
        duration=self.env.get('NNN_UPLOAD_MONITOR_FRESHNESS','35s').strip()
        freshness=max(15,float(duration[:-1])*(60 if duration.endswith('m') else 1)) if duration.endswith(('s','m')) else max(15,float(duration))
        for name in required:
            if not self.collection_expected(name,now):continue
            state=self.states.get(name,{})
            matches=[x for x in health if x.get('pid')==state.get('pid')]
            valid=False
            for value in matches:
                try:
                    ack=datetime.fromisoformat(value.get('last_success_at',''))
                    started=datetime.fromisoformat(state['started_at'])
                    valid |= value.get('state')=='ok' and ack>=started and 0<=(now-ack).total_seconds()<=freshness
                except (ValueError,KeyError,TypeError):continue
            if not valid:problems.append(name+':missing_or_stale_ack')
        return not problems,problems

    def run(self):
        def commands():
            for line in sys.stdin:
                if len(line)>65536:break
                try:self.events.put_nowait(json.loads(line))
                except (ValueError,queue.Full):continue
            self.stopping.set()
        threading.Thread(target=commands,daemon=True).start()
        for sig in (signal.SIGTERM,signal.SIGINT):
            signal.signal(sig,lambda *_:self.stopping.set())
        emit({'type':'hello','protocol':1,'engine':'bundled_business'})
        try:
            while not self.stopping.wait(.25):
                now=datetime.now(ZoneInfo('Asia/Shanghai')); clock=time.monotonic()
                while not self.events.empty():
                    cmd=self.events.get_nowait();name=cmd.get('job_id');action=cmd.get('action')
                    if action=='stop':self.stopping.set();break
                    ok=False
                    if name in self.workers and action=='restart':
                        self.stop_worker(name);self.restarts[name]=[];self.next_start[name]=0
                        self.completed.pop(name,None);self.launch(name);ok=True
                    emit({'type':'command_result','command_id':cmd.get('command_id'),'ok':ok,
                          'message':'component restarted' if ok else 'unsupported component command'})
                if self.stopping.is_set():break
                for name,spec in self.workers.items():
                    if not spec.get('enabled',True):
                        self.states[name]={'id':name,'state':'disabled','ready':False};continue
                    proc=self.procs.get(name)
                    if proc and proc.poll() is not None:
                        code=proc.returncode
                        self.stop_worker(name)
                        if code==0 and (spec.get('once') or spec.get('daily_at')):
                            key=self.states.get(name,{}).get('schedule_key',name)
                            # Capture key when launching; process state is replaced on stop.
                            key=getattr(proc,'schedule_key',name)
                            self.completed[key]=now.isoformat();atomic_json(self.schedule_path,self.completed)
                            self.states[name]={'id':name,'state':'completed','exit_code':0,'business_success':False,'completion_evidence':'worker_exit_zero'}
                        else:
                            attempts=[x for x in self.restarts.get(name,[]) if clock-x<300]
                            attempts.append(clock);self.restarts[name]=attempts
                            # Permission/network failures can be resolved while
                            # we run. Rate-limit a crash burst, never latch it
                            # forever (legacy launchd also retried).
                            self.next_start[name]=clock+(300 if len(attempts)>=4 else 2**len(attempts))
                            self.states[name]={'id':name,'state':'fault' if len(attempts)>=4 else 'recovering','exit_code':code,'ready':False}
                        proc=None
                    if proc:continue
                    if clock<self.next_start.get(name,0):continue
                    due=self.due(name,now)
                    if due:
                        self.launch(name)
                        self.procs[name].schedule_key=due if isinstance(due,str) else name
                    elif name not in self.states or self.states[name].get('state')!='completed':
                        self.states[name]={'id':name,'state':'scheduled_idle','ready':False}
                if 'website' in self.procs and clock>=self.next_probe:
                    self.next_probe=clock+5
                    try:
                        address=self.env.get('HTTP_ADDR','127.0.0.1:8080')
                        with urllib.request.urlopen('http://'+address+'/api/v1/health',timeout=.75) as response:
                            self.website_ok=response.status==200 and json.loads(response.read(65536)).get('ok') is True
                    except (OSError,ValueError):self.website_ok=False
                # The health monitor consumes owned process state instead of
                # inspecting or attempting to control the retired launchd jobs.
                owned=[]
                for name,value in self.states.items():
                    row=dict(value);row['label']=self.workers[name].get('label',name);owned.append(row)
                atomic_json(self.root/'component-processes.json',{'updated_at':now.isoformat(),'jobs':owned})
                health=[]
                for file in Path(self.env['NNN_UPLOAD_HEALTH_DIR']).glob('*.json'):
                    try:
                        if file.stat().st_size>65536:continue
                        value=json.loads(file.read_text())
                        # Only success from a currently owned PID proves this run.
                        if value.get('pid') not in {p.pid for p in self.procs.values()}:continue
                        health.append({k:value.get(k) for k in ('source','pid','state','stage','accepted','symbols','last_success_at','last_failure_at','last_heartbeat_at','updated_at')})
                    except (OSError,ValueError):continue
                jobs=[]
                for name,value in self.states.items():
                    row=dict(value);spec=self.workers[name]
                    row['collection_expected']=self.collection_expected(name,now)
                    row.update(kind='website' if name=='website' else 'bundled_worker',timezone='Asia/Shanghai',
                               run_at=', '.join(spec.get('daily_at',[])),model_version='preserved-baseline')
                    matching=[item for item in health if item.get('pid')==value.get('pid')]
                    if matching:
                        latest=max(matching,key=lambda item:item.get('updated_at') or '')
                        for key in ('stage','last_success_at','last_failure_at','accepted'):row[key]=latest.get(key)
                        row['business_state']=latest.get('state')
                    jobs.append(row)
                ready,readiness_problems=self.business_readiness(now,health)
                collecting=any(self.collection_expected(name,now) for name in self.workers)
                emit({'type':'status','engine':'bundled_business',
                      'state':('running' if collecting else 'scheduled_idle') if ready else 'degraded',
                      'collection_expected':collecting,
                      'record_only':False,'jobs':jobs,'ready':ready,'readiness_problems':readiness_problems,'upload_health':health,
                      'readiness_note':'Readiness checks owned processes, website health and current-process ACK freshness inside the baseline monitoring windows.'})
        finally:self.shutdown()


def main():
    if sys.argv[1:]==['--runtime-check']:
        import ssl
        certificates=ssl.create_default_context().cert_store_stats()['x509_ca']
        assert certificates>0 and os.environ.get('SSL_CERT_FILE')==str(BASE/'ca-bundle.pem')
        emit({'runtime_check':True,'timezone':str(ZoneInfo('Asia/Shanghai')),'ca_certificates':certificates})
        return
    if len(sys.argv)>1 and sys.argv[1].endswith('.py'):
        worker(sys.argv[1],sys.argv[2:]);return
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-root',required=True)
    parser.add_argument('--config',required=True)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    root=Path(args.data_root).resolve();root.mkdir(parents=True,exist_ok=True)
    os.umask(0o077)
    with open(root/'component.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        config=read_config(args.config,root)
        if config.get('website',{}).get('enabled',True):
            address=config.get('environment',{}).get('HTTP_ADDR','127.0.0.1:8080')
            host,port=address.rsplit(':',1)
            if host not in ('127.0.0.1','localhost'):raise ValueError('website must bind loopback')
            with socket.socket() as probe:
                # Match the Go listener's restart semantics: TIME_WAIT is not
                # an active owner. A live listener still prevents this bind.
                probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                probe.bind(('127.0.0.1',int(port)))
        if args.check:emit({'valid':True,'worker_ids':[x['id'] for x in config['workers']]});return
        Supervisor(root,config).run()

if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Avoid accidentally echoing configuration or credentials through errors.
        emit({'type':'fatal','error_type':type(exc).__name__,'errno':getattr(exc,'errno',None),'message':'component failed; inspect private configuration and local component logs'})
        raise SystemExit(2)

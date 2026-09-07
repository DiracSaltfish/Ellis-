import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import supervisor as sup

class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'source';(self.source/'scripts').mkdir(parents=True)
        self.cfg={'schema_version':1,'mode':'live','website':{'enabled':False},
                  'environment':{'NNN_SERVER_URL':'http://127.0.0.1:1','NNN_UPLOAD_TOKEN':'fixture-token'},
                  'workers':[{'id':'sina'}]}
        (self.source/'scripts'/'sina_ws_uploader.py').write_text('import time\ntime.sleep(60)\n')
    def test_config_rejects_world_readable_secrets_and_duplicate_writers(self):
        file=self.root/'private.json';file.write_text(json.dumps(self.cfg));file.chmod(0o644)
        with self.assertRaises(ValueError):sup.read_config(file,self.root)
        file.chmod(0o600);self.assertEqual(sup.read_config(file,self.root),self.cfg)
        self.cfg['workers']=[{'id':'xop-family'},{'id':'xop-smart'}];file.write_text(json.dumps(self.cfg))
        with self.assertRaises(ValueError):sup.read_config(file,self.root)
    def test_worker_is_owned_and_stops(self):
        service=sup.Supervisor(self.root,self.cfg,self.source);service.launch('sina');proc=service.procs['sina']
        time.sleep(.2);self.assertIsNone(proc.poll());service.shutdown();self.assertIsNotNone(proc.poll())
    def test_file_only_website_matches_loopback_mirror_without_fake_mysql(self):
        self.cfg['website']={'enabled':True,'persistence':'file_only'}
        self.cfg['workers']=[{'id':'website'}]
        self.cfg['environment'].update(HTTP_ADDR='127.0.0.1:8080',ENABLE_UPLOAD_QUOTES='false',UPLOAD_TOKEN='fixture',DATA_VIEW_TOKEN='fixture',NAV_SETTINGS_TOKEN='fixture')
        path=self.root/'private.json';path.write_text(json.dumps(self.cfg));path.chmod(0o600)
        self.assertEqual(sup.read_config(path,self.root),self.cfg)
        self.assertEqual(sup.Supervisor(self.root,self.cfg,self.source).env['MYSQL_DSN'],'')
        for name,value in [('HTTP_ADDR','0.0.0.0:8080'),('ENABLE_UPLOAD_QUOTES','true')]:
            bad=json.loads(json.dumps(self.cfg));bad['environment'][name]=value;path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):sup.read_config(path,self.root)
    def test_source_tampering_is_rejected(self):
        run=sup.stage(self.root,self.source);script=run/'scripts'/'sina_ws_uploader.py';script.write_text('raise AssertionError()')
        with patch.dict(os.environ,{'MACHOME_UPLOAD_RUN_ROOT':str(run)}):
            with self.assertRaises(ValueError):sup.worker(str(script),[])
    def test_paths_stay_in_private_root_and_secrets_not_in_status(self):
        service=sup.Supervisor(self.root,self.cfg,self.source)
        self.assertEqual(service.env['HOME'],str(self.root));self.assertNotIn('NNN_UPLOAD_TOKEN',service.states)
        self.assertTrue(service.env['MINUTE_HISTORY_DIR'].startswith(str(self.root)))
        self.assertEqual(service.env['NNN_PRIVATE_SERVER'],self.cfg['environment']['NNN_SERVER_URL'])
    def test_parent_eof_stops_worker_group(self):
        code='import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import supervisor as s;root=Path(sys.argv[2]);s.Supervisor(root,s.read_config(root/"private.json",root),Path(sys.argv[3])).run()'
        file=self.root/'private.json';file.write_text(json.dumps(self.cfg));file.chmod(0o600)
        proc=subprocess.Popen([sys.executable,'-c',code,str(Path(sup.__file__).parent),str(self.root),str(self.source)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(json.loads(proc.stdout.readline())['type'],'hello')
            state=json.loads(proc.stdout.readline());pid=state['jobs'][0]['pid'];os.kill(pid,0)
            proc.stdin.close();proc.wait(timeout=12)
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
            proc.stdout.close()
    def test_readiness_requires_fresh_ack_from_current_process(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        service=sup.Supervisor(self.root,self.cfg,self.source)
        service.states['sina']={'id':'sina','state':'running','pid':123,'started_at':'2026-09-07T09:20:00+08:00'}
        now=datetime(2026,9,7,9,21,tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertFalse(service.business_readiness(now,[])[0])
        valid={'pid':123,'state':'ok','last_success_at':'2026-09-07T09:20:59+08:00'}
        self.assertTrue(service.business_readiness(now,[valid])[0])
        valid['last_success_at']='2026-09-07T09:19:59+08:00'
        self.assertFalse(service.business_readiness(now,[valid])[0])
        valid.update(pid=456,last_success_at='2026-09-07T09:20:59+08:00')
        self.assertFalse(service.business_readiness(now,[valid])[0])
    def test_schedule_marks_only_completed_runs(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        service=sup.Supervisor(self.root,self.cfg,self.source);service.workers['sina']['daily_at']=['09:10','14:50']
        now=datetime(2026,9,7,9,11,tzinfo=ZoneInfo('Asia/Shanghai'))
        key=service.due('sina',now);self.assertTrue(key);self.assertNotIn(key,service.completed)
        service.completed[key]='done';self.assertFalse(service.due('sina',now))
    def test_crash_burst_recovers_after_cooldown(self):
        # Accelerate only the supervisor's retry clock, not subprocess waits.
        (self.source/'scripts'/'sina_ws_uploader.py').write_text('raise SystemExit(1)\n')
        code='import sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import supervisor as s;original=time.monotonic;s.time.monotonic=lambda:original()*100;root=Path(sys.argv[2]);s.Supervisor(root,s.read_config(root/"private.json",root),Path(sys.argv[3])).run()'
        file=self.root/'private.json';file.write_text(json.dumps(self.cfg));file.chmod(0o600)
        proc=subprocess.Popen([sys.executable,'-c',code,str(Path(sup.__file__).parent),str(self.root),str(self.source)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        seen=set();fault_seen=False;resumed=False;deadline=time.monotonic()+12
        try:
            while time.monotonic()<deadline:
                message=json.loads(proc.stdout.readline())
                for job in message.get('jobs',[]):
                    if job['state']=='fault':fault_seen=True
                    if job.get('pid'):
                        seen.add(job['pid'])
                        if fault_seen and len(seen)>=5:resumed=True
                if resumed:break
            self.assertTrue(fault_seen);self.assertTrue(resumed)
        finally:
            proc.stdin.close();proc.wait(timeout=12);proc.stdout.close()
    def test_silver_break_does_not_require_fresh_upload(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        self.cfg['workers']=[{'id':'silver'}]
        service=sup.Supervisor(self.root,self.cfg,self.source)
        service.states['silver']={'state':'running','pid':123,'started_at':'2026-09-07T09:00:00+08:00'}
        for hour,minute in ((10,20),(11,45),(13,20)):
            self.assertTrue(service.business_readiness(datetime(2026,9,7,hour,minute,tzinfo=ZoneInfo('Asia/Shanghai')),[])[0])
        self.assertFalse(service.business_readiness(datetime(2026,9,7,13,31,tzinfo=ZoneInfo('Asia/Shanghai')),[])[0])
if __name__=='__main__':unittest.main()

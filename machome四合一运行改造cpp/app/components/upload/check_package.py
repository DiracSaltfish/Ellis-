#!/usr/bin/env python3
"""Run all packaged worker parsers with old source/Python and networking denied."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from supervisor import ALLOW,stage
p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
bundle=a.bundle.resolve();executable=bundle/'machome-upload-component';results=[]
profile='(version 1)(allow default)(deny network*)(deny file-read* (subpath "/Users/ellis/newnavnav") (subpath "/Users/ellis/NAVNAV") (subpath "/Users/ellis/miniconda3") (subpath "/tmp/machome-upload-buildenv") (subpath "/private/tmp/machome-upload-buildenv"))'
with tempfile.TemporaryDirectory(prefix='hub-frozen-check-',dir='/private/tmp') as temp:
    root=Path(temp);run=stage(root,bundle/'_internal'/'business')
    for name,(script,args) in ALLOW.items():
        env={'PATH':'/usr/bin:/bin','HOME':str(root),'MACHOME_UPLOAD_RUN_ROOT':str(run),
             'NNN_SERVER_URL':'http://127.0.0.1:1','NNN_UPLOAD_TOKEN':'offline-fixture'}
        command=['/usr/bin/sandbox-exec','-p',profile,str(executable),str(run/'scripts'/script),*args,'--help']
        result=subprocess.run(command,env=env,cwd=run,capture_output=True,text=True,timeout=20)
        results.append({'worker':name,'exit_code':result.returncode,'error':result.stderr[-2000:] if result.returncode else ''})
    # Run real business state-machine regressions inside the frozen interpreter,
    # with networking forbidden and the source Python environment inaccessible.
    result=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,str(executable),str(run/'scripts'/'test_index_preopen.py')],
                          env=env,cwd=run,capture_output=True,text=True,timeout=30)
    results.append({'worker':'index-preopen-business-regression','exit_code':result.returncode,
                    'details':result.stderr[-3000:]})
runtime=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,str(executable),'--runtime-check'],
    env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=20)
results.append({'worker':'runtime-timezone-and-ca','exit_code':runtime.returncode,'error':runtime.stdout if runtime.returncode else '',
                'details':json.loads(runtime.stdout) if runtime.returncode==0 else {}})
a.report.write_text(json.dumps({'workers':results,'old_sources_denied':True,'external_python_denied':True,'network_denied':True},indent=2)+'\n')
print(json.dumps({'passed':all(x['exit_code']==0 for x in results),'workers':len(results)}))
raise SystemExit(0 if all(x['exit_code']==0 for x in results) else 1)

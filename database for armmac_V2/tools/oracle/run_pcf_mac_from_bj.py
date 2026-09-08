#!/usr/bin/env python3
"""Run Mac read-only assessment with remote protected credentials in memory only."""
import json
from pathlib import Path
import subprocess
import sys

SOURCE = '''import json,yaml
from pathlib import Path
c=yaml.safe_load(Path('/opt/galaxy-relay/config/config.yaml').read_text())['amazingdata']
e={}
for line in Path('/etc/galaxy-relay/relay.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);e[k.strip()]=v.strip().strip(chr(39)+chr(34))
print(json.dumps(dict(username=e[c['username_env']],password=e[c['password_env']],**c['hosts'][0])))
'''

def main():
    import shlex
    command='sudo -n -u galaxyrelay /opt/galaxy-relay/venv/bin/python -c '+shlex.quote(SOURCE)
    data=subprocess.run(['ssh','-o','BatchMode=yes','bj',command],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=20,check=True).stdout
    json.loads(data)
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('pcf_api_eval.py')),'--backend','mac','--credential-stdin'],input=data,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=180)
    data=b''
    if result.returncode:print(json.dumps({'error':'mac_probe_process_failed','exit_code':result.returncode}))
    else:print(result.stdout.decode().strip())

if __name__=='__main__':main()

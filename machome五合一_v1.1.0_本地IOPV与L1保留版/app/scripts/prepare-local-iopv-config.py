#!/usr/bin/env python3
"""Prepare a new config without overwriting production or altering other modules."""
import argparse,json,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True);p.add_argument('--port',type=int,default=18680);p.add_argument('--basis',choices=['midpoint','settlement_buy','settlement_sell'],default='midpoint');a=p.parse_args()
if not 1024<=a.port<=65535:p.error('invalid port')
src=Path(a.source).resolve();dst=Path(a.output).resolve()
if src==dst or dst.exists():p.error('output must be a new file')
d=json.loads(src.read_text());found=False
for m in d['modules']:
 if m['id']=='premium':
  found=True;m['settings'].update(local_iopv_enabled=True,local_iopv_basis=a.basis,iopv_port=a.port,iopv_listen=f'0.0.0.0:{a.port}',iopv_web_url=f'http://127.0.0.1:{a.port}/')
  m['settings'].pop('test_iopv_l1_address',None)
if not found:p.error('premium module missing')
fd=os.open(dst,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as f:json.dump(d,f,ensure_ascii=False,indent=2);f.write('\n')
print('Prepared config; production is unchanged:',dst)

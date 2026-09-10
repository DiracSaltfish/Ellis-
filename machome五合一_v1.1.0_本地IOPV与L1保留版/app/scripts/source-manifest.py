#!/usr/bin/env python3
"""Hash build inputs only, excluding generated files and private configuration."""
import hashlib
import json
from pathlib import Path
import sys
root=Path(sys.argv[1]).resolve();files=[]
for directory in ('src','modules','helpers','third_party','tests','scripts','deploy','resources'):
    files.extend(p for p in (root/directory).rglob('*') if p.is_file() and not any(x in p.parts for x in ('__pycache__','build','node_modules')))
for directory in ('tools','config'):
    files.extend(p for p in (root.parent/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
component=root/'components'/'upload'
files.extend(p for p in component.glob('*.py'))
business=component/'business'
for directory in ('cmd','internal','scripts','frontend'):
    files.extend(p for p in (business/directory).rglob('*') if p.is_file() and not any(x in p.relative_to(business).parts for x in ('__pycache__','node_modules','dist','.runtime')))
files.extend(p for p in (business/'go.mod',business/'go.sum',root/'CMakeLists.txt') if p.is_file())
iopv=root.parent/'services'/'iopv'
files.extend(p for p in iopv.rglob('*') if p.is_file() and not any(x in p.relative_to(iopv).parts for x in ('data','outputs','build','dist','__pycache__','.git')) and p.name!='config.local.json')
manifest={str(p.relative_to(root)) if p.is_relative_to(root) else "../"+str(p.relative_to(root.parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
wire=json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
if len(sys.argv)>2:Path(sys.argv[2]).write_bytes(wire+b'\n')
print(hashlib.sha256(wire).hexdigest())

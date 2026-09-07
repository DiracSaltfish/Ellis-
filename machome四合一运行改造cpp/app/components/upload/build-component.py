#!/usr/bin/env python3
"""Build the internal business package; never starts workers or deploys."""
import argparse
from pathlib import Path
import subprocess
import sys
import shutil
import json
import hashlib
import ssl
import importlib.metadata
p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
root=Path(__file__).resolve().parent;output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
subprocess.run(['go','test','./...'],cwd=root/'business',check=True)
subprocess.run(['go','build','-trimpath','-o',str(root/'machome-upload-web'),'./cmd/web'],cwd=root/'business',check=True)
subprocess.run(['npm','ci'],cwd=root/'business'/'frontend',check=True)
subprocess.run(['npm','run','build'],cwd=root/'business'/'frontend',check=True)
# OpenSSL defaults from a build-time Python must not leak into the runtime.
ca=ssl.get_default_verify_paths().cafile
if not ca or not Path(ca).is_file():raise RuntimeError('Build interpreter has no verified CA bundle')
shutil.copyfile(ca,output/'ca-bundle.pem')
# Only Python scripts + static data + built web assets enter the executable.
assets=output/'assets';scripts=assets/'business'/'scripts';scripts.mkdir(parents=True,exist_ok=True)
for src in (root/'business'/'scripts').glob('*.py'):shutil.copyfile(src,scripts/src.name)
if (root/'business'/'scripts'/'data').exists():shutil.copytree(root/'business'/'scripts'/'data',scripts/'data',dirs_exist_ok=True)
shutil.copytree(root/'business'/'frontend'/'dist',assets/'business'/'frontend'/'dist',dirs_exist_ok=True)
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir',
    '--name','machome-upload-component','--distpath',str(output/'dist'),'--workpath',str(output/'work'),
    '--specpath',str(output),'--collect-all','ib_insync','--hidden-import','zoneinfo',
    '--collect-submodules','urllib','--hidden-import','html.parser','--hidden-import','xml.etree.ElementTree',
    '--hidden-import','concurrent.futures','--hidden-import','sqlite3','--hidden-import','csv',
    '--hidden-import','gzip','--hidden-import','decimal','--hidden-import','email.utils',
    '--hidden-import','unittest.mock',
    '--add-data',str(output/'ca-bundle.pem')+':.','--add-data',str(assets/'business')+':business','--add-binary',str(root/'machome-upload-web')+':.',
    str(root/'supervisor.py')],check=True)
bundle=output/'dist'/'machome-upload-component'
(bundle/'runtime-build.json').write_text(json.dumps({'python':sys.version,'packages':{name:importlib.metadata.version(name) for name in ('pyinstaller','ib_insync','numpy')},'ca_bundle_sha256':hashlib.sha256((output/'ca-bundle.pem').read_bytes()).hexdigest()},indent=2)+'\n')
hashes={str(f.relative_to(bundle)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(bundle.rglob('*')) if f.is_file() and not f.is_symlink()}
(output/'package-sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
print(bundle)

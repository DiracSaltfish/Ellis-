#!/usr/bin/env python3
"""Import an offline export into a NEW Hub Upload root. No remote operations.

File formats and bytes are retained; MySQL stays MySQL (DSN comes from private
configuration). Refuses existing destinations, symlinks, unstable source files.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
p=argparse.ArgumentParser();p.add_argument('--export-dir',type=Path,required=True)
p.add_argument('--destination',type=Path,required=True);p.add_argument('--private-config',type=Path,required=True)
a=p.parse_args();source=a.export_dir.resolve();destination=a.destination.absolute()
if destination.exists():raise SystemExit('Destination exists; import into a new candidate root.')
config=json.loads(a.private_config.read_text())
if config.get('schema_version')!=1:raise SystemExit('Unsupported private config schema.')
mappings={'snapshots':'website/snapshots','scripts/.runtime':'business-run/scripts/.runtime',
          'scripts/quote_store':'business-run/scripts/quote_store','scripts/intraday_minute_store':'business-run/scripts/intraday_minute_store'}
destination.parent.mkdir(parents=True,exist_ok=True)
stage=Path(tempfile.mkdtemp(prefix='upload-import-',dir=destination.parent));manifest={}
try:
    for old,new in mappings.items():
        folder=source/old
        if not folder.exists():continue
        if folder.is_symlink():raise ValueError('Export symlinks are forbidden')
        for file in sorted(folder.rglob('*')):
            if file.is_symlink():raise ValueError('Export symlinks are forbidden')
            if not file.is_file():continue
            stat=file.stat();digest=hashlib.sha256(file.read_bytes()).hexdigest()
            target=stage/new/file.relative_to(folder);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(file,target);target.chmod(0o600)
            if file.stat().st_mtime_ns!=stat.st_mtime_ns or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                raise ValueError('Source changed during import; use a stable offline export')
            manifest[str(target.relative_to(stage))]={'bytes':stat.st_size,'sha256':digest}
    (stage/'config').mkdir(exist_ok=True)
    private=stage/'config'/'upload-business.json';private.write_text(json.dumps(config,ensure_ascii=False,indent=2));private.chmod(0o600)
    (stage/'migration-manifest.json').write_text(json.dumps({'format':'baseline-bytes-preserved-v1','files':manifest},ensure_ascii=False,indent=2))
    os.rename(stage,destination)
    print(json.dumps({'imported_files':len(manifest),'destination':str(destination),'database':'original MySQL via private DSN; no SQLite conversion','started_services':False}))
except BaseException:
    shutil.rmtree(stage);raise

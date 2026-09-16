"""Stage an isolated build using live web assets and the local accepted ledger.

Does not deploy or reinterpret the ledger. Keep the current live symbol universe.
"""
import csv
import datetime
import hashlib
import io
import json
import shutil
import sys
import urllib.request
from pathlib import Path

stage = Path(sys.argv[1]).resolve()
root = Path(__file__).resolve().parents[2]
source = root / 'machome五合一_v1.1.0_本地IOPV与L1保留版/services/iopv'
build = stage / 'source'
shutil.copytree(source, build, ignore=shutil.ignore_patterns('outputs', 'third_party', '.DS_Store'), dirs_exist_ok=True)
base = 'http://192.168.1.113:18680/'
for name in ['index.html', 'app.js', 'style.css', 'ledger.json']:
    raw = urllib.request.urlopen(base + name, timeout=15).read()
    (stage / ('online-' + name)).write_bytes(raw)
    (build / 'web' / name).write_bytes(raw)
old = json.loads((stage / 'online-ledger.json').read_text())
ledger_path = root / '港股通逐标的分析/验收台账/全量核心台账.csv'
raw = ledger_path.read_bytes()
rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
index = {r['code'] + '.' + r['market']: {k: v for k, v in r.items() if k not in ['prospectus_file', 'prospectus_text_file']} for r in rows}
assert len(index) == len(rows)
records = {symbol: index.get(symbol) for symbol in old['records']}
assert all(records.values()), 'Previously visible symbols must remain covered'
new = dict(old, records=records, matched=len(records), total=len(records), source_sha256=hashlib.sha256(raw).hexdigest(), imported_at=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat())
(build / 'web/ledger.json').write_text(json.dumps(new, ensure_ascii=False, indent=2))
addition = next(line for line in (source / 'web/app.js').read_text().splitlines() if line.startswith('groups.splice(1,0,'))
app = (build / 'web/app.js').read_text()
assert addition not in app
assert app.count('const label=v=>') == 1
app = app.replace('const label=v=>', addition + '\nconst label=v=>')
(build / 'web/app.js').write_text(app)
print(json.dumps({'build': str(build), 'local_rows': len(rows), 'visible_records': len(records), 'source_sha256': new['source_sha256']}, ensure_ascii=False))

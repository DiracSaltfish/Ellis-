"""Run on machome with a staged helper and expected hash arguments; rollback on failure."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

stage = Path(sys.argv[1])
expected_old, expected_new, expected_source = sys.argv[2:5]
app = Path('/Users/ellis/Applications/Machome 四合一运行中心.app')
helper = app / 'Contents/Helpers/machome-iopv-server'
manifest = app / 'Contents/Resources/iopv-webfix-manifest.json'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(helper) == expected_old, 'Live binary changed; refusing concurrent overwrite'
assert sha(stage / 'updated-server') == expected_new
def get(path):
    return json.load(urllib.request.urlopen('http://127.0.0.1:18680' + path, timeout=10))
old_health = get('/api/v1/health')
old_ledger = get('/ledger.json')
history_path = '/api/v1/minutes?symbol=513090.SH&date=2026-09-09'
old_history = get(history_path)
backup = Path('/Users/ellis/Library/Application Support/MachomeHub/backups') / time.strftime('ledger-sync-%Y%m%d-%H%M%S')
backup.mkdir()
shutil.copy2(helper, backup / helper.name)
shutil.copy2(manifest, backup / manifest.name)
(backup / 'ledger.json').write_text(json.dumps(old_ledger, ensure_ascii=False))
def stop():
    allowed = {str(helper), str(app / 'Contents/MacOS/../Helpers/machome-iopv-server')}
    for line in subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True).splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1] in allowed:
            os.kill(int(parts[0]), signal.SIGTERM)
try:
    shutil.copy2(stage / 'updated-server', helper)
    m = json.loads(manifest.read_text())
    m.update(patch='ledger-suspension-sync-20260912', helper_sha256=expected_new, ledger_source_sha256=expected_source)
    m['changes'] = 'Latest accepted ledger plus seven suspension and subscription/redemption consideration fields; existing live UI preserved'
    for key in ['assets', 'web_assets']:
        m[key] = {name: sha(stage / name) for name in ['index.html', 'app.js', 'style.css', 'ledger.json']}
    manifest.write_text(json.dumps(m, ensure_ascii=False, indent=2))
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
    stop()
    for attempt in range(45):
        try:
            health = get('/api/v1/health')
            data = get('/ledger.json')
            if health['run_id'] != old_health['run_id'] and data['source_sha256'] == expected_source:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Service did not return with new ledger')
    assert set(data['records']) == set(old_ledger['records'])
    assert get(history_path) == old_history, 'History response changed'
    result = {'backup': str(backup), 'source_sha256': data['source_sha256'], 'matched': data['matched'], 'run_id': health['run_id'], 'history_rows': len(old_history)}
    (backup / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
except BaseException:
    shutil.copy2(backup / helper.name, helper)
    shutil.copy2(backup / manifest.name, manifest)
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
    stop()
    raise

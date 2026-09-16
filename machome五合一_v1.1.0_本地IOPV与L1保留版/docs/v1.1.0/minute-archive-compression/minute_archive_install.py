#!/usr/bin/env python3
"""Run on machome from a staged payload directory. Only patches Upload scripts.

The caller supplies expected.json with before/after script hashes. Tests run in
the existing frozen runtime before the Sina worker is restarted. No site deploy.
"""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import shutil
import signal
import subprocess
import time

stage = Path(__file__).resolve().parent
expected = json.loads((stage / 'expected.json').read_text())
home = Path('/Users/ellis')
app = home / 'Applications/Machome 四合一运行中心.app'
resources = app / 'Contents/Resources'
component = resources / 'upload/machome-upload-component'
source = component / '_internal/business/scripts'
upload = home / 'Library/Application Support/MachomeHub/data/upload'
run = upload / 'business-run'
runtime = run / 'scripts'
binary = component / 'machome-upload-component'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
names = list(expected['after'])
assert len(names) == len(set(names)) and all(Path(n).name == n and n.endswith('.py') for n in names)
for name in names:
    assert sha(stage / name) == expected['after'][name], name
    for folder in [source, runtime]:
        p = folder / name
        assert not p.is_symlink()
        before = expected['before'].get(name)
        assert (sha(p) if p.exists() else None) == before, 'Live baseline changed: ' + str(p)

state_path = upload / 'component-processes.json'
before_state = json.loads(state_path.read_text())
old_pid = next(j['pid'] for j in before_state['jobs'] if j['id'] == 'sina')
stamp = time.strftime('%Y%m%d-%H%M%S')
backup = home / 'Library/Application Support/MachomeHub/backups' / ('minute-archive-' + stamp)
backup.mkdir(parents=True)
label = 'com.newnavnav.minute-archive-compression'
plist_path = home / 'Library/LaunchAgents' / (label + '.plist')
assert not plist_path.exists(), 'Existing schedule requires an explicit update'
source_manifest = resources / 'source_manifest.json'
patch_manifest = resources / 'minute-archive-patch.json'
hashes_path = run / 'script-hashes.json'
targets = [folder / n for folder in [source, runtime] for n in names]
targets += [source_manifest, patch_manifest, hashes_path]
saved = []
for i, p in enumerate(targets):
    item = {'path': str(p), 'backup': str(backup / str(i)), 'existed': p.exists()}
    if p.exists():
        shutil.copy2(p, item['backup'])
    saved.append(item)
(backup / 'restore-files.json').write_text(json.dumps(saved, indent=2))


def replace_bytes(path, data, mode=0o600):
    tmp = path.with_name(path.name + '.minute-update.tmp')
    with tmp.open('wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(mode)
    os.replace(tmp, path)


def stop_sina(pid):
    cmd = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True)
    assert str(runtime / 'sina_ws_uploader.py') in cmd, 'PID no longer belongs to Sina worker'
    os.kill(pid, signal.SIGTERM)


domain = 'gui/' + str(os.getuid())
env = os.environ.copy()
env.update(MACHOME_UPLOAD_RUN_ROOT=str(run), PYTHONTZPATH='/usr/share/zoneinfo')
loaded = False
restarted = False
try:
    # Dependencies first; stage all source files as well so a Hub restart keeps this patch.
    order = sorted(names, key=lambda n: n == 'sina_ws_uploader.py')
    for name in order:
        for folder in [source, runtime]:
            replace_bytes(folder / name, (stage / name).read_bytes(), 0o644 if folder == source else 0o600)
    hashes = json.loads(hashes_path.read_text())
    hashes.update(expected['after'])
    replace_bytes(hashes_path, json.dumps(hashes, indent=2).encode())
    manifest = json.loads(source_manifest.read_text())
    for name, digest in expected['after'].items():
        manifest['components/upload/business/scripts/' + name] = digest
    replace_bytes(source_manifest, json.dumps(manifest, ensure_ascii=False, indent=2).encode(), 0o644)
    replace_bytes(patch_manifest, json.dumps({'patch': 'minute-archive-gzip-v1', 'time': stamp,
                  'script_sha256': expected['after'], 'backup': str(backup), 'keep_days': 7}, indent=2).encode(), 0o644)
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
    subprocess.run(['codesign', '--verify', str(app)], check=True)
    for test in ['test_minute_archive_compression.py', 'test_intraday_minute_store.py', 'test_ws_capture_timing.py']:
        result = subprocess.run([str(binary), str(runtime / test)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        (backup / (test + '.log')).write_text(result.stdout)
        print('REMOTE_TEST', test, result.returncode, flush=True)
        if result.returncode:
            raise RuntimeError(result.stdout[-6000:])
    stop_sina(old_pid)
    restarted = True
    new_pid = None
    for _ in range(45):
        state = json.loads(state_path.read_text())
        job = next(j for j in state['jobs'] if j['id'] == 'sina')
        if job.get('pid') not in (None, old_pid) and job.get('state') == 'running':
            new_pid = job['pid']
            break
        time.sleep(1)
    if new_pid is None:
        raise RuntimeError('Sina worker did not restart')
    log = upload / 'logs/minute-archive-compression.log'
    plist = {'Label': label, 'ProgramArguments': [str(binary), str(runtime / 'minute_archive_compression.py'),
             '--root', str(runtime / 'intraday_minute_store'), '--keep-days', '7', '--apply'],
             'EnvironmentVariables': {'MACHOME_UPLOAD_RUN_ROOT': str(run), 'PYTHONTZPATH': '/usr/share/zoneinfo',
                                      'TZ': 'Asia/Shanghai', 'PYTHONUNBUFFERED': '1'},
             'StartCalendarInterval': {'Hour': 18, 'Minute': 10}, 'ProcessType': 'Background',
             'Nice': 10, 'StandardOutPath': str(log), 'StandardErrorPath': str(log)}
    replace_bytes(plist_path, plistlib.dumps(plist))
    subprocess.run(['plutil', '-lint', str(plist_path)], check=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(plist_path)], check=True)
    loaded = True
    result = {'status': 'deployed', 'backup': str(backup), 'old_sina_pid': old_pid, 'new_sina_pid': new_pid,
              'schedule': str(plist_path), 'scripts': expected['after'], 'remote_tests': 27,
              'previous_jobs': {j['id']: j.get('pid') for j in before_state['jobs']}}
    (backup / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)
except BaseException:
    if loaded:
        subprocess.run(['launchctl', 'bootout', domain + '/' + label], check=False)
    if plist_path.exists():
        plist_path.unlink()
    for item in saved:
        p = Path(item['path'])
        if item['existed']:
            shutil.copy2(item['backup'], p)
        elif p.exists():
            p.unlink()
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
    if restarted:
        current = next(j for j in json.loads(state_path.read_text())['jobs'] if j['id'] == 'sina')
        if current.get('pid'):
            stop_sina(current['pid'])
    raise

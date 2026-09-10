"""Apply only the reviewed management-UI patch. Backend processes stay running."""
import hashlib, json, os, shutil, signal, subprocess, tarfile, time
from pathlib import Path

root = Path.home() / 'Library/Application Support/MachomeHub'
checkpoint = root / 'ui-update-20260907'
app = Path.home() / 'Applications/Machome 四合一运行中心.app'
ui_relative = 'Contents/MacOS/Machome 四合一运行中心'
expected_paths = {ui_relative, 'Contents/_CodeSignature/CodeResources',
                  'Contents/Resources/ui_release.json', 'Contents/Resources/ui_source_manifest.json'}
manifest = json.loads((checkpoint / 'patch-files.json').read_text())
assert set(manifest) == expected_paths
before = json.loads((checkpoint / 'before-files.json').read_text())
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
current = {str(f.relative_to(app)): digest(f) for f in app.rglob('*') if f.is_file() and not f.is_symlink()}
assert current == before, 'Installed bundle changed since preflight; do not overwrite'
baseline = json.loads((checkpoint / 'before.json').read_text())
assert digest(root / 'config/modules.json') == baseline['config_sha256']

stage = checkpoint / 'staged'
stage.mkdir(exist_ok=True)
with tarfile.open(checkpoint / 'ui-patch.tgz') as archive:
    for member in archive.getmembers():
        if member.isdir():
            assert '..' not in Path(member.name).parts and not Path(member.name).is_absolute()
        else:
            assert member.isfile() and member.name in expected_paths, member.name
    archive.extractall(stage)
for rel, sha in manifest.items(): assert digest(stage / rel) == sha, rel

backup = checkpoint / 'before.app'
if backup.exists():
    saved = {str(f.relative_to(backup)): digest(f) for f in backup.rglob('*') if f.is_file() and not f.is_symlink()}
    assert saved == before, 'Existing backup must match the verified baseline'
else:
    subprocess.run(['/usr/bin/ditto', str(app), str(backup)], check=True)
subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(backup)], check=True)

def processes():
    result = {}
    for line in subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True, errors='replace').splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2: result[int(parts[0])] = parts[1]
    return result

live = processes()
ui_pids = [pid for pid, program in live.items() if program == str(app / ui_relative)]
backend_pids = {pid: program for pid, program in live.items()
                if program.startswith(str(app) + '/') and program != str(app / ui_relative)}
assert any(p.endswith('/machome-hub-agent') for p in backend_pids.values()), 'Agent must already be running'
# Exact UI executable only; never terminate by app-name prefix.
for pid in ui_pids: os.kill(pid, signal.SIGTERM)
until = time.monotonic() + 8
while any(pid in processes() for pid in ui_pids) and time.monotonic() < until: time.sleep(0.2)
assert not any(pid in processes() for pid in ui_pids), 'UI did not exit'

def install(source, rel):
    dest = app / rel
    temporary = dest.with_name(dest.name + '.ui-update-new')
    shutil.copy2(source / rel, temporary)
    os.replace(temporary, dest)

try:
    for rel in sorted(expected_paths): install(stage, rel)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
except Exception:
    for rel in expected_paths:
        if (backup / rel).exists(): install(backup, rel)
        elif (app / rel).exists(): (app / rel).unlink()
    subprocess.run(['/usr/bin/open', '-a', str(app), '--args', '--config', str(root / 'config/modules.json')], check=True)
    raise

after = {str(f.relative_to(app)): digest(f) for f in app.rglob('*') if f.is_file() and not f.is_symlink()}
changed = [rel for rel in sorted(before.keys() | after.keys()) if before.get(rel) != after.get(rel)]
assert set(changed) == expected_paths
for rel, sha in manifest.items(): assert after[rel] == sha
now = processes()
assert all(now.get(pid) == program for pid, program in backend_pids.items()), 'Unexpected backend process change'
assert digest(root / 'config/modules.json') == baseline['config_sha256']
subprocess.run(['/usr/bin/open', '-a', str(app),
                '--args', '--config', str(root / 'config/modules.json')], check=True)
receipt = {'at': time.time(), 'changed_files': changed, 'old_ui_pids': ui_pids,
           'preserved_backend_pids': backend_pids, 'signature_verified': True,
           'config_unchanged': True, 'backup': str(backup), 'installed_hashes': manifest}
(checkpoint / 'deployment-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
print(json.dumps(receipt, ensure_ascii=False))

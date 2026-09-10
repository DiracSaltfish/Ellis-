"""Manual rollback for this UI release only. Never stops backend services."""
from pathlib import Path
import hashlib, json, os, shutil, signal, subprocess, time

r = Path.home() / 'Library/Application Support/MachomeHub/ui-update-20260907'
app = Path.home() / 'Applications/Machome 四合一运行中心.app'
backup = r / 'before.app'
receipt = json.loads((r / 'deployment-receipt.json').read_text())
for rel, expected in receipt['installed_hashes'].items():
    assert hashlib.sha256((app / rel).read_bytes()).hexdigest() == expected, 'A later release is installed; stop'
ui = str(app / 'Contents/MacOS/Machome 四合一运行中心')
for line in subprocess.check_output(['ps', '-axo', 'pid=,comm='], text=True, errors='replace').splitlines():
    row = line.strip().split(None, 1)
    if len(row) == 2 and row[1] == ui: os.kill(int(row[0]), signal.SIGTERM)
time.sleep(1)
for rel in receipt['changed_files']:
    source = backup / rel
    dest = app / rel
    if source.exists():
        temporary = dest.with_name(dest.name + '.rollback-new')
        shutil.copy2(source, temporary)
        os.replace(temporary, dest)
    elif dest.exists(): dest.unlink()
subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
subprocess.run(['open', '-a', str(app), '--args', '--config',
                str(r.parent / 'config/modules.json')], check=True)
print('Previous UI restored; backend service files were not replaced.')

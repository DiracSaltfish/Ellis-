"""Send an explicit PowerShell script via UTF-16LE EncodedCommand."""
import argparse,base64,subprocess,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('script',type=Path);p.add_argument('--only-date',default='');a=p.parse_args()
if a.only_date:assert len(a.only_date)==8 and a.only_date.isdigit() and a.only_date.startswith('2025')
def ps(s):
 encoded=base64.b64encode(s.encode('utf-16le')).decode('ascii')
 return subprocess.call(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','win1','powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -EncodedCommand '+encoded])
root='F:/港股通ETF轧差套利分析/win1_2025'
assert ps(f"New-Item -ItemType Directory -Force -Path '{root}' | Out-Null")==0
deploy=a.script.parent/'deploy';deploy.mkdir(exist_ok=True)
(deploy/'extract_targets.ps1').write_text(a.script.read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
universe=json.loads((a.script.parent.parent.parent/'inputs/universe.json').read_text())
(deploy/'universe.json').write_text(json.dumps(list(universe),ensure_ascii=False),encoding='utf-8')
for name in ['extract_targets.ps1','universe.json']:
 assert subprocess.call(['scp',str(deploy/name),f'win1:{root}/{name}'])==0
raise SystemExit(ps(f"& '{root}/extract_targets.ps1'"+(f" -OnlyDate {a.only_date}" if a.only_date else '')))

"""Read-only Windows SSH inventory. No recursion, extraction, execution of files, or deletion."""
from pathlib import Path,PureWindowsPath
import argparse,base64,datetime,json,subprocess

def command(path):
 p=PureWindowsPath(path)
 if p.drive.upper()!='F:' or not p.is_absolute() or '..' in p.parts:
  raise ValueError('Only absolute F-drive paths without parent traversal are allowed')
 literal=str(p).replace("'","''")
 ps="""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$root = '__PATH__'
$items = @(Get-ChildItem -LiteralPath $root -Force | Select-Object -First 2001)
$tools = @('python','py','7z','7za','tar','cmake','cl') | ForEach-Object {
  $c = Get-Command $_ -ErrorAction SilentlyContinue
  if ($c) { [pscustomobject]@{name=$_;path=$c.Source} }
}
[pscustomobject]@{
  timestamp=(Get-Date).ToUniversalTime().ToString('o')
  machine=$env:COMPUTERNAME
  os=$env:OS
  powershell_version=$PSVersionTable.PSVersion.ToString()
  root=$root
  drive=@(Get-PSDrive -Name F | Select-Object Name,Root,Used,Free)
  tools=@($tools)
  truncated=($items.Count -gt 2000)
  entries=@($items | Select-Object -First 2000 | ForEach-Object {
    [pscustomobject]@{name=$_.Name;full_path=$_.FullName;is_directory=$_.PSIsContainer;length=$_.Length;last_write_utc=$_.LastWriteTimeUtc.ToString('o');attributes=$_.Attributes.ToString()}
  })
} | ConvertTo-Json -Depth 6 -Compress
""".replace('__PATH__',literal)
 encoded=base64.b64encode(ps.encode('utf-16le')).decode('ascii')
 return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ConnectionAttempts=1','win1',f'powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand {encoded}']

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--path',default='F:\\');ap.add_argument('--output',type=Path,default=Path(__file__).with_name('windows_inventory.json'));a=ap.parse_args()
 receipt={'requested_path':a.path,'requested_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'pending'}
 try:
  r=subprocess.run(command(a.path),capture_output=True,timeout=45)
  if r.returncode:raise RuntimeError(r.stderr.decode('utf-8',errors='replace').strip())
  receipt.update(status='read_success',inventory=json.loads(r.stdout.decode('utf-8-sig')))
 except (RuntimeError,subprocess.TimeoutExpired) as e:
  receipt.update(status='connection_or_command_failed',error=str(e))
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
 print(json.dumps(receipt,ensure_ascii=False,indent=2))
 if receipt['status']!='read_success':raise SystemExit(1)

if __name__=='__main__':main()

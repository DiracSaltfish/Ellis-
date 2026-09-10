"""Install only this standalone service; never touches MachomeHub."""
from pathlib import Path
import plistlib,subprocess,os
root=Path(__file__).resolve().parents[1]
label='com.ellis.intranet-iopv'
plist=Path.home()/'Library/LaunchAgents'/f'{label}.plist';plist.parent.mkdir(parents=True,exist_ok=True)
config={'Label':label,'ProgramArguments':[str(root/'build/iopv-server'),'-config',str(root/'config.local.json')],'WorkingDirectory':str(root),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,'StandardOutPath':str(root/'data/service.log'),'StandardErrorPath':str(root/'data/service.log')}
plist.write_bytes(plistlib.dumps(config));os.chmod(plist,0o600)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)],check=True)
print('installed',label)

from pathlib import Path
import plistlib,shutil,subprocess,os
h=Path.home();root=h/'Library/Application Support/MachomeHub/web-entry';root.mkdir(exist_ok=True);dst=root/'machome-iopv-web-entry';shutil.copy2('/tmp/machome-iopv-web-entry',dst);dst.chmod(0o755)
p=h/'Library/LaunchAgents/com.ellis.machome-iopv-web-entry.plist'
assert not p.exists(),'Existing entry configuration requires review'
p.write_bytes(plistlib.dumps({'Label':'com.ellis.machome-iopv-web-entry','ProgramArguments':[str(dst),'-listen',':80','-upstream','http://127.0.0.1:18680'],'RunAtLoad':True,'KeepAlive':True,'StandardOutPath':str(root/'stdout.log'),'StandardErrorPath':str(root/'stderr.log')}))
subprocess.run(['launchctl','bootstrap','gui/'+str(os.getuid()),str(p)],check=True)
print('Port 80 website entry installed')

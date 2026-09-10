"""Explicit recovery utility; pass the printed deployment backup directory."""
import sys,os,subprocess,time,shutil
from pathlib import Path
backup=Path(sys.argv[1]).resolve();home=Path.home();app=home/'Applications/Machome 四合一运行中心.app';cfg=home/'Library/Application Support/MachomeHub/config/modules.json';plist=home/'Library/LaunchAgents/com.ellis.machome-hub-agent.plist';domain=f'gui/{os.getuid()}'
assert backup.parent==home/'Library/Application Support/MachomeHub/backups'
assert (backup/app.name).is_dir() and (backup/'modules.json').is_file()
subprocess.run(['launchctl','bootout',domain,str(plist)],check=True);time.sleep(3)
retained=backup/('replaced-'+time.strftime('%H%M%S')+'.app');os.rename(app,retained);subprocess.run(['ditto',str(backup/app.name),str(app)],check=True);shutil.copy2(backup/'modules.json',cfg);shutil.copy2(backup/plist.name,plist);subprocess.run(['launchctl','bootstrap',domain,str(plist)],check=True);print('Restored app and configuration. Verify module collection state after restart.')

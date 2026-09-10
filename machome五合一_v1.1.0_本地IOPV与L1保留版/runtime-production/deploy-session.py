import pathlib,subprocess,shutil,os,time,json,hashlib,urllib.request,socket
h=pathlib.Path.home();app=h/'Applications/Machome 四合一运行中心.app';root=h/'Library/Application Support/MachomeHub';plist=h/'Library/LaunchAgents/com.ellis.machome-hub-agent.plist';domain='gui/'+str(os.getuid());m=json.load(open('/tmp/session-manifest.json'));files=[('Contents/MacOS/machome-hub-agent','agent_sha256'),('Contents/Helpers/machome-iopv-server','helper_sha256')]
for rel,key in files:assert hashlib.sha256(pathlib.Path('/tmp/session-'+pathlib.Path(rel).name).read_bytes()).hexdigest()==m[key]
bak=root/'backups'/('session-window-'+time.strftime('%Y%m%d-%H%M%S'));bak.mkdir();
for rel,_ in files:shutil.copy2(app/rel,bak/pathlib.Path(rel).name)
f=app/'Contents/Resources/iopv-webfix-manifest.json';shutil.copy2(f,bak/f.name)
subprocess.run(['launchctl','bootout',domain,str(plist)],check=True);time.sleep(4)
try:
 # Wait for the previous child's listener to close before replacing files.
 for attempt in range(25):
  try:
   with socket.create_connection(('127.0.0.1',18680),timeout=1):pass
  except OSError:break
  time.sleep(1)
 else:raise RuntimeError('previous IOPV listener did not close')
 for rel,_ in files:shutil.copy2('/tmp/session-'+pathlib.Path(rel).name,app/rel)
 shutil.copy2('/tmp/session-manifest.json',f)
 subprocess.run(['codesign','--force','--sign','-',str(app)],check=True);subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
 subprocess.run(['launchctl','bootstrap',domain,str(plist)],check=True)
 for attempt in range(25):
  try:
   health=json.load(urllib.request.urlopen('http://127.0.0.1:18680/api/v1/health',timeout=2))
   if 'quote_window_open' in health and (root/'runtime/agent.sock').exists():break
  except Exception:pass
  time.sleep(2)
 else:raise RuntimeError('new Agent/IOPV health verification failed')
except BaseException:
 subprocess.run(['launchctl','bootout',domain,str(plist)]);time.sleep(2)
 for rel,_ in files:shutil.copy2(bak/pathlib.Path(rel).name,app/rel)
 shutil.copy2(bak/f.name,f);subprocess.run(['codesign','--force','--sign','-',str(app)],check=True);subprocess.run(['launchctl','bootstrap',domain,str(plist)])
 raise
print('DEPLOYED',bak)

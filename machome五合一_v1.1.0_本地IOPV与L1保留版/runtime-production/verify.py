import os,json,time,socket,struct,urllib.request,urllib.error,base64,hashlib
from pathlib import Path
root=Path.home()/'Library/Application Support/MachomeHub'
def ipc():
 s=socket.socket(socket.AF_UNIX);s.settimeout(10);s.connect(str(root/'runtime/agent.sock'));b=json.dumps(dict(schema_version=1,protocol='module.control.v1',type='hello',client_instance_id='acceptance-readonly')).encode();s.sendall(struct.pack('>I',len(b))+b);f=s.makefile('rb');out={};hello={}
 while len(out)<5:
  n=struct.unpack('>I',f.read(4))[0];m=json.loads(f.read(n))
  if m.get('type')=='hello':hello={k:m.get(k)for k in ['version','pid','control_ready','audit_ready','unresolved_modules','artifact_sha256']}
  if m.get('type')=='snapshot':out[m['module_id']]=m['payload']
 s.close();return dict(hello=hello,modules=out)
def get(url):
 try:
  with urllib.request.urlopen(url,timeout=8) as r:return {'http':r.status,'data':json.load(r)}
 except urllib.error.HTTPError as e:return {'http':e.code,'data':e.read().decode()[:500]}
 except Exception as e:return {'error':str(e)}
def ws(port,path):
 s=socket.create_connection(('127.0.0.1',port),5);s.settimeout(5);k=base64.b64encode(os.urandom(16)).decode();s.sendall(f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {k}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode());f=s.makefile('rb');h=[]
 while True:
  l=f.readline()
  if l==b'\r\n':break
  h.append(l.decode().strip())
 assert '101' in h[0],h[0]
 if port==18976:
  b=json.dumps(dict(action='hello',device_id='production-acceptance',request_id='verify')).encode();mask=os.urandom(4);s.sendall(bytes([129,128|len(b)])+mask+bytes(x^mask[i%4]for i,x in enumerate(b)))
 a=f.read(2);n=a[1]&127
 if n==126:n=struct.unpack('>H',f.read(2))[0]
 elif n==127:n=struct.unpack('>Q',f.read(8))[0]
 m=json.loads(f.read(n));s.close();return m
r=ipc();r['at']=time.strftime('%Y-%m-%d %H:%M:%S');r['http']={str(p):get(f'http://127.0.0.1:{p}{path}')for p,path in [(8080,'/api/v1/health'),(6787,'/api/v1/health'),(18765,'/v2/health/live'),(18680,'/api/v1/health')]}
token=(root/'data/webull/runtime/api.token').read_text().strip()
req=urllib.request.Request('http://127.0.0.1:18765/v2/health/ready',headers={'Authorization':'Bearer '+token})
try:
 with urllib.request.urlopen(req,timeout=8) as response:r['webull_ready']=json.load(response)
except urllib.error.HTTPError as e:r['webull_ready']=json.loads(e.read())
s=socket.create_connection(('127.0.0.1',19195),5);s.settimeout(5);f=s.makefile('rb');r['l1_hello']=json.loads(f.readline());s.sendall(b'{"v":1,"t":"subscribe","symbols":["513090.SH","00700.HK"]}\n');msgs=[]
try:
 for _ in range(8):
  m=json.loads(f.readline());msgs.append(m)
  if m.get('t')=='l1':break
except socket.timeout:pass
s.sendall(b'{"v":1,"t":"ping"}\n')
try:
 for _ in range(8):
  m=json.loads(f.readline());msgs.append(m)
  if m.get('t')=='pong':break
except (socket.timeout,OSError):pass
s.close();r['l1_messages']=msgs
r['websockets']={}
for p,path in [(8421,'/ws/v2/summary'),(8421,'/ws/v3/local-iopv-signals'),(18976,'/')]:
 try:r['websockets'][path]=ws(p,path)
 except Exception as e:r['websockets'][path]={'error':str(e)}
output=Path('/tmp/machome-production-acceptance.json');output.write_text(json.dumps(r,ensure_ascii=False,indent=2));output.chmod(0o600)
print(json.dumps({'at':r['at'],'hello':r['hello'],'http':r['http'],'modules':{k:{x:v.get(x)for x in ['lifecycle','work_state','headline','last_error']}for k,v in r['modules'].items()},'l1_types':[m.get('t')for m in msgs],'websocket_types':{k:v.get('type',v.get('ok',v.get('error')))for k,v in r['websockets'].items()}},ensure_ascii=False,indent=2))

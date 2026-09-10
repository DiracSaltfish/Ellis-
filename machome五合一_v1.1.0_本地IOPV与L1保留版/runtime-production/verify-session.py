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

r=ipc()
print("Agent:",r["hello"])
for name,p in r["modules"].items():
 print(name,{k:p.get(k) for k in ["lifecycle","work_state","headline","last_error"]})
 def walk(v):
  if isinstance(v,dict):
   for k,x in v.items():
    if k in ["cn_quotes_desired","hk_quotes_desired","schedule_mode","collector_desired","monitoring_desired","scheduled_idle"]: print(name,k,x)
    elif isinstance(x,(dict,list)):walk(x)
  elif isinstance(v,list):
   for x in v:walk(x)
 walk(p)
s=socket.create_connection(("127.0.0.1",19195),5);s.settimeout(5);f=s.makefile("rb");print("L1 hello",json.loads(f.readline()).get("t"));s.sendall(b'{"v":1,"t":"ping"}\n');print("L1 ping",json.loads(f.readline()).get("t"));s.close()
print("IOPV",get("http://127.0.0.1:18680/api/v1/health"))

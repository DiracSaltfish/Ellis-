import socket,struct,json,uuid,datetime,sys,time
from pathlib import Path
path=Path.home()/'Library/Application Support/MachomeHub/runtime/agent.sock'
def connect(cfg='',cursor=None):
 s=socket.socket(socket.AF_UNIX);s.settimeout(60);s.connect(str(path))
 def send(m):
  m.update(schema_version=1,protocol='module.control.v1');b=json.dumps(m).encode();s.sendall(struct.pack('>I',len(b))+b)
 def read(n):
  b=b''
  while len(b)<n:
   x=s.recv(n-len(b))
   if not x:raise RuntimeError('closed')
   b+=x
  return b
 def recv():
  n=struct.unpack('>I',read(4))[0];assert n<4000000;return json.loads(read(n))
 request=dict(type='hello',client_instance_id='hk-connect-deployment-check',config_sha256=cfg)
 if cursor:request.update(last_critical_event_epoch=cursor['audit_epoch'],last_critical_event_id=cursor['critical_event_high_water'])
 send(request);h=recv();return s,send,recv,h
s,send,recv,h=connect();cfg=h['config_sha256'];s.close();s,send,recv,h=connect(cfg,h)
if sys.argv[1]=='status':
 print(json.dumps({k:h.get(k) for k in ['control_ready','client_config_bound','unresolved_modules','instance_id','pid']},ensure_ascii=False));sys.exit()
while True:
 m=recv()
 if m.get('module_id')=='redemption' and m.get('type')=='snapshot':rev=m['control_revision'];break
cid=str(uuid.uuid4());action=sys.argv[1];args=json.loads(sys.argv[2]) if len(sys.argv)>2 else {}
c=dict(type='command',command_id=cid,module_id='redemption',action=action,arguments=args,expected_revision=rev,deadline_ms=120000,requested_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),requested_by='hk-connect-deployment',reason='用户已授权新池开发部署；验证订阅并恢复生产工作模式',agent_instance_id=h['instance_id'],config_sha256=cfg)
send(c)
for _ in range(10000):
 m=recv()
 if m.get('command_id')==cid:
  # Only summarize public command outcome, not snapshots/config or approval credentials.
  print(json.dumps({k:m.get(k) for k in ['type','state','message','code','command_id']},ensure_ascii=False),flush=True)
  if m.get('type') in ['rejected','error'] or (m.get('type')=='command_result' and m.get('state') in ['succeeded','failed','timed_out']):break

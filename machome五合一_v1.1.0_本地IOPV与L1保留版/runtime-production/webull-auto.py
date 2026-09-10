import socket,struct,json,uuid,datetime,sys
from pathlib import Path
path=Path.home()/'Library/Application Support/MachomeHub/runtime/agent.sock'
def connect(cfg='',cursor=None):
 s=socket.socket(socket.AF_UNIX);s.settimeout(30);s.connect(str(path));f=s.makefile('rb')
 def send(m):
  m.update(schema_version=1,protocol='module.control.v1');b=json.dumps(m).encode();s.sendall(struct.pack('>I',len(b))+b)
 def recv():
  n=struct.unpack('>I',f.read(4))[0];return json.loads(f.read(n))
 request=dict(type='hello',client_instance_id='production-restore-monitor',config_sha256=cfg)
 if cursor:request.update(last_critical_event_epoch=cursor['audit_epoch'],last_critical_event_id=cursor['critical_event_high_water'])
 send(request);h=recv();return s,send,recv,h
s,send,recv,h=connect();cfg=h['config_sha256'];s.close();s,send,recv,h=connect(cfg,h)
while True:
 m=recv()
 if m.get('module_id')=='webull' and m.get('type')=='snapshot':rev=m['control_revision'];break
cid=str(uuid.uuid4());send(dict(type='command',command_id=cid,module_id='webull',action='webull_set_mode',arguments={'mode':'auto'},expected_revision=rev,deadline_ms=30000,requested_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),requested_by='production-deployment',reason='恢复7x24常驻自动采集时段',agent_instance_id=h['instance_id'],config_sha256=cfg))
for i in range(10000):
 m=recv()
 if m.get('command_id')==cid:
  print(json.dumps({k:v for k,v in m.items()if k!='details'},ensure_ascii=False))
  if m.get('type') in ['rejected','error'] or (m.get('type')=='command_result' and m.get('state') in ['succeeded','failed','timed_out']):break

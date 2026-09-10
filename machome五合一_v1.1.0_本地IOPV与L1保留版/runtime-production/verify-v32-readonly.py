import socket,struct,json,os,base64,time,urllib.request,sqlite3
from pathlib import Path
root=Path.home()/'Library/Application Support/MachomeHub'
def get(path):return json.load(urllib.request.urlopen('http://127.0.0.1:18680'+path,timeout=6))
def exact(f,n):
 b=b''
 while len(b)<n:
  part=f.read(n-len(b))
  if not part:raise EOFError()
  b+=part
 return b
class WS:
 def __init__(self,path):
  self.s=socket.create_connection(('127.0.0.1',8421),5);self.s.settimeout(10);self.f=self.s.makefile('rb');key=base64.b64encode(os.urandom(16)).decode()
  self.s.sendall(f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:8421\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode())
  assert b'101' in self.f.readline()
  while self.f.readline()!=b'\r\n':pass
 def send(self,obj):
  b=json.dumps(obj).encode();m=os.urandom(4);n=len(b);h=bytes([129,128|n]) if n<126 else bytes([129,254])+struct.pack('>H',n)
  self.s.sendall(h+m+bytes(v^m[i%4]for i,v in enumerate(b)))
 def recv(self):
  h=exact(self.f,2);n=h[1]&127
  if n==126:n=struct.unpack('>H',exact(self.f,2))[0]
  if n==127:n=struct.unpack('>Q',exact(self.f,8))[0]
  return json.loads(exact(self.f,n))
 def close(self):self.f.close();self.s.close()
r={};s=WS('/ws/v3/summary');types=[];summaries=[]
for _ in range(500):
 p=s.recv();types.append(p.get('type'))
 if p.get('type')=='summary':summaries.append(p)
 if p.get('type')=='sync_complete':break
assert 'hello' in types and 'sync_complete' in types
assert all(p.get('schema_version')=='local-iopv-client.v1' for p in summaries)
r['v3_summary']={'types':list(dict.fromkeys(types)),'snapshots':len(summaries)};s.close()
symbol=next(p['symbol'] for p in get('/api/v1/snapshots')['snapshots'] if p.get('midpoint_iopv') is not None)
s=WS('/ws/v3/detail');s.send({'op':'subscribe','symbol':symbol});ack=None;detail=None
for _ in range(30):
 p=s.recv()
 if p.get('type')=='detail_ack':ack=p
 if p.get('type')=='detail' and p.get('iopv_e6',0)>0:detail=p;break
assert ack and ack.get('valuation_supported');assert detail, 'no live local valuation'
v=detail['valuation'];assert abs(detail['iopv_e6']-round(v['midpoint_iopv']*1000000))<=1;assert 'exchange_iopv_e6' in detail
r['v3_detail']={k:detail.get(k)for k in ['s','iopv_e6','exchange_iopv_e6','valuation_basis','sell_premium_ppm']};r['v3_detail']['calculated_at']=v['calculated_at']
s.send({'op':'unsubscribe','symbol':symbol});s.close()
r['settings']=get('/api/v1/signal-settings');assert r['settings']['radar_pct']==.6
h=get('/api/v1/health');r['health']={k:h.get(k)for k in ['run_id','pcf_ready','candidates','frames','quote_count','subscriptions','written_rows','last_write_at','active_date','pcf_next_attempt','errors']}
rows=get('/api/v1/minutes?symbol=513090.SH&date='+h['active_date']);r['history_rows']=len(rows);assert len(rows)>=151
r['last_history_row']=rows[-1] if rows else None
with sqlite3.connect('file:'+str(root/'data/premium/local-iopv/data/iopv.sqlite')+'?mode=ro',uri=True)as db:r['sqlite_quick_check']=db.execute('PRAGMA quick_check').fetchone()[0]
assert r['sqlite_quick_check']=='ok'
html=urllib.request.urlopen('http://127.0.0.1/',timeout=6).read().decode();assert 'signal-settings' in html;r['port80']='ok'
r['at']=time.strftime('%Y-%m-%d %H:%M:%S');Path('/tmp/v32-readonly-result.json').write_text(json.dumps(r,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in r.items()if k!='last_history_row'},ensure_ascii=False))

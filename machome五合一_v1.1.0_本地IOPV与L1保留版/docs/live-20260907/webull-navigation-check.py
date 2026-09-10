"""Opt-in real Chrome navigation check, fresh isolated profile, public quote page only."""
import json,os,select,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='hub-webull-nav-') as root:
 p=subprocess.Popen([sys.argv[1],'--profile',root,'--ticker-id','913243629','--symbol','XOP'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 events=[];urls=[]
 try:
  p.stdin.write('{"command":"start_collector","arguments":{}}\n');p.stdin.flush();until=time.monotonic()+25
  while time.monotonic()<until:
   if select.select([p.stdout],[],[],.2)[0]:
    line=p.stdout.readline()
    if line:events.append(json.loads(line))
   f=Path(root)/'DevToolsActivePort'
   if f.exists():
    port=f.read_text().splitlines()[0]
    try:
     with urllib.request.urlopen('http://127.0.0.1:'+port+'/json/list',timeout=1) as r:urls=[x['url'] for x in json.load(r) if x.get('type')=='page']
    except OSError:pass
   if any(x.startswith('https://app.webull.com/watch') for x in urls) and any(e.get('type')=='auth' for e in events):break
  assert any(x.startswith('https://app.webull.com/watch') for x in urls),urls
  p.stdin.write('{"command":"open_login","arguments":{}}\n');p.stdin.flush()
  visible=False; until=time.monotonic()+15
  while time.monotonic()<until:
   assert p.poll() is None, 'helper exited while switching from headless to login'
   if select.select([p.stdout],[],[],.2)[0]:
    line=p.stdout.readline()
    if line:
     event=json.loads(line);events.append(event)
     if event.get('type')=='browser' and event.get('visible') and event.get('state')=='running':visible=True
  assert visible, 'visible browser did not connect'
  print(json.dumps({'navigation_to_watch':True,'headless_to_visible_survived':visible,'page_urls':urls,'auth_events':[e for e in events if e.get('type')=='auth'],'order_interfaces_called':False},ensure_ascii=False))
 finally:
  p.stdin.write('{"command":"shutdown","arguments":{}}\n');p.stdin.flush();p.wait(timeout=8)

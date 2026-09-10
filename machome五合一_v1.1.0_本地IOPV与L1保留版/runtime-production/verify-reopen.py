import json,urllib.request,time,datetime,pathlib
base='http://127.0.0.1:18680/api/v1/'
def get(p):return json.load(urllib.request.urlopen(base+p,timeout=8))
end=time.time()+12*60;result=None
while time.time()<end:
 h=get('health');rows=get('minutes?symbol=513090.SH&date=2026-09-09');fresh=[p for p in rows if p['minute']>='13:00' and p['mode']=='live']
 if fresh:
  p=fresh[-1];assert p['pcf_sha256'] and p['unit']>0;assert len([r for r in rows if r['mode']=='historical_reconstruction'])==151
  result={'at':datetime.datetime.now().isoformat(),'health':h,'history_rows':len(rows),'live_row':p,'historical_rows_preserved':151};break
 time.sleep(10)
if result is None:raise RuntimeError('No 13:00 live minute recorded before deadline')
pathlib.Path('/tmp/machome-reopen-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'at':result['at'],'history_rows':result['history_rows'],'live_minute':p['minute'],'mode':p['mode'],'midpoint_iopv':p['midpoint_iopv'],'etf_price':p['etf_price'],'written_rows':h['written_rows'],'feed_state':h['feed_state']},ensure_ascii=False))

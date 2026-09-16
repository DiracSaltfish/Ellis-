from pathlib import Path
import json,time
root=Path('/tmp/machome-hk-perf-v2-20260915');raw=Path.home()/'Library/Containers/com.windin.mac.free/Data/tmp/machome-hk-perf-v2-20260915';seen={}
with (root/'callback-events.jsonl').open('w') as out:
 for _ in range(1800):
  for p in raw.glob('wind_tbapi_live_*.json'):
   if p.name.endswith('_status.json'):continue
   try:
    d=json.loads(p.read_text());k=(p.name,d.get('sub_id'));seq=d.get('callback_seq')
    if seq==seen.get(k):continue
    seen[k]=seq;out.write(json.dumps({k:d.get(k) for k in ['requested_windcode','sub_id','callback_seq','callback_epoch_ms','error_code','frame_error']})+'\n');out.flush()
   except Exception:pass
  if (root/'summary.json').exists():break
  time.sleep(1)

from pathlib import Path
import json,subprocess,math,statistics,shutil
r=Path(__file__).parent;results={}
for interval in [60000,5000,1000]:
 e=r/f'end-{interval}.json'
 if not (r/f'stopped-{interval}.json').exists():continue
 state=json.loads(e.read_text());ready=json.loads((r/f'ready-{interval}.json').read_text());before={x['symbol']:x['callback_seq'] for x in ready['items']}
 dest=r/f'decode-{interval}'/'captures';shutil.copytree(r/f'captures-{interval}',dest,dirs_exist_ok=True)
 p=subprocess.run(['/tmp/machome-hk-build/machome-wind-probe-helper','--stdio','--mode','fixture','--data-root',str(dest.parent)],input='{"action":"subscribe"}\n{"action":"quit"}\n',capture_output=True,text=True,timeout=15)
 (r/f'decoded-{interval}.jsonl').write_text(p.stdout)
 rows=[json.loads(l)['payload'] for l in p.stdout.splitlines() if json.loads(l).get('type')=='capture'];assert p.returncode==0 and len(rows)==len({x['windcode'] for x in rows})
 assert all(len(x['values'])==6 and all(isinstance(v,(float,int)) and math.isfinite(v) and v>=0 for v in x['values'].values()) and x['observed_at'].startswith('2026-09-15') for x in rows)
 ids={x['symbol']:x['sub_id'] for x in state['items']};assert all(ids[x['windcode']]==x['sub_id'] for x in rows)
 updates=[x['callback_seq']-before[x['symbol']] for x in state['items']]
 files=list(dest.glob('*.json'));lag=[f.stat().st_mtime-json.loads(f.read_text())['callback_epoch_ms']/1000 for f in files]
 results[interval]={'valid_decoded':len(rows),'decode_errors':[json.loads(l) for l in p.stdout.splitlines() if json.loads(l).get('type')=='error'],'undecoded_symbols':sorted(set(ids)-{x['windcode'] for x in rows}),'additional_callbacks_during_90s':sum(updates),'symbols_with_additional_callbacks':sum(x>0 for x in updates),'max_seq':max(x['callback_seq'] for x in state['items']),'main_queue_max_ms':state['max_main_queue_lag_ms'],'last_capture_to_export_seconds_max':max(lag),'callback_errors':sum(x['error']!=0 for x in state['items'])}
(r/'validation.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False,indent=2))

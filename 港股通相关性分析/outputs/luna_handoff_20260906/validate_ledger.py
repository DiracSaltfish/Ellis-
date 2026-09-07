"""Structural and accounting checks; does not certify source truth or research quality."""
import json, math, sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent
schema=json.loads((ROOT/'schema.json').read_text())
ledger=json.loads((ROOT/'ledger.json').read_text())
records=ledger['records']; errors=[]
terminal={'DONE','BLOCKED','EXCLUDED','INSUFFICIENT_HISTORY'}
statuses=terminal|{'TODO','RUNNING'}
def need(ok,message):
    if not ok: errors.append(message)
def present(v): return v is not None and v != ''

need(ledger['schema_version']==schema['schema_version'],'schema version mismatch')
for sheet in schema['sheets']:
    name=sheet['name']; cols=sheet['columns']; keys={c['key'] for c in cols}; seen=set()
    for i,row in enumerate(records.get(name,[])):
        prefix=f'{name}:{i+1}'
        key=(row.get(cols[0]['key']),row.get('attempt')) if name=='问题与重试' else row.get(cols[0]['key'])
        need(key not in seen,f'{prefix} duplicate key'); seen.add(key)
        need(not(set(row)-keys),f'{prefix} unknown columns: {set(row)-keys}')
        for c in cols:
            v=row.get(c['key']); t=c['type']; label=prefix+':'+c['key']
            if c['required_when']=='始终': need(present(v),label+' required')
            if not present(v): continue
            if t in {'text','enum'}: need(isinstance(v,str),label+' must be text')
            if t in {'number','integer','ratio','bp'}:
                need(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v),label+' must be finite numeric')
                if t=='integer': need(isinstance(v,int) and not isinstance(v,bool),label+' must be integer')
            if t=='boolean': need(isinstance(v,bool),label+' must be boolean')
            if t in {'date','datetime'}:
                try: datetime.fromisoformat(v)
                except (ValueError,TypeError): errors.append(label+' invalid ISO date/time')
            if t=='datetime': need(isinstance(v,str) and v.endswith('+08:00'),label+' must use Asia/Shanghai +08:00')
        if 'task_status' in row: need(row['task_status'] in statuses,prefix+' invalid task_status')
        if 'status' in row:
            allowed=statuses if name=='任务台账' else {'PASS','FAIL','NOT_APPLICABLE'}
            need(row['status'] in allowed,prefix+' invalid status')
funds={r['fund_id'] for r in records['基金目录']}
need(len(funds)==210,'candidate denominator must remain 210')
tasks={r['task_id']:r for r in records['任务台账']}
checks={r['check_id']:r for r in records['验收检查']}
issues={r['issue_id'] for r in records['问题与重试']}
results={r['result_id'] for r in records['回测结果']}
for name,rows in records.items():
    for row in rows:
        if present(row.get('fund_id')): need(row['fund_id'] in funds,f'{name} unknown fund {row["fund_id"]}')
for tid,r in tasks.items():
    for dep in filter(None,r.get('depends_on','').split(';')): need(dep in tasks,tid+' unknown dependency '+dep)
    if present(r.get('parent_id')): need(r['parent_id'] in tasks,tid+' unknown parent')
    if r['status'] in terminal:
        for k in ['finished_at','output_summary','artifact_paths','source_refs']:
            need(present(r.get(k)),tid+' terminal missing '+k)
        for p in filter(None,r.get('artifact_paths','').split(';')):
            need(Path(p).is_absolute() and Path(p).exists(),tid+' artifact missing '+p)
        if r['status']=='DONE':
            need(present(r.get('check_ids')),tid+' DONE needs checks')
            for cid in filter(None,r.get('check_ids','').split(';')):
                need(cid in checks,tid+' unknown check '+cid)
                if cid in checks: need(checks[cid]['status']!='FAIL',tid+' failed check '+cid)
        if r['status']=='BLOCKED': need(present(r.get('issue_ids')),tid+' BLOCKED needs issue')
        for iid in filter(None,r.get('issue_ids','').split(';')): need(iid in issues,tid+' unknown issue '+iid)
for f in records['基金目录']:
    tid='F-'+f['fund_id']; need(tid in tasks,f'{tid} missing')
    if tid in tasks: need(f['task_status']==tasks[tid]['status'],tid+' status disagreement')
for r in records['回测结果']:
    if all(present(r.get(k)) for k in ['unhedged_std_bp','residual_std_bp','variance_reduction']):
        a,b,v=r['unhedged_std_bp'],r['residual_std_bp'],r['variance_reduction']
        need(a>0 and b>=0,r['result_id']+' invalid std')
        if a>0: need(abs((1-(b/a)**2)-v)<=1e-8,r['result_id']+' variance identity fails')
    need(r.get('train_days')==60 and r.get('validation_days')==10,r['result_id']+' wrong split')
    need(r.get('horizon_min') in [5,15,30,60],r['result_id']+' wrong horizon')
for r in records['敏感性']: need(r['base_result_id'] in results,r['sensitivity_id']+' missing base result')
for r in records['问题与重试']: need(1<=r['attempt']<=3,r['issue_id']+' retry count outside rule')
if '--final' in sys.argv:
    for r in records['任务台账']: need(r['status'] in terminal,r['task_id']+' still open')
summary={'structural_validation':'FAIL' if errors else 'PASS','fund_count':len(funds),'task_count':len(tasks),'errors':errors,'scope':'Structural checks only; conditional fields, methodology and source truth also require task QA.'}
(ROOT/'ledger_validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False));sys.exit(bool(errors))

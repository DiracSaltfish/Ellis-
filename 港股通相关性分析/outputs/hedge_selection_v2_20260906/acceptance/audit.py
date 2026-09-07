from pathlib import Path
import json, csv, hashlib, math, collections, zipfile, shutil
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np

OUT=Path(__file__).resolve().parent
BASE=OUT.parent
PROJECT=BASE.parents[1]

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p): return json.loads(p.read_text())
def resolve(p):
    p=Path(p)
    return p if p.is_absolute() else BASE/p
def save(name,obj): (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2))
def decode(v):
    if isinstance(v,str):
        try:return json.loads(v)
        except ValueError:return v
    return v

counts={};reviews=[];mismatches=[];checks=[];snapshots={};rows_checked=0;group_issues={};workbooks={}
for a in 'ABC':
    folder=BASE/f'agent_{a}';funds=load(folder/'fund_decisions.json');metrics=load(folder/'model_metrics.json')
    counts[a]={'funds':len(funds),'decisions':dict(collections.Counter(r['decision'] for r in funds)),'old_metric_rows':len(metrics),'old_funds':len({r['fund_id'] for r in metrics})}
    for name in ['fund_decisions.json','model_metrics.json','evidence.json','qa_checks.json','tasks.json','FINAL_REPORT.md','RESULTS.xlsx']:
        snapshots[str(folder/name)]=sha(folder/name)
    for r in funds:
        reviews.append({'fund_id':r['fund_id'],'fund_name':r['fund_name'],'owner':a,'reported_decision':r['decision'],
          'acceptance':'排除证据需补核' if r['decision']=='OUT_OF_SCOPE' else '尚未形成经确认的对冲结论',
          'recommended_policy_id':r['recommended_policy_id'],'scope_evidence_id':r['scope_evidence_id'],
          'reported_remaining_gaps':decode(r['remaining_gaps']),'source':str(folder/'fund_decisions.json')})
    # Recompute every old-result row from its stored endpoint residuals, not from results.json.
    cache={}
    for r in metrics:
        p=resolve(r['residual_path'])
        if not p.exists():mismatches.append({'owner':a,'fund':r['fund_id'],'field':'residual_path','reported':str(p),'actual':'MISSING'});continue
        if p not in cache: cache[p]=pd.read_parquet(p)
        df=cache[p];q=df[(df['model']==r['model_id'])&(df['horizon']==r['horizon_min'])]
        if q.empty:mismatches.append({'owner':a,'fund':r['fund_id'],'field':'model subset','actual':'EMPTY'});continue
        y=q.y.to_numpy();e=q.residual.to_numpy();k=max(1,math.ceil(len(e)*.05))
        actual={'target_std_bp':float(np.std(y,ddof=1)*1e4),'residual_std_bp':float(np.std(e,ddof=1)*1e4),
          'variance_reduction':float(1-np.var(e,ddof=1)/np.var(y,ddof=1)), 'residual_mean_bp':float(e.mean()*1e4),
          'up_es95_bp':float(np.sort(e)[-k:].mean()*1e4),'down_es95_bp':float(np.sort(-e)[-k:].mean()*1e4),
          'oos_days':int(q.date.nunique()),'sample_count':len(q),'oos_start':str(q.date.min()),'oos_end':str(q.date.max())}
        row_errors=[]
        for key,val in actual.items():
            old=r[key]
            ok=abs(float(old)-val)<1e-8 if isinstance(val,(float,int)) else str(old).replace('-','')==val
            if not ok:
                z={'owner':a,'fund':r['fund_id'],'horizon':r['horizon_min'],'model':r['model_id'],'field':key,'reported':old,'actual':val}
                row_errors.append(z);mismatches.append(z)
        checks.append({'owner':a,'fund_id':r['fund_id'],'horizon':r['horizon_min'],'model':r['model_id'],'passed':not row_errors,'residual_path':str(p)})
        rows_checked+=1
    evidence=load(folder/'evidence.json');tasks=load(folder/'tasks.json');qa=load(folder/'qa_checks.json')
    ids=collections.Counter(r['evidence_id'] for r in evidence)
    group_issues[a]={'duplicate_evidence_ids':{k:v for k,v in ids.items() if v>1},
       'tasks_same_start_finish':sum(r.get('started_at_utc')==r.get('finished_at_utc') for r in tasks),
       'tasks_total':len(tasks),'failed_qa':[r['check_id'] for r in qa if r['passed'] is not True],
       'evidence_missing_paths':[],'evidence_hash_mismatch':[]}
    for r in evidence:
        if not r.get('local_path'):continue
        p=resolve(r['local_path'])
        if not p.exists():group_issues[a]['evidence_missing_paths'].append({'evidence_id':r['evidence_id'],'path':str(p)})
        elif p.is_file() and r.get('sha256') and sha(p)!=r['sha256']:group_issues[a]['evidence_hash_mismatch'].append({'evidence_id':r['evidence_id'],'path':str(p)})
    # Inspect saved workbook OOXML values and cached error cells without editing it.
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(folder/'RESULTS.xlsx') as z:
        w=ET.fromstring(z.read('xl/workbook.xml'))
        sheets=[s.attrib['name'] for s in w.findall('s:sheets/s:sheet',ns)]
        errs=[]
        for name in z.namelist():
            if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'):
                tree=ET.fromstring(z.read(name))
                for c in tree.findall('.//s:c',ns):
                    if c.get('t')=='e':errs.append({'sheet_file':name,'cell':c.get('r'),'error':c.findtext('s:v',namespaces=ns)})
        workbooks[a]={'sheets':sheets,'cached_formula_errors':errs}

assert len({r['fund_id'] for r in reviews})==237 and len(reviews)==237
expected={r['fund_id'] for r in load(BASE/'control/manifest.json')}
with (BASE/'agent_A/additions.csv').open(encoding='utf-8-sig') as f: expected.update(r['fund_id'] for r in csv.DictReader(f))
assert expected=={r['fund_id'] for r in reviews}
save('reviewed_funds.json',reviews);save('numerical_checks.json',checks);save('metric_mismatches.json',mismatches)
save('provenance_checks.json',group_issues);save('source_hashes.json',snapshots);save('workbook_read_checks.json',workbooks)
summary={'overall':'NOT_ACCEPTED_FOR_REQUESTED_HEDGE_MAPPING','candidate_count':237,'groups':counts,
  'new_confirmed_suitable':0,'new_confirmed_none':0,'reported_insufficient':216,'reported_excluded_pending_review':21,
  'old_metric_rows_recomputed':rows_checked,'old_metric_rows_passed':sum(r['passed'] for r in checks),
  'old_metric_field_mismatches':len(mismatches),'old_funds':48,'note':'Old metric verification is not new confirmation or full raw-data audit.'}
save('summary.json',summary)
# Preserve C executable sources that were left only in its disposable worktree.
src=Path('/Users/ellis/.codex/worktrees/e135/工具程序开发');dest=OUT/'recovered_C_sources';dest.mkdir(exist_ok=True)
files=['extract_new_period_remote.py','probe_c_candidates.py','write_selection_lock_C.py','build_agent_C_outputs.py','agent_C_remote_audit.py','build_results_xlsx.mjs']
recovered=[]
for name in files:
    p=src/name
    if p.exists():shutil.copy2(p,dest/name);recovered.append({'original':str(p),'copy':str(dest/name),'sha256':sha(p)})
save('recovered_C_sources.json',recovered)
print(json.dumps(summary,ensure_ascii=False,indent=2))
print('Mismatch samples:',json.dumps(mismatches[:8],ensure_ascii=False))
print('Provenance counts:',{a:{k:len(v) if isinstance(v,(list,dict)) else v for k,v in r.items()} for a,r in group_issues.items()})

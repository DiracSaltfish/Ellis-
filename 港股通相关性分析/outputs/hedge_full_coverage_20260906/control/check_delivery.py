"""Structural full-coverage gate. Passing is not methodology or investment approval."""
from pathlib import Path
import argparse,json,collections,math

p=argparse.ArgumentParser();p.add_argument('--owner',required=True,choices=list('ABC'));a=p.parse_args()
C=Path(__file__).resolve().parent; D=C.parent/f'agent_{a.owner}'
def read(name,default=None):
    f=D/name
    if not f.exists(): return default
    if name.endswith('jsonl'):return [json.loads(s) for s in f.read_text().splitlines() if s.strip()]
    return json.loads(f.read_text())
expected=set(json.loads((C/f'fund_ids_{a.owner}.json').read_text()))
adds=read('additions.json',[]) if a.owner=='A' else []
expected.update(r['fund_id'] for r in adds)
rows=read('mapping.json',[]);targets=read('target_results.jsonl',[]);attempts=read('fetch_attempts.jsonl',[]);ev=read('evidence.jsonl',[])
errors=[]
def require(ok,msg):
    if not ok:errors.append(msg)
ids=[r.get('fund_id') for r in rows]
require(set(ids)==expected,f'Fund set missing={sorted(expected-set(ids))}, extra={sorted(set(ids)-expected)}')
require(len(ids)==len(set(ids)),'Duplicate mapping IDs')
required=[x['field'] for x in json.loads((C/'workbook_schema.json').read_text())['sheets']['全量对冲映射']]
attempt_ids={r.get('attempt_id') for r in attempts}; evidence={r.get('evidence_id'):r for r in ev}
actual_targets={(r.get('fund_id'),r.get('run_id'),r.get('target_type')):r for r in targets}
for r in rows:
    f=r.get('fund_id');prefix=f'{f}: '
    require(all(k in r for k in required),prefix+'Missing columns')
    require(r.get('processing_status')=='PROCESSED',prefix+'Not processed')
    require(r.get('decision') in ['MATCH','NO_MATCH_IN_TESTED_SET','INSUFFICIENT_DATA','OUT_OF_SCOPE'],prefix+'Invalid decision')
    require(type(r.get('actual_backtest_run')) is bool,prefix+'Backtest flag must be boolean')
    for key in ['structural_candidates','candidate_tools_tested','candidate_tools_missing','scope_evidence_ids','remaining_gaps','fetch_attempt_ids']:
        require(isinstance(r.get(key),list),prefix+key+' must be array')
    if r.get('decision')!='OUT_OF_SCOPE':
        require(bool(r.get('structural_candidates') or r.get('candidate_tools_tested')),prefix+'No explicit candidate investigation')
    if r.get('actual_backtest_run'):
        key=(f,r.get('source_run_id'),r.get('target_type'));t=actual_targets.get(key)
        require(t is not None,prefix+'Missing target run')
        if t:
            for k in ['residual_path','weights_path','data_manifest_path']:
                raw=t.get(k);path=Path(raw) if raw else None
                require(bool(path and (path if path.is_absolute() else D/path).is_file()),prefix+'Missing actual '+k)
    if r.get('decision') in ['MATCH','NO_MATCH_IN_TESTED_SET']:
        require(r.get('actual_backtest_run') is True,prefix+'Numerical decision without real OOS run')
        require(bool(r.get('candidate_tools_tested')),prefix+'No actual tested candidates')
    if r.get('decision')=='MATCH':
        rho=r.get('hedge_return_correlation');vr=r.get('variance_reduction')
        require(isinstance(rho,(int,float)) and math.isfinite(rho) and .6<=rho<=1,prefix+'rho not >=.60')
        require(isinstance(vr,(int,float)) and math.isfinite(vr) and vr>0,prefix+'Residual variance not reduced')
        require(r.get('target_type') in ['PCF_BASKET','ETF_MARKET_PRICE'],prefix+'Structural/index result is not tested fund match')
        require(r.get('evidence_level') in ['UNSEEN_CONFIRMATION','SEEN_EXPLORATORY','SHORT_SAMPLE'],prefix+'Descriptive is not OOS match')
    if r.get('evidence_level')=='UNSEEN_CONFIRMATION':require((r.get('new_unseen_oos_days') or 0)>=20,prefix+'Fewer than20 unseen days')
    if r.get('decision')=='INSUFFICIENT_DATA':
        require(bool(r.get('remaining_gaps')),prefix+'No precise data gap')
        refs=r.get('fetch_attempt_ids') or [];require(bool(refs) and all(k in attempt_ids for k in refs),prefix+'Missing actual inquiry evidence')
    if r.get('decision')=='OUT_OF_SCOPE':
        refs=r.get('scope_evidence_ids') or []
        require(bool(refs) and all(k in evidence for k in refs),prefix+'Missing scope evidence')
        require(any(evidence.get(k,{}).get('evidence_type')=='SCOPE' and evidence.get(k,{}).get('supporting_excerpt') for k in refs),prefix+'No supporting scope excerpt')
report={'owner':a.owner,'expected':len(expected),'rows':len(rows),'actual_backtested':sum(r.get('actual_backtest_run') is True for r in rows),'decision_counts':dict(collections.Counter(r.get('decision') for r in rows)),'errors':errors,'structural_pass':not errors,'note':'This checks coverage and references only; independent numeric/method QA still required.'}
if D.exists():(D/'delivery_check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(bool(errors))

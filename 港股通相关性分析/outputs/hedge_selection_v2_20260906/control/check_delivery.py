"""Structural and hard-gate audit. This does not replace numerical verification."""
import argparse, csv, json, math
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

def read_rows(folder, name):
    j = folder/f'{name}.json'
    c = folder/f'{name}.csv'
    if j.exists():
        a = json.loads(j.read_text())
        if not isinstance(a, list): raise ValueError(f'{j}: expected row array')
        return a
    if c.exists():
        with c.open(encoding='utf-8-sig', newline='') as f: return list(csv.DictReader(f))
    return None

def truth(v): return v is True or isinstance(v,str) and v.lower() == 'true'
def number(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (ValueError,TypeError): return None

def check(owner):
    schema=json.loads((BASE/'control/schema.json').read_text())
    assigned=json.loads((BASE/f'control/assignment_{owner}.json').read_text())
    expected={r['fund_id'] for r in assigned}
    other={r['fund_id'] for r in json.loads((BASE/'control/manifest.json').read_text()) if r['owner']!=owner}
    folder=BASE/f'agent_{owner}'
    errors=[];pending=[]
    if owner=='A':
        additions=read_rows(folder,'additions') or []
        expected.update(r['fund_id'] for r in additions)
        if expected & other: errors.append('A additions collide with another owner')
    allrows={}
    for name,t in schema['tables'].items():
        rows=read_rows(folder,name)
        if rows is None:
            pending.append(name);continue
        allrows[name]=rows
        fields={c['key'] for c in t['columns']}
        for i,r in enumerate(rows):
            missing=fields-set(r)
            if missing: errors.append(f'{name}:{i}: missing columns {sorted(missing)}')
            fid=r.get('fund_id')
            if fid and fid!='GLOBAL' and fid not in expected: errors.append(f'{name}:{i}: unowned fund {fid}')
    rows=allrows.get('fund_decisions',[])
    ids=[r.get('fund_id') for r in rows]
    if rows:
        if len(ids)!=len(set(ids)):errors.append('duplicate fund conclusions')
        if set(ids)!=expected:errors.append(f'fund denominator mismatch missing={sorted(expected-set(ids))} extra={sorted(set(ids)-expected)}')
    for r in rows:
        fid=r['fund_id'];d=r.get('decision')
        if d not in schema['decision_enums']:errors.append(f'{fid}: invalid decision {d}')
        if r.get('owner')!=owner:errors.append(f'{fid}: wrong owner')
        if d=='SUITABLE_PRICE_PROXY':
            if not r.get('recommended_policy_id'):errors.append(f'{fid}: suitable without policy')
            for k,threshold in [('oos_days',20),('variance_reduction',0.5),('ci_low',0.3),('strict_refit_vr',0.4),('positive_block_fraction',0.7),('effective_quote_coverage',0.95)]:
                val=number(r.get(k))
                if val is None or val<threshold:errors.append(f'{fid}: {k} below gate or missing')
            for k in ['candidate_coverage_complete','event_coverage_complete']:
                if not truth(r.get(k)):errors.append(f'{fid}: {k} incomplete')
            if r.get('confirmation_status')!='NEW_LOCKED':errors.append(f'{fid}: no new confirmation')
            if not r.get('scope_evidence_id'):errors.append(f'{fid}: no scope evidence')
        elif r.get('recommended_policy_id'):
            errors.append(f'{fid}: nonaccepted row has recommended policy; use exploratory field')
        if d=='NONE_IN_TESTED_SET':
            if not truth(r.get('candidate_coverage_complete')) or not truth(r.get('event_coverage_complete')):errors.append(f'{fid}: NONE with incomplete evidence')
            if (number(r.get('oos_days')) or 0)<20 or r.get('confirmation_status')!='NEW_LOCKED':errors.append(f'{fid}: NONE without sufficient new data')
        if d=='OUT_OF_SCOPE' and not r.get('scope_evidence_id'):errors.append(f'{fid}: exclusion without official evidence')
    for r in allrows.get('qa_checks',[]):
        if not truth(r.get('passed')):errors.append(f"failed or unrun QA {r.get('check_id')}")
    for name in ['RESULTS.xlsx','FINAL_REPORT.md','STATUS.md']:
        if not (folder/name).exists():pending.append(name)
    return {'owner':owner,'expected_funds':len(expected),'conclusions':len(rows),'pending':pending,'errors':errors,'structural_pass':not pending and not errors,'numerical_audit':'NOT_PERFORMED_BY_THIS_SCRIPT'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--owner',choices=list('ABC'));args=p.parse_args()
    result=[check(a) for a in (args.owner or 'ABC')]
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if all(r['structural_pass'] for r in result) else 1)

#!/usr/bin/env python3
"""Finalize C R1 task ledger and status after workbook/QA artifacts exist."""
from __future__ import annotations
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/Users/ellis/工具程序开发/港股通相关性分析')
OUT = ROOT / 'outputs/hedge_rework_01_20260906/agent_C'
now = datetime.now(timezone.utc).isoformat()
run = json.loads((OUT/'results/r1_basket_run.json').read_text(encoding='utf-8'))
tasks = json.loads((OUT/'tasks.json').read_text(encoding='utf-8'))
for t in tasks:
    phase = t.get('phase')
    if phase == 'P0':
        continue
    if not t.get('started_at_utc'):
        t['started_at_utc'] = run['started_at_utc']
    if phase == 'P5':
        t.update({'status':'DONE','started_at_utc':run['started_at_utc'],'finished_at_utc':now,'detailed_result':'C R1 base tables and exploratory comparison tables materialized for the 55 assigned funds.','validation':'fund_decisions/model_metrics/exploratory_policies JSON+CSV; 55 decision rows','blocker_type':'NONE','blocker_evidence':None,'outputs':{'fund_decisions':str(OUT/'fund_decisions.json'),'model_metrics':str(OUT/'model_metrics.json'),'exploratory_policies':str(OUT/'exploratory_policies.json')},'next_action':'none for this phase'})
    elif phase == 'P6':
        t.update({'status':'DONE','started_at_utc':run['started_at_utc'],'finished_at_utc':now,'detailed_result':'Direction/scale/cost scenario rows generated with unknown fees, borrow and funding costs kept null; no order submitted.','validation':'execution_scenarios contains only NOT_AVAILABLE conditional scenarios','blocker_type':'DATA','blocker_evidence':'fee/borrow/funding evidence not sufficient for executable cost certification','outputs':{'execution_scenarios':str(OUT/'execution_scenarios.json')},'next_action':'obtain official fee/borrow/funding terms before execution use'})
    elif phase == 'P7':
        t.update({'status':'DONE','started_at_utc':run['started_at_utc'],'finished_at_utc':now,'detailed_result':'Independent QA, repair checks, 16-sheet workbook rendering and export completed.','validation':'460 QA rows, 4 repair rows, workbook inspect/render artifacts and formula scan saved','blocker_type':'NONE','blocker_evidence':None,'outputs':{'qa_checks':str(OUT/'qa_checks.json'),'repair_checks':str(OUT/'repair_checks.json'),'xlsx':str(OUT/'RESULTS.xlsx')},'next_action':'research remains PARTIAL until the new confirmation window reaches 20 valid OOS days'})
(OUT/'tasks.json').write_text(json.dumps(tasks,ensure_ascii=False,indent=2),encoding='utf-8')
from csv import DictWriter
fields = list(tasks[0])
with (OUT/'tasks.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=DictWriter(f,fieldnames=fields); w.writeheader()
    for t in tasks:
        row=dict(t)
        for k,v in row.items():
            if isinstance(v,(dict,list)): row[k]=json.dumps(v,ensure_ascii=False)
        w.writerow(row)
status = {
    'updated_at_utc': now, 'owner':'C', 'run_id':run['run_id'], 'repair_status':'COMPLETE', 'research_status':'PARTIAL',
    'decision_counts': {'SUITABLE_PRICE_PROXY':0,'NONE_IN_TESTED_SET':0,'INSUFFICIENT_EVIDENCE':55,'OUT_OF_SCOPE':0},
    'fund_count':55, 'funds_with_basket_days':run['funds_with_basket_days'], 'total_oos_rows':run['total_oos_rows'],
    'total_policy_rows':run['total_policy_rows'], 'new_confirmation_rule':'at least 20 valid new OOS days',
    'reason':'TWS parameter bug repaired and full raw history persisted; research cannot be confirmed because new PCF/HK confirmation coverage is below 20 days and event/scope evidence remains partial.',
    'artifacts': {'xlsx':str(OUT/'RESULTS.xlsx'),'final_report':str(OUT/'FINAL_REPORT.md'),'assets':str(OUT/'assets.json')}
}
(OUT/'STATUS.md').write_text('# C组 R1 状态\n\n'+json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(status,ensure_ascii=False,indent=2))

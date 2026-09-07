#!/usr/bin/env python3
"""Independent execution checks for the R1 delivery package."""
from __future__ import annotations
import csv, gzip, hashlib, json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; PROJECT=Path("/Users/ellis/工具程序开发/港股通相关性分析")
TABLES=ROOT/"tables"; CHECKS=ROOT/"checks"; RAW=ROOT/"data/raw/new_period_520600"

def sha(p):
 h=hashlib.sha256();
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
 return h.hexdigest()
def load(name): return list(csv.DictReader((TABLES/f"{name}.csv").open(encoding="utf-8-sig")))
def truth(v): return v is True or v=="True" or v=="true"
def check(cid,actual,expected,passed,notes=""): return {"check_id":cid,"actual":actual,"expected":expected,"passed":bool(passed),"notes":notes}

def main():
 results=[]
 decisions=load("fund_decisions"); candidates=load("tool_candidates"); evidence=load("evidence"); metrics=load("model_metrics"); tasks=load("tasks")
 results.append(check("denominator_81",len(decisions),81,len(decisions)==81))
 results.append(check("fund_ids_unique",len({r["fund_id"] for r in decisions}),81,len({r["fund_id"] for r in decisions})==81))
 results.append(check("no_hk_single_stock_hedge",sorted({r["asset_type"] for r in candidates}),["ETF","期货"],set(r["asset_type"] for r in candidates)<= {"ETF","期货"}))
 new=[r for r in metrics if r["fund_id"]=="520600.SH" and r["confirmation_status"]=="NEW_LOCKED"]
 results.append(check("new_metric_rows",len(new),4,len(new)==4))
 results.append(check("new_metric_rho_gate",[float(r["hedge_return_correlation"]) for r in new],">=0.60",all(float(r["hedge_return_correlation"])>=.6 for r in new)))
 results.append(check("new_metric_vr_gate",[float(r["variance_reduction"]) for r in new],">0",all(float(r["variance_reduction"])>0 for r in new)))
 results.append(check("new_oos_days",sorted({int(r["oos_days"]) for r in new}),[24],all(int(r["oos_days"])>=20 for r in new)))
 panel=RAW/"520600_basket_panel_1m.jsonl.gz"; keys=set(); rows=0; dup=0
 with gzip.open(panel,"rt",encoding="utf-8") as f:
  for line in f:
   r=json.loads(line); rows+=1; k=(r["fund_id"],r["date"],int(r["minute"])); dup += k in keys; keys.add(k)
 results.append(check("panel_rows_unique",rows,rows,dup==0,f"duplicates={dup}"))
 pcf=json.loads((RAW/"520600_pcf_summary.json").read_text()); results.append(check("pcf_exact_dates",pcf["exact_page_count"],24,pcf["exact_page_count"]==24 and not pcf["missing_exact_dates"]))
 manifest=json.loads((ROOT/"data/raw/ibkr_new_period/ibkr_fetch_manifest.json").read_text()); results.append(check("ibkr_manifest_dates",len(manifest["dates"]),24,len(manifest["dates"])==24))
 lock=json.loads((RAW/"520600_selection_lock.json").read_text()); summary=json.loads((RAW/"520600_r1_analysis_summary.json").read_text()); results.append(check("lock_precedes_analysis",[lock["locked_at_utc"],summary["generated_at_utc"]],"lock < summary",lock["locked_at_utc"]<summary["generated_at_utc"]))
 missing_paths=[]; bad_hash=[]
 for e in evidence:
  if not e.get("local_path"): continue
  p=PROJECT/e["local_path"] if not Path(e["local_path"]).is_absolute() else Path(e["local_path"])
  if not p.exists(): missing_paths.append(e["evidence_id"])
  elif e.get("sha256") and sha(p)!=e["sha256"]: bad_hash.append(e["evidence_id"])
 results.append(check("evidence_paths_exist",missing_paths,[],not missing_paths))
 results.append(check("evidence_hashes_match",bad_hash,[],not bad_hash))
 done=[r for r in tasks if r["fund_id"]=="520600.SH" and r["status"]=="DONE"]; results.append(check("actual_task_timestamps",len(done),7,len(done)==7 and all(r.get("started_at_utc") and r.get("finished_at_utc") for r in done)))
 # Research result is not a QA pass/fail; keep it as a separate status check.
 out={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"checks":results,"passed":all(x["passed"] for x in results),"repair_status":"COMPLETE","research_status":"PARTIAL","note":"qa_checks test execution correctness; research_gates carry research outcomes."}
 CHECKS.mkdir(exist_ok=True); (CHECKS/"technical_check.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
 with (CHECKS/"technical_check.md").open("w",encoding="utf-8") as f:
  f.write("# B R1 technical checks\n\n")
  for x in results: f.write(f"- {'PASS' if x['passed'] else 'FAIL'} `{x['check_id']}`: actual={x['actual']}; expected={x['expected']} {x['notes']}\n")
 print(json.dumps(out,ensure_ascii=False,indent=2))
 raise SystemExit(0 if out["passed"] else 1)
if __name__=="__main__": main()

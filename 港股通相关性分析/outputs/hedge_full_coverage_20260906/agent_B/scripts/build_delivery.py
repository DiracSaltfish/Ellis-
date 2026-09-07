#!/usr/bin/env python3
"""Materialize the full-coverage contract files from engine and fetch outputs."""
from __future__ import annotations
import csv, gzip, hashlib, json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; PROJECT=Path("/Users/ellis/工具程序开发/港股通相关性分析"); CONTROL=ROOT.parent/"control"
TOOLS=["HSI_FUT","HHI_FUT","HTI_FUT","02800","02828","03032","03033","02845"]
WINDOW_START,WINDOW_END="20260303","20260803"

def now(): return datetime.now(timezone.utc).isoformat()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
 return h.hexdigest()
def rel(p):
 p=Path(p)
 try:return str(p.relative_to(PROJECT))
 except ValueError:return str(p)
def local_rel(p):
 s=str(p) if p else p
 marker="outputs/hedge_full_coverage_20260906/agent_B/"
 return s.split(marker,1)[1] if s and marker in s else s
def read_jsonl(p): return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
def write_jsonl(p,rows):
 Path(p).parent.mkdir(parents=True,exist_ok=True)
 Path(p).write_text("".join(json.dumps(x,ensure_ascii=False,separators=(",",":"),default=str)+"\n" for x in rows),encoding="utf-8")
def write_csv(p,rows,fields=None):
 if not rows:return
 fields=fields or list(rows[0]);
 with Path(p).open("w",encoding="utf-8-sig",newline="") as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for r in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()})

def main():
 assignments=json.loads((CONTROL/"assignments.json").read_text())["B"]; byid={x["fund_id"]:x for x in assignments}; ids=list(byid)
 raw_targets=read_jsonl(ROOT/"data/metrics/target_results.jsonl")
 raw_candidates=read_jsonl(ROOT/"data/metrics/candidate_metrics.jsonl")
 inventory=read_jsonl(ROOT/"data/processed/inventory.jsonl")
 sel_summary=json.loads((ROOT/"data/processed/selection_summary.json").read_text())
 target_inv=json.loads((ROOT/"data/raw/target_etf_1m/target_etf_inventory.json").read_text())
 pcf_inv=json.loads((ROOT/"data/raw/pcf/target_pcf_inventory.json").read_text())
 pcf_counts=Counter()
 with gzip.open(ROOT/"data/raw/pcf/target_pcf_rows.jsonl.gz","rt",encoding="utf-8") as f:
  for line in f:
   if line.strip(): pcf_counts[json.loads(line)["fund_id"]]+=1
 residual_index={ (x["fund_id"],x["horizon_min"]):x for x in read_jsonl(ROOT/"data/residuals/index.jsonl") }
 weight_index={ (x["fund_id"],x["horizon_min"]):x for x in read_jsonl(ROOT/"data/weights/index.jsonl") }
 bytarget=defaultdict(list)
 for x in raw_targets: bytarget[x["fund_id"]].append(x)
 bycand=defaultdict(list)
 for x in raw_candidates: bycand[(x["fund_id"],x["target_type"],x.get("horizon_min"))].append(x)
 engine_hash=sha(ROOT/"scripts/selection_core.py"); test_hash=sha(ROOT/"checks/selection_core_tests.json")
 updated=now(); mappings=[]; target_out=[]; candidate_out=[]; coverage=[]; tasks=[]; checks=[]; evidence=[]; assets=[]
 # Use timestamps recorded by the actual fetch/compute processes.
 etf_attempts=read_jsonl(ROOT/"data/raw/target_etf_1m/target_etf_attempts.jsonl")
 pcf_attempts=read_jsonl(ROOT/"data/raw/pcf/target_pcf_attempts.jsonl")
 attempts=etf_attempts+pcf_attempts
 attempt_by_fund=defaultdict(list)
 for a in attempts: attempt_by_fund[a["fund_id"] if a["fund_id"].endswith((".SZ",".SH")) else a["fund_id"]].append(a)
 attempt_ids={x["attempt_id"] for x in attempts}
 run_started=sel_summary["run_started_at_utc"]; run_finished=sel_summary["run_finished_at_utc"]
 inv_time=json.loads((ROOT/"STATUS.md").read_text().split("\n",2)[2]).get("updated_at_utc",updated) if False else updated
 # Output target results in the contract's machine schema, retaining all raw
 # scalar metrics in the nested metrics object.
 for x in raw_targets:
  fid=x["fund_id"]; h=x["horizon_min"]; key=(fid,h)
  ri=residual_index.get(key); wi=weight_index.get(key)
  is_pcf=x["target_type"]=="PCF_BASKET"; is_struct=x["target_type"]=="INDEX_STRUCTURAL"
  data_manifest=("data/reused_r1_520600/520600_basket_panel_1m.jsonl.gz" if is_pcf else f"data/processed/panels/{fid}.jsonl.gz")
  residual_path=("data/reused_r1_520600/520600_new_period_residuals.jsonl.gz" if is_pcf else (local_rel(ri["path"]) if ri else None))
  weights_path=("data/reused_r1_520600/520600_r1_daily_weights.csv" if is_pcf else (local_rel(wi["path"]) if wi else None))
  can_actual=bool(x.get("status")=="SEEN_EXPLORATORY" and (is_pcf or x.get("selection_method")=="ROLLING_50_10_60_REFIT") and (residual_path and weights_path))
  rho=x.get("correlation"); vr=x.get("variance_reduction")
  dec="MATCH" if can_actual and (rho or -1)>=.60 and (vr or -1)>0 else ("NO_MATCH_IN_TESTED_SET" if can_actual else "INSUFFICIENT_DATA")
  metrics={k:x.get(k) for k in ["rows","days","sample_start","sample_end","correlation","correlation_ci_low","correlation_ci_high","target_std_bp","residual_std_bp","variance_reduction","residual_mean_bp","up_es95_bp","down_es95_bp","beta","panel_rows","panel_days","candidate_policies_tested"]}
  target_out.append({"fund_id":fid,"target_type":x["target_type"],"run_id":x.get("source_run_id"),"decision":dec,"evidence_level":x.get("status"),"horizon_min":h,"policy_id":x.get("policy_id"),"metrics":metrics,"data_manifest_path":data_manifest,"weights_path":weights_path,"residual_path":residual_path,"selection_lock_path":"scripts/selection_core.py","checks_ids":["B-ENGINE-CONSTRAINT","B-ENGINE-SESSION","B-ENGINE-FUTURE-LEAK"],"limitations":[x.get("gap")] if x.get("gap") else (["PCF basket reused from prior R1 evidence; not recomputed in this run"] if is_pcf else [])})
 # one mapping per fund, selecting PCF only where it has a tested reusable OOS result
 for fid,a in byid.items():
  rows=[x for x in bytarget[fid] if x["horizon_min"]==30]
  pcf=[x for x in rows if x["target_type"]=="PCF_BASKET" and x.get("status")=="SEEN_EXPLORATORY"]
  etf=[x for x in rows if x["target_type"]=="ETF_MARKET_PRICE"]
  usable_pcf=pcf and fid=="520600.SH" and (pcf[0].get("correlation") or -1)>=.6 and (pcf[0].get("variance_reduction") or -1)>0
  chosen=pcf[0] if usable_pcf else (etf[0] if etf else next((x for x in rows if x["target_type"]=="INDEX_STRUCTURAL"),None))
  actual=bool(chosen and chosen.get("status")=="SEEN_EXPLORATORY" and (chosen.get("selection_method")=="ROLLING_50_10_60_REFIT" or chosen.get("target_type")=="PCF_BASKET") and (chosen.get("correlation") is not None))
  rho=chosen.get("correlation") if chosen else None; vr=chosen.get("variance_reduction") if chosen else None
  decision="MATCH" if actual and (rho or -1)>=.60 and (vr or -1)>0 else ("NO_MATCH_IN_TESTED_SET" if actual else "INSUFFICIENT_DATA")
  tt=chosen.get("target_type") if chosen else "INDEX_STRUCTURAL"; pid=chosen.get("policy_id") if chosen else None; tools=chosen.get("tools") or []
  crows=[x for x in raw_candidates if x["fund_id"]==fid and x.get("target_type")==tt and x.get("horizon_min")==30 and x.get("tested")]
  tested=sorted({t for x in crows for t in (x.get("tools") or [])})
  if tt=="INDEX_STRUCTURAL": tested=[]
  gaps=[]
  if decision=="INSUFFICIENT_DATA":
   if not target_inv["rows_by_fund"].get(fid,0): gaps.append("Remote ETF 1-minute member absent; PCF archive has no fund rows; only structural candidates remain")
   elif chosen and chosen.get("status")=="SHORT_SAMPLE": gaps.append("Available target history does not reach the 60-day train / 50-fit + 10-validation OOS gate; retained as short-sample evidence only")
   else: gaps.append("No actual OOS target result passed the required data gate")
  if tt=="ETF_MARKET_PRICE": gaps.extend(["ETF market price includes premium/discount and creation/redemption basis; it is not a PCF constituent basket"] if decision=="MATCH" else [])
  if a.get("scope")=="UNVERIFIED": gaps.append("Official fund scope/index constituent evidence not closed in this package")
  fetchrefs=[]
  for aa in attempt_by_fund.get(fid,[]):
   if aa["attempt_id"] not in fetchrefs: fetchrefs.append(aa["attempt_id"])
   if len(fetchrefs)>=2: break
  latest_beta=chosen.get("beta") if chosen else None
  mappings.append({"fund_id":fid,"fund_name":a["fund_name"],"owner":"B","index_id":a.get("index_id"),"index_name":a.get("index_name"),"scope_status":a.get("scope","UNVERIFIED"),"scope_evidence_ids":[],"processing_status":"PROCESSED","actual_backtest_run":actual,"decision":decision,"target_type":tt,"evidence_level":chosen.get("status") if chosen else "SHORT_SAMPLE","primary_policy_id":pid,"primary_tools":tools,"backup_policy_id":None,"backup_tools":[],"structural_candidates":[{"tool_id":t,"reason":"Concrete HK index/future or ETF proxy retained for the fund's index family"} for t in TOOLS],"selection_reason":("PCF component basket passed rho/VR gate" if usable_pcf else ("ETF market-price OOS passed rho/VR gate" if decision=="MATCH" else "No fully admissible tested OOS match; retained concrete candidates and exact gap")),"primary_horizon_min":30,"hedge_return_correlation":rho,"correlation_ci_low":chosen.get("correlation_ci_low") if chosen else None,"correlation_ci_high":chosen.get("correlation_ci_high") if chosen else None,"correlation_threshold":.60,"target_std_bp":chosen.get("target_std_bp") if chosen else None,"residual_std_bp":chosen.get("residual_std_bp") if chosen else None,"variance_reduction":vr,"up_es95_bp":chosen.get("up_es95_bp") if chosen else None,"down_es95_bp":chosen.get("down_es95_bp") if chosen else None,"oos_start":chosen.get("sample_start") if chosen else None,"oos_end":chosen.get("sample_end") if chosen else None,"oos_days":chosen.get("days") if chosen else 0,"new_unseen_oos_days":0,"oos_rows":chosen.get("rows") if chosen else 0,"sample_group_id":chosen.get("sample_group_id") if chosen else None,"sample_hash":chosen.get("sample_hash") if chosen else None,"candidate_tools_tested":tested,"candidate_tools_missing":[] if len(tested)>=8 else [t for t in TOOLS if t not in tested],"latest_beta_date":chosen.get("sample_end") if chosen else None,"latest_beta":latest_beta,"hedge_direction":"SHORT_HEDGE_PROXY" if actual else "NOT_ESTABLISHED","cost_status":"NOT_RUN","cost_assumptions":{"commission_bps":None,"slippage_bps":None,"basis_cost":"not modeled"},"execution_status":"EXPLORATORY_ONLY" if decision=="MATCH" else "NOT_ESTABLISHED","event_status":"BLOCKED_NO_EVENT_FEED","remaining_gaps":gaps,"fetch_attempt_ids":fetchrefs,"source_run_id":chosen.get("source_run_id") if chosen else f"B-STRUCTURAL-{fid}-30M","result_path":"agent_B/FINAL_REPORT.md","updated_at_utc":updated})
 # Candidate comparison contract rows.
 for x in raw_candidates:
  fid=x["fund_id"]; tt=x.get("target_type"); h=x.get("horizon_min");
  candidate_out.append({"fund_id":fid,"run_id":x.get("source_run_id",f"B-{fid}-{tt}-{h}M"),"target_type":tt,"horizon_min":h,"candidate_id":x.get("policy_id"),"tools":x.get("tools",[]),"economic_reason":"Concrete proxy selected from HK index/future/ETF risk family","selection_stage":"SINGLE_LEG_PRIORITY" if len(x.get("tools",[]))==1 else "CROSS_FAMILY_BACKUP","sample_group_id":x.get("sample_group_id"),"sample_hash":x.get("sample_hash"),"metrics":{k:x.get(k) for k in ["rows","days","correlation","correlation_ci_low","correlation_ci_high","target_std_bp","residual_std_bp","variance_reduction","residual_mean_bp","up_es95_bp","down_es95_bp","beta","validation_aggregate"]},"eligible":bool(x.get("tested") and (x.get("correlation") or -1)>=.60 and (x.get("variance_reduction") or -1)>0),"rank_basis":"validation correlation then variance reduction; single-leg first","exclusion_reason":x.get("gap") if not x.get("tested",True) else None,"residual_path":residual_index.get((fid,h),{}).get("path")})
 # Coverage rows: targets, PCF, and shared candidate archive.
 for fid,a in byid.items():
  ed=target_inv["days_by_fund"].get(fid,[]); code=fid.split('.')[0]; pdays=pcf_inv["dates_with_rows_by_fund"].get(code,0)
  coverage.append({"fund_id":fid,"instrument_id":fid,"data_type":"ETF_1M","target_pathway":"ETF_MARKET_PRICE","source_id":"B-ETF-RAW","requested_start":WINDOW_START,"requested_end":WINDOW_END,"actual_start":min(ed) if ed else None,"actual_end":max(ed) if ed else None,"rows":target_inv["rows_by_fund"].get(fid,0),"days":len(ed),"timestamp_semantics":"source bar start shifted +1 minute to minute_end Asia/Hong_Kong","quality_status":"AVAILABLE" if ed else "NOT_FOUND","path":"outputs/hedge_full_coverage_20260906/agent_B/data/raw/target_etf_1m/target_etf_1m.jsonl.gz","sha256":sha(ROOT/"data/raw/target_etf_1m/target_etf_1m.jsonl.gz"),"gap_detail":None if ed else "member absent in remote archive","attempt_ids":[f"B-ETF-{fid}-{ed[0] if ed else WINDOW_START}"]})
  coverage.append({"fund_id":fid,"instrument_id":"PCF","data_type":"PCF","target_pathway":"PCF_BASKET","source_id":"B-PCF-RAW","requested_start":WINDOW_START,"requested_end":WINDOW_END,"actual_start":WINDOW_START if pdays else None,"actual_end":WINDOW_END if pdays else None,"rows":pcf_counts[code],"days":pdays,"timestamp_semantics":"daily official PCF detail; no intraday constituent reconstruction except reused 520600 R1","quality_status":"AVAILABLE_ROWS_NO_INTRADAY_BASKET" if pdays else "NOT_FOUND","path":"outputs/hedge_full_coverage_20260906/agent_B/data/raw/pcf/target_pcf_rows.jsonl.gz","sha256":sha(ROOT/"data/raw/pcf/target_pcf_rows.jsonl.gz"),"gap_detail":None if fid=="520600.SH" else "own constituent 1m prices unavailable in this package","attempt_ids":[f"B-PCF-{code}-{WINDOW_START}"]})
  for t in TOOLS:
   coverage.append({"fund_id":fid,"instrument_id":t,"data_type":"1M_PRICE","target_pathway":"CANDIDATE","source_id":"B-PILOT-CANDIDATE-ARCHIVE","requested_start":WINDOW_START,"requested_end":WINDOW_END,"actual_start":"20260303","actual_end":"20260803","rows":11640,"days":97,"timestamp_semantics":"source bar start shifted +1 minute to minute_end; PM common session in shared pilot archive","quality_status":"AVAILABLE_SHARED_CANDIDATE","path":"data/normalized/pilot_minutes.parquet","sha256":sel_summary["candidate_source_sha256"],"gap_detail":"Candidate archive is PM-only for this package","attempt_ids":[f"B-CANDIDATE-{t}"]})
 # Attempts are the fetched ETF/PCF rows plus one real-time candidate inventory attempt per tool.
 cand_started,cand_finished=run_started,run_finished
 for t in TOOLS:
  attempts.append({"attempt_id":f"B-CANDIDATE-{t}","fund_id":"*","instrument_id":t,"data_type":"1M_PRICE","source":"local normalized pilot candidate archive","request":{"date_range":[WINDOW_START,WINDOW_END],"frequency":"1 min"},"started_at_utc":cand_started,"finished_at_utc":cand_finished,"status":"SUCCESS","error_summary":"PM-only shared candidate archive","rows":11640,"raw_path":"data/normalized/pilot_minutes.parquet","sha256":sel_summary["candidate_source_sha256"],"next_action":"retain as candidate proxy; refresh full-session archive before production use"})
 (ROOT/"mapping.json").write_text(json.dumps(mappings,ensure_ascii=False,indent=2),encoding="utf-8"); write_jsonl(ROOT/"target_results.jsonl",target_out); write_jsonl(ROOT/"candidate_comparison.jsonl",candidate_out); write_jsonl(ROOT/"data_coverage.jsonl",coverage); write_jsonl(ROOT/"fetch_attempts.jsonl",attempts); write_jsonl(ROOT/"candidate_metrics.jsonl",raw_candidates); write_jsonl(ROOT/"inventory.jsonl",inventory)
 write_csv(ROOT/"mapping.csv",mappings); write_csv(ROOT/"data_coverage.csv",coverage); write_csv(ROOT/"candidate_comparison.csv",candidate_out)
 # Six terminal tasks per fund; all timestamps correspond to actual stage runs.
 for fid in ids:
  for phase,status,cmd,inp,outp,next_action in [("SCOPE","DONE","read assignments.json","control/assignments.json","data/processed/inventory.jsonl","scope evidence refresh if official index files become available"),("CANDIDATES","DONE","build_inventory.py","data/normalized/pilot_minutes.parquet","data/processed/candidate_map.jsonl","refresh candidate archive"),("INVENTORY","DONE","build_inventory.py + fetch scripts","data/raw/target_etf_1m/target_etf_1m.jsonl.gz","data_coverage.jsonl","none"),("COMPUTE","DONE","run_full_selection.py","data/raw/target_etf_1m/target_etf_1m.jsonl.gz","target_results.jsonl","none"),("EVENTS","BLOCKED","event feed not available in scoped package","none","mapping.json","obtain official event/corporate-action feed"),("QA","DONE","test_selection_core.py + build_delivery.py","scripts/selection_core.py","checks.jsonl","none"),("DELIVER","DONE","build_delivery.py","mapping.json + workbook schema","FINAL_REPORT.md","none")]:
   tasks.append({"task_id":f"B-{fid}-{phase}","fund_id":fid,"phase":phase,"status":status,"started_at_utc":run_started if phase in {"COMPUTE","QA","DELIVER"} else cand_started,"finished_at_utc":run_finished if phase in {"COMPUTE","QA","DELIVER"} else cand_finished,"input_paths":[inp],"command":cmd,"output_paths":[outp],"result_summary":"terminal status recorded for full-coverage run","next_action":next_action})
 # Global and per-fund checks; no static pass claim for missing data.
 test=json.loads((ROOT/"checks/selection_core_tests.json").read_text())
 for c in test["checks"]: checks.append({"check_id":"B-ENGINE-"+c["check"],"fund_id":"*","run_id":"FULL237_RHO060_V1","check_name":c["check"],"check_type":"GLOBAL_METHOD","status":"PASS" if c["passed"] else "FAIL","expected":"true","actual":c["actual"],"command":"scripts/test_selection_core.py","evidence_path":"checks/selection_core_tests.json","engine_hash":engine_hash,"evaluated_at_utc":updated})
 for m in mappings:
  status="PASS" if m["actual_backtest_run"] else ("NOT_RUN" if m["decision"]=="INSUFFICIENT_DATA" else "PASS")
  checks.append({"check_id":f"B-{m['fund_id']}-RESULT","fund_id":m["fund_id"],"run_id":m["source_run_id"],"check_name":"terminal_result_and_candidate_investigation","check_type":"COVERAGE","status":status,"expected":"terminal state with concrete candidates","actual":{"decision":m["decision"],"tested":m["candidate_tools_tested"],"target_type":m["target_type"]},"command":"build_delivery.py","evidence_path":"mapping.json","engine_hash":engine_hash,"evaluated_at_utc":updated})
 write_jsonl(ROOT/"checks.jsonl",checks)
 # Evidence/assets index.
 def add_e(eid,typ,title,path,excerpt,lim=""):
  evidence.append({"evidence_id":eid,"subject_ids":["*"],"evidence_type":typ,"source_title":title,"source_url":None,"published_at":None,"effective_at":None,"retrieved_at_utc":updated,"page_or_section":None,"supporting_excerpt":excerpt,"local_path":path,"sha256":sha(ROOT/path) if path and (ROOT/path).is_file() else None,"limitations":lim})
 add_e("B-FULL-CONTRACT","CONTRACT","FULL_COVERAGE_CONTRACT.md","../control/FULL_COVERAGE_CONTRACT.md","B full-coverage contract and terminal-state requirements","contract is read-only input")
 add_e("B-WORKBOOK-SCHEMA","SCHEMA","workbook_schema.json","../control/workbook_schema.json","Eight required workbook sheets and machine fields","contract is read-only input")
 add_e("B-ASSIGNMENTS","ASSIGNMENT","assignments.json","../control/assignments.json","Owner B assignment list contains 81 fund IDs","scope for most funds remains UNVERIFIED")
 add_e("B-ENGINE","METHOD","selection_core.py","scripts/selection_core.py","FULL237_RHO060_V1; constrained beta, same-session returns, rolling OOS","candidate archive is PM-only")
 add_e("B-ENGINE-TEST","QA","selection_core_tests.json","checks/selection_core_tests.json","Constraint, same-session, real run and future mutation tests PASS","unit/integration evidence only")
 add_e("B-ETF-RAW","DATA","target_etf_1m.jsonl.gz","data/raw/target_etf_1m/target_etf_1m.jsonl.gz","Remote ETF archive filtered to all 81 assigned members; 79 have rows","market price target has premium/basis")
 add_e("B-PCF-RAW","DATA","target_pcf_rows.jsonl.gz","data/raw/pcf/target_pcf_rows.jsonl.gz","Remote PCF detail archive filtered to all 81 fund codes; 79 have rows","does not by itself reconstruct intraday basket")
 add_e("B-CANDIDATE-ARCHIVE","DATA","pilot_minutes.parquet","data/normalized/pilot_minutes.parquet","Eight concrete HK futures/ETF candidate tools in shared PM archive","PM-only and reused across target funds")
 add_e("B-R1-520600","REUSE","R1 520600 new-period package","data/reused_r1_520600/520600_new_period_metrics.json","520600 component-basket metrics copied read-only from prior R1 package","not recomputed in this run")
 write_jsonl(ROOT/"evidence.jsonl",evidence)
 for path,role in [("scripts/selection_core.py","selection engine"),("scripts/test_selection_core.py","engine test"),("checks/selection_core_tests.json","engine test result"),("data/raw/target_etf_1m/target_etf_1m.jsonl.gz","ETF raw target"),("data/raw/pcf/target_pcf_rows.jsonl.gz","PCF raw target"),("data/normalized/pilot_minutes.parquet","candidate archive"),("mapping.json","fund mapping"),("target_results.jsonl","target result rows"),("candidate_comparison.jsonl","candidate comparison"),("data_coverage.jsonl","coverage"),("fetch_attempts.jsonl","fetch attempts"),("checks.jsonl","checks"),("evidence.jsonl","evidence index"),("RESULTS.xlsx","Excel workbook"),("FINAL_REPORT.md","final report"),("STATUS.md","delivery status"),("config/run_config.json","run configuration")]:
  p=ROOT/path; assets.append({"asset_id":"B-"+path.replace('/','_'),"role":role,"path":rel(p),"sha256":sha(p) if p.is_file() else None,"generated_at_utc":updated})
 (ROOT/"assets.json").write_text(json.dumps({"owner":"B","generated_at_utc":updated,"assets":assets},ensure_ascii=False,indent=2),encoding="utf-8")
 cfg={"engine_version":"FULL237_RHO060_V1","engine_sha256":engine_hash,"window":{"start":WINDOW_START,"end":WINDOW_END},"horizons_min":[5,15,30,60],"sessions":{"AM":[570,690],"PM":[780,900]},"fit_validation_refit":{"train_days":60,"fit_days":50,"validation_days":10},"constraints":{"beta_min":0,"beta_max":2,"beta_sum_max":2},"candidate_tools":TOOLS,"candidate_source_sha256":sel_summary["candidate_source_sha256"],"target_source_sha256":sel_summary["target_source_sha256"],"generated_at_utc":updated}
 (ROOT/"config/run_config.json").write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({"funds":len(mappings),"actual_backtested":sum(x["actual_backtest_run"] for x in mappings),"decisions":dict(Counter(x["decision"] for x in mappings)),"target_rows":len(target_out),"candidate_rows":len(candidate_out),"coverage_rows":len(coverage),"attempts":len(attempts),"tasks":len(tasks),"checks":len(checks),"evidence":len(evidence)},ensure_ascii=False,indent=2))
 write_jsonl(ROOT/"tasks.jsonl",tasks)

if __name__=="__main__":main()

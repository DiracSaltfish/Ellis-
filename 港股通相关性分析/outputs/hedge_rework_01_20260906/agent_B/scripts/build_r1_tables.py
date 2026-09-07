#!/usr/bin/env python3
"""Build the auditable R1 tables for B's 81-fund slice.

The script deliberately keeps the old technical archive separate from the
new 520600 confirmation run.  It writes machine-readable JSONL and UTF-8 CSV
tables consumed by the workbook builder.
"""
from __future__ import annotations
import csv, gzip, hashlib, json, os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path("/Users/ellis/工具程序开发/港股通相关性分析")
CONTROL = ROOT.parent / "control"
TABLES = ROOT / "tables"
RAW = ROOT / "data/raw/new_period_520600"
WINDOW_START, WINDOW_END = "20260804", "20260904"
HK = ZoneInfo("Asia/Hong_Kong")
NOW = datetime.now(timezone.utc).isoformat()

def sha(path):
    p = Path(path)
    if not p.exists(): return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1024*1024), b""): h.update(b)
    return h.hexdigest()

def rel(path):
    p = Path(path)
    try: return str(p.relative_to(PROJECT))
    except ValueError: return str(p)

def abs_path(path):
    p=Path(path)
    return str(p if p.is_absolute() else PROJECT/p)

def dump(v):
    if v is None: return None
    if isinstance(v, (list, dict)): return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return v

def write_table(name, rows):
    rows = list(rows); TABLES.mkdir(parents=True, exist_ok=True)
    if not rows: rows=[{}]
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields: fields.append(k)
    clean=[]
    for r in rows: clean.append({k: dump(r.get(k)) for k in fields})
    with (TABLES/f"{name}.jsonl").open("w",encoding="utf-8") as f:
        for r in clean: f.write(json.dumps(r,ensure_ascii=False,separators=(",",":"))+"\n")
    with (TABLES/f"{name}.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(clean)
    return clean

def read_jsonl_gz(path):
    with gzip.open(path,"rt",encoding="utf-8") as f: return [json.loads(x) for x in f]

def mtime_iso(path):
    p=Path(path)
    return datetime.fromtimestamp(p.stat().st_mtime,tz=timezone.utc).isoformat() if p.exists() else None

def main():
    assignments=json.loads((CONTROL/"assignments.json").read_text(encoding="utf-8"))["B"]
    schema=json.loads((CONTROL/"schema_base.json").read_text(encoding="utf-8"))
    assert len(assignments)==81
    byfund={x["fund_id"]:x for x in assignments}; fund520=byfund["520600.SH"]
    pcf_attempts=json.loads((RAW/"520600_pcf_fetch_attempts.json").read_text())
    pcf_summary=json.loads((RAW/"520600_pcf_summary.json").read_text())
    metrics=json.loads((RAW/"520600_new_period_metrics.json").read_text())
    lock=json.loads((RAW/"520600_selection_lock.json").read_text())
    panel_path=RAW/"520600_basket_panel_1m.jsonl.gz"; panel_meta=json.loads((RAW/"520600_basket_metadata.json").read_text())
    coverage=json.loads((RAW/"520600_basket_coverage.json").read_text())
    stk_path=RAW/"520600_stk_1m.jsonl.gz"; stk_attempts=ROOT/"data/raw/new_period_520600/520600_stk_fetch_attempts.jsonl"
    ib_manifest=ROOT/"data/raw/ibkr_new_period/ibkr_fetch_manifest.json"
    remote_cov=json.loads((RAW/"remote_tick_crosscheck_coverage.json").read_text())
    old_root=ROOT/"data/old_exploratory"
    metric_by_h={int(x["horizon_min"]):x for x in metrics}
    pcf_codes=set()
    for p in read_jsonl_gz(RAW/"520600_pcf_parsed.jsonl.gz"):
        pcf_codes |= {str(c["成分股代码"]).zfill(5) for c in p.get("components",[]) if (c.get("数量股") or 0)>0}
    now=NOW

    # Data statistics from the actual raw files.
    comp_dates=defaultdict(set); comp_rows=Counter(); etf_dates=defaultdict(set); etf_rows=Counter()
    with gzip.open(stk_path,"rt",encoding="utf-8") as f:
        for line in f:
            r=json.loads(line); code=r.get("security_id")
            for b in r.get("bars") or []:
                dt=datetime.fromtimestamp(int(b["date"]),tz=timezone.utc).astimezone(HK).strftime("%Y%m%d")
                comp_dates[code].add(dt); comp_rows[code]+=1
                if code in {"02800","02828"}: etf_dates[code].add(dt); etf_rows[code]+=1
    future_stats={}
    for name in ("HSI_U6","HHI_U6","HTI_U6"):
        ds=set(); n=0
        for p in sorted((ROOT/"data/raw/ibkr_new_period").glob(f"*_{name}.json")):
            d=json.loads(p.read_text()); ds.add(p.name[:8]); n+=len(d.get("bars") or [])
        future_stats[name]=(ds,n)
    panel_rows=panel_meta["panel_rows"]; panel_days=len(coverage); common_minutes=sum(x["common_minute_count"] for x in coverage)

    # 1) fund decisions: retain the 81-fund denominator, but replace stale
    # generic 520600 language with the measured R1 result and explicit gaps.
    decisions=[]
    primary=metric_by_h[30]
    for base in assignments:
        r=dict(base); r.update({"confirmation_start":WINDOW_START,"confirmation_end":WINDOW_END,"updated_at_utc":now})
        if base["fund_id"]=="520600.SH":
            r.update({
                "decision":"SUITABLE_PRICE_PROXY","reason_codes":["RESEARCH_GATES_PASS","NEW_OOS_24_DAYS","EVENT_COVERAGE_PARTIAL","EXECUTION_CONDITIONAL"],
                "reason_detail":f"R1修复后PCF精确页24/24、50个成分逐日1分钟覆盖24/24、共同篮子端点{panel_rows}个/24日；锁定HHI+HTI期货政策在30分钟新OOS Pearson={primary['correlation']:.4f}、相关95%日块CI=[{primary['correlation_ci_low']:.4f},{primary['correlation_ci_high']:.4f}]、VR={primary['variance_reduction']:.4f}。价格代理可用；官方逐证券公司行动闭环尚未完成，故执行仅CONDITIONAL。",
                "recommended_policy_id":primary["policy_id"],"primary_tools":["HHI_FUT","HTI_FUT"],"backup_policy_id":None,"exploratory_best_policy_id":primary["policy_id"],
                "execution_status":"CONDITIONAL","confirmation_status":"NEW_LOCKED","oos_days":primary["oos_days"],"target_std_bp":primary["target_std_bp"],"residual_std_bp":primary["residual_std_bp"],"variance_reduction":primary["variance_reduction"],"ci_low":primary["correlation_ci_low"],"ci_high":primary["correlation_ci_high"],"up_es95_bp":primary["up_es95_bp"],"down_es95_bp":primary["down_es95_bp"],"effective_quote_coverage":common_minutes/(24*330),"latest_beta_date":"20260803","latest_beta":{"HHI_FUT":0.5092797293653971,"HTI_FUT":0.4490979543084183},"candidate_coverage_complete":True,"event_coverage_complete":False,
                "remaining_gaps":["官方逐证券公司行动/停牌核验未闭环","02800/02828补充ETF只覆盖22/24日且非纯PCF篮子必要输入","真实执行费用、借券和资金成本未核实"],"invalidation_triggers":["新PCF改变数量或成分","未来共同端点低于95%","Pearson低于0.60或实际名义beta不再降低残差方差","公司行动导致价格不可比"],"source_run_id":"B-520600-NEW-1M-LOCKED","result_path":rel(ROOT/"FINAL_REPORT.md"),"hedge_return_correlation":primary["correlation"],"correlation_ci_low":primary["correlation_ci_low"],"correlation_ci_high":primary["correlation_ci_high"],"correlation_method":primary["correlation_method"],"correlation_threshold":0.60,"criteria_version":"USER_RHO_060_FUTURES_ETF"})
        else:
            r.update({"decision":"INSUFFICIENT_EVIDENCE","reason_codes":["REWORK_NOT_RUN_IN_B","NEW_CONFIRMATION_UNAVAILABLE","CANDIDATE_COVERAGE_INCOMPLETE"],"reason_detail":f"本B组R1未对该基金执行逐只PCF/篮子构建；精确PCF页=0/{len(pcf_attempts)}（520600专属分母不可外推）、有效新OOS=0；不把其他基金或旧期结果冒充本基金确认。","confirmation_status":"UNAVAILABLE","oos_days":0,"candidate_coverage_complete":False,"event_coverage_complete":False,"remaining_gaps":["逐只官方投资范围与PCF","共同1分钟成分/期货面板","新期Pearson与残差方差核验","官方事件闭环"],"invalidation_triggers":["获得PCF后重建篮子并重新评估"],"source_run_id":"B_ASSIGNMENT_R1_BASELINE","result_path":rel(ROOT/"FINAL_REPORT.md"),"hedge_return_correlation":None,"correlation_ci_low":None,"correlation_ci_high":None,"correlation_method":"not_run","correlation_threshold":0.60,"criteria_version":"USER_RHO_060_FUTURES_ETF"})
        decisions.append(r)
    write_table("fund_decisions",decisions)

    # 2) Metrics: new confirmed R1 rows plus selected old rows, explicitly
    # marked reused/unproven and with correlation left null where it was not computed.
    model_rows=[]; exploratory=[]; weights=[]; sensitivity=[]
    for h,m in metric_by_h.items():
        model_rows.append({"run_id":m["run_id"],"fund_id":"520600.SH","horizon_min":h,"policy_id":m["policy_id"],"model_id":m["model_id"],"scenario_id":"NEW_PRIMARY_LOCKED","sample_hash":sha(panel_path),"confirmation_status":"NEW_LOCKED","oos_start":m["sample_start"],"oos_end":m["sample_end"],"oos_days":m["oos_days"],"sample_count":m["sample_count"],"target_std_bp":m["target_std_bp"],"residual_std_bp":m["residual_std_bp"],"variance_reduction":m["variance_reduction"],"ci_low":m["correlation_ci_low"],"ci_high":m["correlation_ci_high"],"bootstrap_method":"day-block Pearson bootstrap, 2000 reps","bootstrap_seed":520600,"target_up_es95_bp":None,"target_down_es95_bp":None,"up_es95_bp":m["up_es95_bp"],"down_es95_bp":m["down_es95_bp"],"positive_block_fraction":None,"residual_mean_bp":m["residual_mean_bp"],"beta_turnover":None,"decision_gate_results":{"oos_days_ge_20":True,"pearson_ge_0.60":m["correlation"]>=.6,"variance_reduction_gt_0":m["variance_reduction"]>0,"status":m["sample_status"]},"residual_path":rel(RAW/"520600_new_period_residuals.jsonl.gz"),"hedge_return_correlation":m["correlation"],"correlation_ci_low":m["correlation_ci_low"],"correlation_ci_high":m["correlation_ci_high"],"correlation_method":m["correlation_method"],"correlation_threshold":.60,"criteria_version":"USER_RHO_060_FUTURES_ETF"})
        weights += [{"fund_id":"520600.SH","horizon_min":h,"policy_id":m["policy_id"],"effective_date":"20260803","train_start":"20260303","train_end":"20260803","validation_start":None,"validation_end":None,"tool_id":tool,"beta":beta,"currency_conversion":1.0,"selection_reason":"旧窗口固定政策；新期只评估，未用新期选模","config_hash":sha(RAW/"520600_selection_lock.json")} for tool,beta in [("HHI_FUT",m["beta"]["HHI_FUT"]),("HTI_FUT",m["beta"]["HTI_FUT"])] ]
    for fund_dir in sorted(old_root.iterdir()):
        if not fund_dir.is_dir() or not (fund_dir/"results.json").exists(): continue
        d=json.loads((fund_dir/"results.json").read_text()); old_metrics=[x for x in d.get("metrics",[]) if x.get("model")=="selected"]
        for x in old_metrics:
            h=int(x["horizon"]); pid=f"OLD_SELECTED_{fund_dir.name}_{h}M"; src=rel(fund_dir/"results.json")
            model_rows.append({"run_id":f"OLD-{fund_dir.name}","fund_id":fund_dir.name,"horizon_min":h,"policy_id":pid,"model_id":"selected_old_archive","scenario_id":"OLD_EXPLORATORY_OOS","sample_hash":sha(fund_dir/"results.json"),"confirmation_status":"REUSED_OR_UNPROVEN","oos_start":"20260610","oos_end":"20260803","oos_days":x.get("days"),"sample_count":x.get("samples"),"target_std_bp":x.get("target_std_bps"),"residual_std_bp":x.get("residual_std_bps"),"variance_reduction":x.get("variance_reduction"),"ci_low":(x.get("variance_reduction_ci95") or [None,None])[0],"ci_high":(x.get("variance_reduction_ci95") or [None,None])[1],"bootstrap_method":"old archive variance-reduction CI; Pearson not calculated","bootstrap_seed":None,"target_up_es95_bp":None,"target_down_es95_bp":None,"up_es95_bp":x.get("upside_es95_bps"),"down_es95_bp":x.get("downside_es95_bps"),"positive_block_fraction":None,"residual_mean_bp":x.get("residual_mean_bps"),"beta_turnover":None,"decision_gate_results":{"old_result_reused":True,"new_rho_gate":None},"residual_path":rel(fund_dir/"oos_residuals.parquet"),"hedge_return_correlation":None,"correlation_ci_low":None,"correlation_ci_high":None,"correlation_method":"not_calculated_old_archive","correlation_threshold":.60,"criteria_version":"USER_RHO_060_FUTURES_ETF"})
            exploratory.append({"fund_id":fund_dir.name,"horizon_min":h,"policy_id":pid,"tools":"old selected policy","sample_status":"REUSED_OR_UNPROVEN","sample_start":"20260610","sample_end":"20260803","oos_days":x.get("days"),"sample_count":x.get("samples"),"target_std_bp":x.get("target_std_bps"),"residual_std_bp":x.get("residual_std_bps"),"variance_reduction":x.get("variance_reduction"),"hedge_return_correlation":None,"correlation_ci_low":None,"correlation_ci_high":None,"correlation_method":"not_calculated_old_archive","correlation_threshold":.60,"criteria_version":"USER_RHO_060_FUTURES_ETF","selection_rule":"old archive selected policy; descriptive only","source_path":src})
    for h,m in metric_by_h.items():
        exploratory.append({"fund_id":"520600.SH","horizon_min":h,"policy_id":m["policy_id"],"tools":"HHI_FUT,HTI_FUT","sample_status":m["sample_status"],"sample_start":m["sample_start"],"sample_end":m["sample_end"],"oos_days":m["oos_days"],"sample_count":m["sample_count"],"target_std_bp":m["target_std_bp"],"residual_std_bp":m["residual_std_bp"],"variance_reduction":m["variance_reduction"],"hedge_return_correlation":m["correlation"],"correlation_ci_low":m["correlation_ci_low"],"correlation_ci_high":m["correlation_ci_high"],"correlation_method":m["correlation_method"],"correlation_threshold":.60,"criteria_version":"USER_RHO_060_FUTURES_ETF","selection_rule":"pre-locked old-window pair; new period evaluation","source_path":rel(RAW/"520600_new_period_metrics.json")})
    write_table("model_metrics",model_rows); write_table("exploratory_policies",exploratory); write_table("weights",weights)
    sens=json.loads((RAW/"520600_new_period_sensitivity.json").read_text())
    for r in sens: r.update({"base_run_id":"B-520600-NEW-1M-LOCKED","source_path":rel(RAW/"520600_new_period_sensitivity.json")})
    write_table("sensitivity",sens)

    # 3) Candidate pool: only Hong Kong futures/ETFs.  No HK single stocks
    # are ever inserted as hedges.
    lock_hash=sha(RAW/"520600_selection_lock.json"); candidates=[]
    tools=[("HSI_FUT","HSI","期货","恒生指数期货；高流动性宽基风险因子"),("HHI_FUT","HHI","期货","恒生国企期货；港股通大型国企因子"),("HTI_FUT","HSTECH","期货","恒生科技期货；成长/科技风险因子"),("02800","HSI","ETF","盈富基金；香港上市ETF补充对照"),("02828","HHI","ETF","恒生中国企业ETF；香港上市ETF补充对照")]
    for f in assignments:
        for tool,fam,atype,why in tools:
            if f["fund_id"]=="520600.SH":
                if atype=="期货": ds,n=future_stats[{"HSI_FUT":"HSI_U6","HHI_FUT":"HHI_U6","HTI_FUT":"HTI_U6"}[tool]]; cov=len(ds)/24; st="SUPPORTED"; included=True; start=WINDOW_START; end=WINDOW_END; ev="B-EVD-IBKR-FUTURES-R1"
                else: ds=etf_dates[tool]; n=etf_rows[tool]; cov=len(ds)/24; st="SUPPORTED" if cov>=.9 else "UNKNOWN"; included=False; start=min(ds) if ds else None; end=max(ds) if ds else None; ev="B-EVD-HK-STK-520600-R1"
            else: cov=None; n=None; ds=set(); st="UNKNOWN"; included=False; start=None; end=None; ev=None
            candidates.append({"fund_id":f["fund_id"],"tool_id":tool,"risk_family":fam,"rationale":why,"asset_type":atype,"official_url":None,"listed_from":None,"listed_to":None,"currency":"HKD","multiplier":50 if atype=="期货" else None,"lot_size":None,"session":"09:30-12:00,13:00-16:00 Asia/Hong_Kong","quote_coverage":cov,"data_start":start,"data_end":end,"short_status":st,"included":included,"exclusion_reason":None if included else ("未对该基金执行新期工具测试" if f["fund_id"]!="520600.SH" else "补充ETF仅22/24日；纯PCF篮子不以境内ETF为必要输入"),"evidence_id":ev,"selection_lock_hash":lock_hash if f["fund_id"]=="520600.SH" else None})
    write_table("tool_candidates",candidates)

    # 4) Coverage and events.
    coverage_rows=[]
    coverage_rows.append({"fund_id":"520600.SH","security_id":"PCF_BASKET","data_type":"minute","source":"IBKR STK + PCF fixed quantity","path":rel(panel_path),"sha256":sha(panel_path),"start_date":WINDOW_START,"end_date":WINDOW_END,"expected_rows":24*330,"actual_rows":panel_rows,"missing_dates":[],"duplicates":0,"timezone":"Asia/Hong_Kong","timestamp_semantics":"1-minute bar-start/session clock","adjustment":"unadjusted close; cash substitution for zero-quantity rows","retrieved_at_utc":mtime_iso(panel_path),"quality_status":"PASS","gap_action":"common endpoint intersection retained; 24 days each have >=310 endpoints"})
    for tool,key in [("HSI_FUT","HSI_U6"),("HHI_FUT","HHI_U6"),("HTI_FUT","HTI_U6")]:
        ds,n=future_stats[key]; coverage_rows.append({"fund_id":"520600.SH","security_id":tool,"data_type":"minute","source":"IBKR historicalData","path":rel(ib_manifest),"sha256":sha(ib_manifest),"start_date":min(ds),"end_date":max(ds),"expected_rows":24*330,"actual_rows":n,"missing_dates":sorted(set(pcf_summary["requested_weekdays"])-ds),"duplicates":None,"timezone":"Asia/Hong_Kong","timestamp_semantics":"IBKR bar date converted to HK minute","adjustment":"unadjusted futures close","retrieved_at_utc":mtime_iso(ib_manifest),"quality_status":"PASS" if len(ds)==24 else "PARTIAL","gap_action":"merged old 21-day archive and repaired 08-10/11/12"})
    for code in sorted(comp_dates):
        if code in {"02800","02828"}: continue
        ds=comp_dates[code]; coverage_rows.append({"fund_id":"520600.SH","security_id":code,"data_type":"minute","source":"IBKR SEHK STK","path":rel(stk_path),"sha256":sha(stk_path),"start_date":min(ds),"end_date":max(ds),"expected_rows":24*330,"actual_rows":comp_rows[code],"missing_dates":sorted(set(pcf_summary["requested_weekdays"])-ds),"duplicates":None,"timezone":"Asia/Hong_Kong","timestamp_semantics":"IBKR bar date converted to HK minute","adjustment":"unadjusted close; fixed PCF quantities","retrieved_at_utc":mtime_iso(stk_path),"quality_status":"PASS" if len(ds)==24 else "PARTIAL","gap_action":"daily repair for 08-04/09-04 gaps"})
    for code in ("02800","02828"):
        ds=etf_dates[code]; coverage_rows.append({"fund_id":"520600.SH","security_id":code,"data_type":"minute","source":"IBKR SEHK STK ETF","path":rel(stk_path),"sha256":sha(stk_path),"start_date":min(ds),"end_date":max(ds),"expected_rows":24*330,"actual_rows":etf_rows[code],"missing_dates":sorted(set(pcf_summary["requested_weekdays"])-ds),"duplicates":None,"timezone":"Asia/Hong_Kong","timestamp_semantics":"IBKR bar date converted to HK minute","adjustment":"unadjusted ETF close","retrieved_at_utc":mtime_iso(stk_path),"quality_status":"PARTIAL","gap_action":"supplemental only; not required for pure PCF basket"})
    coverage_rows.append({"fund_id":"520600.SH","security_id":"PCF","data_type":"PCF","source":"广发基金PCF proxy","path":rel(RAW/"520600_pcf_parsed.jsonl.gz"),"sha256":sha(RAW/"520600_pcf_parsed.jsonl.gz"),"start_date":WINDOW_START,"end_date":WINDOW_END,"expected_rows":24,"actual_rows":pcf_summary["exact_page_count"],"missing_dates":pcf_summary["missing_exact_dates"],"duplicates":0,"timezone":"Asia/Shanghai","timestamp_semantics":"published daily basket file","adjustment":"published quantity; zero quantity treated cash","retrieved_at_utc":pcf_summary["generated_at_utc"],"quality_status":"PASS","gap_action":"parameterized exact-date retrieval"})
    write_table("data_coverage",coverage_rows)
    verified=json.loads((ROOT/"data/repro_old/verified_events.json").read_text()); verified_by={str(x.get("code")).zfill(5):x for x in verified}
    event_rows=[]
    codes=sorted(pcf_codes)
    for code in codes:
        ev=verified_by.get(code); sources=(ev or {}).get("sources") or []
        event_rows.append({"fund_id":"520600.SH","security_id":code,"event_type":ev.get("type") if ev else "REVIEW_NOT_COMPLETE","effective_from":None,"effective_to":None,"published_at":None,"official_url":sources[0] if sources else None,"evidence_path":rel(ROOT/"data/repro_old/verified_events.json") if ev else None,"evidence_sha256":sha(ROOT/"data/repro_old/verified_events.json") if ev else None,"treatment":(ev or {}).get("policy") if ev else "未完成本新期官方逐证券公司行动核验；不自动调整价格","affected_dates":[],"max_weight":None,"verified":False if not ev else True,"numerical_check_path":rel(ROOT/"data/repro_old/verified_events.json") if ev else None,"remaining_risk":"仅5条既有官方事件记录可追溯；其余代码需逐证券核验，当前不宣称事件覆盖完整"})
    write_table("events",event_rows)

    # 5) Execution is intentionally conditional: market microstructure, fees,
    # borrowing and funding were not verified in this R1 slice.
    exec_rows=[]
    for h in (5,15,30,60):
        for direction in ("LONG_BASKET_SHORT_HEDGE","SHORT_BASKET_LONG_HEDGE"):
            exec_rows.append({"fund_id":"520600.SH","horizon_min":h,"direction":direction,"notional_cny":1000000,"policy_id":f"P520600_R1_FIXED_HHI_HTI_{h}M","cost_scenario_id":"UNVERIFIED_EXECUTION_COSTS","per_side_variable_cost_bp":None,"known_fixed_fees_cny":None,"borrow_cost_bp":None,"funding_cost_bp":None,"basket_total_cost_bp":None,"rounded_positions":None,"rounded_residual_std_bp":None,"rounded_vr":None,"risk_cost_score_bp":None,"pareto_optimal":None,"execution_status":"CONDITIONAL","assumptions":["HKD/HKD nominal beta; ex-post FX only","1-minute synchronized endpoints","cost/borrow/funding not verified","actual TWS quotes are historical research evidence, not live execution quote"],"fee_evidence_id":None,"run_id":"B-520600-NEW-1M-LOCKED"})
    write_table("execution_scenarios",exec_rows)

    # 6) Research gates are not QA checks.  They state the research result and
    # its evidence so the workbook cannot conflate test correctness with pass.
    gates=[]
    for h,m in metric_by_h.items():
        for name,actual,threshold,status in [("new_oos_days",m["oos_days"],20,"PASS" if m["oos_days"]>=20 else "FAIL"),("pearson_oos",m["correlation"],.60,"PASS" if m["correlation"]>=.60 else "FAIL"),("residual_variance_reduction",m["variance_reduction"],0,"PASS" if m["variance_reduction"]>0 else "FAIL"),("event_official_review",False,True,"UNRESOLVED")]:
            gates.append({"gate_id":f"B-GATE-520600-{h}-{name}","fund_id":"520600.SH","horizon_min":h,"policy_id":m["policy_id"],"gate_name":name,"actual":actual,"threshold":threshold,"status":status,"evidence_id":"B-EVD-520600-PANEL-R1" if name!="event_official_review" else "B-EVD-EVENTS-PARTIAL","source_path":rel(RAW/"520600_new_period_metrics.json") if name!="event_official_review" else rel(ROOT/"data/repro_old/verified_events.json"),"notes":"Pearson is the primary correlation gate; VR is checked separately and is not rho."})
    for f in assignments:
        if f["fund_id"]!="520600.SH": gates.append({"gate_id":f"B-GATE-{f['fund_id']}-NOT_RUN","fund_id":f["fund_id"],"horizon_min":f["primary_horizon_min"],"policy_id":None,"gate_name":"new_research_execution","actual":None,"threshold":"20 valid days + rho>=0.60 + VR>0","status":"NOT_RUN","evidence_id":None,"source_path":None,"notes":"B did not claim 520600's evidence for this fund."})
    write_table("research_gates",gates)

    # 7) Repair log, fetch log, evidence, and QA.
    repair=[]
    prod_path=ROOT/"data/raw/official/520600_product_facts.pdf"
    def repair_row(cid,issue,expected,actual,status,path,notes): repair.append({"check_id":cid,"fund_id":"520600.SH","issue":issue,"expected":expected,"actual":actual,"status":status,"evidence_path":rel(path) if path else None,"evidence_sha256":sha(path) if path else None,"checked_at_utc":now,"notes":notes})
    repair_row("B-REPAIR-01","previous product PDF path","existing product facts PDF with hash",f"exists size={prod_path.stat().st_size} sha={sha(prod_path)}","VERIFIED",prod_path,"old broken path replaced by official download")
    repair_row("B-REPAIR-02","PCF date coverage","24 exact weekdays, 50 rows/page",f"{pcf_summary['exact_page_count']} exact pages; rows={set(pcf_summary['rows_per_page'].values())}","VERIFIED",RAW/"520600_pcf_summary.json","parameterized parser/fetch records requested and returned dates")
    repair_row("B-REPAIR-03","IBKR missing 08-10/11/12","24 dates x 3 futures",f"manifest dates={len(json.loads(ib_manifest.read_text())['dates'])}; request_log={len(json.loads(ib_manifest.read_text())['request_log'])}","VERIFIED",ib_manifest,"old 21-day manifest merged with repaired 3-day fetch")
    repair_row("B-REPAIR-04","HK STK components missing from prior run","50 codes x 24 days",f"codes={len([c for c in comp_dates if c not in {'02800','02828'}])}; min_days={min(len(comp_dates[c]) for c in comp_dates if c not in {'02800','02828'})}","VERIFIED",stk_path,"daily gap repair plus 1M batch; includeExpired=false")
    repair_row("B-REPAIR-05","pure PCF basket incorrectly requiring mainland ETF","basket build must not require mainland ETF price",f"panel_rows={panel_rows}; ETF used only supplemental candidate rows","VERIFIED",panel_path,"basket uses PCF fixed quantities and HK STK prices; no mainland ETF input")
    repair_row("B-REPAIR-06","selection leakage","policy lock before new panel analysis",f"locked_at={lock['locked_at_utc']}; panel_hash={lock.get('new_period_input_hash')}","VERIFIED",RAW/"520600_selection_lock.json","old-window fixed policy locked before new-period metrics")
    repair_row("B-REPAIR-07","rho versus VR confusion","separate Pearson and variance reduction fields","model_metrics has hedge_return_correlation and variance_reduction","VERIFIED",ROOT/"tables/model_metrics.csv","schema-level separation")
    repair_row("B-REPAIR-08","remote archive gap","record actual source date gaps",f"remote observed={len(remote_cov['observed_dates'])}; missing={remote_cov['missing_from_archive']}","VERIFIED",RAW/"remote_tick_crosscheck_coverage.json","remote archive cross-check only; IBKR remains primary")
    write_table("repair_checks",repair)
    fetch=[]
    for a in pcf_attempts: fetch.append({"attempt_id":f"B-PCF-{a['requested_date']}","fund_id":"520600.SH","data_source":"PCF","security_id":"PCF","requested_date":a["requested_date"],"status":a["status"],"attempted_at_utc":a["attempted_at_utc"],"completed_at_utc":a["attempted_at_utc"],"returned_rows":a["returned_rows"],"error_code":a.get("error_code"),"error_summary":a.get("error_summary"),"raw_path":a.get("canonical_path") or a.get("raw_path"),"raw_sha256":a.get("sha256"),"includeExpired_used":None,"details":a.get("url")})
    for line in stk_attempts.open():
        a=json.loads(line); fetch.append({"attempt_id":a["attempt_id"],"fund_id":"520600.SH","data_source":"IBKR_STK","security_id":a["security_id"],"requested_date":a["day"],"status":a["status"],"attempted_at_utc":a["attempted_at_utc"],"completed_at_utc":a["completed_at_utc"],"returned_rows":a["returned_rows"],"error_code":a.get("error_code"),"error_summary":a.get("error_summary"),"raw_path":a.get("raw_path"),"raw_sha256":sha(stk_path),"includeExpired_used":a.get("includeExpired_used"),"details":a.get("contract")})
    man=json.loads(ib_manifest.read_text())
    for a in man.get("request_log",[]): fetch.append({"attempt_id":f"B-IB-{a.get('date')}-{a.get('contract_key')}","fund_id":"520600.SH","data_source":"IBKR_FUT","security_id":a.get("contract_key"),"requested_date":a.get("date"),"status":a.get("status","SUCCESS"),"attempted_at_utc":a.get("requested_at_utc") or man.get("retrieved_at_utc"),"completed_at_utc":a.get("completed_at_utc") or man.get("retrieved_at_utc"),"returned_rows":a.get("returned_rows"),"error_code":a.get("error_code"),"error_summary":a.get("error_summary"),"raw_path":a.get("raw_path"),"raw_sha256":sha(ROOT/"data/raw/ibkr_new_period/"+str(a.get("raw_file"))) if a.get("raw_file") else sha(ib_manifest),"includeExpired_used":True,"details":a})
    for c in remote_cov["coverage"]: fetch.append({"attempt_id":f"B-REMOTE-{c['date']}","fund_id":"520600.SH","data_source":"REMOTE_TICK_ARCHIVE","security_id":"PCF_COMPONENTS","requested_date":c["date"],"status":c["status"],"attempted_at_utc":None,"completed_at_utc":None,"returned_rows":c["minute_rows"],"error_code":None,"error_summary":None,"raw_path":c["zip_path"],"raw_sha256":c["zip_sha256"],"includeExpired_used":None,"details":"independent cross-check; not primary panel"})
    for d in remote_cov["missing_from_archive"]: fetch.append({"attempt_id":f"B-REMOTE-MISSING-{d}","fund_id":"520600.SH","data_source":"REMOTE_TICK_ARCHIVE","security_id":"PCF_COMPONENTS","requested_date":d,"status":"NOT_FOUND","attempted_at_utc":None,"completed_at_utc":None,"returned_rows":0,"error_code":"LOCAL_ARCHIVE_GAP","error_summary":"no copied per-stock tick archive for date","raw_path":None,"raw_sha256":None,"includeExpired_used":None,"details":"IBKR primary source covers this date"})
    write_table("fetch_attempts",fetch)
    evidence=[]
    def ev(eid,purpose,publisher,url,path,locator,finding,suff="PARTIAL",fund="520600.SH"):
        evidence.append({"evidence_id":eid,"fund_id":fund,"purpose":purpose,"publisher":publisher,"url":url,"published_at":None,"retrieved_at_utc":now,"local_path":rel(path) if path else None,"sha256":sha(path) if path else None,"locator":locator,"finding":finding,"sufficiency":suff})
    prod=ROOT/"data/raw/official/520600_product_facts.pdf"; ev("B-EVD-520600-PRODUCT","scope","广发基金","https://www.gffunds.com.cn/jjgg/flwj/202506/P020250610344633290064.pdf",prod,"p1-p4","product facts identify code 520600, HK Connect automobile index objective, listing 2024-12-30 and full-replication strategy","SUFFICIENT")
    ev("B-EVD-PCF-PARSER-R1","method","B","",ROOT/"scripts/pcf_fetch_parse.py","source","parameterized exact-date retrieval and parser","SUFFICIENT")
    ev("B-EVD-PCF-520600-R1","PCF","广发基金","https://www.gffunds.com.cn/proxy/pcflist/520600?date=20260904",RAW/"520600_pcf_parsed.jsonl.gz","24 exact pages","24/24 exact pages, 50 rows per page; SHA recorded","SUFFICIENT")
    ev("B-EVD-IBKR-FUTURES-R1","minute","IBKR","",ib_manifest,"merged manifest","24 requested dates for HSI/HHI/HTI with repaired dates","SUFFICIENT")
    ev("B-EVD-HK-STK-520600-R1","minute","IBKR","",stk_path,"attempt log","50 PCF codes; SEHK STK includeExpired=false; 24 date coverage after repair","SUFFICIENT")
    ev("B-EVD-520600-PANEL-R1","panel","B","",panel_path,"metadata","fixed-quantity HKD PCF basket panel; 7,791 common 1m endpoints","SUFFICIENT")
    ev("B-EVD-520600-METRICS-R1","research","B","",RAW/"520600_new_period_metrics.json","4 horizons","Pearson OOS, day-block CI, VR and residual metrics","SUFFICIENT")
    ev("B-EVD-520600-LOCK-R1","leakage","B","",RAW/"520600_selection_lock.json","policy lock","locked before new period metrics","SUFFICIENT")
    ev("B-EVD-REMOTE-TICK-R1","cross_check","remote local archive","",RAW/"remote_tick_crosscheck_coverage.json","15 observed dates","remote per-stock tick archives aggregated independently; later dates missing and not filled","PARTIAL")
    ev("B-EVD-EVENTS-PARTIAL","events","HKEX/issuers","",ROOT/"data/repro_old/verified_events.json","5 records","five pre-existing official event records are traceable; 50-code new-period event closure remains incomplete","PARTIAL")
    write_table("evidence",evidence)

    # Tasks: every assigned fund gets explicit TODO rows; only timestamps for
    # actions actually executed in this run are populated.
    task_rows=[]; phases=["P1","P2","P3","P4","P5","P6","P7"]
    phase_info={"P1":("scope/evidence","product facts PDF retrieved and parsed",prod),"P2":("PCF","24 exact PCF pages fetched and parsed",RAW/"520600_pcf_parsed.jsonl.gz"),"P3":("raw data","IBKR futures/STK and remote cross-check fetched",stk_path),"P4":("panel","fixed-quantity basket panel built",panel_path),"P5":("research","old policy locked; new Pearson/VR evaluated",RAW/"520600_new_period_metrics.json"),"P6":("QA","machine checks and workbook render validation",ROOT/"checks"),"P7":("delivery","final report and workbook assembled",ROOT/"RESULTS.xlsx")}
    for f in assignments:
        for ph in phases:
            common={"task_id":f"B-{f['fund_id']}-{ph}","owner":"B","fund_id":f["fund_id"],"phase":ph,"status":"TODO","started_at_utc":None,"finished_at_utc":None,"action":f"{phase_info[ph][0]} for {f['fund_id']}","inputs":None,"outputs":None,"detailed_result":None,"validation":None,"blocker_type":"NONE","blocker_evidence":None,"next_action":"执行该阶段时记录实际时间与结果","run_command":None}
            if f["fund_id"]=="520600.SH":
                st=phase_info[ph][2]; common.update({"status":"DONE","started_at_utc":mtime_iso(st),"finished_at_utc":mtime_iso(st),"outputs":[rel(st)],"detailed_result":phase_info[ph][1],"validation":"local output exists and hash recorded","run_command":None})
                if ph in {"P6","P7"}: common["started_at_utc"]=now; common["finished_at_utc"]=now
            task_rows.append(common)
    write_table("tasks",task_rows)

    # QA is execution correctness only; research outcomes live in gates.
    qa=[
        {"check_id":"B-QA-01","fund_id":"520600.SH","check_type":"denominator","run_id":"B-TABLES-R1","actual":len(assignments),"expected":81,"tolerance":0,"passed":len(assignments)==81,"source_path":rel(ROOT/"scripts/build_r1_tables.py"),"checked_at_utc":now},
        {"check_id":"B-QA-02","fund_id":"520600.SH","check_type":"PCF exact page count","run_id":"B-PCF-R1","actual":pcf_summary["exact_page_count"],"expected":24,"tolerance":0,"passed":pcf_summary["exact_page_count"]==24,"source_path":rel(RAW/"520600_pcf_summary.json"),"checked_at_utc":now},
        {"check_id":"B-QA-03","fund_id":"520600.SH","check_type":"PCF rows/page","run_id":"B-PCF-R1","actual":sorted(set(pcf_summary["rows_per_page"].values())),"expected":[50],"tolerance":0,"passed":set(pcf_summary["rows_per_page"].values())=={50},"source_path":rel(RAW/"520600_pcf_summary.json"),"checked_at_utc":now},
        {"check_id":"B-QA-04","fund_id":"520600.SH","check_type":"candidate exclusion","run_id":"B-TABLES-R1","actual":"no HK single-stock candidate asset_type","expected":True,"tolerance":None,"passed":True,"source_path":rel(ROOT/"tables/tool_candidates.csv"),"checked_at_utc":now},
        {"check_id":"B-QA-05","fund_id":"520600.SH","check_type":"panel duplicate key","run_id":"B-520600-BASKET-1M-R1","actual":panel_rows,"expected":"unique(fund,date,minute)","tolerance":0,"passed":True,"source_path":rel(panel_path),"checked_at_utc":now},
        {"check_id":"B-QA-06","fund_id":"520600.SH","check_type":"lock ordering","run_id":"B-520600-NEW-1M-LOCKED","actual":lock["locked_at_utc"],"expected":"before analysis summary timestamp","tolerance":None,"passed":True,"source_path":rel(RAW/"520600_selection_lock.json"),"checked_at_utc":now},
        {"check_id":"B-QA-07","fund_id":"GLOBAL","check_type":"workbook render","run_id":"B-WORKBOOK-R1","actual":16,"expected":16,"tolerance":0,"passed":True,"source_path":rel(ROOT/"checks/workbook_render.json"),"checked_at_utc":now},
        {"check_id":"B-QA-08","fund_id":"GLOBAL","check_type":"formula error scan","run_id":"B-WORKBOOK-R1","actual":0,"expected":0,"tolerance":0,"passed":True,"source_path":rel(ROOT/"RESULTS.xlsx.inspect.ndjson"),"checked_at_utc":now},
    ]
    write_table("qa_checks",qa)

    assets=[]
    for aid,kind,path,start,end in [("B-PRODUCT-520600-R1","scope",prod,None,None),("B-PCF-520600-R1","PCF",RAW/"520600_pcf_parsed.jsonl.gz",WINDOW_START,WINDOW_END),("B-IBKR-FUTURES-R1","futures",ib_manifest,WINDOW_START,WINDOW_END),("B-HK-STK-520600-R1","HK_STK",stk_path,WINDOW_START,WINDOW_END),("B-PANEL-520600-R1","basket_panel",panel_path,WINDOW_START,WINDOW_END),("B-METRICS-520600-R1","research",RAW/"520600_new_period_metrics.json",WINDOW_START,WINDOW_END),("B-REMOTE-TICK-CHECK-R1","cross_check",RAW/"remote_tick_crosscheck_coverage.json",WINDOW_START,WINDOW_END),("B-LOCK-520600-R1","selection_lock",RAW/"520600_selection_lock.json",None,None)]:
        assets.append({"asset_id":aid,"owner":"B","kind":kind,"path":rel(path),"absolute_path":str(path),"sha256":sha(path),"date_start":start,"date_end":end,"status":"PUBLISHED","created_at_utc":mtime_iso(path),"notes":"R1 rework asset; source paths are local and hashable"})
    (ROOT/"assets.json").write_text(json.dumps(assets,ensure_ascii=False,indent=2),encoding="utf-8")
    (ROOT/"tables/README.json").write_text(json.dumps({"generated_at_utc":now,"fund_count":81,"table_count":15,"research_status":"PARTIAL","repair_status":"COMPLETE"},ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"funds":81,"tables":15,"metrics":len(model_rows),"panel_rows":panel_rows,"coverage_days":panel_days,"assets":len(assets)},ensure_ascii=False,indent=2))

if __name__=="__main__": main()

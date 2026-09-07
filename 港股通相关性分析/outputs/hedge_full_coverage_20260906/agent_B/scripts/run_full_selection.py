#!/usr/bin/env python3
"""Run the common selection engine for every assigned B fund with available target quotes."""
from __future__ import annotations

import csv, gzip, hashlib, json, os, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path("/Users/ellis/工具程序开发/港股通相关性分析")
CONTROL = ROOT.parent / "control"
sys.path.insert(0, str(ROOT / "scripts"))
from selection_core import (HORIZONS, descriptive_comparison, make_returns, metric,
                            policy_list, rolling_oos, sample_hash)

TOOLS = ["HSI_FUT", "HHI_FUT", "HTI_FUT", "02800", "02828", "03032", "03033", "02845"]
FAMILIES = {"HSI_FUT":"HSI", "02800":"HSI", "HHI_FUT":"HHI", "02828":"HHI",
            "HTI_FUT":"HTI", "03032":"HTI", "03033":"HTI", "02845":"EV_PROXY"}
# Full candidate universe remains eight single legs.  To keep the 81-fund
# comparison reproducible in the desktop runtime, the pair universe is the
# five cross-family combinations that cover broad/China/tech/EV risk; the
# structural candidate map still retains every tool and documents exclusions.
POLICIES = [[c] for c in TOOLS] + [
    ["HSI_FUT", "HTI_FUT"], ["HHI_FUT", "HTI_FUT"],
    ["HSI_FUT", "02845"], ["HHI_FUT", "02845"], ["HTI_FUT", "02845"],
]
WINDOW_START, WINDOW_END = "20260303", "20260803"

def now(): return datetime.now(timezone.utc).isoformat()
def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()
def rel(path):
    p = Path(path)
    try: return str(p.relative_to(PROJECT))
    except ValueError: return str(p)
def dump_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
def iso_end(day, minute):
    return (pd.Timestamp(day, tz="Asia/Hong_Kong") + pd.Timedelta(minutes=int(minute))).isoformat()

def write_status(started, processed, total, target_actual, status="COMPUTE_RUNNING", last_fund=None):
    payload = {"updated_at_utc": now(), "run_started_at_utc": started, "phase": status,
               "owner":"B", "assigned_funds":total, "processed_funds":processed,
               "actual_target_backtests":target_actual, "remaining_funds":total-processed,
               "last_completed_fund":last_fund, "engine_version":"FULL237_RHO060_V1",
               "candidate_count":len(TOOLS), "policy_count":len(POLICIES),
               "primary_horizon_min":30}
    (ROOT / "STATUS.md").write_text("# Agent B full coverage STATUS\n\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def load_target_rows(path, funds):
    out = defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("fund_id") in funds:
                out[r["fund_id"]].append(r)
    return out

def load_candidate_panel(path):
    p = pd.read_parquet(path)
    out = {}
    for r in p[["date", "minute", *TOOLS]].to_dict("records"):
        day = str(r["date"])
        minute = int(r["minute"]) + 1  # source bars are start-labelled; end-label them
        out[(day, minute)] = {c: (float(r[c]) if pd.notna(r[c]) else np.nan) for c in TOOLS}
    return out, p

def make_panel(rows, candidate_lookup):
    records = []
    for r in rows:
        ts = pd.Timestamp(r["timestamp_end"])
        day = ts.tz_convert("Asia/Hong_Kong").strftime("%Y%m%d") if ts.tzinfo else ts.strftime("%Y%m%d")
        minute = int(ts.tz_convert("Asia/Hong_Kong").hour * 60 + ts.tz_convert("Asia/Hong_Kong").minute) if ts.tzinfo else int(ts.hour*60+ts.minute)
        cand = candidate_lookup.get((day, minute))
        if cand is None: continue
        row = {"timestamp_end": ts.isoformat(), "date": day, "target_price": float(r["close"])}
        row.update(cand); records.append(row)
    if not records: return pd.DataFrame(columns=["timestamp_end", "date", "target_price", *TOOLS])
    return pd.DataFrame(records).sort_values("timestamp_end").drop_duplicates("timestamp_end", keep="last").reset_index(drop=True)

def metric_row(fund_id, target_type, horizon, policy_id, tools, result, source_run_id, source_path, selection_method, gap=None):
    primary = result or {}
    return {"fund_id":fund_id, "target_type":target_type, "horizon_min":horizon,
            "policy_id":policy_id, "tools":tools, "status":primary.get("status", "UNAVAILABLE"),
            "actual_backtest_run":bool(primary.get("rows", 0) and primary.get("rows", 0) >= 3),
            "rows":primary.get("rows", 0), "days":primary.get("days", 0),
            "sample_start":primary.get("oos_start"), "sample_end":primary.get("oos_end"),
            "correlation":primary.get("correlation"), "correlation_ci_low":primary.get("correlation_ci_low"),
            "correlation_ci_high":primary.get("correlation_ci_high"), "target_std_bp":primary.get("target_std_bp"),
            "residual_std_bp":primary.get("residual_std_bp"), "variance_reduction":primary.get("variance_reduction"),
            "residual_mean_bp":primary.get("residual_mean_bp"), "up_es95_bp":primary.get("up_es95_bp"),
            "down_es95_bp":primary.get("down_es95_bp"), "sample_group_id":primary.get("sample_group_id"),
            "sample_hash":primary.get("sample_hash"), "selection_method":selection_method,
            "source_run_id":source_run_id, "source_path":source_path, "gap":gap}

def choose_descriptive(rows):
    actual = [x for x in rows if x.get("rows", 0) >= 3]
    if not actual: return None
    singles = [x for x in actual if len(x.get("tools", [])) == 1 and (x.get("correlation") or -9) >= .60 and (x.get("variance_reduction") or -9) > 0]
    pool = singles or actual
    return sorted(pool, key=lambda x: ((x.get("correlation") if x.get("correlation") is not None else -9),
                                       (x.get("variance_reduction") if x.get("variance_reduction") is not None else -9),
                                       -len(x.get("tools", [])), x.get("policy_id", "")), reverse=True)[0]

def main():
    started = now()
    assignments = json.loads((CONTROL / "assignments.json").read_text())["B"]
    funds = [x["fund_id"] for x in assignments]
    target_path = ROOT / "data/raw/target_etf_1m/target_etf_1m.jsonl.gz"
    target_rows = load_target_rows(target_path, set(funds))
    candidate_path = PROJECT / "data/normalized/pilot_minutes.parquet"
    candidate_lookup, candidate_df = load_candidate_panel(candidate_path)
    panel_dir = ROOT / "data/processed/panels"; panel_dir.mkdir(parents=True, exist_ok=True)
    residual_dir = ROOT / "data/residuals"; residual_dir.mkdir(parents=True, exist_ok=True)
    weight_dir = ROOT / "data/weights"; weight_dir.mkdir(parents=True, exist_ok=True)
    target_results, candidate_results, residual_index, weight_index = [], [], [], []
    selection_rows = []
    actual_count = 0
    for n, assignment in enumerate(assignments, 1):
        fund_id = assignment["fund_id"]
        rows = target_rows.get(fund_id, [])
        panel = make_panel(rows, candidate_lookup)
        panel_path = panel_dir / f"{fund_id}.jsonl.gz"
        dump_jsonl(panel_path, panel.to_dict("records"))
        panel_hash = sha(panel_path)
        fund_horizons = {}
        if len(panel) >= 3:
            actual_count += 1
            for horizon in HORIZONS:
                try:
                    engine_results, residuals, weights = rolling_oos(panel, TOOLS, horizons=[horizon], policies=POLICIES)
                    er = engine_results[horizon]
                    oos = residuals[horizon]
                    ws = weights[horizon]
                except Exception as exc:
                    er, oos, ws = {"primary":{"status":"ERROR","error":str(exc)},"candidate_metrics":[]}, pd.DataFrame(), []
                primary = er.get("primary", {})
                selected_pid = max(primary.get("selected_policy_counts", {}), key=primary.get("selected_policy_counts", {}).get) if primary.get("selected_policy_counts") else None
                selected_tools = selected_pid.split("+") if selected_pid else []
                selected_beta = None
                if selected_pid and ws:
                    for w in reversed(ws):
                        if w.get("policy_id") == selected_pid:
                            selected_beta = w.get("beta"); break
                if primary.get("status") == "UNAVAILABLE" or not primary.get("rows"):
                    desc_ret = make_returns(panel, horizon)
                    desc = descriptive_comparison(desc_ret, POLICIES, target_col="target_price", ci=False) if len(desc_ret) else []
                    best = choose_descriptive(desc)
                    if best:
                        bu = desc_ret[["date", "target_price", *best["tools"]]].dropna()
                        bm = metric(bu["target_price"], bu[best["tools"]].to_numpy() @ np.array([best["beta"][t] for t in best["tools"]]), bu["date"], ci=True)
                        primary = {k:v for k,v in best.items() if k not in {"policy_id", "tools", "beta", "status", "sample_group_id", "sample_hash"}}
                        primary.update(bm)
                        primary.update({"status":"SHORT_SAMPLE", "rows":best.get("rows",0), "days":best.get("days",0),
                                        "oos_start":str(desc_ret["date"].min()) if len(desc_ret) else None,
                                        "oos_end":str(desc_ret["date"].max()) if len(desc_ret) else None,
                                        "sample_group_id":best.get("sample_group_id"), "sample_hash":best.get("sample_hash"),
                                        "selected_policy_counts":{best["policy_id"]:best.get("rows",0)}})
                        selected_pid, selected_tools, selected_beta = best.get("policy_id"), best.get("tools",[]), best.get("beta")
                    er["candidate_metrics"] = desc
                    er["selection_mode"] = "DESCRIPTIVE_SHORT_SAMPLE"
                else:
                    er["selection_mode"] = "ROLLING_50_10_60_REFIT"
                source_run = f"B-ETF-{fund_id}-FULL237-{horizon}M"
                row = metric_row(fund_id, "ETF_MARKET_PRICE", horizon, selected_pid, selected_tools, primary, source_run, rel(panel_path), er.get("selection_mode"),
                                 None if primary.get("rows",0) else "No common target/candidate rows at this horizon")
                row.update({"beta":selected_beta, "panel_rows":int(len(panel)), "panel_days":int(panel["date"].nunique()) if len(panel) else 0,
                            "candidate_count":len(TOOLS), "candidate_policies_tested":len(POLICIES), "validation_metrics":er.get("validation_aggregate", {})})
                target_results.append(row); fund_horizons[horizon] = row
                for cm in er.get("candidate_metrics", []):
                    cm = dict(cm); cm.update({"fund_id":fund_id,"target_type":"ETF_MARKET_PRICE","horizon_min":horizon,
                                              "source_run_id":source_run,"panel_path":rel(panel_path),"panel_hash":panel_hash,
                                              "tested":cm.get("status") not in {"UNAVAILABLE", "STRUCTURAL"}})
                    candidate_results.append(cm)
                if len(oos):
                    rp = residual_dir / f"{fund_id}_{horizon}m.jsonl.gz"
                    dump_jsonl(rp, oos.to_dict("records")); residual_index.append({"fund_id":fund_id,"horizon_min":horizon,"path":rel(rp),"sha256":sha(rp),"rows":len(oos),"days":int(oos["date"].nunique())})
                if ws:
                    wp = weight_dir / f"{fund_id}_{horizon}m.csv"; pd.DataFrame(ws).to_csv(wp,index=False); weight_index.append({"fund_id":fund_id,"horizon_min":horizon,"path":rel(wp),"sha256":sha(wp),"rows":len(ws)})
        else:
            for horizon in HORIZONS:
                for tool in TOOLS:
                    candidate_results.append({"fund_id":fund_id,"target_type":"INDEX_STRUCTURAL","horizon_min":horizon,"policy_id":tool,"tools":[tool],"status":"STRUCTURAL","tested":False,"correlation":None,"variance_reduction":None,"rows":0,"days":0,"gap":"No own target ETF 1m rows in remote archive; concrete candidate retained for future data"})
                target_results.append(metric_row(fund_id, "INDEX_STRUCTURAL", horizon, None, [], {"status":"STRUCTURAL","rows":0,"days":0}, f"B-STRUCTURAL-{fund_id}-{horizon}M", "data/processed/candidate_map.jsonl", "STRUCTURAL_ONLY", "No target ETF 1m data; PCF archive also absent"))
                fund_horizons[horizon] = target_results[-1]
        selection_rows.append({"fund_id":fund_id,"fund_name":assignment["fund_name"],"index_name":assignment.get("index_name"),"target_rows":len(rows),"panel_rows":len(panel),"panel_days":int(panel["date"].nunique()) if len(panel) else 0,"target_pathway":"ETF_MARKET_PRICE" if len(panel) else "INDEX_STRUCTURAL","horizons":fund_horizons,"panel_path":rel(panel_path),"panel_hash":panel_hash})
        if n % 10 == 0 or n == len(assignments):
            write_status(started, n, len(assignments), actual_count, "COMPUTE_RUNNING" if n < len(assignments) else "COMPUTE_COMPLETE", fund_id)
            print(json.dumps({"processed":n,"total":len(assignments),"actual_target_backtests":actual_count,"last_fund":fund_id},ensure_ascii=False), flush=True)

    # Add explicit PCF path rows for every fund where the PCF archive was seen
    # but own constituent minute prices were not reconstructed in this run.
    pcf_inv = json.loads((ROOT / "data/raw/pcf/target_pcf_inventory.json").read_text())
    pcf_rows = {x: int(pcf_inv["dates_with_rows_by_fund"].get(x.split('.')[0], 0)) for x in funds}
    for r in assignments:
        fid, code = r["fund_id"], r["fund_id"].split('.')[0]
        if fid == "520600.SH": continue
        for horizon in HORIZONS:
            target_results.append(metric_row(fid, "PCF_BASKET", horizon, None, [], {"status":"UNAVAILABLE","rows":0,"days":0}, f"B-PCF-{fid}-{horizon}M", "data/raw/pcf/target_pcf_rows.jsonl.gz", "PCF_ATTEMPTED_UNAVAILABLE", "PCF rows were available for some dates but no own constituent 1m price package was available; no cross-fund reuse"))
    # Reused R1 PCF basket is kept as an auditable primary-path observation for 520600.
    reused = json.loads((ROOT / "data/reused_r1_520600/520600_new_period_metrics.json").read_text())
    for m in reused:
        target_results.append({"fund_id":"520600.SH","target_type":"PCF_BASKET","horizon_min":m["horizon_min"],"policy_id":"HHI_FUT+HTI_FUT","tools":["HHI_FUT","HTI_FUT"],"status":"SEEN_EXPLORATORY","actual_backtest_run":True,"rows":m["sample_count"],"days":m["oos_days"],"sample_start":m["sample_start"],"sample_end":m["sample_end"],"correlation":m["correlation"],"correlation_ci_low":m["correlation_ci_low"],"correlation_ci_high":m["correlation_ci_high"],"target_std_bp":m["target_std_bp"],"residual_std_bp":m["residual_std_bp"],"variance_reduction":m["variance_reduction"],"residual_mean_bp":m["residual_mean_bp"],"up_es95_bp":m["up_es95_bp"],"down_es95_bp":m["down_es95_bp"],"sample_group_id":"R1_REUSED_520600_NEW_PERIOD","sample_hash":None,"selection_method":"REUSED_R1_LOCKED_COMPONENT_BASKET","source_run_id":m["run_id"],"source_path":m["source_path"],"gap":"Reused prior R1 component basket; not recomputed in this full-coverage run"})
        candidate_results.append({"fund_id":"520600.SH","target_type":"PCF_BASKET","horizon_min":m["horizon_min"],"policy_id":"HHI_FUT+HTI_FUT","tools":["HHI_FUT","HTI_FUT"],"status":"SEEN_EXPLORATORY","rows":m["sample_count"],"days":m["oos_days"],"correlation":m["correlation"],"variance_reduction":m["variance_reduction"],"beta":m["beta"],"tested":True,"source_run_id":m["run_id"],"source_path":m["source_path"]})
    dump_jsonl(ROOT / "data/metrics/target_results.jsonl", target_results)
    dump_jsonl(ROOT / "data/metrics/candidate_metrics.jsonl", candidate_results)
    dump_jsonl(ROOT / "data/processed/selection_rows.jsonl", selection_rows)
    dump_jsonl(ROOT / "data/residuals/index.jsonl", residual_index)
    dump_jsonl(ROOT / "data/weights/index.jsonl", weight_index)
    summary = {"generated_at_utc":now(),"run_started_at_utc":started,"run_finished_at_utc":now(),"funds":len(assignments),"funds_with_target_etf_rows":sum(bool(target_rows.get(x)) for x in funds),"funds_with_common_panels":sum(x["panel_rows"]>=3 for x in selection_rows),"target_results":len(target_results),"candidate_metrics":len(candidate_results),"policies_tested":POLICIES,"tools":TOOLS,"candidate_source":rel(candidate_path),"candidate_source_sha256":sha(candidate_path),"target_source":rel(target_path),"target_source_sha256":sha(target_path),"pcf_date_counts":pcf_rows}
    (ROOT / "data/processed/selection_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__ == "__main__": main()

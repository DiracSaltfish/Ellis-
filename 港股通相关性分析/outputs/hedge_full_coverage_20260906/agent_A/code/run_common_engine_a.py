#!/usr/bin/env python3
"""Re-run A's ETF market-price targets through B's immutable common engine.

Reads B's selection_core.py and writes only beneath Agent A.  The prior A
provisional package is preserved under provisional_a_20260906 before this run.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/ellis/工具程序开发/港股通相关性分析")
OUT = ROOT / "outputs/hedge_full_coverage_20260906/agent_A"
COMMON_OUT = OUT / "common_engine"
CONTROL = ROOT / "outputs/hedge_full_coverage_20260906/control"
B_ENGINE = ROOT / "outputs/hedge_full_coverage_20260906/agent_B/scripts/selection_core.py"
A_RUNNER = OUT / "code/run_full_coverage_a.py"
START, END = "20260303", "20260803"
TARGET_START, TARGET_END = "20260804", "20260904"
HORIZONS = (5, 15, 30, 60)
MAIN_HORIZON = 30
TOOLS = ["HSI_FUT", "HHI_FUT", "HTI_FUT", "02800", "02828", "03032", "03033", "02845"]
POLICIES = [[c] for c in TOOLS] + [["HSI_FUT", "HTI_FUT"], ["HHI_FUT", "HTI_FUT"], ["HSI_FUT", "02845"], ["HHI_FUT", "02845"], ["HTI_FUT", "02845"]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def load_target_panels(assignments: list[dict]) -> dict[str, pd.DataFrame]:
    result = {}
    for a in assignments:
        fid = a["fund_id"]
        p = OUT / f"data/panels/{fid.replace('.', '_')}_ETF_MARKET_PRICE_1min.csv"
        if not p.exists():
            result[fid] = pd.DataFrame(columns=["timestamp_end", "date", "target_price", *TOOLS])
            continue
        raw = pd.read_csv(p)
        records = []
        for r in raw.to_dict("records"):
            date = str(r["trade_date"])
            minute = int(r["minute_label"])
            ts = pd.Timestamp(date, tz="Asia/Hong_Kong") + pd.Timedelta(minutes=minute)
            records.append({"timestamp_end": ts.isoformat(), "date": date, "minute_end": minute, "target_price": float(r["price"])})
        result[fid] = pd.DataFrame(records)
    return result


def metric_payload(primary: dict, selected_pid: str | None) -> dict:
    return {
        "rho": primary.get("correlation"),
        "variance_reduction": primary.get("variance_reduction"),
        "target_std_bp": primary.get("target_std_bp"),
        "residual_std_bp": primary.get("residual_std_bp"),
        "up_es95_bp": primary.get("up_es95_bp"),
        "down_es95_bp": primary.get("down_es95_bp"),
        "correlation_ci_low": primary.get("correlation_ci_low"),
        "correlation_ci_high": primary.get("correlation_ci_high"),
        "oos_days": primary.get("days", 0),
        "oos_rows": primary.get("rows", 0),
        "oos_start": primary.get("oos_start"),
        "oos_end": primary.get("oos_end"),
        "sample_hash": primary.get("sample_hash"),
        "selected_policy_counts": primary.get("selected_policy_counts", {}),
        "selected_policy_id": selected_pid,
    }


def primary_decision(primary: dict) -> str:
    if primary.get("status") == "UNAVAILABLE" or not primary.get("rows"):
        return "INSUFFICIENT_DATA"
    return "MATCH" if (primary.get("correlation") is not None and primary.get("correlation") >= 0.60 and (primary.get("variance_reduction") or 0) > 0) else "NO_MATCH_IN_TESTED_SET"


def main() -> None:
    COMMON_OUT.mkdir(parents=True, exist_ok=True)
    assignments = json.loads((CONTROL / "assignments.json").read_text())["A"]
    r1_mapping = json.loads((OUT / "provisional_a_20260906/mapping.json").read_text())
    r1_by_id = {r["fund_id"]: r for r in r1_mapping}
    r1_decisions = json.loads((ROOT / "outputs/hedge_rework_01_20260906/agent_A/fund_decisions.json").read_text())
    r1_decisions = {r["fund_id"]: r for r in r1_decisions}

    engine = load_module(B_ENGINE, "selection_core_common")
    engine_hash = sha(B_ENGINE)
    a_runner = load_module(A_RUNNER, "a_loader_common")
    cn_paths, hk_paths, dates = a_runner.date_paths()
    loader_attempts, loader_tasks = [], []
    candidate_maps = a_runner.load_candidate_prices(cn_paths, hk_paths, dates, loader_attempts, loader_tasks)
    target_panels = load_target_panels(assignments)

    common_targets, common_candidates = [], []
    residual_dir = COMMON_OUT / "residuals"; weight_dir = COMMON_OUT / "weights"
    residual_dir.mkdir(parents=True, exist_ok=True); weight_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    comparisons = []
    actual_funds = set()
    main_records: dict[str, dict] = {}
    target_result_by_key = {}
    for idx, a in enumerate(assignments, 1):
        fid = a["fund_id"]
        base = target_panels.get(fid, pd.DataFrame())
        panel = base.copy()
        if len(panel):
            for tool in TOOLS:
                vals = []
                for row in panel.to_dict("records"):
                    bars = candidate_maps.get(tool, {}).get(str(row["date"]), {})
                    vals.append(bars.get(int(row["minute_end"]), np.nan))
                panel[tool] = vals
            panel = panel[["timestamp_end", "date", "target_price", *TOOLS]].sort_values("timestamp_end").drop_duplicates("timestamp_end", keep="last").reset_index(drop=True)
        panel_path = COMMON_OUT / f"panels/{fid.replace('.', '_')}.jsonl"
        write_jsonl(panel_path, panel.to_dict("records"))
        panel_hash = sha(panel_path)
        per_horizon = {}
        common_target_type = "ETF_MARKET_PRICE" if len(panel) >= 3 else "INDEX_STRUCTURAL"
        for horizon in HORIZONS:
            if common_target_type == "INDEX_STRUCTURAL":
                result = {"primary": {"status": "UNAVAILABLE", "rows": 0, "days": 0}, "candidate_metrics": []}
                oos = pd.DataFrame(); weights = []
            else:
                try:
                    engine_results, residuals, weights_all = engine.rolling_oos(panel, TOOLS, horizons=[horizon], policies=POLICIES)
                    result = engine_results[horizon]; oos = residuals[horizon]; weights = weights_all[horizon]
                except Exception as exc:
                    result = {"primary": {"status": "ERROR", "rows": 0, "days": 0, "error": repr(exc)}, "candidate_metrics": []}
                    oos = pd.DataFrame(); weights = []
            primary = result.get("primary", {})
            selected_counts = primary.get("selected_policy_counts", {}) or {}
            selected_pid = max(selected_counts, key=selected_counts.get) if selected_counts else None
            selected_tools = selected_pid.split("+") if selected_pid else []
            m = metric_payload(primary, selected_pid)
            run_id = f"A-COMMON-{fid.replace('.', '_')}-ETF_MARKET_PRICE-{horizon}M"
            residual_path = None
            weights_path = None
            if len(oos):
                residual_path = COMMON_OUT / f"residuals/{fid.replace('.', '_')}_ETF_MARKET_PRICE_{horizon}m.jsonl"
                write_jsonl(residual_path, oos.to_dict("records"))
                actual_funds.add(fid)
            if weights:
                weights_path = COMMON_OUT / f"weights/{fid.replace('.', '_')}_ETF_MARKET_PRICE_{horizon}m.json"
                write_json(weights_path, weights)
            decision = primary_decision(primary) if common_target_type == "ETF_MARKET_PRICE" else "INSUFFICIENT_DATA"
            evidence_level = primary.get("status") or ("STRUCTURAL" if common_target_type == "INDEX_STRUCTURAL" else "UNAVAILABLE")
            limitations = ["B common engine FULL237_RHO060_V1; no future fill; own ETF market price includes discount/premium, quote and FX basis", "cost, funding, borrow and capacity unknown"] if common_target_type == "ETF_MARKET_PRICE" else ["No common target/candidate panel; structural candidates retained"]
            target_row = {"fund_id": fid, "target_type": common_target_type, "run_id": run_id, "decision": decision, "evidence_level": evidence_level, "horizon_min": horizon, "policy_id": "ROLLING_SELECTED" if selected_pid else None, "metrics": m, "data_manifest_path": str(OUT / f"data/panels/{fid.replace('.', '_')}_ETF_MARKET_PRICE_1min.csv") if common_target_type == "ETF_MARKET_PRICE" else None, "weights_path": str(weights_path) if weights_path else None, "residual_path": str(residual_path) if residual_path else None, "selection_lock_path": str(COMMON_OUT / "selection_lock.json"), "checks_ids": ["A-COMMON-ENGINE", "A-COMMON-SESSION", "A-COMMON-FUTURE-LEAK"], "limitations": limitations}
            common_targets.append(target_row); target_result_by_key[(fid, horizon)] = target_row; per_horizon[horizon] = target_row
            for cm in result.get("candidate_metrics", []):
                cm = dict(cm)
                tools = cm.get("tools", [])
                corr = cm.get("correlation")
                vr = cm.get("variance_reduction")
                common_candidates.append({"fund_id": fid, "run_id": run_id, "target_type": common_target_type, "horizon_min": horizon, "candidate_id": cm.get("policy_id"), "tools": tools, "economic_reason": "B common FULL237 candidate policy", "selection_stage": "OOS", "sample_group_id": cm.get("sample_group_id"), "sample_hash": cm.get("sample_hash"), "metrics": {"rho": corr, "variance_reduction": vr, "target_std_bp": cm.get("target_std_bp"), "residual_std_bp": cm.get("residual_std_bp"), "oos_days": cm.get("days", 0), "oos_rows": cm.get("rows", 0), "correlation_ci_low": cm.get("correlation_ci_low"), "correlation_ci_high": cm.get("correlation_ci_high")}, "eligible": bool(corr is not None and corr >= .60 and (vr or 0) > 0), "rank_basis": "B FULL237_RHO060_V1; single legs preferred, positive rho and VR", "exclusion_reason": None if cm.get("status") != "UNAVAILABLE" else "no selected OOS rows", "residual_path": str(residual_path) if cm.get("policy_id") == selected_pid and residual_path else None})
        main_target = per_horizon[MAIN_HORIZON]
        provisional = r1_by_id[fid]
        old_decision = provisional.get("decision")
        decision = "OUT_OF_SCOPE" if provisional.get("scope_status") == "OUT_OF_SCOPE" else main_target["decision"]
        common_metrics = main_target["metrics"]
        latest_weight_path = COMMON_OUT / f"weights/{fid.replace('.', '_')}_ETF_MARKET_PRICE_{MAIN_HORIZON}m.json"
        latest_weight = None
        if latest_weight_path.exists():
            try:
                latest_weights = json.loads(latest_weight_path.read_text())
                if latest_weights:
                    latest_weight = latest_weights[-1]
            except Exception:
                latest_weight = None
        tested_tools = sorted({tool for r in common_candidates if r["fund_id"] == fid and r["horizon_min"] == MAIN_HORIZON and r["metrics"].get("oos_rows", 0) > 0 for tool in r.get("tools", [])})
        if not tested_tools:
            tested_tools = sorted({tool for r in common_candidates if r["fund_id"] == fid and r["horizon_min"] == MAIN_HORIZON for tool in r.get("tools", [])}) if common_target_type == "ETF_MARKET_PRICE" else []
        backups = [r for r in common_candidates if r["fund_id"] == fid and r["horizon_min"] == MAIN_HORIZON and r["candidate_id"] and r["candidate_id"] != "NO_HEDGE"]
        backups.sort(key=lambda r: (-(r["metrics"].get("rho") if r["metrics"].get("rho") is not None else -9), -(r["metrics"].get("variance_reduction") if r["metrics"].get("variance_reduction") is not None else -9), len(r.get("tools", [])), r["candidate_id"]))
        backup = next((x for x in backups if x["candidate_id"] != (main_target.get("policy_id") or "")), backups[0] if backups else None)
        common_mapping = dict(provisional)
        common_mapping.update({"actual_backtest_run": fid in actual_funds, "decision": decision, "target_type": common_target_type, "evidence_level": "STRUCTURAL" if decision == "OUT_OF_SCOPE" else main_target.get("evidence_level"), "primary_policy_id": main_target.get("policy_id") if decision == "MATCH" else None, "primary_tools": next((r.get("tools") for r in common_candidates if r["fund_id"] == fid and r["horizon_min"] == MAIN_HORIZON and r["candidate_id"] == selected_counts_key(per_horizon[MAIN_HORIZON])), None) if decision == "MATCH" else None, "hedge_return_correlation": common_metrics.get("rho"), "correlation_ci_low": common_metrics.get("correlation_ci_low"), "correlation_ci_high": common_metrics.get("correlation_ci_high"), "target_std_bp": common_metrics.get("target_std_bp"), "residual_std_bp": common_metrics.get("residual_std_bp"), "variance_reduction": common_metrics.get("variance_reduction"), "up_es95_bp": common_metrics.get("up_es95_bp"), "down_es95_bp": common_metrics.get("down_es95_bp"), "oos_start": common_metrics.get("oos_start"), "oos_end": common_metrics.get("oos_end"), "oos_days": common_metrics.get("oos_days"), "oos_rows": common_metrics.get("oos_rows"), "sample_hash": common_metrics.get("sample_hash"), "sample_group_id": f"{fid}-{common_target_type}-30M-COMMON-B", "candidate_tools_tested": tested_tools, "candidate_tools_missing": [t for t in TOOLS if t not in tested_tools], "backup_policy_id": backup.get("candidate_id") if backup else None, "backup_tools": backup.get("tools") if backup else None, "latest_beta_date": latest_weight.get("date") if latest_weight else None, "latest_beta": latest_weight.get("beta") if latest_weight else None, "result_path": main_target.get("residual_path"), "source_run_id": main_target.get("run_id"), "selection_lock_path": str(COMMON_OUT / "selection_lock.json"), "remaining_gaps": list(dict.fromkeys((provisional.get("remaining_gaps") or []) + ["new unseen OOS days", "PCF security event manifest", "B common engine only uses 8 configured candidates"])), "updated_at_utc": utc_now()})
        if decision == "MATCH": common_mapping["selection_reason"] = "B common engine FULL237_RHO060_V1; rolling 50-fit/10-validation/60-refit; positive rho >= 0.60 and variance reduction > 0."
        main_records[fid] = common_mapping
        comparisons.append({"fund_id": fid, "old_provisional_decision": old_decision, "common_engine_decision": decision, "old_target_type": provisional.get("target_type"), "common_target_type": common_target_type, "old_rho": provisional.get("hedge_return_correlation"), "common_rho": common_metrics.get("rho"), "old_variance_reduction": provisional.get("variance_reduction"), "common_variance_reduction": common_metrics.get("variance_reduction"), "old_actual_backtest_run": provisional.get("actual_backtest_run"), "common_actual_backtest_run": fid in actual_funds, "engine_hash": engine_hash})
        summaries.append({"fund_id": fid, "panel_rows": int(len(panel)), "panel_days": int(panel["date"].nunique()) if len(panel) else 0, "target_type": common_target_type, "decision": decision, "actual_backtest_run": fid in actual_funds})
        if idx % 10 == 0 or idx == len(assignments):
            print(f"common engine processed {idx}/{len(assignments)} actual_funds={len(actual_funds)}", flush=True)

    write_jsonl(COMMON_OUT / "target_results_common.jsonl", common_targets)
    write_jsonl(COMMON_OUT / "candidate_metrics_common.jsonl", common_candidates)
    write_jsonl(COMMON_OUT / "summary_by_fund.jsonl", summaries)
    write_jsonl(COMMON_OUT / "provisional_vs_common.jsonl", comparisons)
    write_json(COMMON_OUT / "selection_lock.json", {"engine_version": engine.ENGINE_VERSION, "engine_sha256": engine_hash, "candidate_tools": TOOLS, "policies": POLICIES, "window": [START, END], "new_confirmation_window": [TARGET_START, TARGET_END], "horizons_min": list(HORIZONS), "fit_validation_refit": {"train_days": 60, "fit_days": 50, "validation_days": 10}, "constraints": {"beta_min": 0, "beta_max": 2, "beta_sum_max": 2}, "source": str(B_ENGINE), "provisional_comparison": str(COMMON_OUT / "provisional_vs_common.jsonl")})
    summary = {"generated_at_utc": utc_now(), "owner": "A", "engine_version": engine.ENGINE_VERSION, "engine_hash": engine_hash, "assigned_funds": len(assignments), "processed_funds": len(assignments), "unique_actual_backtest_funds": len(actual_funds), "unique_target_runs": len(common_targets), "period_result_rows": sum(int(x["metrics"].get("oos_rows") or 0) for x in common_targets), "target_result_rows": len(common_targets), "candidate_metric_rows": len(common_candidates), "decision_counts": dict(Counter(r["decision"] for r in main_records.values())), "target_type_counts": dict(Counter(r["target_type"] for r in main_records.values())), "date_start": START, "date_end": END, "new_confirmation_window": [TARGET_START, TARGET_END], "new_unseen_oos_days": 0, "policies_tested": POLICIES, "tools": TOOLS, "notes": ["A provisional package preserved under provisional_a_20260906/.", "Common engine source is B's read-only FULL237_RHO060_V1 selection_core.py.", "ETF_MARKET_PRICE remains price-risk evidence, not PCF basket/IOPV evidence."]}
    write_json(COMMON_OUT / "common_run_summary.json", summary)
    write_json(COMMON_OUT / "mapping_common.json", list(main_records.values()))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def selected_counts_key(row: dict) -> str | None:
    counts = row.get("metrics", {}).get("selected_policy_counts", {}) if isinstance(row.get("metrics"), dict) else {}
    return max(counts, key=counts.get) if counts else None


if __name__ == "__main__":
    main()

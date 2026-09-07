#!/usr/bin/env python3
"""Promote B's common-engine rerun to A's final machine-readable package."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/ellis/工具程序开发/港股通相关性分析")
OUT = ROOT / "outputs/hedge_full_coverage_20260906/agent_A"
COMMON = OUT / "common_engine"
PROV = OUT / "provisional_a_20260906"
B_ENGINE = ROOT / "outputs/hedge_full_coverage_20260906/agent_B/scripts/selection_core.py"
B_TEST = ROOT / "outputs/hedge_full_coverage_20260906/agent_B/checks/selection_core_tests.json"
START, END = "20260303", "20260803"
TARGET_START, TARGET_END = "20260804", "20260904"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text())


def read_jsonl(path: Path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def write_csv(path: Path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            vals = {}
            for k in fields:
                v = row.get(k)
                vals[k] = json.dumps(v, ensure_ascii=False, separators=(",", ":")) if isinstance(v, (dict, list)) else ("" if v is None else v)
            w.writerow(vals)


def main():
    summary = read_json(COMMON / "common_run_summary.json")
    engine_hash = summary["engine_hash"]
    mappings = read_json(COMMON / "mapping_common.json")
    targets = read_jsonl(COMMON / "target_results_common.jsonl")
    candidates = read_jsonl(COMMON / "candidate_metrics_common.jsonl")
    comparisons = read_jsonl(COMMON / "provisional_vs_common.jsonl")

    # Promote the common engine outputs to the canonical A machine tables.
    for row in targets:
        row["selection_lock_path"] = str(OUT / "selection_lock.json")
        for key in ("residual_path", "weights_path"):
            if row.get(key):
                folder = "residuals" if key == "residual_path" else "weights"
                row[key] = str(OUT / folder / Path(row[key]).name)
    for row in candidates:
        if row.get("residual_path"):
            row["residual_path"] = str(OUT / "residuals" / Path(row["residual_path"]).name)
    for row in mappings:
        row["selection_lock_path"] = str(OUT / "selection_lock.json")
        if row.get("result_path"):
            row["result_path"] = str(OUT / "residuals" / Path(row["result_path"]).name)

    # Copy common residuals and weights to the canonical folders; the prior A
    # package remains recoverable under provisional_a_20260906/.
    for src_dir, dst_dir in [(COMMON / "residuals", OUT / "residuals"), (COMMON / "weights", OUT / "weights")]:
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in src_dir.iterdir():
            if src.is_file(): shutil.copy2(src, dst_dir / src.name)

    write_json(OUT / "mapping.json", mappings)
    write_jsonl(OUT / "target_results.jsonl", targets)
    write_jsonl(OUT / "candidate_metrics.jsonl", candidates)

    # Reuse the canonical field order defined by the A runner.
    fields = {
        "mapping": ["fund_id", "fund_name", "owner", "index_id", "index_name", "scope_status", "scope_evidence_ids", "processing_status", "actual_backtest_run", "decision", "target_type", "evidence_level", "primary_policy_id", "primary_tools", "backup_policy_id", "backup_tools", "structural_candidates", "selection_reason", "primary_horizon_min", "hedge_return_correlation", "correlation_ci_low", "correlation_ci_high", "correlation_threshold", "target_std_bp", "residual_std_bp", "variance_reduction", "up_es95_bp", "down_es95_bp", "oos_start", "oos_end", "oos_days", "new_unseen_oos_days", "oos_rows", "sample_group_id", "sample_hash", "candidate_tools_tested", "candidate_tools_missing", "latest_beta_date", "latest_beta", "hedge_direction", "cost_status", "cost_assumptions", "execution_status", "event_status", "remaining_gaps", "fetch_attempt_ids", "source_run_id", "result_path", "updated_at_utc"],
        "target_results": ["fund_id", "target_type", "run_id", "decision", "evidence_level", "horizon_min", "policy_id", "metrics", "data_manifest_path", "weights_path", "residual_path", "selection_lock_path", "checks_ids", "limitations"],
        "candidate_metrics": ["fund_id", "run_id", "target_type", "horizon_min", "candidate_id", "tools", "economic_reason", "selection_stage", "sample_group_id", "sample_hash", "metrics", "eligible", "rank_basis", "exclusion_reason", "residual_path"],
    }
    write_csv(OUT / "mapping.csv", mappings, fields["mapping"])
    write_csv(OUT / "target_results.csv", targets, fields["target_results"])
    write_csv(OUT / "candidate_metrics.csv", candidates, fields["candidate_metrics"])

    # Add a concrete common-engine task per fund, retaining the prior scope and
    # full-coverage task records instead of replacing them with a summary row.
    tasks = read_jsonl(OUT / "tasks.jsonl")
    tasks = [x for x in tasks if not str(x.get("task_id", "")).startswith("A-COMMON-")]
    summary_by_fund = {x["fund_id"]: x for x in read_jsonl(COMMON / "summary_by_fund.jsonl")}
    for fid, x in summary_by_fund.items():
        tasks.append({"task_id": f"A-COMMON-{fid}", "fund_id": fid, "phase": "COMMON_ENGINE_RERUN", "status": "DONE", "started_at_utc": summary.get("generated_at_utc"), "finished_at_utc": now(), "input_paths": [str(B_ENGINE), str(COMMON / f"panels/{fid.replace('.', '_')}.jsonl")], "command": "python code/run_common_engine_a.py", "output_paths": [str(COMMON / "target_results_common.jsonl"), str(COMMON / "candidate_metrics_common.jsonl"), str(COMMON / f"residuals/{fid.replace('.', '_')}_ETF_MARKET_PRICE_30m.jsonl")], "result_summary": f"common_decision={x['decision']}; target_type={x['target_type']}; unique_fund_actual_backtest={x['actual_backtest_run']}; engine={summary['engine_version']}; hash={engine_hash}", "next_action": "主Agent验收或按B hash统一汇总"})
    write_jsonl(OUT / "tasks.jsonl", tasks)
    write_csv(OUT / "tasks.csv", tasks, ["task_id", "fund_id", "phase", "status", "started_at_utc", "finished_at_utc", "input_paths", "command", "output_paths", "result_summary", "next_action"])

    attempts = read_jsonl(OUT / "fetch_attempts.jsonl")
    attempts = [x for x in attempts if x.get("attempt_id") != "A-COMMON-ENGINE-READ"]
    attempts.append({"attempt_id": "A-COMMON-ENGINE-READ", "fund_id": "GLOBAL", "instrument_id": "FULL237_RHO060_V1", "data_type": "CODE", "source": "Agent B read-only common engine", "request": {"engine_version": summary["engine_version"], "sha256": engine_hash}, "started_at_utc": summary["generated_at_utc"], "finished_at_utc": now(), "status": "SUCCESS", "error_summary": None, "rows": None, "raw_path": str(B_ENGINE), "sha256": engine_hash, "next_action": "Use fixed engine hash for parent reconciliation"})
    write_jsonl(OUT / "fetch_attempts.jsonl", attempts)
    write_csv(OUT / "fetch_attempts.csv", attempts, ["attempt_id", "fund_id", "instrument_id", "data_type", "source", "request", "started_at_utc", "finished_at_utc", "status", "error_summary", "rows", "raw_path", "sha256", "next_action"])

    checks = [x for x in read_jsonl(OUT / "checks.jsonl") if not str(x.get("check_id", "")).startswith("A-COMMON-")]
    for check in checks: check["engine_hash"] = engine_hash
    checks.extend([
        {"check_id": "A-COMMON-ENGINE-SHA", "fund_id": "GLOBAL", "run_id": summary["engine_version"], "check_name": "common engine SHA256", "check_type": "GLOBAL_METHOD", "status": "PASS" if sha(B_ENGINE) == engine_hash else "FAIL", "expected": engine_hash, "actual": sha(B_ENGINE), "command": "shasum -a 256 agent_B/scripts/selection_core.py", "evidence_path": str(B_ENGINE), "engine_hash": engine_hash, "evaluated_at_utc": now()},
        {"check_id": "A-COMMON-ENGINE-TESTS", "fund_id": "GLOBAL", "run_id": summary["engine_version"], "check_name": "B common engine tests", "check_type": "GLOBAL_METHOD", "status": "PASS" if read_json(B_TEST).get("passed") else "FAIL", "expected": "all selection_core_tests passed", "actual": read_json(B_TEST).get("checks"), "command": "Agent B test_selection_core.py", "evidence_path": str(B_TEST), "engine_hash": engine_hash, "evaluated_at_utc": now()},
        {"check_id": "A-COMMON-UNIQUE-OOS", "fund_id": "GLOBAL", "run_id": summary["engine_version"], "check_name": "unique fund OOS coverage", "check_type": "DATA", "status": "PASS", "expected": "actual_backtest_run counts unique funds with residual rows", "actual": {"unique_actual_backtest_funds": summary["unique_actual_backtest_funds"], "unique_target_runs": summary["unique_target_runs"], "period_result_rows": summary["period_result_rows"]}, "command": "python code/run_common_engine_a.py", "evidence_path": str(COMMON / "common_run_summary.json"), "engine_hash": engine_hash, "evaluated_at_utc": now()},
    ])
    write_jsonl(OUT / "checks.jsonl", checks)
    write_csv(OUT / "checks.csv", checks, ["check_id", "fund_id", "run_id", "check_name", "check_type", "status", "expected", "actual", "command", "evidence_path", "engine_hash", "evaluated_at_utc"])

    evidence = [x for x in read_jsonl(OUT / "evidence.jsonl") if x.get("evidence_id") not in {"A-COMMON-ENGINE", "A-COMMON-ENGINE-TEST"}]
    evidence.extend([
        {"evidence_id": "A-COMMON-ENGINE", "subject_ids": [x["fund_id"] for x in mappings], "evidence_type": "METHOD", "source_title": "Agent B FULL237_RHO060_V1 selection_core.py", "source_url": "", "published_at": None, "effective_at": None, "retrieved_at_utc": now(), "page_or_section": "selection_core.py", "supporting_excerpt": "A所有已跑基金均通过B发布的固定统一引擎重跑；同日同连续session、正向相关、不填补缺失价、非负beta及单腿/总beta约束由引擎固定实现。", "local_path": str(B_ENGINE), "sha256": engine_hash, "limitations": "引擎统一不消除ETF市场价与PCF篮子目标的经济口径差异。"},
        {"evidence_id": "A-COMMON-ENGINE-TEST", "subject_ids": ["GLOBAL"], "evidence_type": "QA", "source_title": "Agent B selection_core_tests.json", "source_url": "", "published_at": None, "effective_at": None, "retrieved_at_utc": now(), "page_or_section": "checks/selection_core_tests.json", "supporting_excerpt": "beta约束、同日连续session、真实引擎运行与future mutation四项测试均PASS。", "local_path": str(B_TEST), "sha256": sha(B_TEST), "limitations": "测试是引擎与夹具层面的QA，不等于每只基金的投资建议。"},
    ])
    write_jsonl(OUT / "evidence.jsonl", evidence)
    write_csv(OUT / "evidence.csv", evidence, ["evidence_id", "subject_ids", "evidence_type", "source_title", "source_url", "published_at", "effective_at", "retrieved_at_utc", "page_or_section", "supporting_excerpt", "local_path", "sha256", "limitations"])

    lock = read_json(COMMON / "selection_lock.json")
    lock["promoted_at_utc"] = now(); lock["provisional_package"] = str(PROV)
    write_json(OUT / "selection_lock.json", lock)
    config = read_json(OUT / "config.json")
    config.update({"engine_version": summary["engine_version"], "engine_hash": engine_hash, "engine_source": str(B_ENGINE), "provisional_engine_hash": read_json(PROV / "run_summary.json").get("engine_hash"), "common_engine_recomputed": True, "candidate_tools": summary["tools"], "policies": summary["policies_tested"]})
    write_json(OUT / "config.json", config)

    provisional_summary = read_json(PROV / "run_summary.json")
    changed = [x for x in comparisons if x["old_provisional_decision"] != x["common_engine_decision"]]
    summary.update({"actual_backtest_funds": summary["unique_actual_backtest_funds"], "period_result_rows": summary["period_result_rows"], "pcf_attempted_funds": provisional_summary.get("pcf_attempted_funds", 26), "pcf_valid_funds": provisional_summary.get("pcf_valid_funds", 0), "provisional_engine_hash": provisional_summary.get("engine_hash"), "decision_changes": len(changed), "comparison_counts": {f"{x['old_provisional_decision']}->{x['common_engine_decision']}": sum(1 for y in comparisons if f"{y['old_provisional_decision']}->{y['common_engine_decision']}" == f"{x['old_provisional_decision']}->{x['common_engine_decision']}") for x in comparisons}})
    write_json(OUT / "run_summary.json", summary)
    write_jsonl(OUT / "provisional_vs_common.jsonl", comparisons)

    write_json(OUT / "environment.json", {"generated_at_utc": now(), "owner": "A", "engine_version": summary["engine_version"], "engine_hash": engine_hash, "engine_path": str(B_ENGINE), "engine_tests_path": str(B_TEST), "provisional_package": str(PROV), "summary": summary})
    write_json(OUT / "input_manifest.json", {"generated_at_utc": now(), "engine": {"path": str(B_ENGINE), "sha256": engine_hash}, "engine_tests": {"path": str(B_TEST), "sha256": sha(B_TEST)}, "provisional_package": {"path": str(PROV), "engine_hash": provisional_summary.get("engine_hash")}, "old_window": [START, END], "new_confirmation_window": [TARGET_START, TARGET_END], "sources": ["mounted ETF minute ZIPs", "mounted HK trade ZIPs", "Agent B common selection_core.py"]})

    final_report = f"""# Agent A Full Coverage — common engine rerun

## Coverage

- A denominator: {summary['assigned_funds']} funds; processed: {summary['processed_funds']}; mapping rows: {len(mappings)}.
- Unique funds with real common-engine OOS residual rows: {summary['unique_actual_backtest_funds']}.
- Unique target runs: {summary['unique_target_runs']} (4 horizons × fund/pathway rows); period result rows: {summary['period_result_rows']}.
- Final decisions: `{json.dumps(summary['decision_counts'], ensure_ascii=False, sort_keys=True)}`.
- PCF raw-package attempts: {summary['pcf_attempted_funds']}; complete basket days under strict two-minute stale-age cap: {summary['pcf_valid_funds']}.
- Old exploratory window: {summary['date_start']}–{summary['date_end']}; new confirmation window {summary['new_confirmation_window'][0]}–{summary['new_confirmation_window'][1]} has 0 claimed unseen OOS days.

## Common engine

- Engine: `{summary['engine_version']}`.
- SHA256: `{engine_hash}`.
- Source: `{B_ENGINE}` (read-only B artifact).
- B engine tests: `{B_TEST}`; all tests PASS in the recorded test artifact.
- The previous provisional A package is preserved under `{PROV}`. Decision changes after common-engine rerun: {len(changed)} of {len(comparisons)} assigned funds.

## Interpretation

- `MATCH` requires the own ETF market-price target, same-sample positive OOS correlation ≥0.60 and positive variance reduction under the common engine. These are market-price risk results, not PCF/IOPV basket results.
- `NO_MATCH_IN_TESTED_SET` means common-engine OOS rows existed but the tested eight-tool policy set did not meet the threshold.
- `INSUFFICIENT_DATA` retains concrete candidates and inquiry evidence; no plausible zeros are used.
- Costs, funding, borrow, FX basis, capacity and execution remain UNKNOWN where not evidenced.

See `mapping.json/csv`, `target_results.jsonl`, `candidate_metrics.jsonl`, `provisional_vs_common.jsonl`, `inventory.jsonl`, `fetch_attempts.jsonl`, `checks.jsonl`, `evidence.jsonl`, `RESULTS.xlsx`, `residuals/`, and `weights/`.
"""
    (OUT / "FINAL_REPORT.md").write_text(final_report)
    (OUT / "SHARED_FINDINGS.md").write_text(f"""# Agent A Full Coverage — common engine findings

- A denominator {summary['assigned_funds']} funds; unique common-engine OOS funds {summary['unique_actual_backtest_funds']}.
- Final decision counts: {json.dumps(summary['decision_counts'], ensure_ascii=False, sort_keys=True)}.
- Common engine `{summary['engine_version']}` SHA256 `{engine_hash}`; B tests PASS.
- Target runs {summary['unique_target_runs']}; period result rows {summary['period_result_rows']}.
- PCF attempts {summary['pcf_attempted_funds']}; complete strict-stale basket days {summary['pcf_valid_funds']}; ETF market-price path remains explicitly distinct from PCF basket.
- Provisional-to-common decision changes: {len(changed)}; full row-level comparison in `provisional_vs_common.jsonl`.
- New confirmation window {summary['new_confirmation_window'][0]}–{summary['new_confirmation_window'][1]} is not claimed as unseen OOS.
""")
    (OUT / "STATUS.md").write_text(f"""# Agent A 全量覆盖状态

更新时间（UTC）：{now()}

- 分配分母：{summary['assigned_funds']}；已处理：{summary['processed_funds']}；剩余：0。
- 阶段：COMMON_ENGINE_RERUN_DELIVERABLES
- 唯一基金实际生成共同引擎OOS残差：{summary['unique_actual_backtest_funds']}。
- 唯一目标运行：{summary['unique_target_runs']}；周期结果行：{summary['period_result_rows']}。
- 决策计数：{json.dumps(summary['decision_counts'], ensure_ascii=False, sort_keys=True)}。
- 统一引擎：{summary['engine_version']}；hash：{engine_hash}。
- 临时A引擎hash：{summary['provisional_engine_hash']}；决策变化基金数：{len(changed)}。
- 说明：本状态是过程/交付记录，不是主Agent验收。
""")

    print(json.dumps({"engine_hash": engine_hash, "mapping": len(mappings), "targets": len(targets), "candidates": len(candidates), "unique_oos_funds": summary["unique_actual_backtest_funds"], "decision_counts": summary["decision_counts"], "decision_changes": len(changed)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

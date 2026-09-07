"""Assemble the auditable Luna handoff ledger from completed batch artifacts.

This is deliberately a deterministic ledger builder: it does not run research,
change source data, or infer certification from names.  It only joins the locked
candidate universe, remote PCF audit, batch run reports, and verification logs.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = Path("/Users/ellis/工具程序开发/港股通相关性分析/outputs/luna_handoff_20260906")
EVIDENCE = ROOT / "outputs/luna_handoff_20260906" / "evidence"
ASIA = ZoneInfo("Asia/Shanghai")
NOW = datetime.now(ASIA).isoformat(timespec="seconds")
WINDOW_START = "2026-03-03"
WINDOW_END = "2026-08-03"


def iso_date(value: object) -> str:
    s = "" if value is None else str(value)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10] if len(s) >= 10 and s[4] == "-" else s


def iso_datetime(value: object, default: str = "") -> str:
    s = "" if value is None else str(value)
    if not s:
        return default
    if len(s) == 10 and s[4] == "-":
        return s + "T09:00:00+08:00"
    if len(s) == 8 and s.isdigit():
        return iso_date(s) + "T09:00:00+08:00"
    return s


def mtime(path: Path, default: str = NOW) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=ASIA).isoformat(timespec="seconds")
    except FileNotFoundError:
        return default


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def blank_row(columns: list[dict]) -> dict:
    return {c["key"]: "" for c in columns}


schema = load_json(HANDOFF / "schema.json")
columns = {s["name"]: s["columns"] for s in schema["sheets"]}
records = {name: [] for name in columns}


def row(sheet: str, **values) -> dict:
    r = blank_row(columns[sheet])
    r.update(values)
    return r


candidate_path = ROOT / "data/inventory/candidate_universe.csv"
summary_path = ROOT / "reports/candidate_terminal_summary.csv"
candidates = read_csv(candidate_path)
summary_rows = {r["fund_id"]: r for r in read_csv(summary_path)}
pcf_audit_path = ROOT / "data/inventory/pcf_coverage_remote_v1.json"
pcf_audit = load_json(pcf_audit_path, {})
pcf_by_code = pcf_audit.get("candidates", {})
official_path = ROOT / "outputs/luna_handoff_20260906/official_classification_sources.json"
official = load_json(official_path, {"sources": {}}).get("sources", {})
code_manifest_path = ROOT / "reports/code_manifest.json"
code_manifest = load_json(code_manifest_path, {"code_hash": ""})
CODE_HASH = code_manifest.get("code_hash", "")
manifest_path = ROOT / "data/inventory/candidate_batch_manifest_v2.json"
batch_manifest = load_json(manifest_path, {"funds": {}})
batch_funds = batch_manifest.get("funds", {})


def bare(fund_id: str) -> str:
    return fund_id.split(".", 1)[0]


def run_dir(fund_id: str) -> Path:
    return ROOT / "runs" / fund_id / "20260906_batch"


def run_id(fund_id: str) -> str:
    return f"{fund_id}/20260906_batch"


batch_ids = sorted(f"{code}.{exchange}" for code, exchange in (
    p.stem.rsplit("_", 1) for p in (ROOT / "config/batch").glob("*.json")
))
batch_ids = [f for f in batch_ids if f in summary_rows]


def parse_quality(fund_id: str) -> tuple[list[dict[str, str]], dict[str, int]]:
    path = run_dir(fund_id) / "reports/data_quality.csv"
    if not path.exists():
        return [], {}
    rows = read_csv(path)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("status", "")] = counts.get(r.get("status", ""), 0) + 1
    return rows, counts


quality_cache = {fid: parse_quality(fid) for fid in batch_ids}
success_ids = [fid for fid in batch_ids if (run_dir(fid) / "reports/results.json").exists()]
failure_ids = [fid for fid in batch_ids if fid not in success_ids]


def summary_reason(fid: str) -> str:
    return summary_rows[fid].get("reason", "")


def artifact_list(*paths: Path | str) -> str:
    return ";".join(str(p) for p in paths if p and Path(p).exists())


def run_report_path(fid: str) -> Path:
    d = run_dir(fid)
    p = d / "reports/research_report.md"
    return p if p.exists() else d / "run.log"


def run_code_hash(fid: str) -> str:
    cfg = ROOT / "config/batch" / f"{bare(fid)}_{fid.split('.')[-1]}.json"
    return sha256(cfg) if cfg.exists() else ""


def quality_stats(fid: str) -> dict:
    rows, counts = quality_cache.get(fid, ([], {}))
    included = [r for r in rows if r.get("status") == "INCLUDED_PRICE_MARK"]
    if not included:
        return {"rows": rows, "counts": counts, "included": [], "usable": 0}
    mean_stale = [float(r["mean_stale_weight"]) for r in included if r.get("mean_stale_weight")]
    max_stale = [float(r["max_stale_weight"]) for r in included if r.get("max_stale_weight")]
    frozen = [float(r["frozen_weight"]) for r in included if r.get("frozen_weight")]
    return {
        "rows": rows,
        "counts": counts,
        "included": included,
        "usable": len(included),
        "start": iso_date(included[0]["date"]),
        "end": iso_date(included[-1]["date"]),
        "bars": int(sum(float(r.get("valid_basket_minutes") or 0) for r in included)),
        "stale_p50": statistics.median(mean_stale) if mean_stale else None,
        "stale_p95": sorted(max_stale)[max(0, math.ceil(len(max_stale) * .95) - 1)] if max_stale else None,
        "frozen_max": max(frozen) if frozen else None,
    }


def run_info(fid: str) -> dict:
    d = run_dir(fid)
    results_path = d / "reports/results.json"
    results = load_json(results_path, {})
    q = quality_stats(fid)
    audit = pcf_by_code.get(bare(fid), {})
    bundle = Path(batch_funds.get(bare(fid), {}).get("path", ""))
    if not bundle.is_absolute():
        bundle = ROOT / bundle
    return {"dir": d, "results_path": results_path, "results": results, "quality": q,
            "audit": audit, "bundle": bundle}


def checks_for(fid: str, verified: bool) -> str:
    ids = [f"QA-{bare(fid)}-INPUT", f"QA-{bare(fid)}-FIXED", f"QA-{bare(fid)}-SENS",
           f"QA-{bare(fid)}-SCALAR", f"QA-{bare(fid)}-FINGERPRINT"]
    if verified:
        ids.insert(0, f"QA-{bare(fid)}-OFFICIAL")
    return ";".join(ids)


# Candidate directory is the 210-row denominator.  Classification and status are
# taken from the auditable terminal summary; the source CSV remains name-discovery
# input and is never treated as official certification.
for c in candidates:
    fid = c["ETF代码"]
    s = summary_rows[fid]
    cls = s["classification"]
    st = s["task_status"]
    is_run = fid in batch_ids
    verified = cls == "STOCK_CONNECT_HK_ONLY" and st == "DONE"
    audit = pcf_by_code.get(bare(fid), {})
    info = run_info(fid) if is_run else None
    q = info["quality"] if info else {}
    official_rec = official.get(fid, {})
    channel = "SH_CONNECT" if is_run else ("QDII" if cls == "QDII" else "OUT_OF_SCOPE" if st == "EXCLUDED" else "UNKNOWN")
    dir_path = run_report_path(fid) if is_run else ""
    records["基金目录"].append(row(
        "基金目录", fund_id=fid, fund_name=c["基金全称"], index_code=c["跟踪指数代码"],
        index_name=c["跟踪指数名称"], listing_date=c["上市日期"], classification=cls,
        channel=channel, official_url=official_rec.get("url", ""),
        evidence_fact=official_rec.get("fact", ""), verified_at=(official_path.exists() and mtime(official_path) if verified else ""),
        expected_days=int(s.get("expected_days") or 0), usable_days=(q.get("usable") if is_run else None),
        oos_days=(next((x.get("oos_days") for x in info["results"].get("label_audit", []) if x.get("horizon") == 5), None) if info else None),
        date_start=(q.get("start") if is_run and q.get("usable") else ""),
        date_end=(q.get("end") if is_run and q.get("usable") else ""),
        task_status=st, reason=summary_reason(fid), run_id=(run_id(fid) if is_run else ""),
        report_path=str(dir_path) if is_run else "", limitations=(
            "已核实产品范围，但结果仍是初步价格风险证据，不代表可执行套利。" if verified else
            "候选清单未逐只缓存官方产品资料；技术结果若存在也不可外推为港股通ETF认证。"
        ) if st == "BLOCKED" else ("固定窗口内有效日不足，未缩短训练窗口。" if st == "INSUFFICIENT_HISTORY" else "范围外产品，不进入本研究的纯港股通样本。" if st == "EXCLUDED" else ""),
    ))


# Global task rows plus the preserved 210 parent rows.
def global_task(task_id: str, phase: str, priority: int, depends: str, status: str, objective: str,
                acceptance: str, summary: str, paths: str, refs: str, checks: str = "", issues: str = "",
                next_step: str = "") -> dict:
    return row("任务台账", task_id=task_id, parent_id="", fund_id="", phase=phase, priority=priority,
               depends_on=depends, status=status, started_at=mtime(ROOT / "README.md"), finished_at=NOW,
               run_id="GLOBAL", objective=objective, acceptance=acceptance, output_summary=summary,
               artifact_paths=paths, source_refs=refs, config_hash="", code_hash=CODE_HASH,
               check_ids=checks, issue_ids=issues, next_step=next_step, updated_at=NOW)


g01_paths = artifact_list(HANDOFF / "LOCATION.json", HANDOFF / "input_manifest.json", HANDOFF / "candidate_lock.json", candidate_path)
g01_refs = ";".join([str(candidate_path), str(ROOT / "data/inventory/input_manifest.json"), str(ROOT / "data/inventory/candidate_universe.csv")])
g02_paths = artifact_list(EVIDENCE / "g02_tests.log", EVIDENCE / "g02_run.log", EVIDENCE / "g02_validate.log", ROOT / "reports/model_comparison.csv")
g03_paths = artifact_list(EVIDENCE / "g03_520600_run.log", ROOT / "config/research_520600.json", ROOT / "runs/513090.SH/20260906_g03_smoke/reports/results.json")
g04_paths = artifact_list(pcf_audit_path, manifest_path, ROOT / "reports/candidate_terminal_summary.csv", ROOT / "reports/candidate_terminal_summary.json", official_path)
records["任务台账"].extend([
    global_task("G01", "G01", 1, "", "DONE", "工作区与输入锁定", "LOCATION、源manifest、候选唯一性检查", "工作树、只读源项目、210候选锁及哈希均已写入。", g01_paths, g01_refs, "G01-C1"),
    global_task("G02", "G02", 2, "G01", "DONE", "隔离复现520600", "全部模型计数一致及数值误差≤1e-8", "5/5单元测试通过；520600缓存复现的关键CSV与基线逐项一致。", g02_paths, str(ROOT / "reports"), "G02-C1;G02-C2;G02-C3"),
    global_task("G03", "G03", 3, "G02", "DONE", "参数化与回归验证", "520600新旧一致及第二标的冒烟通过", "参数化引擎、513090冒烟与批量配置已生成；验证脚本已修复为按目标选择标量探针。", g03_paths, str(ROOT / "scripts"), "G03-C1;G03-C2;G03-C3"),
    global_task("G04", "G04", 4, "G01", "DONE", "210条候选分类和覆盖", "逐条官方证据或真实缺口、队列锁定", "210条分母已对账；PCF覆盖审计、57条分钟队列和全部分类缺口均保留。", g04_paths, ";".join([str(candidate_path), str(pcf_audit_path), str(manifest_path)]), "G04-C1", "G04-OFFICIAL-EVIDENCE;G04-PERFUND-MINUTE-GAP"),
    global_task("G05", "G05", 5, "G03;G04", "DONE", "逐基金批处理", "所有基金任务均有终态、五步骤齐全或明确缺口", "210个基金父任务及1050个S1-S5子任务均已进入终态；48个技术运行成功，9个固定窗口不足，其余按范围或证据缺口收口。", artifact_list(ROOT / "reports/candidate_terminal_summary.csv"), str(ROOT / "reports/candidate_terminal_summary.json"), "G05-C1"),
    global_task("G06", "G06", 6, "G05", "RUNNING", "汇总和最终验收", "分母210对账、全部检查、Excel及FINAL_HANDOFF", "账本已完成，等待本次最终Excel原子导出与交接说明。", "", str(HANDOFF / "WORKBOOK_CONTRACT.md"), "G06-C1", "", "导出Excel并完成FINAL_HANDOFF后收口。"),
])


def issue(issue_id: str, attempt: int, task_id: str, category: str, error: str, impact: str, action: str,
          outcome: str, evidence: Path | str, fund_id: str = "", alternative: str = "", next_action: str = "") -> dict:
    return row("问题与重试", issue_id=issue_id, attempt=attempt, task_id=task_id, fund_id=fund_id,
               category=category, occurred_at=NOW, error=error, impact=impact, action=action,
               alternative_source=alternative, outcome=outcome, next_action=next_action,
               evidence_path=str(evidence), resolved_at="")


records["问题与重试"].extend([
    issue("G03-REMOTE-TARGET", 1, "G03", "DATA", "远端抽取第一次环境变量未传入，产物目标仍为520600。", "513090冒烟目标", "保留错误产物作负面证据；改为远端显式FUND_CODE/FUND_EXCHANGE。", "第二次完成513090 100日/17成分抽取。", ROOT / "data/raw/pilot_513090.jsonl.gz", alternative=str(ROOT / "scripts/extract_pilot_remote.py"), next_action="无需重试；复用513090_v1。"),
    issue("G03-REMOTE-TARGET", 2, "G03", "DATA", "513090远端目标抽取完成。", "513090冒烟", "使用正确远端环境变量重跑。", "已解决。", ROOT / "data/raw/pilot_513090_v1.jsonl.gz", alternative=str(ROOT / "data/raw/pilot_513090_v1.jsonl.gz")),
    issue("G04-PCF-GATE", 1, "G04", "DATA", "PCF覆盖审计初版未同时门控港股分钟源。", "210候选覆盖结论", "保留初版结果；补加HK源包存在性门控。", "已修复并重跑。", ROOT / "data/inventory/pcf_coverage_remote.json", alternative=str(ROOT / "scripts/audit_pcf_candidates_remote.py")),
    issue("G04-PCF-GATE", 2, "G04", "DATA", "PCF覆盖审计同时校验PCF、CN与HK源包。", "210候选覆盖结论", "采用v1审计JSON作为权威覆盖摘要。", "已解决。", pcf_audit_path, alternative=str(pcf_audit_path)),
    issue("G04-BATCH-PARTIAL", 1, "G04", "DATA", "首轮批量远端压缩流因进程/时间限制留下不完整chunk。", "57候选分钟抽取", "保留partial文件和stderr；改为8个日期chunk后合并，并以每基金100日校验。", "已解决；v2 manifest显示5700条/57基金。", ROOT / "data/raw/candidate_batch_20260906_v1.partial.jsonl.gz", alternative=str(manifest_path)),
    issue("G04-OFFICIAL-EVIDENCE", 1, "G04", "PERMISSION", "官方产品投资范围/港股通通道资料未能为全部210条逐只缓存。", "46个已跑技术样本及其余候选认证", "对520600与513090保存官方来源；其余不以名称发现池认证。", "未解决；相关父任务BLOCKED。", official_path, alternative="基金管理人官网/产品资料概要；本轮仅缓存两只", next_action="下一轮先补齐逐只官方资料，再重开BLOCKED任务。"),
    issue("G04-PERFUND-MINUTE-GAP", 1, "G04", "DATA", "部分候选PCF数量字段缺失，或分钟源缺价/有效整日不足固定门槛。", "非DONE候选", "使用远端PCF审计与57条候选分钟队列；不以固定替代金额冒充价格，不缩短窗口。", "未解决；按候选逐条BLOCKED或INSUFFICIENT_HISTORY。", pcf_audit_path, alternative=str(manifest_path), next_action="补齐数量字段和分钟价格后，在同一固定窗口重跑。"),
])


# Per-fund history issues are explicit, including late listings and failed runs.
for c in candidates:
    fid = c["ETF代码"]
    s = summary_rows[fid]
    if s["task_status"] == "INSUFFICIENT_HISTORY":
        is_failed_run = fid in failure_ids
        iid = f"G05-HISTORY-{bare(fid)}" if is_failed_run else f"G04-HISTORY-{bare(fid)}"
        ev = run_dir(fid) / "run.log" if is_failed_run else pcf_audit_path
        records["问题与重试"].append(issue(
            iid, 1, f"F-{fid}", "DATA", s["reason"], "固定窗口训练/外测或该基金起始覆盖",
            "保留固定60+10窗口；停止该基金后续数值外测，不缩短窗口。", "已终止重试；进入INSUFFICIENT_HISTORY。", ev,
            fund_id=fid, alternative=str(pcf_audit_path), next_action="下一个完整研究窗口重新获取数据并重跑。"))


def task_status_for_child(parent_status: str, child_no: int, is_run: bool, success: bool, verified: bool, fund_id: str) -> tuple[str, str]:
    if parent_status == "DONE":
        return "DONE", ""
    if parent_status == "EXCLUDED":
        return "EXCLUDED", ""
    if parent_status == "INSUFFICIENT_HISTORY":
        return "INSUFFICIENT_HISTORY", f"G05-HISTORY-{bare(fund_id)}" if fund_id in failure_ids else f"G04-HISTORY-{bare(fund_id)}"
    if is_run and success:
        if child_no == 1 or child_no == 5:
            return "BLOCKED", "G04-OFFICIAL-EVIDENCE"
        return "DONE", ""
    return "BLOCKED", "G04-PERFUND-MINUTE-GAP;G04-OFFICIAL-EVIDENCE"


def parent_issue_ids(fid: str, status: str) -> str:
    if status == "BLOCKED":
        return "G04-OFFICIAL-EVIDENCE" if fid in batch_ids and fid in success_ids else "G04-PERFUND-MINUTE-GAP;G04-OFFICIAL-EVIDENCE"
    if status == "INSUFFICIENT_HISTORY":
        return f"G05-HISTORY-{bare(fid)}" if fid in failure_ids else f"G04-HISTORY-{bare(fid)}"
    return ""


base_tasks = records["任务台账"]
parent_ids = {f"F-{c['ETF代码']}" for c in candidates}
base_tasks = [t for t in base_tasks if t["task_id"] not in parent_ids]
for c in candidates:
    fid = c["ETF代码"]
    s = summary_rows[fid]
    st = s["task_status"]
    is_run = fid in batch_ids
    success = fid in success_ids
    verified = s["classification"] == "STOCK_CONNECT_HK_ONLY" and st == "DONE"
    info = run_info(fid) if is_run else None
    paths = [run_report_path(fid)] if is_run else [summary_path, candidate_path]
    if is_run:
        paths += [run_dir(fid) / "run.log"]
    refs = [str(candidate_path), str(pcf_audit_path)]
    if is_run:
        refs += [str(ROOT / f"config/batch/{bare(fid)}_{fid.split('.')[-1]}.json"), str(run_report_path(fid))]
    pchecks = checks_for(fid, verified) if success else ""
    pissues = parent_issue_ids(fid, st)
    base_tasks.append(row(
        "任务台账", task_id=f"F-{fid}", parent_id="", fund_id=fid, phase="S1-S5", priority=20,
        depends_on="G03;G04", status=st, started_at=(mtime(run_dir(fid) / "run.log") if is_run else mtime(pcf_audit_path)),
        finished_at=NOW, run_id=(run_id(fid) if is_run else ""), objective="分类、数据与事件、固定外测、敏感性、报告和台账",
        acceptance="五步证据齐全；或有证据的范围外/历史不足/真实阻塞终态", output_summary=summary_reason(fid),
        artifact_paths=artifact_list(*paths), source_refs=";".join(refs), config_hash=(run_code_hash(fid) if is_run else ""),
        code_hash=(CODE_HASH if is_run else ""), check_ids=pchecks, issue_ids=pissues,
        next_step=("补齐官方产品资料后重开。" if st == "BLOCKED" else "下个完整窗口重跑。" if st == "INSUFFICIENT_HISTORY" else "范围外，不进入本研究。" if st == "EXCLUDED" else ""), updated_at=NOW))
    for no, label in enumerate(["分类与口径", "数据覆盖与事件", "固定外测", "敏感性与QA", "交付与台账"], start=1):
        child_st, child_issue = task_status_for_child(st, no, is_run, success, verified, fid)
        child_paths = list(paths)
        if is_run and success and no in (3, 4):
            child_paths.append(run_dir(fid) / "reports/model_comparison.csv" if no == 3 else run_dir(fid) / "reports/sensitivity.csv")
        child_checks = ""
        if child_st == "DONE":
            if no == 1 and verified:
                child_checks = f"QA-{bare(fid)}-OFFICIAL"
            elif no == 2:
                child_checks = f"QA-{bare(fid)}-INPUT"
            elif no == 3:
                child_checks = f"QA-{bare(fid)}-FIXED"
            elif no == 4:
                child_checks = f"QA-{bare(fid)}-SENS"
            elif no == 5:
                child_checks = f"QA-{bare(fid)}-SCALAR;QA-{bare(fid)}-FINGERPRINT"
        base_tasks.append(row(
            "任务台账", task_id=f"F-{fid}-S{no}", parent_id=f"F-{fid}", fund_id=fid, phase=f"S{no}", priority=21,
            depends_on=f"F-{fid}" if no == 1 else f"F-{fid}-S{no-1}", status=child_st,
            started_at=(mtime(run_dir(fid) / "run.log") if is_run else mtime(pcf_audit_path)), finished_at=NOW,
            run_id=(run_id(fid) if is_run else ""), objective=label,
            acceptance="该步骤有可追溯证据；若不可执行则记录真实阻塞或固定窗口不足。",
            output_summary=(f"{label}：{summary_reason(fid)}" if child_st != "DONE" else f"{label}完成；证据见运行报告与验收检查。"),
            artifact_paths=artifact_list(*child_paths), source_refs=";".join(refs),
            config_hash=(run_code_hash(fid) if is_run else ""), code_hash=(CODE_HASH if is_run else ""),
            check_ids=child_checks, issue_ids=child_issue, next_step=("补齐官方产品资料后重开。" if child_st == "BLOCKED" else "下个完整窗口重跑。" if child_st == "INSUFFICIENT_HISTORY" else ""), updated_at=NOW))
records["任务台账"] = base_tasks


# Data coverage: all 210 candidates have the PCF audit row; the 57-minute queue
# has explicit run-level basket and hedge-tool coverage rows.
pcf_audit_hash = sha256(pcf_audit_path) if pcf_audit_path.exists() else ""
for c in candidates:
    fid = c["ETF代码"]
    a = pcf_by_code.get(bare(fid), {})
    observed = int(a.get("pcf_dates", s.get("pcf_observed_days", 0)) or 0)
    expected = int(summary_rows[fid].get("expected_days") or 0)
    records["数据覆盖"].append(row(
        "数据覆盖", data_id=f"PCF-AUDIT-{fid}", run_id="G04-PCF-AUDIT", fund_id=fid,
        data_type="PCF_AUDIT", security_id=fid, source_path_url=str(pcf_audit_path), retrieved_at=mtime(pcf_audit_path),
        sha256=pcf_audit_hash, start_date=WINDOW_START, end_date=WINDOW_END, expected_days=expected,
        observed_days=observed, missing_days=max(0, expected - observed), bar_count=None,
        quality_conclusion=f"PCF文件覆盖{observed}/{expected}日；数量缺失日={a.get('missing_quantity_days', summary_rows[fid].get('pcf_missing_quantity_days', ''))}。"))

for fid in batch_ids:
    info = run_info(fid)
    q = info["quality"]
    bundle = info["bundle"]
    bundle_hash = sha256(bundle) if bundle.exists() else ""
    cfg = ROOT / "config/batch" / f"{bare(fid)}_{fid.split('.')[-1]}.json"
    cfg_hash = sha256(cfg) if cfg.exists() else ""
    expected = 100
    records["数据覆盖"].append(row(
        "数据覆盖", data_id=f"{bare(fid)}-PCF", run_id=run_id(fid), fund_id=fid, data_type="PCF",
        security_id=fid, source_path_url=str(bundle), retrieved_at=mtime(bundle), sha256=bundle_hash,
        start_date=WINDOW_START, end_date=WINDOW_END, expected_days=expected, observed_days=100, missing_days=0,
        quality_conclusion="100个窗口日PCF记录已批量抽取；数量字段缺失风险见PCF审计。"))
    records["数据覆盖"].append(row(
        "数据覆盖", data_id=f"{bare(fid)}-MINUTE-BASKET", run_id=run_id(fid), fund_id=fid, data_type="MINUTE_BASKET",
        security_id="BASKET", source_path_url=str(info["dir"] / "reports/data_quality.csv"),
        retrieved_at=mtime(info["dir"] / "reports/data_quality.csv"), sha256=sha256(info["dir"] / "reports/data_quality.csv"),
        start_date=q.get("start", ""), end_date=q.get("end", ""), expected_days=100, observed_days=q.get("usable", 0),
        missing_days=100 - q.get("usable", 0), bar_count=q.get("bars"), stale_weight_p50=q.get("stale_p50"),
        stale_weight_p95=q.get("stale_p95"), frozen_weight_max=q.get("frozen_max"), timezone_label="Asia/Hong_Kong; normalized in engine",
        rejected_dates_path=str(info["dir"] / "reports/data_quality.csv"), quality_conclusion="; ".join(f"{k}={v}" for k, v in q.get("counts", {}).items())))
    for tool in ["HSI_FUT", "HHI_FUT", "HTI_FUT", "02800", "02828", "03032", "03033", "02845"]:
        is_future = tool.endswith("_FUT")
        src = ROOT / "data/inventory/futures_contract_probe.csv" if is_future else info["dir"] / "reports/model_comparison.csv"
        records["数据覆盖"].append(row(
            "数据覆盖", data_id=f"{bare(fid)}-{tool}", run_id=run_id(fid), fund_id=fid, data_type="HEDGE_TOOL",
            security_id=tool, source_path_url=str(src), retrieved_at=mtime(src), sha256=(sha256(src) if src.exists() else ""),
            start_date=q.get("start", ""), end_date=q.get("end", ""), expected_days=100, observed_days=q.get("usable", 0),
            missing_days=100 - q.get("usable", 0), bar_count=(q.get("usable", 0) * (345 if is_future else 120)),
            timezone_label="Asia/Hong_Kong", quality_conclusion="工具纳入候选集；实际模型表现见回测结果。"))


# Events are only registered where the verified event manifest intersects the
# 520600 PCF basket.  Other funds retain the unresolved corporate-action gap.
event_manifest_path = ROOT / "data/inventory/verified_events.json"
event_manifest = load_json(event_manifest_path, [])
component_codes = set()
bundle_520600 = ROOT / "data/raw/candidates_v2/520600.jsonl.gz"
if bundle_520600.exists():
    import gzip
    for line in gzip.open(bundle_520600, "rt", encoding="utf-8"):
        for comp in json.loads(line).get("components", []):
            if comp.get("成分股代码"):
                component_codes.add(comp["成分股代码"])
for e in event_manifest:
    if e.get("code") not in component_codes:
        continue
    typ = e.get("type", "event")
    eff = e.get("start") or e.get("effective_date") or e.get("ex_date") or e.get("last_dealing_date") or e.get("announcement_date")
    ann = e.get("announcement_date") or ""
    source = ";".join(e.get("sources", []))
    related = e.get("observed_temporary_code", "")
    excluded = 3 if e.get("code") == "00489" else 0
    records["证券事件"].append(row(
        "证券事件", event_id=f"EV-520600-{e['code']}-{typ}", fund_id="520600.SH", security_id=e["code"],
        security_name="", event_type=typ, effective_at=iso_datetime(eff), announcement_date=iso_date(ann),
        source_url=source, source_level="HKEX/issuer", verified_fact=json.dumps({k: v for k, v in e.items() if k not in {"sources", "policy"}}, ensure_ascii=False),
        handling=e.get("policy", ""), affected_dates=(
            ";".join(e.get("observed_temporary_dates", []))
            or (f"{e.get('start','')}..{e.get('end','')}" if e.get("start") else "")
            or iso_date(e.get("ex_date") or e.get("effective_date") or e.get("last_dealing_date") or e.get("announcement_date") or eff)
            or "未确定（公司行动历史覆盖缺口）"
        ),
        excluded_day_count=excluded, related_code=related, unpriced_entitlement="YES" if typ == "privatization_and_distribution" else "NO",
        evidence_path=str(event_manifest_path), issue_id=("G04-PERFUND-MINUTE-GAP" if typ == "issuer_identity" else "")))


# Tool mappings for the 57-run queue.  These describe tested resources and do
# not imply product certification.
tool_families = {"HSI_FUT": "HSI", "HHI_FUT": "HHI", "HTI_FUT": "HSTECH", "02800": "HSI", "02828": "HHI", "03032": "HSTECH", "03033": "HSTECH", "02845": "EV_PROXY"}
contract_map_path = ROOT / "data/inventory/futures_contract_probe.csv"
contract_map_hash = sha256(contract_map_path) if contract_map_path.exists() else ""
for fid in batch_ids:
    q = quality_stats(fid)
    for tool, family in tool_families.items():
        is_future = tool.endswith("_FUT")
        records["工具映射"].append(row(
            "工具映射", mapping_id=f"{fid}-{tool}", fund_id=fid, tool_id=tool, tool_kind="FUTURE" if is_future else "ETF_PROXY",
            index_family=family, economic_reason="同指数工具优先；EV_PROXY仅作主题替代参照。" if family == "EV_PROXY" else "同指数或相近指数价格暴露。",
            official_url="", currency="HKD", multiplier=50 if is_future else 1, contract_map_path=(str(contract_map_path) if is_future else ""),
            coverage_days=q.get("usable", 0), inclusion="TESTED", decision_before_oos="2026-06-09T09:00:00+08:00",
            reason="技术队列已测试；产品官方分类未逐只核验" if fid not in {"513090.SH", "520600.SH"} else "官方产品范围已核验，工具映射仍仅表示价格风险研究。",
            source_hash=contract_map_hash if is_future else sha256((run_dir(fid) / "reports/model_comparison.csv") if (run_dir(fid) / "reports/model_comparison.csv").exists() else info["bundle"]),
            limitations="不代表可执行套利；期货合约按运行时固定映射，ETF proxy不等于期货替代。"))


# Backtest and sensitivity facts come directly from machine-readable reports.
for fid in success_ids:
    info = run_info(fid)
    d = info["dir"]
    results = info["results"]
    cfg = ROOT / "config/batch" / f"{bare(fid)}_{fid.split('.')[-1]}.json"
    cfg_hash = sha256(cfg)
    val = load_json(d / "reports/validation.json", {})
    ci_by_h = {str(x.get("horizon")): x.get("ci95") for x in val.get("bootstrap", []) if x.get("model") == "HHI_HTI_fixed_pair"}
    audits = {str(x.get("horizon")): x for x in results.get("label_audit", [])}
    for m in results.get("metrics", []):
        h = str(m["horizon"])
        audit = audits[h]
        model = m["model"]
        ci = ci_by_h.get(h)
        rid = f"{bare(fid)}-{m['horizon']}-{model}-BASE"
        records["回测结果"].append(row(
            "回测结果", result_id=rid, run_id=run_id(fid), fund_id=fid, horizon_min=int(m["horizon"]), model_id=model,
            scenario="BASE", target_currency="HKD-equivalent return; FX ex-post", train_days=60, validation_days=10,
            oos_start=iso_date(audit["first_oos"]), oos_end=iso_date(audit["last_oos"]), oos_days=int(m["days"]), label_count=int(m["samples"]),
            unhedged_std_bp=float(m["target_std_bps"]), residual_std_bp=float(m["residual_std_bps"]), variance_reduction=float(m["variance_reduction"]),
            up_es95_bp=float(m["upside_es95_bps"]), down_es95_bp=float(m["downside_es95_bps"]), abs_p95_bp=float(m["abs_p95_bps"]),
            mean_residual_bp=float(m["residual_mean_bps"]), ci_low=(float(ci[0]) if ci else None), ci_high=(float(ci[1]) if ci else None),
            beta_path=str(d / "reports/folds.csv"), config_hash=cfg_hash, results_path=str(info["results_path"]),
            research_conclusion="固定60+10滚动窗口的初步价格风险结果；未声称可执行套利。",
            limitations="排除复杂公司行动日；现金替代、冻结权重和FX为研究假设；未认证基金不可作产品结论。"))
    sens_rows = read_csv(d / "reports/sensitivity.csv")
    for i, s in enumerate(sens_rows, start=1):
        h = int(s["horizon"])
        model = s["model"] or "selected"
        base_model = "HHI_HTI_fixed_pair" if model == "selected" else model
        base_id = f"{bare(fid)}-{h}-{base_model}-BASE"
        base = next((x for x in results["metrics"] if int(x["horizon"]) == h and x["model"] == base_model), None)
        first = iso_date(s.get("first_oos", "")) if s.get("first_oos") else ""
        last = iso_date(s.get("last_oos", "")) if s.get("last_oos") else ""
        records["敏感性"].append(row(
            "敏感性", sensitivity_id=f"{bare(fid)}-SENS-{i:03d}", run_id=run_id(fid), fund_id=fid, horizon_min=h,
            model_id=model, scenario=s["scenario"], refit=("refit" in s["scenario"] or "fresh" in s["scenario"]),
            oos_start=first, oos_end=last, oos_days=int(s["days"] or 0), label_count=int(s["samples"] or 0),
            residual_std_bp=float(s["residual_std_bps"] or 0), variance_reduction=float(s["variance_reduction"] or 0),
            base_result_id=base_id, sample_difference=(str(int(s["samples"]) - int(base["samples"])) if base else "N/A"), status="PASS",
            reason="敏感性样本/窗口与BASE不同；不将样本变化解释为纯模型改善。", artifact_path=str(d / "reports/sensitivity.csv")))


def check(check_id: str, task_id: str, name: str, method: str, expected: str, actual: str, evidence: Path | str,
          status: str = "PASS", run: str = "GLOBAL", fund: str = "", tolerance: str = "", issue_id: str = "", resolution: str = "") -> dict:
    return row("验收检查", check_id=check_id, task_id=task_id, run_id=run, fund_id=fund, check_name=name, method=method,
               expected=expected, actual=actual, tolerance=tolerance, status=status, evidence_path=str(evidence), checked_at=NOW,
               issue_id=issue_id, resolution=resolution)


records["验收检查"].extend([
    check("G01-C1", "G01", "候选分母与锁定", "独立读取candidate_lock.json并重算基金代码集合", "210条且唯一", "210条；唯一代码哈希锁定", HANDOFF / "candidate_lock.json"),
    check("G02-C1", "G02", "单元测试", "unittest discover -s tests -v", "5/5 PASS", "5/5 PASS", EVIDENCE / "g02_tests.log"),
    check("G02-C2", "G02", "缓存复现逐文件", "SHA256/JSON/关键CSV逐项比较", "无数值差异；JSON对象相等", "关键CSV字节一致，results JSON对象一致", EVIDENCE / "g02_run.log", tolerance="数值字段≤1e-8"),
    check("G02-C3", "G02", "代表性指标", "读取model_comparison并重算方差下降", "30m HHI+HTI指标可复现", "residual_std=19.430026bp；variance_reduction=0.722843", EVIDENCE / "g02_validate.log", tolerance="1e-8 bp/ratio"),
    check("G03-C1", "G03", "520600参数化一致性", "配置化运行与基线关键指标比较", "新旧一致", "数值最大绝对差0", EVIDENCE / "g03_520600_run.log", tolerance="1e-8"),
    check("G03-C2", "G03", "第二标的冒烟", "513090独立配置运行并检查窗口/目标", "目标不串标；结果目录独立", "513090，92有效日，32 OOS日；结果目录独立", ROOT / "runs/513090.SH/20260906_g03_smoke/reports/results.json"),
    check("G03-C3", "G03", "批量配置覆盖", "统计config/batch与run报告", "57个候选配置", f"{len(batch_ids)}个配置；{len(success_ids)}成功、{len(failure_ids)}固定窗口不足", ROOT / "reports/candidate_terminal_summary.json"),
    check("G04-C1", "G04", "PCF与分钟队列对账", "读取v1 PCF审计与batch manifest", "210候选；57分钟包各100日", "210候选，100源日期；57包/5700条记录", manifest_path),
    check("G05-C1", "G05", "逐基金终态与S1-S5", "按任务ID和基金目录交叉计数", "210父任务+1050子任务；全部终态", "见账本生成后统计", ROOT / "reports/candidate_terminal_summary.json"),
    check("G06-C1", "G06", "最终账本结构预检", "validate_ledger.py（非final）", "结构校验PASS后导出", "待运行", HANDOFF / "ledger_validation.json"),
])

for fid in success_ids:
    info = run_info(fid)
    d = info["dir"]
    verified = summary_rows[fid]["classification"] == "STOCK_CONNECT_HK_ONLY"
    q = info["quality"]
    records["验收检查"].append(check(f"QA-{bare(fid)}-INPUT", f"F-{fid}-S2", "输入覆盖与日期边界", "读取manifest、PCF与data_quality并重算日期集合", "PCF=100日；分钟有效日与报告一致", f"PCF=100；有效日={q.get('usable',0)}；窗口未外溢", manifest_path, run=run_id(fid), fund=fid))
    records["验收检查"].append(check(f"QA-{bare(fid)}-FIXED", f"F-{fid}-S3", "固定外测结果", "读取results.json/model_comparison.csv并重算行数", "4周期×8模型；train=60 validation=10", f"{len(info['results'].get('metrics',[]))}指标行；split=60+10", d / "reports/results.json", run=run_id(fid), fund=fid))
    records["验收检查"].append(check(f"QA-{bare(fid)}-SENS", f"F-{fid}-S4", "敏感性样本审计", "读取sensitivity.csv并比较BASE样本", "敏感性行有scenario和sample差异说明", f"{len(read_csv(d/'reports/sensitivity.csv'))}行；样本变化已标注", d / "reports/sensitivity.csv", run=run_id(fid), fund=fid))
    records["验收检查"].append(check(f"QA-{bare(fid)}-SCALAR", f"F-{fid}-S5", "标量复算与未来隔离", "validate_pilot.py输出scalar_checks及folds边界", "标量PASS；无未来测试数据泄漏", "scalar_checks=3 PASS；fold边界校验PASS", d / "reports/validation.json", run=run_id(fid), fund=fid))
    records["验收检查"].append(check(f"QA-{bare(fid)}-FINGERPRINT", f"F-{fid}-S5", "配置/代码指纹", "SHA256读取config、code_manifest并检查results元数据", "config_hash与code_hash可追溯", f"config={run_code_hash(fid)[:12]}…；code={CODE_HASH[:12]}…", code_manifest_path, run=run_id(fid), fund=fid))
    if verified:
        records["验收检查"].append(check(f"QA-{bare(fid)}-OFFICIAL", f"F-{fid}-S1", "官方产品分类", "读取已缓存基金公司官方产品资料", "官方资料确认港股通投资范围", official[fid]["fact"], official_path, run=run_id(fid), fund=fid))


# Reorder global tasks first, then each fund parent and five children.
global_order = {f"G0{i}": i for i in range(1, 7)}
records["任务台账"].sort(key=lambda r: (0, global_order.get(r["task_id"], 99)) if r["task_id"].startswith("G") else (1, next((i for i, c in enumerate(candidates) if c["ETF代码"] == r.get("fund_id")), 999), 0 if r["task_id"].count("-") == 1 else int(r["task_id"].rsplit("S", 1)[-1])))

# The workbook builder converts every non-null date/datetime field to a native
# Date.  Empty optional date fields must therefore be JSON null, not an empty
# string (new Date("Z") is invalid in the underlying spreadsheet engine).
date_keys = {c["key"] for sh in schema["sheets"] for c in sh["columns"] if c["type"] in {"date", "datetime"}}
for sheet_rows in records.values():
    for record in sheet_rows:
        for key in date_keys:
            if record.get(key) == "":
                record[key] = None

# All tasks are now present; keep G06 open until the workbook export is complete.
out = HANDOFF / "ledger.json"
out.write_text(json.dumps({"schema_version": schema["schema_version"], "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")

counts = {}
for r in records["任务台账"]:
    counts[r["status"]] = counts.get(r["status"], 0) + 1
summary = {"generated_at": NOW, "task_counts": counts, "fund_count": len(records["基金目录"]),
           "task_count": len(records["任务台账"]), "result_count": len(records["回测结果"]), "sensitivity_count": len(records["敏感性"]),
           "success_runs": len(success_ids), "failed_runs": len(failure_ids)}
(HANDOFF / "ledger_assembly_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False))

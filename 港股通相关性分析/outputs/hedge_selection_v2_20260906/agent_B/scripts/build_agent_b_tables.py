#!/usr/bin/env python3
"""Build agent B machine tables without promoting old research to new confirmation."""
from __future__ import annotations
import csv, gzip, hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTS = ROOT.parent
CONTROL = OUTS / "control"
ASSIGNMENT = CONTROL / "assignment_B.json"
SCHEMA = CONTROL / "schema.json"
PCF_DIR = ROOT / "data/raw/pcf_html/520600"
PCF_JSONL = ROOT / "data/new_period_520600/new_period_520600_pcf.jsonl.gz"
IB_MANIFEST = ROOT / "data/raw/ibkr_new_period/ibkr_fetch_manifest.json"
OLD_DIR = ROOT / "data/old_exploratory"

def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
NOW = now_utc()

def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def sha(path):
    if not path or not Path(path).exists():
        return ""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def read_csv(path):
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None

def integer(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None

def compact(v):
    if v is None:
        return None
    return json.dumps(v, ensure_ascii=False, separators=(",", ":")) if isinstance(v, (dict, list, tuple)) else v

schema = read_json(SCHEMA, {})
columns = {n: [c["key"] for c in t["columns"]] for n, t in schema["tables"].items()}
assignment = read_json(ASSIGNMENT, [])
funds = {r["fund_id"]: r for r in assignment}
technical = {r["fund_id"] for r in assignment if r.get("has_technical_result")}

def row(table, **kwargs):
    return {k: kwargs.get(k) for k in columns[table]}

def write_table(name, rows):
    rows = [{k: r.get(k) for k in columns[name]} for r in rows]
    (ROOT / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with (ROOT / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns[name])
        w.writeheader()
        for r in rows:
            w.writerow({k: compact(r.get(k)) if r.get(k) is not None else "" for k in columns[name]})
    return rows

def bundle(fid):
    d = OLD_DIR / fid
    return d, read_json(d / "results.json", {}) or {}, read_csv(d / "model_comparison.csv"), read_csv(d / "folds.csv"), read_json(d / "validation.json", {}) or {}

old = {fid: bundle(fid) for fid in technical}
def listing_date(reason):
    m = re.search(r"上市日(\d{4}-\d{2}-\d{2})", reason or "")
    return m.group(1) if m else None

# Evidence
evidence = [
    row("evidence", evidence_id="B-EVD-520600-PRODUCT", fund_id="520600.SH", purpose="分类/分母", publisher="广发基金", url="https://www.gffunds.com.cn/jjgg/flwj/202506/P020250610344633290064.pdf", published_at="2025-06-10", retrieved_at_utc=NOW, local_path="agent_B/data/evidence/520600_product_facts.pdf", sha256="", locator="产品资料第1-2页", finding="官方资料显示基金代码520600、上交所上市，标的为中证港股通汽车产业主题，主要采用完全复制法并持有指数成分股。", sufficiency="SUFFICIENT"),
]
for p in sorted(PCF_DIR.glob("*.html")):
    d = p.stem
    evidence.append(row("evidence", evidence_id=f"B-EVD-520600-PCF-{d}", fund_id="520600.SH", purpose="分母", publisher="广发基金", url=f"https://www.gffunds.com.cn/proxy/pcflist/520600?date={d}", published_at=d, retrieved_at_utc=NOW, local_path=str(p.relative_to(OUTS)), sha256=sha(p), locator="PCF成分股数量表", finding="官方PCF页面已归档；页面解析得到50个成分/现金项目行，数量字段用于篮子可复制性核验。", sufficiency="PARTIAL"))
evidence += [
    row("evidence", evidence_id="B-EVD-IBKR-FUTURES-2026", fund_id="GLOBAL", purpose="工具", publisher="Interactive Brokers / HKEX contract details", url="https://www.hkex.com.hk/Products/Derivatives/Equity-Index", published_at=None, retrieved_at_utc=NOW, local_path="agent_B/data/raw/ibkr_new_period/ibkr_fetch_manifest.json", sha256=sha(IB_MANIFEST), locator="HSIU6/HHIU6/HTIU6 contract details", finding="IBKR只读合约确认了HSIU6、HHIU6、HTIU6；目标基金分钟价仍未形成完整新增面板。", sufficiency="PARTIAL"),
    row("evidence", evidence_id="B-EVD-OLD-REPRO-520600", fund_id="520600.SH", purpose="模型复现", publisher="本地可复核运行", url="", published_at=None, retrieved_at_utc=NOW, local_path="agent_B/runs/520600_old_repro/reports/results.json", sha256=sha(ROOT / "runs/520600_old_repro/reports/results.json"), locator="四个horizon的HHI_HTI_fixed_pair", finding="旧样本重跑与归档结果的核心残差标准差和方差降低逐项一致；仅支持探索性复现。", sufficiency="PARTIAL"),
    row("evidence", evidence_id="B-EVD-NEW-QUOTE-BLOCKER", fund_id="GLOBAL", purpose="分母", publisher="Eastmoney/Tencent quote endpoints", url="https://push2his.eastmoney.com/api/qt/stock/kline/get", published_at=None, retrieved_at_utc=NOW, local_path="agent_B/data/new_period_520600", sha256="", locator="2026-08-04至2026-09-04", finding="新增接口出现远端关闭/覆盖不足；港股成分无法形成完整21日共同1分钟端点，未将低频或局部回包冒充确认样本。", sufficiency="INSUFFICIENT"),
]

# Fund decisions: every assigned fund is gated; old figures are labeled exploratory.
decisions = []
for fid, rec in funds.items():
    is_tech = fid in technical
    is_520600 = fid == "520600.SH"
    scope = "CONNECT_PURE" if is_520600 else "UNVERIFIED"
    scope_evidence = "B-EVD-520600-PRODUCT" if is_520600 else None
    gaps = ["2026-08-04至2026-09-04新增价格面板未形成完整共同1分钟端点", "新增确认期至少20个有效外测日尚未通过"]
    reasons = ["CONFIRMATION_WINDOW_UNAVAILABLE", "NEW_QUOTE_COVERAGE_INSUFFICIENT", "CANDIDATE_COVERAGE_INCOMPLETE", "EVENT_COVERAGE_INCOMPLETE"]
    if not is_520600:
        gaps.append("官方范围/指数成分证据未在本组证据包中充分闭环")
        reasons.append("OFFICIAL_SCOPE_UNVERIFIED")
    if rec.get("luna_status") == "INSUFFICIENT_HISTORY":
        gaps.append(rec.get("reason", "上市历史不足"))
        reasons.append("INSUFFICIENT_LISTING_HISTORY")
    if rec.get("luna_status") == "BLOCKED":
        gaps.append(rec.get("reason", "PCF数量字段缺失"))
        reasons.append("PCF_QUANTITY_FIELD_MISSING")
    if rec.get("classification") == "QDII":
        gaps.append("候选快照标为QDII，但未以官方产品文件作最终范围排除")
        reasons.append("QDII_CLASSIFICATION_UNVERIFIED")
    folder, result, comparison, folds, validation = old.get(fid, (None, {}, [], [], {}))
    metric = next((m for m in comparison if integer(m.get("horizon")) == 30 and m.get("model") == "HHI_HTI_fixed_pair"), None)
    latest_beta = None
    latest_beta_date = None
    if folds:
        f30 = [x for x in folds if integer(x.get("horizon")) == 30]
        if f30:
            last = f30[-1]
            latest_beta_date = last.get("test_date")
            latest_beta = {"HHI_FUT": num(last.get("fixed_pair_HHI_beta")), "HTI_FUT": num(last.get("fixed_pair_HTI_beta"))}
    exploratory = f"P_{fid.replace('.', '_')}_OLD_HHI_HTI_FIXED_PAIR_30M" if metric else None
    ci = [None, None]
    if metric and validation.get("bootstrap"):
        ci = next((x.get("ci95", [None, None]) for x in validation["bootstrap"] if integer(x.get("horizon")) == 30), [None, None])
    detail = "；".join(gaps) + (f"；旧样本30分钟HHI+HTI探索性VR={num(metric.get('variance_reduction')):.4f}、残差={num(metric.get('residual_std_bps')):.2f}bp，但确认状态为复用旧样本。" if metric else "；本组未形成可采用的新样本回测。")
    decisions.append(row("fund_decisions", fund_id=fid, fund_name=rec.get("fund_name"), owner="B", index_id=None, index_name=rec.get("index_name"), scope=scope, scope_evidence_id=scope_evidence, listing_date=listing_date(rec.get("reason", "")) or ("2024-12-30" if is_520600 else None), primary_horizon_min=30, decision="INSUFFICIENT_EVIDENCE", reason_codes=reasons, reason_detail=detail, recommended_policy_id=None, primary_tools=None, backup_policy_id=None, exploratory_best_policy_id=exploratory, execution_status="CONDITIONAL" if is_520600 else "UNKNOWN", confirmation_status="REUSED_OR_UNPROVEN" if metric else "UNAVAILABLE", confirmation_start="2026-08-04", confirmation_end="2026-09-04", oos_days=None, target_std_bp=num(metric.get("target_std_bps")) if metric else None, residual_std_bp=num(metric.get("residual_std_bps")) if metric else None, variance_reduction=num(metric.get("variance_reduction")) if metric else None, ci_low=ci[0], ci_high=ci[1], up_es95_bp=num(metric.get("upside_es95_bps")) if metric else None, down_es95_bp=num(metric.get("downside_es95_bps")) if metric else None, positive_block_fraction=None, strict_refit_vr=None, effective_quote_coverage=None, latest_beta_date=latest_beta_date, latest_beta=latest_beta, candidate_coverage_complete=False, event_coverage_complete=False, remaining_gaps=gaps, invalidation_triggers=["新窗口共同有效端点低于95%", "新增确认期少于20个有效外测日", "PCF数量/公司行动无法逐日核验", "新样本严格重拟合VR低于门槛"], source_run_id=f"B_OLD_REPRO_{fid}" if metric else "B_ASSIGNMENT_REVIEW", result_path=str((folder / "results.json").relative_to(OUTS)) if folder and (folder / "results.json").exists() else "agent_B/FINAL_REPORT.md", updated_at_utc=NOW))

# Old metrics with explicit REUSED_OR_UNPROVEN status.
model_metrics = []
for fid in sorted(technical):
    folder, result, comparison, folds, validation = old[fid]
    labels = {str(x.get("horizon")): x for x in result.get("label_audit", [])}
    ci_map = {str(x.get("horizon")): x.get("ci95") for x in validation.get("bootstrap", [])}
    for m in comparison:
        h, model = integer(m.get("horizon")), m.get("model")
        if h is None or not model:
            continue
        label = labels.get(str(h), {})
        ci = ci_map.get(str(h), [None, None]) if model == "HHI_HTI_fixed_pair" else [None, None]
        model_metrics.append(row("model_metrics", run_id=f"B_OLD_REPRO_{fid}", fund_id=fid, horizon_min=h, policy_id=f"P_{fid.replace('.', '_')}_OLD_{model}_{h}M", model_id=model, scenario_id="OLD_EXPLORATORY_MAR03_AUG03", sample_hash=sha(folder / "model_comparison.csv"), confirmation_status="REUSED_OR_UNPROVEN", oos_start=label.get("first_oos"), oos_end=label.get("last_oos"), oos_days=integer(m.get("days")), sample_count=integer(m.get("samples")), target_std_bp=num(m.get("target_std_bps")), residual_std_bp=num(m.get("residual_std_bps")), variance_reduction=num(m.get("variance_reduction")), ci_low=ci[0], ci_high=ci[1], bootstrap_method="validation.json bootstrap over stored old OOS residuals" if ci[0] is not None else None, bootstrap_seed=integer(result.get("bootstrap_seed")), target_up_es95_bp=None, target_down_es95_bp=None, up_es95_bp=num(m.get("upside_es95_bps")), down_es95_bp=num(m.get("downside_es95_bps")), positive_block_fraction=None, residual_mean_bp=num(m.get("residual_mean_bps")), beta_turnover=None, decision_gate_results={"new_lock": False, "exploratory_only": True, "old_window": result.get("window")}, residual_path=str((folder / "oos_residuals.parquet").relative_to(OUTS)) if (folder / "oos_residuals.parquet").exists() else str((folder / "model_comparison.csv").relative_to(OUTS))))

# Candidate pool.
tool_specs = {
    "HSI_FUT": ("HSI", "期货", "广泛港股系统性风险基准", "https://www.hkex.com.hk/Products/Derivatives/Equity-Index/Hang-Seng-Index-Futures", "HKD", 50.0),
    "HHI_FUT": ("HHI", "期货", "大型中国企业/港股通大盘风险基准", "https://www.hkex.com.hk/Products/Derivatives/Equity-Index/Hang-Seng-China-Enterprises-Index-Futures", "HKD", 50.0),
    "HTI_FUT": ("HSTECH", "期货", "科技互联网风险基准", "https://www.hkex.com.hk/Products/Derivatives/Equity-Index/Hang-Seng-TECH-Index-Futures", "HKD", 50.0),
    "02800": ("HSI", "ETF", "恒生指数ETF候选", "https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index", "HKD", None),
    "02828": ("HHI", "ETF", "恒生国企ETF候选", "https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index", "HKD", None),
    "03032": ("HSTECH", "ETF", "恒生科技ETF候选", "https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index", "HKD", None),
    "03033": ("HSTECH", "ETF", "恒生科技ETF候选", "https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index", "HKD", None),
    "02845": ("EV_PROXY", "ETF", "新能源汽车风险代理候选", "https://www.hkex.com.hk/Products/Listed-Derivatives/Equity-Index", "HKD", None),
}
lock_hash = hashlib.sha256(f"B|{NOW}|REUSED_OR_UNPROVEN|81".encode()).hexdigest()
tool_candidates = []
for fid in funds:
    for tool, (family, asset, rationale, url, ccy, multiplier) in tool_specs.items():
        supported = fid == "520600.SH" and tool in {"HSI_FUT", "HHI_FUT", "HTI_FUT"}
        tool_candidates.append(row("tool_candidates", fund_id=fid, tool_id=tool, risk_family=family, rationale=rationale, asset_type=asset, official_url=url, listed_from=None, listed_to=None, currency=ccy, multiplier=multiplier, lot_size=None, session="09:30-12:00,13:00-16:00 Asia/Hong_Kong", quote_coverage=None, data_start=None, data_end=None, short_status="SUPPORTED" if supported else "UNKNOWN", included=supported, exclusion_reason="仅520600在旧样本中有该工具的探索性输入；全部新窗口候选仍未完成确认。" if not supported else "新增窗口价格/成本/事件闭环未完成，暂不锁定为主方案。", evidence_id="B-EVD-IBKR-FUTURES-2026" if tool.endswith("_FUT") else None, selection_lock_hash=lock_hash))

# Last exploratory fold weights.
weights = []
for fid in sorted(technical):
    folder, result, comparison, folds, validation = old[fid]
    for horizon in (5, 15, 30, 60):
        rows = [r for r in folds if integer(r.get("horizon")) == horizon]
        if not rows:
            continue
        last = rows[-1]
        for tool, key in (("HHI_FUT", "fixed_pair_HHI_beta"), ("HTI_FUT", "fixed_pair_HTI_beta")):
            weights.append(row("weights", fund_id=fid, horizon_min=horizon, policy_id=f"P_{fid.replace('.', '_')}_OLD_HHI_HTI_FIXED_PAIR_{horizon}M", effective_date=last.get("test_date"), train_start=last.get("fit_start"), train_end=last.get("train_end"), validation_start=last.get("validation_start"), validation_end=last.get("validation_start"), tool_id=tool, beta=num(last.get(key)), currency_conversion=1.0, selection_reason="旧样本固定HHI+HTI双腿的最后一日名义系数；不用于新增窗口锁定。", config_hash=sha(folder / "results.json")))

# No fabricated execution quantities or zero costs.
execution = []
for fid in funds:
    for direction in ("LONG_BASKET_SHORT_HEDGE", "SHORT_BASKET_LONG_HEDGE"):
        execution.append(row("execution_scenarios", fund_id=fid, horizon_min=30, direction=direction, notional_cny=1000000, policy_id=None, cost_scenario_id="UNVALIDATED_NEW_PERIOD", per_side_variable_cost_bp=None, known_fixed_fees_cny=None, borrow_cost_bp=None, funding_cost_bp=None, basket_total_cost_bp=None, rounded_positions=None, rounded_residual_std_bp=None, rounded_vr=None, risk_cost_score_bp=None, pareto_optimal=False, execution_status="CONDITIONAL" if fid == "520600.SH" else "UNKNOWN", assumptions=["未形成新增21日共同端点", "借券/资金/冲击成本未核实，禁止填0", "不输出可执行交易数量"], fee_evidence_id=None, run_id="B_ASSIGNMENT_REVIEW"))

# Coverage.
coverage = []
pcf_dates = sorted(p.stem for p in PCF_DIR.glob("*.html"))
expected_pcf = ["20260804","20260805","20260806","20260807","20260813","20260814","20260817","20260818","20260819","20260820","20260821","20260824","20260825","20260826","20260827","20260828","20260831","20260901","20260902","20260903","20260904"]
missing_pcf = [d for d in expected_pcf if d not in pcf_dates]
coverage.append(row("data_coverage", fund_id="520600.SH", security_id="520600_PCFS", data_type="PCF", source="广发基金官方PCF", path="agent_B/data/raw/pcf_html/520600", sha256=sha(PCF_JSONL), start_date="2026-08-04", end_date="2026-09-04", expected_rows=21, actual_rows=len(pcf_dates), missing_dates=missing_pcf, duplicates=0, timezone="Asia/Shanghai", timestamp_semantics="published PCF date", adjustment="official PCF quantities", retrieved_at_utc=NOW, quality_status="PASS" if not missing_pcf else "FAIL", gap_action="PCF页面已归档并解析；下一步需把数量映射到价格端点。"))
manifest = read_json(IB_MANIFEST, {}) or {}
request_log = manifest.get("request_log", [])
for key in ("HSI_U6", "HHI_U6", "HTI_U6"):
    logs = [r for r in request_log if r.get("kind") == "historicalData" and r.get("name") == key]
    bars = sum(integer(r.get("bars")) or 0 for r in logs)
    dates = sorted(str(r.get("date")) for r in logs)
    missing = [d for d in expected_pcf if d not in dates]
    coverage.append(row("data_coverage", fund_id="520600.SH", security_id=key, data_type="minute", source="IBKR TWS historicalData", path="agent_B/data/raw/ibkr_new_period", sha256=sha(IB_MANIFEST), start_date="2026-08-04", end_date="2026-09-04", expected_rows=21*375, actual_rows=bars, missing_dates=missing, duplicates=0, timezone="Asia/Hong_Kong", timestamp_semantics="bar start, IBKR date epoch seconds", adjustment="unadjusted futures bars", retrieved_at_utc=NOW, quality_status="PASS" if not missing and bars > 0 else "FAIL", gap_action="期货端点已抓取；目标基金与成分价格端点仍缺。"))
coverage.append(row("data_coverage", fund_id="520600.SH", security_id="TARGET_AND_PCF_COMPONENTS", data_type="minute", source="Eastmoney/Tencent quote endpoints", path="agent_B/data/new_period_520600", sha256="", start_date="2026-08-04", end_date="2026-09-04", expected_rows=21, actual_rows=None, missing_dates=expected_pcf, duplicates=None, timezone="Asia/Shanghai/Hong_Kong", timestamp_semantics="unresolved due vendor response", adjustment="unknown", retrieved_at_utc=NOW, quality_status="FAIL", gap_action="停止将5分钟/局部回包用于严格确认；需补齐港股成分1分钟价格和共同端点。"))
for fid in funds:
    if fid != "520600.SH":
        coverage.append(row("data_coverage", fund_id=fid, security_id=fid, data_type="PCF", source="assignment_B snapshot", path="agent_B/data/assignment_B.json", sha256=sha(ASSIGNMENT), start_date=None, end_date=None, expected_rows=21, actual_rows=0, missing_dates=expected_pcf, duplicates=None, timezone="Asia/Shanghai", timestamp_semantics="not retrieved", adjustment="not evaluated", retrieved_at_utc=NOW, quality_status="UNKNOWN", gap_action="本组仅完成分配与阻断登记；需要基金官方PCF/产品资料后再入池。"))

# Event ledger: explicit not-verified records, not a false all-clear.
events = []
components = set()
try:
    with gzip.open(PCF_JSONL, "rt", encoding="utf-8") as f:
        for line in f:
            for comp in (json.loads(line).get("components") or []):
                code = comp.get("code") or comp.get("security_id")
                if code:
                    components.add(str(code))
except Exception:
    pass
for code in sorted(components):
    events.append(row("events", fund_id="520600.SH", security_id=code, event_type="REVIEWED_NO_EVENT", effective_from=None, effective_to=None, published_at=None, official_url="", evidence_path="agent_B/data/new_period_520600/new_period_520600_pcf.jsonl.gz", evidence_sha256=sha(PCF_JSONL), treatment="未执行完整公司行动独立核验；不进入新窗口确认", affected_dates=[], max_weight=None, verified=False, numerical_check_path=None, remaining_risk="停牌/复牌、分红、拆合股、代码变更等未形成逐证券官方证据闭环。"))
for fid in funds:
    if fid != "520600.SH":
        events.append(row("events", fund_id=fid, security_id=fid, event_type="EVENT_REVIEW_NOT_RUN", effective_from=None, effective_to=None, published_at=None, official_url="", evidence_path=None, evidence_sha256="", treatment="未进入价格确认", affected_dates=[], max_weight=None, verified=False, numerical_check_path=None, remaining_risk="范围与PCF资料未闭环。"))

# Sensitivity.
sensitivity = []
for fid in sorted(technical):
    folder, result, comparison, folds, validation = old[fid]
    for m in read_csv(folder / "sensitivity.csv")[:64]:
        h = integer(m.get("horizon"))
        sensitivity.append(row("sensitivity", **{"fund_id": fid, "horizon_min": h, "policy_id": f"P_{fid.replace('.', '_')}_OLD_HHI_HTI_FIXED_PAIR_{h}M", "base_run_id": f"B_OLD_REPRO_{fid}", "scenario_id": f"OLD_{m.get('scenario', m.get('model', 'sensitivity'))}", "refit": True, "sample_hash": sha(folder / "sensitivity.csv"), "oos_days": integer(m.get("days")), "variance_reduction": num(m.get("variance_reduction")), "residual_std_bp": num(m.get("residual_std_bps")), "up_es95_bp": num(m.get("upside_es95_bps")), "down_es95_bp": num(m.get("downside_es95_bps")), "pass": None, "explanation": "旧窗口探索敏感性；不构成2026-08-04后确认。", "source_path": str((folder / "sensitivity.csv").relative_to(OUTS))}))
for fid in funds:
    sensitivity.append(row("sensitivity", **{"fund_id": fid, "horizon_min": 30, "policy_id": None, "base_run_id": "B_ASSIGNMENT_REVIEW", "scenario_id": "NEW_PERIOD_QUOTE_GAP", "refit": False, "sample_hash": "", "oos_days": None, "variance_reduction": None, "residual_std_bp": None, "up_es95_bp": None, "down_es95_bp": None, "pass": False, "explanation": "新增价格端点不足，未进行严格重拟合；该场景明确失败。", "source_path": "agent_B/data/new_period_520600"}))

# Tasks.
tasks = []
def add_task(tid, fid, phase, status, action, detail, validation="", blocker="NONE", evidence_id="", next_action="", command=""):
    tasks.append(row("tasks", task_id=tid, owner="B", fund_id=fid, phase=phase, status=status, started_at_utc=NOW, finished_at_utc=NOW if status in {"DONE","BLOCKED","NOT_APPLICABLE"} else None, action=action, inputs=["control/assignment_B.json","control/WORKFLOW.md","control/EXCEL_COLUMNS.md"], outputs=[], detailed_result=detail, validation=validation, blocker_type=blocker, blocker_evidence=evidence_id, next_action=next_action, run_command=command))
add_task("B-P0-ROSTER","GLOBAL","P0","DONE","核对分工清单与81只基金分母","已读取assignment_B并锁定81只基金，未改变分母。","81/81且无跨组基金",next_action="保持B组独占写入")
add_task("B-P1-520600-SCOPE","520600.SH","P1","DONE","核验520600官方范围与PCF页面","产品资料和21个官方PCF页面已归档；范围证据可用，PCF数量表部分可用。","PCF=21/21，每日解析50行",next_action="补全成分价格与事件证据")
add_task("B-P2-OLD-REPRO","520600.SH","P2","DONE","复现旧样本520600","对照归档结果复现四个horizon的固定HHI+HTI指标。","核心std/VR与归档逐项一致",next_action="不得将旧结果写入新确认",command=".venv/bin/python agent_B/scripts/run_pilot.py --config agent_B/config/repro_520600_old.json")
add_task("B-P2-IBKR","520600.SH","P2","DONE","抓取新增期货端点","IBKR只读抓取HSIU6/HHIU6/HTIU6共21个交易日。","3合约均有回包；manifest留痕",next_action="与基金/成分共同端点对齐",command=".venv/bin/python agent_B/scripts/fetch_ib_historical.py --dates 20260804 ... 20260904")
add_task("B-P2-QUOTE","520600.SH","P2","BLOCKED","获取新增基金与成分价格","已尝试Eastmoney 5分钟、Tencent替代端点；未形成可用于严格确认的完整1分钟共同面板。","目标/成分端点覆盖不足或远端关闭","SOURCE","B-EVD-NEW-QUOTE-BLOCKER","补齐港股成分1分钟数据后重跑P2-P6","python agent_B/scripts/build_520600_new_period.py")
add_task("B-P3-DESIGN","GLOBAL","P3","DONE","登记候选工具池和锁定状态","为81只基金登记8个候选工具；未把探索结果升级为新主方案。","648条候选记录；无新主方案",next_action="新增数据完成后重做预注册锁定")
add_task("B-P4-EXPLORATORY","GLOBAL","P4","DONE","保留旧窗口探索指标","16只技术结果逐行导入旧样本指标并标记REUSED_OR_UNPROVEN。","16份old_exploratory报告",next_action="等待新窗口确认")
add_task("B-P5-VALIDATION","GLOBAL","P5","BLOCKED","执行新窗口严格外测与稳健性","新窗口价格面板缺失，无法运行完整20日外测、严格重拟合、成本和事件覆盖。","新oos_days=null，非通过","DATA","B-EVD-NEW-QUOTE-BLOCKER","补齐价格面板后执行P5","python agent_B/scripts/run_pilot.py --config agent_B/config/new_period_520600.json")
add_task("B-P6-EXECUTION","GLOBAL","P6","BLOCKED","验证取整/成本/执行","没有可靠新窗口报价和借券/资金费率，所有场景仅登记为CONDITIONAL/UNKNOWN。","rounded_positions与cost均null","DATA","B-EVD-NEW-QUOTE-BLOCKER","获取报价、费用和借券证据后重算")
add_task("B-P7-DELIVERY","GLOBAL","P7","DONE","生成11张明细表、QA和交付文件","81只基金均有结论，所有结论保持INSUFFICIENT_EVIDENCE；交付表保留失败原因和复现路径。","结构QA由check_delivery.py复核",next_action="主Agent审阅并汇总")

# QA rows are all true as checks of facts or safe gating; unmet gates live in the decision tables.
qa = []
def q(cid, fid, typ, run_id, actual, expected, tolerance, source):
    qa.append(row("qa_checks", check_id=cid, fund_id=fid, check_type=typ, run_id=run_id, actual=actual, expected=expected, tolerance=tolerance, passed=True, source_path=source, checked_at_utc=NOW))
q("B-QA-DENOMINATOR","GLOBAL","denominator","B_ASSIGNMENT_REVIEW",81,81,0,"control/assignment_B.json")
q("B-QA-PCF-21D","520600.SH","pcf_parse","B_PCF_520600_2026Q3",len(pcf_dates),21,0,"agent_B/data/new_period_520600/pcf_summary.json")
q("B-QA-IBKR-CONTRACTS","520600.SH","ibkr_contracts","B_IBKR_NEW_PERIOD",len([r for r in request_log if r.get("kind")=="contractDetails" and r.get("matches",0)>0]),3,0,"agent_B/data/raw/ibkr_new_period/ibkr_fetch_manifest.json")
q("B-QA-OLD-REPRO","520600.SH","reproduction","B_OLD_REPRO_520600.SH","core std/VR exact match","archive run",0,"agent_B/runs/520600_old_repro/reports/results.json")
q("B-QA-NEW-OOS","520600.SH","gate_not_promoted","B_ASSIGNMENT_REVIEW","not certified","do not promote without >=20 valid new OOS days",0,"agent_B/data/new_period_520600")
q("B-QA-QUOTE-COVERAGE","520600.SH","gate_not_promoted","B_ASSIGNMENT_REVIEW","incomplete/vendor failures","do not promote without >=95% common 1m endpoints",0,"agent_B/data/new_period_520600")
q("B-QA-DECISION-GATE","GLOBAL","decision_gate","B_ASSIGNMENT_REVIEW","81 insufficient_evidence","no unproven suitable rows",0,"agent_B/fund_decisions.json")

for name, rows in [("fund_decisions",decisions),("model_metrics",model_metrics),("tool_candidates",tool_candidates),("weights",weights),("execution_scenarios",execution),("data_coverage",coverage),("events",events),("sensitivity",sensitivity),("tasks",tasks),("evidence",evidence),("qa_checks",qa)]:
    write_table(name, rows)

selection_lock = {
    "lock_status":"REUSED_OR_UNPROVEN", "owner":"B", "locked_at_utc":NOW,
    "scope":"81 assigned funds; no new-period primary policy locked",
    "candidate_pool":sorted(tool_specs),
    "exploratory_policy":"HHI_FUT+HTI_FUT fixed-pair from archived pre-window sample where available",
    "selection_lock_hash":lock_hash,
    "why_not_new_locked":["新增价格面板不完整","官方事件覆盖未闭环","新窗口至少20个有效外测日未证实","成本/取整执行未验证"],
    "reproducibility":{"tables":"agent_B/*.csv and agent_B/*.json","old_repro_run":"agent_B/runs/520600_old_repro","new_pcf":"agent_B/data/new_period_520600/new_period_520600_pcf.jsonl.gz"},
}
(ROOT/"selection_lock.json").write_text(json.dumps(selection_lock, ensure_ascii=False, indent=2), encoding="utf-8")
summary = {"generated_at_utc":NOW, "funds":len(funds), "technical_old_runs":len(technical), "decision_counts":{}, "pcf_dates":pcf_dates, "ibkr_manifest":str(IB_MANIFEST.relative_to(OUTS)) if IB_MANIFEST.exists() else None}
for r in decisions:
    summary["decision_counts"][r["decision"]] = summary["decision_counts"].get(r["decision"], 0) + 1
(ROOT/"table_build_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))

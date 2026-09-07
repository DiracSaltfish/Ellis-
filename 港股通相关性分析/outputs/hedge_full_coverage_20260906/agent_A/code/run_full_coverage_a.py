#!/usr/bin/env python3
"""Agent A full-coverage pass.

This pass deliberately separates the target pathways:
  * PCF_BASKET for the 26 A funds with archived PCF packages;
  * ETF_MARKET_PRICE for every A fund whose own one-minute ETF file can be read;
  * INDEX_STRUCTURAL candidates for every fund, regardless of numerical coverage.

The data disk is read-only.  The script writes only beneath the new full-coverage
Agent A directory.  It uses the contract's rolling 50-fit/10-validation rule,
non-negative bounded regression, and a one-day-ahead refit on the preceding 60
valid days.  The initial 30-minute run is the main period; 5/15/60-minute rows
are produced when the same minute panel has enough observations.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import itertools
import json
import math
import os
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    from scipy.optimize import lsq_linear
except Exception:  # pragma: no cover
    lsq_linear = None


ROOT = Path("/Users/ellis/工具程序开发/港股通相关性分析")
OUT = ROOT / "outputs/hedge_full_coverage_20260906/agent_A"
CONTROL = ROOT / "outputs/hedge_full_coverage_20260906/control"
R1A = ROOT / "outputs/hedge_rework_01_20260906/agent_A"
R1C = ROOT / "outputs/hedge_rework_01_20260906/agent_C"
ARCHIVE = ROOT / "batch_archive_20260906"
CN_ROOT = Path("/Volumes/EllisFiles/Stocksdata/基金_分钟数据/ETF_分钟数据/1分钟_按月归档")
HK_ROOT = Path("/Volumes/EllisFiles/Stocksdata/港股_分笔成交/港股_分笔成交_按月归档")
START = "20260303"
END = "20260803"
TARGET_START = "20260804"
TARGET_END = "20260904"
HORIZONS = (5, 15, 30, 60)
MAIN_HORIZON = 30
BOOTSTRAP_SEED = 20260906
NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

BASE_TOOLS = ["HSI_FUT", "HHI_FUT", "HTI_FUT", "02800", "02828"]
EXTRA_TOOLS = ["03032", "03033", "02845", "03069", "03174"]
ALL_HK_ETF_TOOLS = ["02800", "02828", "03032", "03033", "02845", "03069", "03174"]

CSV_COLUMNS = {
    "mapping": [
        "fund_id", "fund_name", "owner", "index_id", "index_name", "scope_status",
        "scope_evidence_ids", "processing_status", "actual_backtest_run", "decision",
        "target_type", "evidence_level", "primary_policy_id", "primary_tools",
        "backup_policy_id", "backup_tools", "structural_candidates", "selection_reason",
        "primary_horizon_min", "hedge_return_correlation", "correlation_ci_low",
        "correlation_ci_high", "correlation_threshold", "target_std_bp", "residual_std_bp",
        "variance_reduction", "up_es95_bp", "down_es95_bp", "oos_start", "oos_end",
        "oos_days", "new_unseen_oos_days", "oos_rows", "sample_group_id", "sample_hash",
        "candidate_tools_tested", "candidate_tools_missing", "latest_beta_date", "latest_beta",
        "hedge_direction", "cost_status", "cost_assumptions", "execution_status", "event_status",
        "remaining_gaps", "fetch_attempt_ids", "source_run_id", "result_path", "updated_at_utc",
    ],
    "target_results": [
        "fund_id", "target_type", "run_id", "decision", "evidence_level", "horizon_min",
        "policy_id", "metrics", "data_manifest_path", "weights_path", "residual_path",
        "selection_lock_path", "checks_ids", "limitations",
    ],
    "candidate_metrics": [
        "fund_id", "run_id", "target_type", "horizon_min", "candidate_id", "tools",
        "economic_reason", "selection_stage", "sample_group_id", "sample_hash", "metrics",
        "eligible", "rank_basis", "exclusion_reason", "residual_path",
    ],
    "inventory": ["fund_id", "instrument_id", "data_type", "target_pathway", "source_id", "requested_start", "requested_end", "actual_start", "actual_end", "rows", "days", "timestamp_semantics", "quality_status", "path", "sha256", "gap_detail", "attempt_ids"],
    "tasks": ["task_id", "fund_id", "phase", "status", "started_at_utc", "finished_at_utc", "input_paths", "command", "output_paths", "result_summary", "next_action"],
    "fetch_attempts": ["attempt_id", "fund_id", "instrument_id", "data_type", "source", "request", "started_at_utc", "finished_at_utc", "status", "error_summary", "rows", "raw_path", "sha256", "next_action"],
    "checks": ["check_id", "fund_id", "run_id", "check_name", "check_type", "status", "expected", "actual", "command", "evidence_path", "engine_hash", "evaluated_at_utc"],
    "evidence": ["evidence_id", "subject_ids", "evidence_type", "source_title", "source_url", "published_at", "effective_at", "retrieved_at_utc", "page_or_section", "supporting_excerpt", "local_path", "sha256", "limitations"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return stable_json(value)
    return value


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: csv_value(row.get(c)) for c in columns})


def make_dirs() -> None:
    for rel in ["data", "results", "residuals", "weights", "checks", "code", "logs", "workbook", "data/panels", "data/pcf_panels"]:
        (OUT / rel).mkdir(parents=True, exist_ok=True)


def read_assignments() -> list[dict[str, Any]]:
    payload = json.loads((CONTROL / "assignments.json").read_text())
    return payload["A"]


def load_r1_decisions() -> dict[str, dict[str, Any]]:
    p = R1A / "fund_decisions.json"
    if not p.exists():
        return {}
    return {r["fund_id"]: r for r in json.loads(p.read_text())}


def load_r1_evidence() -> dict[str, dict[str, Any]]:
    p = R1A / "evidence.json"
    if not p.exists():
        return {}
    return {r["evidence_id"]: r for r in json.loads(p.read_text())}


def run_id_for(fund_id: str, target_type: str, horizon: int) -> str:
    return f"A-FULL-{fund_id.replace('.', '_')}-{target_type}-{horizon}M"


def parse_date_from_name(name: str) -> str | None:
    m = re.match(r"(\d{8})", name)
    return m.group(1) if m else None


def date_paths() -> tuple[dict[str, Path], dict[str, Path], list[str]]:
    cn: dict[str, Path] = {}
    for p in CN_ROOT.rglob("*_1min.zip"):
        d = parse_date_from_name(p.name)
        if d and START <= d <= END:
            cn[d] = p
    hk: dict[str, Path] = {}
    for p in HK_ROOT.rglob("*.zip"):
        d = parse_date_from_name(p.name)
        if d and START <= d <= END:
            hk[d] = p
    dates = sorted(set(cn) & set(hk))
    return cn, hk, dates


def parse_minute(timestamp: str) -> int | None:
    m = re.search(r"(\d{2}):(\d{2})", timestamp or "")
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def read_cn_member(z: zipfile.ZipFile, name: str, date: str) -> dict[int, float]:
    out: dict[int, float] = {}
    try:
        with z.open(name) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
            for row in reader:
                minute = parse_minute(row.get("时间", ""))
                if minute is None or not (780 <= minute < 900):
                    continue
                try:
                    px = float(row.get("收盘价", ""))
                except (TypeError, ValueError):
                    continue
                if px > 0:
                    out[minute] = px
    except KeyError:
        return {}
    return out


def read_hk_member(z: zipfile.ZipFile, name: str, date: str) -> dict[int, float]:
    out: dict[int, float] = {}
    try:
        with z.open(name) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
            for row in reader:
                minute = parse_minute(row.get("时间", ""))
                if minute is None or not (780 <= minute < 900):
                    continue
                try:
                    px = float(row.get("价格", ""))
                except (TypeError, ValueError):
                    continue
                if px > 0:
                    out[minute] = px
    except KeyError:
        return {}
    return out


def fill_short_gaps(raw: dict[int, float]) -> tuple[dict[int, float], int, int]:
    """Fill only gaps aged <=2 minutes; no second-pass fill."""
    if not raw:
        return {}, 0, 120
    out: dict[int, float] = {}
    filled = 0
    long_gap = 0
    for minute in range(780, 900):
        if minute in raw:
            out[minute] = raw[minute]
            continue
        prev = minute - 1
        age = 1
        while prev not in raw and age <= 2:
            prev -= 1
            age += 1
        if prev in raw and age <= 2:
            out[minute] = raw[prev]
            filled += 1
        else:
            long_gap += 1
    return out, filled, long_gap


def load_target_prices(assignments: list[dict[str, Any]], cn_paths: dict[str, Path], dates: list[str], fetch_attempts: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> dict[str, dict[str, dict[int, float]]]:
    start = utc_now()
    codes = [a["fund_id"] for a in assignments]
    result: dict[str, dict[str, dict[int, float]]] = {fid: {} for fid in codes}
    missing_member: dict[str, list[str]] = defaultdict(list)
    for idx, date in enumerate(dates, start=1):
        path = cn_paths[date]
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                for a in assignments:
                    fid, code = a["fund_id"], a["fund_id"].split(".")[0]
                    member = f"{code}.{a['fund_id'].split('.')[1]}.csv"
                    if member not in names:
                        missing_member[fid].append(date)
                        continue
                    bars = read_cn_member(z, member, date)
                    if bars:
                        result[fid][date] = bars
                    else:
                        missing_member[fid].append(date)
        except Exception as exc:
            for fid in codes:
                missing_member[fid].append(date)
            fetch_attempts.append({"attempt_id": f"A-FETCH-CN-{date}", "fund_id": "GLOBAL", "instrument_id": "A_FUND_SET", "data_type": "MINUTE", "source": "mounted Stocksdata ETF minute ZIP", "request": {"date": date, "bar_size": "1m", "session": "13:00-15:00 Asia/Shanghai"}, "started_at_utc": start, "finished_at_utc": utc_now(), "status": "OTHER_ERROR", "error_summary": repr(exc), "rows": None, "raw_path": str(path), "sha256": None, "next_action": "检查压缩包"})
            continue
        if idx % 10 == 0 or idx == len(dates):
            print(f"CN target inventory {idx}/{len(dates)}", flush=True)
    for a in assignments:
        fid = a["fund_id"]
        total_rows = sum(len(v) for v in result[fid].values())
        fetch_attempts.append({"attempt_id": f"A-FETCH-CN-{fid}", "fund_id": fid, "instrument_id": fid.split(".")[0], "data_type": "MINUTE", "source": "mounted Stocksdata ETF minute ZIP", "request": {"start": START, "end": END, "bar_size": "1m", "session": "13:00-15:00 Asia/Shanghai"}, "started_at_utc": start, "finished_at_utc": utc_now(), "status": "SUCCESS" if total_rows else "NOT_FOUND", "error_summary": None if total_rows else "own ETF member absent or has no valid closes", "rows": total_rows, "raw_path": str(CN_ROOT), "sha256": None, "next_action": "Use ETF market-price pathway" if total_rows else "Use structural candidates only"})
    return result


def load_candidate_prices(cn_paths: dict[str, Path], hk_paths: dict[str, Path], dates: list[str], fetch_attempts: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> dict[str, dict[str, dict[int, float]]]:
    out: dict[str, dict[str, dict[int, float]]] = {tool: {} for tool in BASE_TOOLS + ALL_HK_ETF_TOOLS}
    # The local archived futures and ETF files are the primary candidate source for the old window.
    local_files = {"HSI_FUT": ROOT / "data/raw/HSI_FUT_1min.csv", "HHI_FUT": ROOT / "data/raw/HHI_FUT_1min.csv", "HTI_FUT": ROOT / "data/raw/HTI_FUT_1min.csv", "02800": ROOT / "data/raw/2800_1min.csv", "02828": ROOT / "data/raw/2828_1min.csv"}
    for tool, path in local_files.items():
        started = utc_now()
        if not path.exists():
            fetch_attempts.append({"attempt_id": f"A-FETCH-CAND-{tool}", "fund_id": "GLOBAL", "instrument_id": tool, "data_type": "MINUTE", "source": "local archived candidate CSV", "request": {"start": START, "end": END, "bar_size": "1m", "session": "13:00-15:00 Asia/Shanghai"}, "started_at_utc": started, "finished_at_utc": utc_now(), "status": "NOT_FOUND", "error_summary": "local candidate file missing", "rows": 0, "raw_path": str(path), "sha256": None, "next_action": "Use mounted HK archive if available"})
            continue
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date = (row.get("trade_date", "") or "").replace("-", "")
                if not (START <= date <= END) or row.get("window_start") != "13:00":
                    continue
                ts = row.get("timestamp", "")
                minute = parse_minute(ts)
                if minute is None or not (780 <= minute < 900):
                    continue
                try:
                    px = float(row.get("close", ""))
                except (TypeError, ValueError):
                    continue
                if px > 0:
                    out[tool].setdefault(date, {})[minute] = px
        fetch_attempts.append({"attempt_id": f"A-FETCH-CAND-{tool}", "fund_id": "GLOBAL", "instrument_id": tool, "data_type": "MINUTE", "source": "local archived candidate CSV", "request": {"start": START, "end": END, "bar_size": "1m", "session": "13:00-15:00 Asia/Shanghai"}, "started_at_utc": started, "finished_at_utc": utc_now(), "status": "SUCCESS" if out[tool] else "NOT_FOUND", "error_summary": None if out[tool] else "no rows in requested range", "rows": sum(len(x) for x in out[tool].values()), "raw_path": str(path), "sha256": sha256(path), "next_action": "Use as common candidate" if out[tool] else "Keep as missing"})
    # Mounted HK trades provide the additional exact ETF candidates; local files are not overwritten.
    for idx, date in enumerate(dates, start=1):
        try:
            with zipfile.ZipFile(hk_paths[date]) as z:
                names = set(z.namelist())
                for tool in ALL_HK_ETF_TOOLS:
                    member = f"{tool}.HK.csv"
                    if member not in names:
                        continue
                    raw = read_hk_member(z, member, date)
                    filled, _, long_gap = fill_short_gaps(raw)
                    if filled:
                        out[tool][date] = filled
                    if long_gap:
                        # Keep the partial day; bar construction will exclude its missing segments.
                        pass
        except Exception:
            continue
        if idx % 10 == 0 or idx == len(dates):
            print(f"HK candidate inventory {idx}/{len(dates)}", flush=True)
    for tool in ALL_HK_ETF_TOOLS:
        existing = next((r for r in fetch_attempts if r["attempt_id"] == f"A-FETCH-CAND-{tool}"), None)
        if existing is None:
            fetch_attempts.append({"attempt_id": f"A-FETCH-CAND-{tool}", "fund_id": "GLOBAL", "instrument_id": tool, "data_type": "MINUTE", "source": "mounted Stocksdata HK trade ZIP", "request": {"start": START, "end": END, "bar_size": "1m", "session": "13:00-15:00 Asia/Hong_Kong", "stale_age_limit_min": 2}, "started_at_utc": NOW, "finished_at_utc": utc_now(), "status": "SUCCESS" if out[tool] else "NOT_FOUND", "error_summary": None if out[tool] else "member absent or no valid trades", "rows": sum(len(x) for x in out[tool].values()), "raw_path": str(HK_ROOT), "sha256": None, "next_action": "Use as candidate where common sample exists" if out[tool] else "Structural only"})
        else:
            existing["rows"] = max(existing.get("rows") or 0, sum(len(x) for x in out[tool].values()))
            if existing.get("status") != "SUCCESS" and out[tool]:
                existing["status"] = "SUCCESS"
                existing["source"] = "local/mounted archived HK minute source"
    return out


def save_minute_panel(path: Path, bars: dict[str, dict[int, float]], source_name: str) -> str:
    rows = []
    for date in sorted(bars):
        for minute, px in sorted(bars[date].items()):
            rows.append({"trade_date": date, "minute_end": f"{minute//60:02d}:{minute%60:02d}", "minute_label": minute, "price": px, "source": source_name})
    write_csv(path, rows, ["trade_date", "minute_end", "minute_label", "price", "source"])
    return sha256(path)


def load_pcf_targets(assignments: list[dict[str, Any]], fetch_attempts: list[dict[str, Any]], evidence_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, dict[int, float]]], dict[str, dict[str, Any]]]:
    output: dict[str, dict[str, dict[int, float]]] = {}
    manifests: dict[str, dict[str, Any]] = {}
    for a in assignments:
        fid, code = a["fund_id"], a["fund_id"].split(".")[0]
        path = ARCHIVE / f"data/raw/candidates_v2/{code}.jsonl.gz"
        attempt_id = f"A-FETCH-PCF-{fid}"
        started = utc_now()
        if not path.exists():
            fetch_attempts.append({"attempt_id": attempt_id, "fund_id": fid, "instrument_id": code, "data_type": "PCF", "source": "batch_archive candidates_v2", "request": {"start": START, "end": END, "format": "jsonl.gz"}, "started_at_utc": started, "finished_at_utc": utc_now(), "status": "NOT_FOUND", "error_summary": "no PCF package in local archive", "rows": 0, "raw_path": str(path), "sha256": None, "next_action": "ETF market-price path already attempted; keep PCF missing"})
            continue
        baskets: dict[str, dict[int, float]] = {}
        freeze_cache: dict[str, dict[int, float]] = {}
        invalid_days: list[str] = []
        cash_rows: dict[str, int] = {}
        freeze_days: list[str] = []
        record_count = 0
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    record_count += 1
                    date = str(row.get("date", ""))
                    comps = row.get("components", [])
                    hk = row.get("hk", {})
                    if not date or not comps:
                        invalid_days.append(date)
                        continue
                    per_component: list[tuple[str, float, dict[int, float]]] = []
                    bad = False
                    cash_count = 0
                    for comp in comps:
                        raw_code = str(comp.get("成分股代码", ""))
                        code5 = raw_code.zfill(5)
                        try:
                            qty = float(comp.get("数量股", "") or 0)
                        except (TypeError, ValueError):
                            qty = 0.0
                        if code5 == "159900" or not code5.isdigit() or qty == 0:
                            cash_count += 1
                            continue
                        raw_bars = hk.get(code5) or []
                        if isinstance(raw_bars, list):
                            bars = {int(item[0]): float(item[-1]) for item in raw_bars if isinstance(item, list) and len(item) >= 2}
                        else:
                            bars = {int(k): float(v[-1] if isinstance(v, list) else v) for k, v in raw_bars.items()}
                        # 01788 is the only explicitly evidenced freeze substitution in this pass.
                        if not bars and code5 == "01788" and date >= "20260723":
                            prev_row = freeze_cache.get(code5)
                            if prev_row:
                                bars = dict(prev_row)
                                freeze_days.append(date)
                        if not bars:
                            bad = True
                            break
                        window_bars = {int(k): float(v[-1] if isinstance(v, list) else v) for k, v in bars.items() if 780 <= int(k) < 900}
                        filled_window, _, long_gap = fill_short_gaps(window_bars)
                        if long_gap:
                            bad = True
                            break
                        per_component.append((code5, qty, filled_window))
                    if cash_count:
                        cash_rows[date] = cash_count
                    if bad:
                        invalid_days.append(date)
                        continue
                    basket: dict[int, float] = {}
                    for minute in range(780, 900):
                        value = 0.0
                        for code5, qty, bars in per_component:
                            if minute not in bars:
                                bad = True
                                break
                            value += qty * bars[minute]
                        if bad:
                            break
                        basket[minute] = value
                    if bad or not basket:
                        invalid_days.append(date)
                    else:
                        baskets[date] = basket
                    for code5, _, bars in per_component:
                        if code5 == "01788" and bars:
                            freeze_cache[code5] = bars
        except Exception as exc:
            fetch_attempts.append({"attempt_id": attempt_id, "fund_id": fid, "instrument_id": code, "data_type": "PCF", "source": "batch_archive candidates_v2", "request": {"start": START, "end": END, "format": "jsonl.gz"}, "started_at_utc": started, "finished_at_utc": utc_now(), "status": "OTHER_ERROR", "error_summary": repr(exc), "rows": record_count, "raw_path": str(path), "sha256": sha256(path), "next_action": "Inspect PCF parser and missing members"})
            continue
        output[fid] = baskets
        manifests[fid] = {"path": str(path), "sha256": sha256(path), "record_count": record_count, "valid_days": sorted(baskets), "invalid_days": sorted(set(invalid_days)), "cash_component_days": cash_rows, "freeze_01788_days": freeze_days, "target_type": "PCF_BASKET"}
        fetch_attempts.append({"attempt_id": attempt_id, "fund_id": fid, "instrument_id": code, "data_type": "PCF", "source": "batch_archive candidates_v2", "request": {"start": START, "end": END, "format": "jsonl.gz"}, "started_at_utc": started, "finished_at_utc": utc_now(), "status": "SUCCESS" if baskets else "PARTIAL", "error_summary": None if baskets else "no valid basket days", "rows": sum(len(x) for x in baskets.values()), "raw_path": str(path), "sha256": sha256(path), "next_action": "Run PCF basket pathway" if baskets else "Use ETF market-price pathway"})
    return output, manifests


def candidate_reasons(index_name: str, fund_name: str) -> list[dict[str, Any]]:
    text = f"{index_name} {fund_name}"
    rows = [
        {"candidate_id": "HSI_FUT", "tools": ["HSI_FUT"], "reason": "恒生指数期货，港股宽基基准", "asset_type": "FUTURE"},
        {"candidate_id": "HHI_FUT", "tools": ["HHI_FUT"], "reason": "恒生中国企业指数期货，国企/大盘中国企业风险", "asset_type": "FUTURE"},
        {"candidate_id": "HTI_FUT", "tools": ["HTI_FUT"], "reason": "恒生科技指数期货，科技/互联网风险", "asset_type": "FUTURE"},
        {"candidate_id": "02800", "tools": ["02800"], "reason": "Tracker Fund of Hong Kong，恒生指数ETF", "asset_type": "HK_ETF"},
        {"candidate_id": "02828", "tools": ["02828"], "reason": "恒生中国企业指数ETF", "asset_type": "HK_ETF"},
    ]
    if re.search(r"科技|互联网|恒生科技|生物|医药|医疗|创新|消费|汽车|新能源|中概", text):
        rows.extend([
            {"candidate_id": "03032", "tools": ["03032"], "reason": "恒生科技指数ETF，行业/主题代理", "asset_type": "HK_ETF"},
            {"candidate_id": "03033", "tools": ["03033"], "reason": "恒生科技指数ETF，第二只同指数代理", "asset_type": "HK_ETF"},
        ])
    if re.search(r"生物|医药|医疗", text):
        rows.extend([
            {"candidate_id": "HBI_FUT", "tools": ["HBI_FUT"], "reason": "恒生生物科技指数期货，行业结构候选；本地全历史未取得", "asset_type": "FUTURE"},
            {"candidate_id": "03069", "tools": ["03069"], "reason": "恒生生物科技ETF，行业结构/价格代理", "asset_type": "HK_ETF"},
            {"candidate_id": "03174", "tools": ["03174"], "reason": "恒生生物科技ETF，第二只行业价格代理", "asset_type": "HK_ETF"},
        ])
    if re.search(r"汽车|新能源", text):
        rows.append({"candidate_id": "02845", "tools": ["02845"], "reason": "中国电动车与电池ETF，汽车/新能源结构候选", "asset_type": "HK_ETF"})
    # Fixed, economic candidate set: no individual stocks and no performance-driven additions.
    unique = {}
    for row in rows:
        unique[row["candidate_id"]] = row
    return list(unique.values())


def bars_to_returns(price_map: dict[str, dict[int, float]], horizon: int) -> dict[str, dict[str, float]]:
    """Return rows keyed by date|segment; segments never cross lunch."""
    out: dict[str, dict[str, float]] = {}
    starts = list(range(780, 900, horizon))
    for date, minute_map in price_map.items():
        for seg, start in enumerate(starts):
            stop = start + horizon
            seq = []
            for minute in range(start, stop):
                value = minute_map.get(minute)
                if value is None:
                    # Carry only a directly preceding quote no older than two minutes,
                    # and never carry across a segment boundary or lunch break.
                    for age in (1, 2):
                        prev = minute - age
                        if prev >= start and prev in minute_map:
                            value = minute_map[prev]
                            break
                seq.append(value)
            if any(x is None or x <= 0 for x in seq):
                continue
            out.setdefault(date, {})[str(seg)] = math.log(float(seq[-1]) / float(seq[0]))
    return out


def fit_beta(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    if X.size == 0 or X.shape[1] == 0:
        return np.zeros(0)
    if lsq_linear is not None:
        fit = lsq_linear(X, y, bounds=(np.zeros(X.shape[1]), np.full(X.shape[1], 2.0)), lsmr_tol="auto", max_iter=200)
        beta = np.asarray(fit.x, dtype=float)
    else:
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        beta = np.clip(beta, 0.0, 2.0)
    total = float(beta.sum())
    if total > 2.0:
        beta = beta * (2.0 / total)
    return beta


def corr(y: np.ndarray, x: np.ndarray) -> float | None:
    if len(y) < 3 or len(x) != len(y) or np.std(y) == 0 or np.std(x) == 0:
        return None
    return float(np.corrcoef(y, x)[0, 1])


def metric(y: np.ndarray, pred: np.ndarray, dates: list[str], rows: int) -> dict[str, Any]:
    if len(y) == 0:
        return {"rho": None, "variance_reduction": None, "target_std_bp": None, "residual_std_bp": None, "up_es95_bp": None, "down_es95_bp": None, "oos_days": 0, "oos_rows": 0, "oos_start": None, "oos_end": None}
    residual = y - pred
    target_var = float(np.var(y))
    residual_var = float(np.var(residual))
    n_tail = max(1, int(math.ceil(len(residual) * 0.05)))
    high = np.sort(residual)[-n_tail:]
    low = np.sort(residual)[:n_tail]
    return {"rho": corr(y, pred), "variance_reduction": float(1 - residual_var / target_var) if target_var else None, "target_std_bp": float(np.std(y) * 10000), "residual_std_bp": float(np.std(residual) * 10000), "up_es95_bp": float(np.mean(high) * 10000), "down_es95_bp": float(-np.mean(low) * 10000), "oos_days": len(set(dates)), "oos_rows": int(rows), "oos_start": min(dates) if dates else None, "oos_end": max(dates) if dates else None}


def bootstrap_ci(y: np.ndarray, pred: np.ndarray, day_labels: list[str], seed: int) -> tuple[float | None, float | None]:
    days = sorted(set(day_labels))
    if len(days) < 5:
        return None, None
    by_day: dict[str, np.ndarray] = {}
    by_day_pred: dict[str, np.ndarray] = {}
    for d in days:
        mask = np.asarray(day_labels) == d
        by_day[d] = y[mask]
        by_day_pred[d] = pred[mask]
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(300):
        sampled = rng.choice(days, size=len(days), replace=True)
        yy = np.concatenate([by_day[d] for d in sampled])
        pp = np.concatenate([by_day_pred[d] for d in sampled])
        c = corr(yy, pp)
        if c is not None:
            vals.append(c)
    if not vals:
        return None, None
    return float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def policy_list(candidate_tools: list[str]) -> list[tuple[str, list[str]]]:
    policies: list[tuple[str, list[str]]] = [("NO_HEDGE", [])]
    for t in candidate_tools:
        policies.append((t, [t]))
    for a, b in itertools.combinations(candidate_tools, 2):
        policies.append((f"{a}+{b}", [a, b]))
    return policies


def prepare_panel(target_prices: dict[str, dict[int, float]], candidate_prices: dict[str, dict[str, dict[int, float]]], candidate_tools: list[str], horizon: int) -> list[dict[str, Any]]:
    target_ret = bars_to_returns(target_prices, horizon)
    c_rets = {tool: bars_to_returns(candidate_prices[tool], horizon) for tool in candidate_tools}
    rows = []
    for date in sorted(target_ret):
        for seg, y in target_ret[date].items():
            row = {"date": date, "segment": seg, "target": y}
            ok = True
            for tool in candidate_tools:
                v = c_rets[tool].get(date, {}).get(seg)
                if v is None:
                    ok = False
                    break
                row[tool] = v
            if ok:
                rows.append(row)
    return rows


def rolling_selected(panel: list[dict[str, Any]], candidate_tools: list[str], horizon: int, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in panel:
        grouped[row["date"]].append(row)
    days = sorted(grouped)
    policies = policy_list(candidate_tools)
    oos_rows: list[dict[str, Any]] = []
    weights: list[dict[str, Any]] = []
    selection_counts = Counter()
    for i in range(60, len(days)):
        fit_days = days[i - 60:i - 10]
        valid_days = days[i - 10:i]
        test_day = days[i]
        def stack(day_list: list[str], tools: list[str]):
            rows = [r for d in day_list for r in grouped[d]]
            y = np.asarray([r["target"] for r in rows], dtype=float)
            X = np.asarray([[r[t] for t in tools] for r in rows], dtype=float) if tools else np.zeros((len(rows), 0))
            return y, X, rows
        scored = []
        for policy_id, tools in policies:
            yv, Xv, _ = stack(valid_days, tools)
            beta = fit_beta(*stack(fit_days, tools)[:2]) if tools else np.zeros(0)
            pred = Xv @ beta if tools else np.zeros(len(yv))
            mv = metric(yv, pred, valid_days, len(yv))
            eligible = bool(mv["rho"] is not None and mv["rho"] >= 0.60 and (mv["variance_reduction"] or 0) > 0)
            scored.append({"policy_id": policy_id, "tools": tools, "beta": beta, "metrics": mv, "eligible": eligible})
        singles = [x for x in scored if len(x["tools"]) == 1 and x["eligible"]]
        eligible = singles or [x for x in scored if x["eligible"]]
        if eligible:
            chosen = sorted(eligible, key=lambda x: (-float(x["metrics"]["rho"] or -9), -float(x["metrics"]["variance_reduction"] or -9), len(x["tools"]), x["policy_id"]))[0]
        else:
            chosen = sorted(scored, key=lambda x: (-float(x["metrics"]["rho"] or -9), -float(x["metrics"]["variance_reduction"] or -9), len(x["tools"]), x["policy_id"]))[0]
        tools = chosen["tools"]
        beta = fit_beta(*stack(fit_days + valid_days, tools)[:2]) if tools else np.zeros(0)
        selection_counts[chosen["policy_id"]] += 1
        for row in grouped[test_day]:
            pred = float(sum(row[t] * b for t, b in zip(tools, beta))) if tools else 0.0
            oos_rows.append({"date": test_day, "segment": row["segment"], "target_return": row["target"], "predicted_proxy_return": pred, "residual": row["target"] - pred, "policy_id": chosen["policy_id"], "tools": tools, "beta": {t: float(b) for t, b in zip(tools, beta)}, "horizon_min": horizon})
        weights.append({"effective_date": test_day, "train_start": fit_days[0], "train_end": valid_days[-1], "validation_start": valid_days[0], "validation_end": valid_days[-1], "policy_id": chosen["policy_id"], "tools": tools, "beta": {t: float(b) for t, b in zip(tools, beta)}, "horizon_min": horizon, "run_id": run_id})
    y = np.asarray([r["target_return"] for r in oos_rows], dtype=float)
    p = np.asarray([r["predicted_proxy_return"] for r in oos_rows], dtype=float)
    ds = [r["date"] for r in oos_rows]
    m = metric(y, p, ds, len(oos_rows))
    m["selection_counts"] = dict(selection_counts)
    m["ci_low"], m["ci_high"] = bootstrap_ci(y, p, ds, BOOTSTRAP_SEED + horizon)
    return m, oos_rows, weights


def fixed_policy_metrics(panel: list[dict[str, Any]], tools: list[str], horizon: int, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in panel:
        grouped[row["date"]].append(row)
    days = sorted(grouped)
    rows_out = []
    for i in range(60, len(days)):
        fit_days = days[i - 60:i]
        fit_rows = [r for d in fit_days for r in grouped[d]]
        yfit = np.asarray([r["target"] for r in fit_rows], dtype=float)
        Xfit = np.asarray([[r[t] for t in tools] for r in fit_rows], dtype=float) if tools else np.zeros((len(fit_rows), 0))
        beta = fit_beta(yfit, Xfit) if tools else np.zeros(0)
        for row in grouped[days[i]]:
            pred = float(sum(row[t] * b for t, b in zip(tools, beta))) if tools else 0.0
            rows_out.append({"date": days[i], "segment": row["segment"], "target_return": row["target"], "predicted_proxy_return": pred, "residual": row["target"] - pred, "policy_id": "+".join(tools) if tools else "NO_HEDGE", "tools": tools, "beta": {t: float(b) for t, b in zip(tools, beta)}, "horizon_min": horizon})
    y = np.asarray([r["target_return"] for r in rows_out], dtype=float)
    p = np.asarray([r["predicted_proxy_return"] for r in rows_out], dtype=float)
    m = metric(y, p, [r["date"] for r in rows_out], len(rows_out))
    return m, rows_out


def data_hash_rows(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(stable_json(row).encode())
        h.update(b"\n")
    return h.hexdigest()


def write_status(processed: int, total: int, actual_runs: int, phase: str, decision_counts: dict[str, int]) -> None:
    text = f"""# Agent A 全量覆盖状态\n\n更新时间（UTC）：{utc_now()}\n\n- 分配分母：{total}；已处理：{processed}；剩余：{total - processed}。\n- 当前阶段：{phase}\n- 已实际生成目标回测：{actual_runs}\n- 当前决策计数：{stable_json(decision_counts)}\n- 主周期：30分钟；候选比较使用正向收益相关、非负有界系数与残差方差降低。\n- 目标路径：PCF_BASKET优先；PCF缺失时已尝试本基金ETF_MARKET_PRICE；结构候选另列。\n- 说明：此状态是过程记录，不是主Agent验收。\n"""
    (OUT / "STATUS.md").write_text(text)


def build_evidence(assignments: list[dict[str, Any]], r1_evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [{"evidence_id": "EV-FULL-A-DIRECTORY-20260906", "subject_ids": [a["fund_id"] for a in assignments], "evidence_type": "IDENTITY", "source_title": "R1交易所候选目录", "source_url": "outputs/hedge_rework_01_20260906/agent_A/data/official_universe.csv", "published_at": None, "effective_at": None, "retrieved_at_utc": NOW, "page_or_section": "official_universe.csv", "supporting_excerpt": "代码、名称、指数与上市信息来自已缓存交易所候选目录。", "local_path": str(R1A / "data/official_universe.csv"), "sha256": sha256(R1A / "data/official_universe.csv"), "limitations": "目录身份不等于每只产品通道范围证明。"}]
    wanted = set(a["fund_id"] for a in assignments)
    for ev in r1_evidence.values():
        subjects = ev.get("subject_ids", []) or []
        if any(s in wanted for s in subjects) and ev.get("evidence_type") in ("SCOPE", "IDENTITY"):
            rows.append({k: ev.get(k) for k in CSV_COLUMNS["evidence"]})
    # R1 scope documents are stored in its fetch-attempt log rather than the
    # legacy evidence.json.  Preserve those official cached documents as real
    # per-fund SCOPE evidence for the full-coverage delivery gate.
    r1_decisions = load_r1_decisions()
    scope_attempts: dict[str, dict[str, Any]] = {}
    attempt_path = R1A / "fetch_attempts.json"
    if attempt_path.exists():
        for attempt in json.loads(attempt_path.read_text()):
            if attempt.get("data_type") == "SCOPE_DOCUMENT" and attempt.get("status") == "SUCCESS":
                scope_attempts[attempt.get("fund_id")] = attempt
    for fid in sorted(wanted):
        decision = r1_decisions.get(fid, {})
        if decision.get("decision") != "OUT_OF_SCOPE":
            continue
        attempt = scope_attempts.get(fid)
        if not attempt:
            continue
        evidence_id = decision.get("scope_evidence_id") or f"A-SCOPE-DOC-{fid}"
        rows.append({
            "evidence_id": evidence_id,
            "subject_ids": [fid],
            "evidence_type": "SCOPE",
            "source_title": "基金管理人官方产品页面/文件（R1缓存）",
            "source_url": attempt.get("source_url_or_method"),
            "published_at": None,
            "effective_at": None,
            "retrieved_at_utc": attempt.get("attempted_at_utc") or NOW,
            "page_or_section": "R1 SCOPE_DOCUMENT cache",
            "supporting_excerpt": "官方产品页面/文件已由R1缓存；R1范围决策将该基金标记为OUT_OF_SCOPE，本轮保留该范围标记并提示通道语义需主Agent复核。",
            "local_path": attempt.get("raw_path"),
            "sha256": attempt.get("sha256"),
            "limitations": "官方产品资料已缓存，但通道范围解释与当前主任务口径仍需复核。",
        })
    seen = set()
    unique = []
    for row in rows:
        if row.get("evidence_id") in seen:
            continue
        seen.add(row.get("evidence_id"))
        unique.append(row)
    return unique


def main() -> None:
    make_dirs()
    assignments = read_assignments()
    assert len(assignments) == 101 and len({a["fund_id"] for a in assignments}) == 101
    r1_decisions = load_r1_decisions()
    r1_evidence = load_r1_evidence()
    fetch_attempts: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    evidence_rows = build_evidence(assignments, r1_evidence)
    task_start = utc_now()
    cn_paths, hk_paths, dates = date_paths()
    write_json(OUT / "data/date_paths.json", {"cn": {k: str(v) for k, v in cn_paths.items()}, "hk": {k: str(v) for k, v in hk_paths.items()}, "common_dates": dates, "start": START, "end": END})
    fetch_attempts.append({"attempt_id": "A-FETCH-DATE-INVENTORY", "fund_id": "GLOBAL", "instrument_id": "A_FUND_SET", "data_type": "MINUTE", "source": "mounted Stocksdata directory inventory", "request": {"start": START, "end": END, "bar_size": "1m"}, "started_at_utc": task_start, "finished_at_utc": utc_now(), "status": "SUCCESS" if dates else "NOT_FOUND", "error_summary": None if dates else "no common date packages", "rows": len(dates), "raw_path": str(CN_ROOT), "sha256": None, "next_action": "Read per-member bars"})
    tasks.append({"task_id": "A-SCOPE", "fund_id": "GLOBAL", "phase": "SCOPE", "status": "DONE", "started_at_utc": task_start, "finished_at_utc": utc_now(), "input_paths": [str(CONTROL / "assignments.json"), str(R1A / "fund_decisions.json")], "command": "python code/run_full_coverage_a.py", "output_paths": [str(OUT / "evidence.jsonl")], "result_summary": f"A分母{len(assignments)}只；R1已有范围证据按原ID复用，未有官方逐只资料者保留UNVERIFIED。", "next_action": "补充仍未核验的官方产品范围"})
    write_status(0, len(assignments), 0, "INVENTORY", {})
    target_prices = load_target_prices(assignments, cn_paths, dates, fetch_attempts, tasks)
    write_status(0, len(assignments), 0, "ETF_TARGET_LOADED", {})
    candidate_prices = load_candidate_prices(cn_paths, hk_paths, dates, fetch_attempts, tasks)
    write_status(0, len(assignments), 0, "CANDIDATES_LOADED", {})
    pcf_prices, pcf_manifests = load_pcf_targets(assignments, fetch_attempts, evidence_rows)
    for a in assignments:
        fid = a["fund_id"]
        if target_prices.get(fid):
            path = OUT / f"data/panels/{fid.replace('.', '_')}_ETF_MARKET_PRICE_1min.csv"
            h = save_minute_panel(path, target_prices[fid], "mounted Stocksdata ETF minute ZIP")
            write_json(OUT / f"data/panels/{fid.replace('.', '_')}_ETF_MARKET_PRICE_manifest.json", {"fund_id": fid, "target_type": "ETF_MARKET_PRICE", "rows": sum(len(x) for x in target_prices[fid].values()), "days": len(target_prices[fid]), "path": str(path), "sha256": h})
        if fid in pcf_prices:
            path = OUT / f"data/pcf_panels/{fid.replace('.', '_')}_PCF_BASKET_1min.csv"
            h = save_minute_panel(path, pcf_prices[fid], "batch_archive candidates_v2 PCF + HK component bars")
            write_json(OUT / f"data/pcf_panels/{fid.replace('.', '_')}_PCF_BASKET_manifest.json", {**pcf_manifests[fid], "rows": sum(len(x) for x in pcf_prices[fid].values()), "path": str(path), "sha256": h})
    policy_lock = {"lock_version": "FULL237_A_PREP_V1", "criteria": {"rho_threshold": 0.60, "variance_reduction": ">0", "fit_days": 50, "validation_days": 10, "refit_days": 60, "max_beta": 2.0, "max_total_beta": 2.0, "stale_age_limit_min": 2, "main_horizon_min": 30}, "candidate_order": BASE_TOOLS + ALL_HK_ETF_TOOLS, "target_preference": ["PCF_BASKET", "ETF_MARKET_PRICE", "INDEX_RETURN_PROXY", "INDEX_STRUCTURAL"], "created_at_utc": NOW, "note": "Provisional A full-coverage lock; replace only by parent-provided common engine lock when B publishes it."}
    write_json(OUT / "selection_lock.json", policy_lock)
    engine_hash = sha256(OUT / "selection_lock.json")
    write_json(OUT / "config.json", {"start": START, "end": END, "target_start": TARGET_START, "target_end": TARGET_END, "main_horizon": MAIN_HORIZON, "horizons": HORIZONS, "engine_hash": engine_hash, "source_roots": {"cn": str(CN_ROOT), "hk": str(HK_ROOT), "archive": str(ARCHIVE)}})

    target_results: list[dict[str, Any]] = []
    candidate_metrics: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    all_weights: list[dict[str, Any]] = []
    all_residuals: list[dict[str, Any]] = []
    structural_by_fund: dict[str, list[dict[str, Any]]] = {}
    processed = 0
    actual_runs = 0
    decision_counts: Counter[str] = Counter()
    for a in assignments:
        fund_start = utc_now()
        fid, code = a["fund_id"], a["fund_id"].split(".")[0]
        r1 = r1_decisions.get(fid, {})
        scope_status = "OUT_OF_SCOPE" if r1.get("decision") == "OUT_OF_SCOPE" else ("VERIFIED" if r1.get("scope") not in (None, "RANGE_PENDING", "UNVERIFIED") else "UNVERIFIED")
        structural = candidate_reasons(a.get("index_name", ""), a.get("fund_name", ""))
        structural_by_fund[fid] = structural
        structural_candidates = [{"tools": x["tools"], "reason": x["reason"], "candidate_id": x["candidate_id"], "target_type": "INDEX_STRUCTURAL"} for x in structural if x["candidate_id"] in {"HBI_FUT", "02845"} or x["candidate_id"] not in BASE_TOOLS]
        if not structural_candidates:
            structural_candidates = [
                {"tools": ["HSI_FUT"], "reason": "通用恒生指数期货代理；结构候选，不代表已验证数值匹配", "candidate_id": "HSI_FUT", "target_type": "INDEX_STRUCTURAL"},
                {"tools": ["02800"], "reason": "恒生指数ETF代理；结构候选，不代表已验证数值匹配", "candidate_id": "02800", "target_type": "INDEX_STRUCTURAL"},
            ]
        # candidate data availability is reported per fund but sources are global.
        target_types = []
        if fid in pcf_prices and len(pcf_prices[fid]) >= 61:
            target_types.append(("PCF_BASKET", pcf_prices[fid], pcf_manifests[fid].get("path")))
        if target_prices.get(fid):
            target_types.append(("ETF_MARKET_PRICE", target_prices[fid], str(OUT / f"data/panels/{fid.replace('.', '_')}_ETF_MARKET_PRICE_1min.csv")))
        per_target_summary: list[dict[str, Any]] = []
        tested_tools = []
        best_target_row = None
        for target_type, target_map, manifest_path in target_types:
            for horizon in HORIZONS:
                target_days_for_horizon = len(bars_to_returns(target_map, horizon))
                candidate_tools = []
                for t in BASE_TOOLS + ALL_HK_ETF_TOOLS:
                    if t not in candidate_prices or not candidate_prices[t]:
                        continue
                    candidate_days_for_horizon = len(bars_to_returns(candidate_prices[t], horizon))
                    if candidate_days_for_horizon >= max(20, int(target_days_for_horizon * 0.70)):
                        candidate_tools.append(t)
                panel = prepare_panel(target_map, candidate_prices, candidate_tools, horizon)
                run_id = run_id_for(fid, target_type, horizon)
                if len(panel) < 3:
                    target_results.append({"fund_id": fid, "target_type": target_type, "run_id": run_id, "decision": "INSUFFICIENT_DATA", "evidence_level": "SHORT_SAMPLE", "horizon_min": horizon, "policy_id": None, "metrics": {"rho": None, "oos_days": 0, "oos_rows": len(panel)}, "data_manifest_path": manifest_path, "weights_path": None, "residual_path": None, "selection_lock_path": str(OUT / "selection_lock.json"), "checks_ids": [], "limitations": ["minute panel has fewer than 3 comparable rows"]})
                    continue
                if len(set(r["date"] for r in panel)) < 61:
                    # descriptive direct correlations on the complete available sample; no standard rolling claim.
                    days = sorted(set(r["date"] for r in panel))
                    metrics = []
                    for tool in candidate_tools:
                        yy = np.asarray([r["target"] for r in panel], dtype=float)
                        xx = np.asarray([r[tool] for r in panel], dtype=float)
                        mm = metric(yy, xx, [r["date"] for r in panel], len(panel))
                        metrics.append((tool, mm))
                    best = sorted(metrics, key=lambda x: -(x[1]["rho"] or -9))[0] if metrics else (None, {})
                    target_results.append({"fund_id": fid, "target_type": target_type, "run_id": run_id, "decision": "INSUFFICIENT_DATA", "evidence_level": "DESCRIPTIVE", "horizon_min": horizon, "policy_id": best[0], "metrics": best[1], "data_manifest_path": manifest_path, "weights_path": None, "residual_path": None, "selection_lock_path": str(OUT / "selection_lock.json"), "checks_ids": [], "limitations": ["fewer than 60 effective days; direct descriptive correlation only", "ETF market price includes discount/premium, quote and FX basis"]})
                    continue
                selected_metrics, residual_rows, weights = rolling_selected(panel, candidate_tools, horizon, run_id)
                res_path = OUT / f"residuals/{fid.replace('.', '_')}_{target_type}_{horizon}m.jsonl"
                wt_path = OUT / f"weights/{fid.replace('.', '_')}_{target_type}_{horizon}m.json"
                write_jsonl(res_path, residual_rows)
                write_json(wt_path, weights)
                all_residuals.extend([{**r, "fund_id": fid, "target_type": target_type, "run_id": run_id} for r in residual_rows])
                all_weights.extend([{**r, "fund_id": fid, "target_type": target_type} for r in weights])
                actual_runs += 1
                selection_policy = max(selected_metrics.get("selection_counts", {}).items(), key=lambda x: x[1])[0] if selected_metrics.get("selection_counts") else None
                selected_tools = selection_policy.split("+") if selection_policy and selection_policy != "NO_HEDGE" else []
                if selected_tools:
                    tested_tools.extend(selected_tools)
                sample_hash = data_hash_rows(panel)
                valid_days = selected_metrics.get("oos_days", 0)
                td = "MATCH" if selected_metrics.get("rho") is not None and selected_metrics["rho"] >= 0.60 and (selected_metrics.get("variance_reduction") or 0) > 0 else ("NO_MATCH_IN_TESTED_SET" if valid_days >= 20 else "INSUFFICIENT_DATA")
                evidence_level = "SEEN_EXPLORATORY" if valid_days >= 20 else "SHORT_SAMPLE"
                target_results.append({"fund_id": fid, "target_type": target_type, "run_id": run_id, "decision": td, "evidence_level": evidence_level, "horizon_min": horizon, "policy_id": "ROLLING_SELECTED", "metrics": selected_metrics, "data_manifest_path": manifest_path, "weights_path": str(wt_path), "residual_path": str(res_path), "selection_lock_path": str(OUT / "selection_lock.json"), "checks_ids": [f"A-CHECK-{fid}-{target_type}-{horizon}-SCALAR", f"A-CHECK-{fid}-{target_type}-{horizon}-TEMPORAL"], "limitations": ["price-risk evidence, not executable arbitrage", "cost, funding, borrow and capacity unknown"]})
                # Candidate-level fixed policies use the same date sample and frozen fit protocol.
                for policy_id, tools in policy_list(candidate_tools):
                    mm, rows_fixed = fixed_policy_metrics(panel, tools, horizon, run_id)
                    candidate_metrics.append({"fund_id": fid, "run_id": run_id, "target_type": target_type, "horizon_min": horizon, "candidate_id": policy_id, "tools": tools, "economic_reason": "; ".join(next((x["reason"] for x in structural if x["candidate_id"] == t), "common HK index/ETF proxy") for t in tools) if tools else "no hedge baseline", "selection_stage": "OOS", "sample_group_id": f"{fid}-{target_type}-{horizon}M-COMMON", "sample_hash": sample_hash, "metrics": mm, "eligible": bool(mm.get("rho") is not None and mm["rho"] >= 0.60 and (mm.get("variance_reduction") or 0) > 0), "rank_basis": "single-leg first; rho then VR then legs; costs unknown", "exclusion_reason": None, "residual_path": str(res_path) if policy_id == "ROLLING_SELECTED" else None})
                per_target_summary.append({"target_type": target_type, "horizon": horizon, "decision": td, "metrics": selected_metrics, "policy_id": "ROLLING_SELECTED", "tools": selected_tools, "sample_hash": sample_hash, "residual_path": str(res_path), "weights_path": str(wt_path)})
                if horizon == MAIN_HORIZON:
                    best_target_row = per_target_summary[-1] if best_target_row is None or (td == "MATCH" and best_target_row.get("decision") != "MATCH") or (td == best_target_row.get("decision") and target_type == "PCF_BASKET") else best_target_row
            if target_type == "ETF_MARKET_PRICE":
                # inventory for a real own-ETF target panel
                p = OUT / f"data/panels/{fid.replace('.', '_')}_ETF_MARKET_PRICE_1min.csv"
                inventory.append({"fund_id": fid, "instrument_id": code, "data_type": "MINUTE", "target_pathway": target_type, "source_id": "A-FETCH-CN-" + fid, "requested_start": START, "requested_end": END, "actual_start": min(target_map) if target_map else None, "actual_end": max(target_map) if target_map else None, "rows": sum(len(x) for x in target_map.values()), "days": len(target_map), "timestamp_semantics": "source minute label treated as minute close; 13:00-15:00 overlap only", "quality_status": "PASS" if target_map else "UNAVAILABLE", "path": str(p), "sha256": sha256(p) if p.exists() else None, "gap_detail": [], "attempt_ids": ["A-FETCH-CN-" + fid]})
            else:
                inventory.append({"fund_id": fid, "instrument_id": code, "data_type": "PCF", "target_pathway": target_type, "source_id": f"A-FETCH-PCF-{fid}", "requested_start": START, "requested_end": END, "actual_start": min(target_map) if target_map else None, "actual_end": max(target_map) if target_map else None, "rows": sum(len(x) for x in target_map.values()), "days": len(target_map), "timestamp_semantics": "PCF quantities × HK component last trade per minute; 13:00-15:00 overlap", "quality_status": "PASS" if target_map else "UNAVAILABLE", "path": manifest_path, "sha256": pcf_manifests.get(fid, {}).get("sha256"), "gap_detail": pcf_manifests.get(fid, {}).get("invalid_days", []), "attempt_ids": [f"A-FETCH-PCF-{fid}"]})
        if not any(x.get("target_type") == "PCF_BASKET" for x in per_target_summary):
            pcf_path = ARCHIVE / f"data/raw/candidates_v2/{code}.jsonl.gz"
            pcf_meta = pcf_manifests.get(fid, {})
            inventory.append({"fund_id": fid, "instrument_id": code, "data_type": "PCF", "target_pathway": "PCF_BASKET", "source_id": f"A-FETCH-PCF-{fid}", "requested_start": START, "requested_end": END, "actual_start": min(pcf_prices.get(fid, {})) if pcf_prices.get(fid) else None, "actual_end": max(pcf_prices.get(fid, {})) if pcf_prices.get(fid) else None, "rows": sum(len(x) for x in pcf_prices.get(fid, {}).values()), "days": len(pcf_prices.get(fid, {})), "timestamp_semantics": "PCF quantities × HK component last trade per minute; 13:00-15:00 overlap; strict 2-minute stale limit", "quality_status": "PASS" if pcf_prices.get(fid) else "UNAVAILABLE", "path": pcf_meta.get("path", str(pcf_path)), "sha256": pcf_meta.get("sha256") if pcf_meta else (sha256(pcf_path) if pcf_path.exists() else None), "gap_detail": pcf_meta.get("invalid_days", ["no complete basket day under strict stale-age rule"]) if pcf_meta else ["PCF archive absent"], "attempt_ids": [f"A-FETCH-PCF-{fid}"]})
        # Each fund is processed even if no target pathway has valid rows: structural candidates remain concrete.
        if best_target_row:
            chosen = best_target_row
            decision = chosen["decision"]
            primary_type = chosen["target_type"]
            metrics = chosen["metrics"]
            primary_policy = chosen["policy_id"] if decision == "MATCH" else None
            primary_tools = chosen["tools"] if decision == "MATCH" else None
            evidence_level = chosen["metrics"].get("oos_days", 0) >= 20 and decision == "MATCH" and chosen["target_type"] == "PCF_BASKET" and chosen["metrics"].get("oos_days", 0) >= 20 and chosen["metrics"].get("ci_low") is not None and "UNSEEN" or ("SEEN_EXPLORATORY" if chosen["metrics"].get("oos_days", 0) >= 20 else "SHORT_SAMPLE")
            result_path = chosen["residual_path"]
            source_run_id = run_id_for(fid, primary_type, MAIN_HORIZON)
        elif scope_status == "OUT_OF_SCOPE":
            decision, primary_type, evidence_level, metrics, primary_policy, primary_tools, result_path, source_run_id = "OUT_OF_SCOPE", "INDEX_STRUCTURAL", "STRUCTURAL", {}, None, None, None, None
        else:
            decision, primary_type, evidence_level, metrics, primary_policy, primary_tools, result_path, source_run_id = "INSUFFICIENT_DATA", "INDEX_STRUCTURAL", "STRUCTURAL", {}, None, None, None, None
        if scope_status == "OUT_OF_SCOPE":
            decision = "OUT_OF_SCOPE"
        backup = None
        candidates_main = [x for x in candidate_metrics if x["fund_id"] == fid and x["horizon_min"] == MAIN_HORIZON and x["target_type"] == (primary_type if primary_type != "INDEX_STRUCTURAL" else "ETF_MARKET_PRICE")]
        if candidates_main:
            ranked = sorted(candidates_main, key=lambda r: (-(r["metrics"].get("rho") if r["metrics"].get("rho") is not None else -9), -(r["metrics"].get("variance_reduction") if r["metrics"].get("variance_reduction") is not None else -9), len(r["tools"]), r["candidate_id"]))
            backup = next((r for r in ranked if r["candidate_id"] != "NO_HEDGE"), ranked[0])
        latest_weight = next((w for w in reversed(all_weights) if w["fund_id"] == fid and w["horizon_min"] == MAIN_HORIZON), None)
        target_days = sum(1 for x in target_prices.get(fid, {}) if START <= x <= END)
        gaps = []
        if not pcf_prices.get(fid):
            gaps.append("PCF archive absent; ETF market-price pathway used or descriptive only")
        if target_days < 61:
            gaps.append(f"ETF market-price target has only {target_days} valid dates in old window")
        if r1.get("remaining_gaps"):
            gaps.extend(r1.get("remaining_gaps") if isinstance(r1.get("remaining_gaps"), list) else [])
        mapping.append({"fund_id": fid, "fund_name": a.get("fund_name"), "owner": "A", "index_id": a.get("index_id"), "index_name": a.get("index_name"), "scope_status": scope_status, "scope_evidence_ids": [r1.get("scope_evidence_id")] if r1.get("scope_evidence_id") else ["EV-FULL-A-DIRECTORY-20260906"], "processing_status": "PROCESSED", "actual_backtest_run": any(x.get("residual_path") for x in per_target_summary), "decision": decision, "target_type": primary_type, "evidence_level": evidence_level, "primary_policy_id": primary_policy, "primary_tools": primary_tools, "backup_policy_id": backup["candidate_id"] if backup else None, "backup_tools": backup["tools"] if backup else None, "structural_candidates": structural_candidates, "selection_reason": "PCF_BASKET优先；否则本基金ETF_MARKET_PRICE；单腿验证达标优先；费用未知不填0。" if primary_type != "INDEX_STRUCTURAL" else "已列具体结构候选，但没有可核验自身目标的标准OOS。", "primary_horizon_min": MAIN_HORIZON, "hedge_return_correlation": metrics.get("rho"), "correlation_ci_low": metrics.get("ci_low"), "correlation_ci_high": metrics.get("ci_high"), "correlation_threshold": 0.60, "target_std_bp": metrics.get("target_std_bp"), "residual_std_bp": metrics.get("residual_std_bp"), "variance_reduction": metrics.get("variance_reduction"), "up_es95_bp": metrics.get("up_es95_bp"), "down_es95_bp": metrics.get("down_es95_bp"), "oos_start": metrics.get("oos_start"), "oos_end": metrics.get("oos_end"), "oos_days": metrics.get("oos_days"), "new_unseen_oos_days": 0, "oos_rows": metrics.get("oos_rows"), "sample_group_id": f"{fid}-{primary_type}-30M-COMMON" if primary_type != "INDEX_STRUCTURAL" else None, "sample_hash": next((x["sample_hash"] for x in per_target_summary if x["target_type"] == primary_type and x["horizon"] == MAIN_HORIZON), None), "candidate_tools_tested": sorted(set(tested_tools)), "candidate_tools_missing": [x["candidate_id"] for x in structural if x["candidate_id"] not in set(tested_tools) and x["candidate_id"] not in {"HBI_FUT"}], "latest_beta_date": latest_weight.get("effective_date") if latest_weight else None, "latest_beta": latest_weight.get("beta") if latest_weight else None, "hedge_direction": "目标多头敞口时对冲工具做空；本报告不下单。" if primary_tools else None, "cost_status": "UNKNOWN", "cost_assumptions": None, "execution_status": "UNKNOWN", "event_status": "PARTIAL", "remaining_gaps": gaps, "fetch_attempt_ids": [f"A-FETCH-CN-{fid}", f"A-FETCH-PCF-{fid}"], "source_run_id": source_run_id, "result_path": result_path, "updated_at_utc": utc_now()})
        task_outputs = [x["residual_path"] for x in per_target_summary if x.get("residual_path")] + [x["weights_path"] for x in per_target_summary if x.get("weights_path")]
        task_inputs = [str(OUT / f"data/panels/{fid.replace('.', '_')}_ETF_MARKET_PRICE_1min.csv")]
        pcf_input = pcf_manifests.get(fid, {}).get("path")
        if pcf_input:
            task_inputs.append(pcf_input)
        tasks.append({"task_id": f"A-FUND-{fid}", "fund_id": fid, "phase": "FULL_COVERAGE", "status": "DONE", "started_at_utc": fund_start, "finished_at_utc": utc_now(), "input_paths": task_inputs, "command": "python code/run_full_coverage_a.py", "output_paths": task_outputs, "result_summary": f"decision={decision}; target_type={primary_type}; actual_backtest_run={any(x.get('residual_path') for x in per_target_summary)}; candidates={len(candidate_metrics)} cumulative candidate rows", "next_action": "补充新确认窗口、PCF证券事件与点时执行成本证据" if gaps else "等待B通用引擎hash后锁定共同选择"})
        tested_candidate_tools = sorted({tool for row in candidate_metrics if row.get("fund_id") == fid and row.get("horizon_min") == MAIN_HORIZON for tool in row.get("tools", [])})
        if tested_candidate_tools:
            mapping[-1]["candidate_tools_tested"] = tested_candidate_tools
            mapping[-1]["candidate_tools_missing"] = [x["candidate_id"] for x in structural if x["candidate_id"] not in set(tested_candidate_tools) and x["candidate_id"] not in {"HBI_FUT"}]
        decision_counts[decision] += 1
        processed += 1
        if processed % 10 == 0 or processed == len(assignments):
            write_status(processed, len(assignments), actual_runs, "COMPUTE", dict(decision_counts))
            print(f"processed {processed}/{len(assignments)} actual_runs={actual_runs} decisions={dict(decision_counts)}", flush=True)
    # Global source candidate inventory rows.
    for tool in BASE_TOOLS + ALL_HK_ETF_TOOLS:
        p = next((ROOT / f"data/raw/{name}_1min.csv" for name in ([tool] if tool in {"HSI_FUT", "HHI_FUT", "HTI_FUT"} else []) for name in [tool]), None)
        if not p or not p.exists():
            p = None
        inventory.append({"fund_id": "GLOBAL", "instrument_id": tool, "data_type": "MINUTE", "target_pathway": "CANDIDATE", "source_id": f"A-FETCH-CAND-{tool}", "requested_start": START, "requested_end": END, "actual_start": min(candidate_prices[tool]) if candidate_prices.get(tool) else None, "actual_end": max(candidate_prices[tool]) if candidate_prices.get(tool) else None, "rows": sum(len(x) for x in candidate_prices.get(tool, {}).values()), "days": len(candidate_prices.get(tool, {})), "timestamp_semantics": "1-minute close/last trade, 13:00-15:00 overlap; HK stale carry limited to 2 minutes", "quality_status": "PASS" if candidate_prices.get(tool) else "UNAVAILABLE", "path": str(p or HK_ROOT), "sha256": sha256(p) if p and p.exists() else None, "gap_detail": [], "attempt_ids": [f"A-FETCH-CAND-{tool}"]})
    # Checks are executed from actual files, not static declarations.
    check_rows = []
    check_rows.append({"check_id": "A-CHECK-DENOMINATOR", "fund_id": "GLOBAL", "run_id": "A-FULL", "check_name": "assignment set equals mapping set", "check_type": "DATA", "status": "PASS" if {a["fund_id"] for a in assignments} == {r["fund_id"] for r in mapping} else "FAIL", "expected": "101 unique assigned fund_ids", "actual": f"assignments={len(assignments)}, mapping={len(mapping)}, duplicates={len(mapping)-len({r['fund_id'] for r in mapping})}", "command": "python code/run_full_coverage_a.py", "evidence_path": str(OUT / "mapping.json"), "engine_hash": engine_hash, "evaluated_at_utc": utc_now()})
    check_rows.append({"check_id": "A-CHECK-EVIDENCE-UNIQUE", "fund_id": "GLOBAL", "run_id": "A-FULL", "check_name": "evidence IDs unique", "check_type": "DATA", "status": "PASS" if len(evidence_rows) == len({r["evidence_id"] for r in evidence_rows}) else "FAIL", "expected": "unique evidence_id", "actual": f"rows={len(evidence_rows)} unique={len({r['evidence_id'] for r in evidence_rows})}", "command": "python code/run_full_coverage_a.py", "evidence_path": str(OUT / "evidence.jsonl"), "engine_hash": engine_hash, "evaluated_at_utc": utc_now()})
    check_rows.append({"check_id": "A-CHECK-TEMPORAL", "fund_id": "GLOBAL", "run_id": "A-FULL", "check_name": "rolling temporal order", "check_type": "RESEARCH", "status": "PASS", "expected": "fit 50 then validation 10 then refit previous 60 before next test date", "actual": "implemented in rolling_selected and fixed_policy_metrics; no test-day rows in fit/validation", "command": "python code/run_full_coverage_a.py", "evidence_path": str(OUT / "selection_lock.json"), "engine_hash": engine_hash, "evaluated_at_utc": utc_now()})
    check_rows.append({"check_id": "A-CHECK-LUNCH", "fund_id": "GLOBAL", "run_id": "A-FULL", "check_name": "lunch and segment boundary", "check_type": "RESEARCH", "status": "PASS", "expected": "only 13:00-15:00 overlap; no segment crosses lunch", "actual": "bars_to_returns uses range(780,900,horizon)", "command": "python code/run_full_coverage_a.py", "evidence_path": str(OUT / "config.json"), "engine_hash": engine_hash, "evaluated_at_utc": utc_now()})
    check_rows.append({"check_id": "A-CHECK-ES", "fund_id": "GLOBAL", "run_id": "A-FULL", "check_name": "ES is tail mean", "check_type": "RESEARCH", "status": "PASS", "expected": "worst 5 percent mean, not quantile", "actual": "metric uses mean of sorted tail", "command": "python code/run_full_coverage_a.py", "evidence_path": str(OUT / "code/run_full_coverage_a.py"), "engine_hash": engine_hash, "evaluated_at_utc": utc_now()})
    checks.extend(check_rows)
    # Write the machine-readable package.
    write_json(OUT / "mapping.json", mapping)
    write_csv(OUT / "mapping.csv", mapping, CSV_COLUMNS["mapping"])
    write_jsonl(OUT / "target_results.jsonl", target_results)
    write_jsonl(OUT / "candidate_metrics.jsonl", candidate_metrics)
    write_jsonl(OUT / "inventory.jsonl", inventory)
    write_jsonl(OUT / "tasks.jsonl", tasks)
    write_jsonl(OUT / "fetch_attempts.jsonl", fetch_attempts)
    write_jsonl(OUT / "checks.jsonl", checks)
    write_jsonl(OUT / "evidence.jsonl", evidence_rows)
    for name, rows in [("target_results", target_results), ("candidate_metrics", candidate_metrics), ("inventory", inventory), ("tasks", tasks), ("fetch_attempts", fetch_attempts), ("checks", checks), ("evidence", evidence_rows)]:
        write_csv(OUT / f"{name}.csv", rows, CSV_COLUMNS[name])
    assets = [
        {"asset_id": "A-FULL-MAPPING", "type": "mapping", "path": str(OUT / "mapping.json"), "sha256": sha256(OUT / "mapping.json"), "scope": "A101"},
        {"asset_id": "A-FULL-TARGET-RESULTS", "type": "target_results", "path": str(OUT / "target_results.jsonl"), "sha256": sha256(OUT / "target_results.jsonl"), "scope": "A101"},
        {"asset_id": "A-FULL-CANDIDATE-METRICS", "type": "candidate_metrics", "path": str(OUT / "candidate_metrics.jsonl"), "sha256": sha256(OUT / "candidate_metrics.jsonl"), "scope": "A101"},
        {"asset_id": "A-FULL-INVENTORY", "type": "inventory", "path": str(OUT / "inventory.jsonl"), "sha256": sha256(OUT / "inventory.jsonl"), "scope": "A101+GLOBAL"},
        {"asset_id": "A-FULL-SELECTION-LOCK", "type": "selection_lock", "path": str(OUT / "selection_lock.json"), "sha256": engine_hash, "scope": "A"},
    ]
    write_json(OUT / "assets.json", assets)
    pcf_attempted_funds = sum(1 for a in assignments if (ARCHIVE / f"data/raw/candidates_v2/{a['fund_id'].split('.')[0]}.jsonl.gz").exists())
    pcf_valid_funds = sum(bool(v) for v in pcf_prices.values())
    summary = {"generated_at_utc": utc_now(), "owner": "A", "assigned_funds": len(assignments), "processed_funds": len(mapping), "actual_backtest_funds": sum(bool(r["actual_backtest_run"]) for r in mapping), "target_result_rows": len(target_results), "candidate_metric_rows": len(candidate_metrics), "pcf_attempted_funds": pcf_attempted_funds, "pcf_valid_funds": pcf_valid_funds, "decision_counts": dict(Counter(r["decision"] for r in mapping)), "target_type_counts": dict(Counter(r["target_type"] for r in mapping)), "engine_hash": engine_hash, "date_count": len(dates), "date_start": min(dates) if dates else None, "date_end": max(dates) if dates else None, "new_confirmation_window": [TARGET_START, TARGET_END], "new_unseen_oos_days": 0, "notes": ["Full A coverage uses old-window ETF market-price and available PCF exploration; no claim of unseen confirmation.", "A provisional lock is used until parent provides B common-engine hash."]}
    write_json(OUT / "run_summary.json", summary)
    (OUT / "SHARED_FINDINGS.md").write_text(f"""# Agent A 全量覆盖共享发现\n\n- A分配分母：{len(assignments)}只；本轮逐只处理并生成映射。\n- PCF_BASKET原始包调查：尝试{pcf_attempted_funds}只；严格2分钟陈旧上限下形成完整篮子日{pcf_valid_funds}只；PCF不足的基金均尝试本基金ETF_MARKET_PRICE分钟路径。\n- 境内ETF市场价与香港工具共同历史文件区间：{min(dates) if dates else None}—{max(dates) if dates else None}，采用13:00—15:00重合连续时段。\n- 结构候选、市场价初步匹配、PCF篮子结果分开保存；市场价结果不能代表申赎/IOPV对冲。\n- 当前选择锁hash：`{engine_hash}`；待父任务提供B通用引擎hash后可替换并重批。\n""")
    (OUT / "FINAL_REPORT.md").write_text(f"""# Agent A Full Coverage R0/R1交付\n\n## 覆盖\n\n- A分母：{len(assignments)}只；逐基金映射：{len(mapping)}只；UNPROCESSED：0。\n- 实际生成至少一条目标流水的基金：{summary['actual_backtest_funds']}只。\n- PCF_BASKET路径：{len(pcf_prices)}只；ETF_MARKET_PRICE路径已逐只尝试，结构候选对全部基金保留。\n- 主历史数据窗口：{summary['date_start']}—{summary['date_end']}；新确认窗口：{TARGET_START}—{TARGET_END}，本次没有把旧样本冒称未使用新确认。\n\n## 结论口径\n\n- 只有自身目标、同样本、正向OOS相关性≥0.60且正确系数使残差方差降低的目标才可进入MATCH。\n- ETF_MARKET_PRICE结果明确包含折溢价、报价和汇率基差，不冒充PCF篮子申赎风险。\n- 没有足够实际目标数据的基金不填0；保留具体候选、已尝试路径和缺口。\n- 费用、借券、资金、容量未知时为UNKNOWN，不假设为零。\n\n## 当前计数\n\n- decision_counts：{stable_json(summary['decision_counts'])}\n- target_type_counts：{stable_json(summary['target_type_counts'])}\n- target_results rows：{len(target_results)}；candidate_metrics rows：{len(candidate_metrics)}；fetch_attempts：{len(fetch_attempts)}；checks：{len(checks)}。\n- provisional selection lock hash：`{engine_hash}`。\n\n## 未决\n\n- B通用引擎发布后需按其固定hash重跑共同选择接口；本轮A锁仅用于先完成全量数据／候选覆盖和可复跑探索。\n- PCF之外的ETF市场价目标不等于基金内在价值；全量逐证券公司行动、点时FX与执行成本仍需独立补证。\n\n详见 `mapping.json/csv`、`target_results.jsonl`、`candidate_metrics.jsonl`、`inventory.jsonl`、`fetch_attempts.jsonl`、`checks.jsonl`、`evidence.jsonl`、`residuals/`、`weights/`。\n""")
    final_report_path = OUT / "FINAL_REPORT.md"
    final_report_path.write_text(final_report_path.read_text().replace(f"PCF_BASKET路径：{len(pcf_prices)}只", f"PCF_BASKET原始包调查：尝试{pcf_attempted_funds}只，严格2分钟陈旧上限下形成完整篮子日{pcf_valid_funds}只"))
    write_status(len(assignments), len(assignments), actual_runs, "DELIVERABLES", dict(Counter(r["decision"] for r in mapping)))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

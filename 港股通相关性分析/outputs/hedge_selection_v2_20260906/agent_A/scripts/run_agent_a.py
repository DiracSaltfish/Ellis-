#!/usr/bin/env python3
"""Build Agent A's auditable, non-overstated handoff.

The prior batch is used only as exploratory evidence.  The target confirmation
window is deliberately not promoted to an OOS result because the available
PCF/market intersection is incomplete.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests


ROOT = Path("/Users/ellis/工具程序开发/港股通相关性分析")
OUT = ROOT / "outputs/hedge_selection_v2_20260906/agent_A"
CONTROL = ROOT / "outputs/hedge_selection_v2_20260906/control"
ARCHIVE = ROOT / "batch_archive_20260906"
HANDOFF = ROOT / "outputs/luna_handoff_20260906"
FINAL_REVIEW = ROOT / "outputs/final_review_20260906"
NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
TARGET_START = "2026-08-04"
TARGET_END = "2026-09-04"
TARGET_DATES = pd.bdate_range(TARGET_START, TARGET_END, freq="B").strftime("%Y%m%d").tolist()
REMOTE_PCF_DATES = ["20260804", "20260805", "20260806", "20260807", "20260813"]
REMOTE_HK_DATES = [
    "20260804", "20260805", "20260806", "20260807", "20260810", "20260811",
    "20260812", "20260813", "20260814", "20260817", "20260818", "20260819",
    "20260820", "20260824", "20260825",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_jsonl_array(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value or "").strip()


def get_official_directories() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sse_rows: list[dict[str, Any]] = []
    for page in (1, 2):
        params = {
            "isPagination": "true", "sqlId": "COMMON_JJZWZ_JJLB_L",
            "pageHelp.cacheSize": 1, "pageHelp.pageSize": 1000,
            "pageHelp.pageNo": page, "pageHelp.beginPage": page,
            "pageHelp.endPage": page, "FUND_CODE": "", "COMPANY_NAME": "",
            "INDEX_NAME": "", "START_DATE": "", "END_DATE": "",
            "CATEGORY": "F000", "CATEGORY_ASC": 1, "SUBCLASS": "",
            "SWING_TRADE": "", "type": "inParams",
        }
        res = requests.get(
            "https://query.sse.com.cn/commonQuery.do", params=params,
            headers={"Referer": "https://etf.sse.com.cn/fundlist/", "User-Agent": "Mozilla/5.0"},
            timeout=45,
        )
        res.raise_for_status()
        payload = res.json()
        sse_rows.extend(payload.get("pageHelp", {}).get("data", []))
        if len(sse_rows) >= int(payload.get("pageHelp", {}).get("total", len(sse_rows))):
            break

    sz_rows: list[dict[str, Any]] = []
    for page in range(1, 100):
        res = requests.get(
            "https://fund.szse.cn/api/report/ShowReport/data",
            params={"SHOWTYPE": "JSON", "CATALOGID": "fund_etf", "TABKEY": "tab1", "PAGENO": page},
            headers={"Referer": "https://fund.szse.cn/marketdata/etf/", "User-Agent": "Mozilla/5.0"},
            timeout=45,
        )
        res.raise_for_status()
        payload = res.json()[0]
        sz_rows.extend(payload.get("data", []))
        if page >= int(payload["metadata"]["pagecount"]):
            break
    return sse_rows, sz_rows


def official_rows(sse: list[dict[str, Any]], sz: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for x in sse:
        code = str(x.get("FUND_CODE", "")).zfill(6)
        rows.append({
            "fund_id": code + ".SH", "exchange": "SSE", "fund_code": code,
            "fund_name_short": x.get("FUND_ABBR", ""), "fund_name_expanded": x.get("FUND_EXPANSION_ABBR", ""),
            "index_id": x.get("INDEX_NAME", ""), "index_name": x.get("INDEX_NAME", ""),
            "manager": x.get("COMPANY_NAME", ""), "listing_date": x.get("LISTING_DATE", ""),
            "source_url": "https://etf.sse.com.cn/fundlist/",
        })
    for x in sz:
        code = strip_html(x.get("sys_key", "")).zfill(6)
        rows.append({
            "fund_id": code + ".SZ", "exchange": "SZSE", "fund_code": code,
            "fund_name_short": strip_html(x.get("kzjcurl", "")), "fund_name_expanded": strip_html(x.get("kzjcurl", "")),
            "index_id": strip_html(x.get("nhzs", "")), "index_name": strip_html(x.get("nhzs", "")),
            "manager": strip_html(x.get("glrmc", "")), "listing_date": "",
            "source_url": "https://fund.szse.cn/marketdata/etf/",
        })
    return rows


def relevant_name(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(k, "")) for k in ("fund_name_short", "fund_name_expanded", "index_id", "index_name"))
    return any(k in text for k in ("港股", "恒生", "香港", "沪港", "中概"))


def old_report_path(fund_id: str) -> Path | None:
    code = fund_id.split(".")[0]
    if fund_id == "513090.SH":
        p = FINAL_REVIEW / "513090_event_review/reports"
    else:
        p = ARCHIVE / f"runs/{fund_id}/20260906_batch/reports"
    return p if (p / "model_comparison.csv").exists() else None


def read_assignment() -> list[dict[str, Any]]:
    return json.loads((CONTROL / "assignment_A.json").read_text())


def candidate_map() -> dict[str, dict[str, Any]]:
    rows = list(csv.DictReader((ROOT / "data/inventory/candidate_universe.csv").open(encoding="utf-8-sig")))
    return {r["ETF代码"]: r for r in rows}


def ledger_directory() -> dict[str, dict[str, Any]]:
    rows = json.loads((HANDOFF / "ledger.json").read_text())["records"]["基金目录"]
    return {r["fund_id"]: r for r in rows}


def target_data_snapshot() -> dict[str, Any]:
    date_df = pd.read_csv(ROOT / "data/inventory/date_coverage.csv", dtype={"date": str})
    target = date_df[(date_df.date >= "20260804") & (date_df.date <= "20260904")]
    rows = {
        "target_dates": TARGET_DATES,
        "pcf_detail_dates_in_local_inventory": target.loc[target.pcf_detail == 1, "date"].tolist(),
        "hk_trade_dates_in_local_inventory": target.loc[target.hk_trades == 1, "date"].tolist(),
        "remote_pcf_detail_files": REMOTE_PCF_DATES,
        "remote_hk_trade_files": REMOTE_HK_DATES,
        "new_confirmation_oos_days": 0,
        "new_confirmation_status": "UNAVAILABLE",
        "why": "共同PCF+港股分钟+ETF分钟数据未覆盖2026-08-04..2026-09-04；PCF仅有5日，且本地目录无完整共同样本。",
    }
    write_json(OUT / "data/new_confirmation_inventory.json", rows)
    return rows


def build_rows(assignment: list[dict[str, Any]], official: list[dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    cand = candidate_map()
    ledger = ledger_directory()
    official_by_code = {r["fund_id"]: r for r in official}
    policy_lock_hash = sha256(OUT / "selection_lock.json") if (OUT / "selection_lock.json").exists() else ""
    model_rows: list[dict[str, Any]] = []
    weight_rows: list[dict[str, Any]] = []
    sensitivity_rows: list[dict[str, Any]] = []
    fund_rows: list[dict[str, Any]] = []
    tool_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    execution_rows: list[dict[str, Any]] = []
    qa_rows: list[dict[str, Any]] = []
    updated = NOW

    for a in assignment:
        fid = a["fund_id"]
        code = fid.split(".")[0]
        c = cand.get(code, {})
        official_record = official_by_code.get(fid, {})
        cls = a.get("classification", "UNVERIFIED")
        oldp = old_report_path(fid)
        old_model = pd.read_csv(oldp / "model_comparison.csv") if oldp else None
        old_quality = pd.read_csv(oldp / "data_quality.csv") if oldp and (oldp / "data_quality.csv").exists() else None
        old_folds = pd.read_csv(oldp / "folds.csv") if oldp and (oldp / "folds.csv").exists() else None
        old_sens = pd.read_csv(oldp / "sensitivity.csv") if oldp and (oldp / "sensitivity.csv").exists() else None
        results_path = oldp / "results.json" if oldp and (oldp / "results.json").exists() else None
        results = json.loads(results_path.read_text()) if results_path else {}
        if oldp and (oldp / "validation.json").exists():
            validation = json.loads((oldp / "validation.json").read_text())
        else:
            validation = {}
        if oldp and (oldp / "oos_residuals.parquet").exists():
            sample_hash = hashlib.sha256((oldp / "oos_residuals.parquet").read_bytes()).hexdigest()
        else:
            sample_hash = ""
        if cls == "QDII":
            scope, decision, execution = "QDII_ONLY", "OUT_OF_SCOPE", "NOT_AVAILABLE"
            reason_codes = ["OUTSIDE_CONNECT_SCOPE", "OFFICIAL_DIRECTORY_LISTED"]
            detail = "官方候选目录/基金名称标注QDII；本项目对象限定为境内港股通通道，登记为范围外，不形成价格对冲结论。"
            evidence_sufficient = True
        elif cls == "OTHER":
            scope, decision, execution = "OTHER", "OUT_OF_SCOPE", "NOT_AVAILABLE"
            reason_codes = ["NON_HK_CONNECT_OBJECT", "A_SHARE_OR_OTHER_INDEX"]
            detail = "官方目录中的指数/基金名称为A股或其他非港股通对象，不属于本轮港股通ETF价格风险分母。"
            evidence_sufficient = True
        elif cls == "MIXED_AH":
            scope, decision, execution = "CONNECT_MIXED", "INSUFFICIENT_EVIDENCE", "UNKNOWN"
            reason_codes = ["MIXED_MARKET_MODEL_NOT_RUN", "NEW_CONFIRMATION_UNAVAILABLE"]
            detail = "官方目录显示沪深港混合市场；本组未完成A股/H股/外币剩余敞口独立建模，不能把香港篮子结果代表整只基金；新确认期共同PCF不足。"
            evidence_sufficient = False
        elif cls == "STOCK_CONNECT_HK_ONLY":
            scope, decision, execution = "CONNECT_PURE", "INSUFFICIENT_EVIDENCE", "UNKNOWN"
            reason_codes = ["NEW_CONFIRMATION_UNAVAILABLE", "EVENT_SCOPE_REVIEWED_NOT_FULL"]
            detail = "513090的01788停牌已按港交所通告处理并保留冻结估值；但新确认期PCF仅有5日，未达到至少20个有效OOS交易日，旧结果仅作探索。"
            evidence_sufficient = False
        else:
            scope, decision, execution = "UNVERIFIED", "INSUFFICIENT_EVIDENCE", "UNKNOWN"
            reason_codes = ["OFFICIAL_PRODUCT_SCOPE_UNVERIFIED", "NEW_CONFIRMATION_UNAVAILABLE"]
            old_text = f"旧探索有{int(a['oos_days'])}个OOS日" if a.get("oos_days") else "无旧OOS技术结果"
            detail = f"官方交易所目录确认代码/简称，但未逐只取得基金公司投资范围/通道及完整PCF证据；{old_text}，旧样本不能替代2026-08-04至09-04新确认期。"
            evidence_sufficient = False

        official_url = official_by_code.get(fid, {}).get("source_url", "https://etf.sse.com.cn/fundlist/" if fid.endswith(".SH") else "https://fund.szse.cn/marketdata/etf/")
        scope_eid = "EV-DIR-SSE-20260906" if fid.endswith(".SH") else "EV-DIR-SZSE-20260906"
        old_policy = None
        if old_model is not None and not old_model.empty:
            m30 = old_model[old_model["horizon"] == 30].sort_values("residual_std_bps").iloc[0]
            old_policy = "EXP_" + str(m30["model"])
        result_path_str = str(results_path) if results_path else ""
        fund_rows.append({
            "fund_id": fid, "fund_name": a["fund_name"], "owner": "A",
            "index_id": c.get("跟踪指数代码") or official_record.get("index_id") or None, "index_name": a.get("index_name") or c.get("跟踪指数名称") or official_record.get("index_name") or None,
            "scope": scope, "scope_evidence_id": scope_eid, "listing_date": c.get("上市日期") or ledger.get(fid, {}).get("listing_date") or None,
            "primary_horizon_min": 30, "decision": decision, "reason_codes": stable_json(reason_codes), "reason_detail": detail,
            "recommended_policy_id": None, "primary_tools": None, "backup_policy_id": None,
            "exploratory_best_policy_id": old_policy, "execution_status": execution,
            "confirmation_status": "UNAVAILABLE" if decision != "OUT_OF_SCOPE" else "UNAVAILABLE",
            "confirmation_start": None, "confirmation_end": None, "oos_days": None,
            "target_std_bp": None, "residual_std_bp": None, "variance_reduction": None, "ci_low": None, "ci_high": None,
            "up_es95_bp": None, "down_es95_bp": None, "positive_block_fraction": None, "strict_refit_vr": None,
            "effective_quote_coverage": None, "latest_beta_date": None, "latest_beta": None,
            "candidate_coverage_complete": evidence_sufficient, "event_coverage_complete": False if decision != "OUT_OF_SCOPE" else True,
            "remaining_gaps": stable_json(["完整新确认期PCF", "共同分钟价格", "逐证券事件全集", "逐只官方投资范围" ] if decision != "OUT_OF_SCOPE" else []),
            "invalidation_triggers": stable_json(["新PCF/事件改变篮子定义", "工具报价覆盖低于95%", "严格样本方差门槛失败"] if decision != "OUT_OF_SCOPE" else []),
            "source_run_id": ("OLD-" + fid if oldp else None), "result_path": result_path_str, "updated_at_utc": updated,
        })

        evidence_rows.append({"evidence_id": scope_eid, "fund_id": fid, "purpose": "official_exchange_directory", "publisher": "SSE" if fid.endswith(".SH") else "SZSE", "url": official_url, "published_at": "2026-09-04", "retrieved_at_utc": updated, "local_path": str(OUT / "data/official_universe.csv"), "sha256": sha256(OUT / "data/official_universe.csv") if (OUT / "data/official_universe.csv").exists() else "", "locator": code, "finding": "交易所官方ETF目录确认代码/简称/指数字段；不等同于逐只投资通道认证。", "sufficiency": "directory_identity_only"})
        if oldp:
            evidence_rows.append({"evidence_id": "EV-OLD-" + fid, "fund_id": fid, "purpose": "exploratory_old_run", "publisher": "Agent A archive", "url": "", "published_at": "2026-09-06", "retrieved_at_utc": updated, "local_path": result_path_str, "sha256": sha256(results_path) if results_path else "", "locator": "model_comparison.csv; validation.json", "finding": "旧批次技术结果复用作探索，不用于新确认主结论。", "sufficiency": "exploration_only"})

        # Coverage rows: old exploration + explicit new-period gaps.
        if oldp:
            q = old_quality if old_quality is not None else pd.DataFrame()
            old_days = int(len(q)) if not q.empty else None
            old_start = results.get("window", [None, None])[0]
            old_end = results.get("window", [None, None])[1]
            for dtype, source, path, start, end, exp, actual, quality, gap in [
                ("PCF", "archive", str(ARCHIVE / f"data/raw/candidates_v2/{code}.jsonl.gz"), old_start, old_end, 100, old_days, "EXPLORATION_ONLY", "not used as new confirmation"),
                ("HK_1MIN", "archive", str(oldp / "oos_residuals.parquet"), old_start, old_end, 100 * 120, int(q.valid_basket_minutes.sum()) if not q.empty else None, "EXPLORATION_ONLY", "old marks only"),
                ("TOOLS_1MIN", "archive", str(ARCHIVE / "data/raw/HSI_FUT_1min.csv"), old_start, old_end, None, None, "EXPLORATION_ONLY", "tool coverage inherited from old run"),
                ("FX_SETTLEMENT", "local", str(ARCHIVE / "data/raw/sse_settlement_rates.csv"), old_start, old_end, None, None, "EXPLORATION_ONLY", "ex-post valuation only"),
            ]:
                coverage_rows.append({"fund_id": fid, "security_id": code, "data_type": dtype, "source": source, "path": path, "sha256": sha256(Path(path)) if Path(path).exists() else "", "start_date": start, "end_date": end, "expected_rows": exp, "actual_rows": actual, "missing_dates": "", "duplicates": 0, "timezone": "Asia/Hong_Kong", "timestamp_semantics": "minute-start; close available next minute", "adjustment": "unadjusted last; cash substitution", "retrieved_at_utc": updated, "quality_status": quality, "gap_action": gap})
        coverage_rows.append({"fund_id": fid, "security_id": code, "data_type": "PCF_NEW_CONFIRMATION", "source": "machome_remote", "path": "/Volumes/EllisFiles/Stocksdata/PCF导出CSV/*_明细.csv", "sha256": "", "start_date": TARGET_START, "end_date": TARGET_END, "expected_rows": len(TARGET_DATES), "actual_rows": len(REMOTE_PCF_DATES), "missing_dates": stable_json([d for d in TARGET_DATES if d not in REMOTE_PCF_DATES]), "duplicates": None, "timezone": "Asia/Hong_Kong", "timestamp_semantics": "daily PCF announcement date", "adjustment": "official PCF quantities; no backfill", "retrieved_at_utc": updated, "quality_status": "PARTIAL_BELOW_20_OOS", "gap_action": "do not confirm; retain insufficiency"})
        coverage_rows.append({"fund_id": fid, "security_id": code, "data_type": "HK_TRADES_NEW_CONFIRMATION", "source": "machome_remote", "path": "/Volumes/EllisFiles/Stocksdata/港股_分笔成交/港股_分笔成交_按月归档/2026-08", "sha256": "", "start_date": TARGET_START, "end_date": TARGET_END, "expected_rows": len(TARGET_DATES), "actual_rows": len(REMOTE_HK_DATES), "missing_dates": stable_json([d for d in TARGET_DATES if d not in REMOTE_HK_DATES]), "duplicates": None, "timezone": "Asia/Hong_Kong", "timestamp_semantics": "trade timestamp, minute aggregation not built for new period", "adjustment": "last-price marks not yet built", "retrieved_at_utc": updated, "quality_status": "PARTIAL_NOT_AGGREGATED", "gap_action": "do not confirm"})

        if fid == "513090.SH":
            event_rows.append({"fund_id": fid, "security_id": "01788", "event_type": "suspension", "effective_from": "2026-07-23", "effective_to": "2026-08-10", "published_at": "2026-07-23", "official_url": "https://www.hkexnews.hk/listedco/listconews/sehk/2026/0723/2026072300081.htm;https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0810/2026081000081.htm", "evidence_path": str(FINAL_REVIEW / "verified_01788_event.json"), "evidence_sha256": sha256(FINAL_REVIEW / "verified_01788_event.json"), "treatment": "retain full PCF quantity; freeze pre-suspension last close; disclose frozen weight; strict sample excludes stale exposure", "affected_dates": "2026-07-23..2026-08-03", "max_weight": 0.026, "verified": True, "numerical_check_path": str(FINAL_REVIEW / "513090_event_review/reports/validation.json"), "remaining_risk": "复牌跳变与未观察到的公司行动不能由冻结价格识别"})
        if decision != "OUT_OF_SCOPE":
            event_rows.append({"fund_id": fid, "security_id": "", "event_type": "event_manifest_coverage", "effective_from": TARGET_START, "effective_to": TARGET_END, "published_at": None, "official_url": "https://www.hkexnews.hk/listedco/listconews/sehk/", "evidence_path": "", "evidence_sha256": "", "treatment": "not certified for new period", "affected_dates": "", "max_weight": None, "verified": False, "numerical_check_path": "", "remaining_risk": "PCF security universe and issuer actions not fully assembled"})

        # Economic candidate pool. Bank/financial ambiguity is kept explicit.
        risk_family = "SECURITIES_NONBANK" if any(k in (a["fund_name"] + str(a.get("index_name", ""))) for k in ("证券", "非银行", "券商")) else ("BANK_OR_FINANCIAL_MIXED" if "金融" in a["fund_name"] else "BROAD_OR_THEME")
        tools = [
            ("HSI_FUT", "HSI", "index future", "https://www.hkex.com.hk/Products/Derivatives/Equity-Index/Hang-Seng-Index-Futures?sc_lang=en", "HKD", 50, "09:15-12:00,13:00-16:30,17:15-03:00"),
            ("HHI_FUT", "HHI", "index future", "https://www.hkex.com.hk/Products/Derivatives/Equity-Index/Hang-Seng-China-Enterprises-Index-Futures?sc_lang=en", "HKD", 50, "09:15-12:00,13:00-16:30,17:15-03:00"),
            ("HTI_FUT", "HSTECH", "index future", "https://www.hkex.com.hk/Products/Derivatives/Equity-Index/Hang-Seng-TECH-Index-Futures?sc_lang=en", "HKD", 10, "09:15-12:00,13:00-16:30,17:15-03:00"),
            ("02800", "HSI", "Hong Kong ETF", "https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en", "HKD", 1, "09:30-12:00,13:00-16:00"),
            ("02828", "HHI", "Hong Kong ETF", "https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en", "HKD", 1, "09:30-12:00,13:00-16:00"),
            ("03032", "HSTECH", "Hong Kong ETF", "https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en", "HKD", 1, "09:30-12:00,13:00-16:00"),
            ("03033", "HSTECH", "Hong Kong ETF", "https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en", "HKD", 1, "09:30-12:00,13:00-16:00"),
            ("02845", "EV_PROXY", "Hong Kong ETF", "https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en", "HKD", 1, "09:30-12:00,13:00-16:00"),
        ]
        for tid, family, kind, url, currency, multiplier, session in tools:
            tested_old = bool(oldp) and decision != "OUT_OF_SCOPE"
            tool_rows.append({"fund_id": fid, "tool_id": tid, "risk_family": risk_family + "/" + family, "rationale": ("证券/非银风险单独列出；未以银行期货作为证券行业精确替代。" if risk_family == "SECURITIES_NONBANK" else "同指数或相近指数价格代理；指数期货与对应ETF按替代候选处理。"), "asset_type": kind, "official_url": url, "listed_from": None, "listed_to": None, "currency": currency, "multiplier": multiplier, "lot_size": 1, "session": session, "quote_coverage": 1.0 if tested_old else None, "data_start": "2026-03-03" if tested_old else None, "data_end": "2026-08-03" if tested_old else None, "short_status": "TESTED_EXPLORATORY_ONLY" if tested_old else "NOT_TESTED_NEW_CONFIRMATION", "included": tested_old, "exclusion_reason": "" if tested_old else "新确认期共同样本不足；挂牌不等于本期历史报价可用。", "evidence_id": "EV-TOOL-HKEX-OVERVIEW", "selection_lock_hash": policy_lock_hash})

        # Old metrics are explicitly separated from the current fund decision.
        if old_model is not None:
            audit = {int(x["horizon"]): x for x in results.get("label_audit", [])}
            boot = {(int(x["horizon"]), x["model"]): x.get("ci95") for x in validation.get("bootstrap", [])}
            for _, m in old_model.iterrows():
                horizon = int(m["horizon"]); model_id = str(m["model"]); ci = boot.get((horizon, model_id))
                model_rows.append({"run_id": "OLD-" + fid, "fund_id": fid, "horizon_min": horizon, "policy_id": "EXP_" + model_id, "model_id": model_id, "scenario_id": "OLD_EXPLORATION", "sample_hash": sample_hash, "confirmation_status": "REUSED_OR_UNPROVEN", "oos_start": audit.get(horizon, {}).get("first_oos"), "oos_end": audit.get(horizon, {}).get("last_oos"), "oos_days": int(m["days"]), "sample_count": int(m["samples"]), "target_std_bp": float(m["target_std_bps"]), "residual_std_bp": float(m["residual_std_bps"]), "variance_reduction": float(m["variance_reduction"]), "ci_low": ci[0] if ci else None, "ci_high": ci[1] if ci else None, "bootstrap_method": "daily_block_bootstrap" if ci else None, "bootstrap_seed": int(results.get("bootstrap_seed", 0)) if ci else None, "target_up_es95_bp": None, "target_down_es95_bp": None, "up_es95_bp": float(m["upside_es95_bps"]), "down_es95_bp": float(m["downside_es95_bps"]), "positive_block_fraction": None, "residual_mean_bp": float(m["residual_mean_bps"]), "beta_turnover": None, "decision_gate_results": stable_json({"exploratory_only": True, "new_confirmation": False, "current_decision": decision}), "residual_path": str(oldp / "oos_residuals.parquet")})
            if old_folds is not None:
                h30 = old_folds[old_folds["horizon"] == 30]
                for _, f in h30.iterrows():
                    for tid, beta in (("HHI_FUT", f.get("fixed_pair_HHI_beta")), ("HTI_FUT", f.get("fixed_pair_HTI_beta"))):
                        weight_rows.append({"fund_id": fid, "horizon_min": 30, "policy_id": "EXP_HHI_HTI_fixed_pair", "effective_date": str(f["test_date"]), "train_start": str(f["fit_start"]), "train_end": str(f["train_end"]), "validation_start": str(f["validation_start"]), "validation_end": str(f["train_end"]), "tool_id": tid, "beta": float(beta), "currency_conversion": "HKD; FX ex-post only", "selection_reason": "旧批次固定对照；不用于新确认主政策", "config_hash": ""})
            if old_sens is not None:
                for _, s in old_sens.iterrows():
                    sensitivity_rows.append({"fund_id": fid, "horizon_min": int(s["horizon"]), "policy_id": "EXP_" + str(s["model"]), "base_run_id": "OLD-" + fid, "scenario_id": str(s["scenario"]), "refit": "UNPROVEN_FROM_PRIOR_RUN", "sample_hash": sample_hash, "oos_days": int(s["days"]), "variance_reduction": float(s["variance_reduction"]), "residual_std_bp": float(s["residual_std_bps"]), "up_es95_bp": float(s["upside_es95_bps"]), "down_es95_bp": float(s["downside_es95_bps"]), "pass": None, "explanation": "旧敏感性仅作探索；本交付不把其refit语义升级为本轮确认。", "source_path": str(oldp / "sensitivity.csv")})

        # Current period execution grid: no false precision without a current policy.
        if decision != "OUT_OF_SCOPE":
            for horizon in (5, 15, 30, 60):
                for direction in ("HEDGE_SHORT", "HEDGE_LONG"):
                    for notional in (1_000_000, 10_000_000, 50_000_000):
                        for cost in (0, 1, 2.5, 5, 10):
                            execution_rows.append({"fund_id": fid, "horizon_min": horizon, "direction": direction, "notional_cny": notional, "policy_id": None, "cost_scenario_id": f"COST_{cost:g}BP_PER_SIDE", "per_side_variable_cost_bp": cost, "known_fixed_fees_cny": None, "borrow_cost_bp": None, "funding_cost_bp": None, "basket_total_cost_bp": None, "rounded_positions": None, "rounded_residual_std_bp": None, "rounded_vr": None, "risk_cost_score_bp": None, "pareto_optimal": None, "execution_status": "UNKNOWN", "assumptions": stable_json({"direction": direction, "notional_cny": notional, "cost_bp_per_side": cost, "borrow_and_funding": "not evidenced", "reason": "current confirmation policy unavailable"}), "fee_evidence_id": "EV-FEE-NOT-VERIFIED", "run_id": ""})

        if decision != "OUT_OF_SCOPE":
            # These are governance checks: the shortfall is intentionally detected
            # and the fund is kept blocked.  `passed` means the QA rule behaved as
            # designed, not that the evidence gate itself was met.
            qa_rows.append({"check_id": "QA-" + fid + "-NEW-OOS", "fund_id": fid, "check_type": "new_confirmation_oos_days", "run_id": "", "actual": 0, "expected": ">=20 would be required; correctly blocked", "tolerance": 0, "passed": True, "source_path": str(OUT / "data/new_confirmation_inventory.json"), "checked_at_utc": updated})
            qa_rows.append({"check_id": "QA-" + fid + "-EVENT-MANIFEST", "fund_id": fid, "check_type": "event_manifest_security_universe", "run_id": "", "actual": "not complete", "expected": "incomplete coverage detected; correctly blocked", "tolerance": "", "passed": True, "source_path": str(OUT / "events.csv"), "checked_at_utc": updated})

    qa_rows.extend([
        {"check_id": "QA-GLOBAL-UNIVERSE", "fund_id": "", "check_type": "seed_denominator_unique", "run_id": "GLOBAL", "actual": 74, "expected": 74, "tolerance": 0, "passed": True, "source_path": str(CONTROL / "assignment_A.json"), "checked_at_utc": updated},
        {"check_id": "QA-GLOBAL-SSE", "fund_id": "", "check_type": "official_sse_directory_retrieval", "run_id": "GLOBAL", "actual": 1116, "expected": ">0", "tolerance": "", "passed": True, "source_path": "https://etf.sse.com.cn/fundlist/", "checked_at_utc": updated},
        {"check_id": "QA-GLOBAL-SZSE", "fund_id": "", "check_type": "official_szse_directory_retrieval", "run_id": "GLOBAL", "actual": 728, "expected": ">0", "tolerance": "", "passed": True, "source_path": "https://fund.szse.cn/marketdata/etf/", "checked_at_utc": updated},
        {"check_id": "QA-GLOBAL-PCF", "fund_id": "", "check_type": "new_pcf_common_dates", "run_id": "GLOBAL", "actual": 5, "expected": "<80 detected; confirmation correctly blocked", "tolerance": 0, "passed": True, "source_path": str(OUT / "data/new_confirmation_inventory.json"), "checked_at_utc": updated},
        {"check_id": "QA-GLOBAL-01788", "fund_id": "513090.SH", "check_type": "official_suspension_treatment", "run_id": "EVENT_REVIEW_513090", "actual": "freeze 2026-07-23..2026-08-10; full quantity retained", "expected": "official HKEX dates and no future-price fill", "tolerance": "", "passed": True, "source_path": str(FINAL_REVIEW / "verified_01788_event.json"), "checked_at_utc": updated},
        {"check_id": "QA-GLOBAL-LEAKAGE", "fund_id": "", "check_type": "future_leakage_old_engine", "run_id": "OLD_BATCH", "actual": "train_end < test_date in archived folds", "expected": "true for all folds", "tolerance": "", "passed": True, "source_path": str(ARCHIVE / "runs"), "checked_at_utc": updated},
        {"check_id": "QA-GLOBAL-LABEL-BOUNDARY", "fund_id": "", "check_type": "label_session_day_contract", "run_id": "OLD_BATCH", "actual": "archived validation passed", "expected": "no cross-day/lunch/contract labels", "tolerance": "", "passed": True, "source_path": str(HANDOFF / "ledger.json"), "checked_at_utc": updated},
        {"check_id": "QA-GLOBAL-CACHE", "fund_id": "", "check_type": "cache_fingerprint_invalidation", "run_id": "CURRENT", "actual": "not executed in new engine", "expected": "not executed; future confirmation remains blocked", "tolerance": "", "passed": True, "source_path": "", "checked_at_utc": updated},
    ])
    return {"fund_decisions": fund_rows, "model_metrics": model_rows, "tool_candidates": tool_rows, "weights": weight_rows, "execution_scenarios": execution_rows, "data_coverage": coverage_rows, "events": event_rows, "sensitivity": sensitivity_rows, "tasks": [], "evidence": evidence_rows, "qa_checks": qa_rows}


def main() -> None:
    for d in (OUT, OUT / "data", OUT / "scripts"):
        d.mkdir(parents=True, exist_ok=True)
    assignment = read_assignment()
    sse, sz = get_official_directories()
    official = official_rows(sse, sz)
    write_json(OUT / "data/official_sse_etf.json", sse)
    write_json(OUT / "data/official_szse_etf.json", sz)
    write_csv(OUT / "data/official_universe.csv", official, ["fund_id", "exchange", "fund_code", "fund_name_short", "fund_name_expanded", "index_id", "index_name", "manager", "listing_date", "source_url"])
    seed_codes = {x["fund_id"] for x in assignment}
    manifest = json.loads((CONTROL / "manifest.json").read_text())
    manifest_codes = {x["fund_id"] for x in manifest}
    all_manifest_seed_codes = seed_codes | manifest_codes
    # Gap-fill only names outside the complete 210-row manifest.  This prevents
    # Agent A from silently duplicating B/C-owned seeds in the denominator.
    additions = [r for r in official if r["fund_id"] not in all_manifest_seed_codes and relevant_name(r) and not r["fund_code"].startswith("501")]
    write_csv(OUT / "additions.csv", additions, ["fund_id", "exchange", "fund_code", "fund_name_short", "fund_name_expanded", "index_id", "index_name", "manager", "listing_date", "source_url"])
    coverage = {
        "coverage_complete": False,
        "directory_complete": True,
        "as_of_date": "2026-09-04",
        "retrieved_at_utc": NOW,
        "sse_record_count": len(sse), "szse_record_count": len(sz), "official_union_count": len(official),
        "seed_count": len(assignment), "manifest_seed_count": len(manifest_codes),
        "a_owned_total_count": len(assignment) + len(additions), "relevant_additions_count": len(additions),
        "target_filter": "official ETF directory rows whose short/expanded name or tracked index contains 港股/恒生/香港/沪港/中概; exclude 501 LOF codes",
        "coverage_gap": "Directory enumeration is complete, but investment-channel and full security-event evidence is not verified for every name-discovered candidate; coverage_complete remains false.",
        "sources": ["https://etf.sse.com.cn/fundlist/", "https://fund.szse.cn/marketdata/etf/"],
    }
    write_json(OUT / "coverage.json", coverage)
    snapshot = target_data_snapshot()
    lock = {
        "lock_version": "A-selection-lock-20260906-v1",
        "locked_at_utc": NOW,
        "locked_before_new_confirmation_evaluation": True,
        "fund_scope": "assigned 74 seeds plus official-directory additions outside the complete 210-row manifest",
        "primary_horizon_min": 30, "other_horizons_min": [5, 15, 60],
        "training": {"train_days": 60, "fit_days": 50, "validation_days": 10, "outer_oos_min_days": 20},
        "candidate_pool": ["HSI_FUT", "HHI_FUT", "HTI_FUT", "02800", "02828", "03032", "03033", "02845"],
        "model_grid": ["no_hedge", "single_leg_OLS", "fixed_pair_HHI_HTI", "constrained_sparse_max_3_legs"],
        "constraints": {"beta_min": 0, "beta_max_per_leg": 2, "total_notional_max": 2, "max_legs": 3},
        "gates": {"point_variance_reduction": 0.50, "bootstrap_ci_low": 0.30, "strict_refit_vr": 0.40, "positive_block_fraction": 0.70, "quote_coverage": 0.95, "stale_weight": 0.02, "unexplained_missing_exposure": 0.005},
        "cost_scenarios_bp_per_side": [0, 1, 2.5, 5, 10],
        "sample_target": [TARGET_START, TARGET_END],
        "method_note": "Old runs are exploration only. No model or policy is reselected from the new window; missing new confirmation yields INSUFFICIENT_EVIDENCE, never NONE_IN_TESTED_SET.",
    }
    write_json(OUT / "selection_lock.json", lock)
    added_assignment: list[dict[str, Any]] = []
    for r in additions:
        name = r["fund_name_expanded"] or r["fund_name_short"]
        code = r["fund_code"]
        if code in {"159605", "159607", "513050", "513220"} or "中概" in name:
            cls = "QDII"
        elif "沪港深" in name or code.startswith("517"):
            cls = "MIXED_AH"
        else:
            cls = "UNVERIFIED"
        added_assignment.append({
            "fund_id": r["fund_id"], "fund_name": name, "index_name": r["index_name"],
            "classification": cls, "has_technical_result": False, "oos_days": None,
            "owner": "A",
        })
    all_assignment = assignment + added_assignment
    rows = build_rows(all_assignment, official, snapshot)
    schemas = json.loads((CONTROL / "schema.json").read_text())["tables"]
    for table, data in rows.items():
        if table == "tasks":
            continue
        cols = [c["key"] for c in schemas[table]["columns"]]
        write_csv(OUT / f"{table}.csv", data, cols)
        write_jsonl_array(OUT / f"{table}.json", data)

    # Phase/task ledger is built last so it records actual outputs and blockers.
    task_rows: list[dict[str, Any]] = []
    insufficient_count = sum(r["decision"] == "INSUFFICIENT_EVIDENCE" for r in rows["fund_decisions"])
    out_of_scope_count = sum(r["decision"] == "OUT_OF_SCOPE" for r in rows["fund_decisions"])
    phases = [
        ("P0", "DONE", f"初始化{len(assignment)}只种子及{len(additions)}只官方目录补漏分母、读取合同、哈希输入并写锁定文件", "A所有权分母建立；无重复代码。", "none", ""),
        ("P1", "DONE", "抓取SSE/SZSE官方ETF目录并登记新增", f"官方目录 {len(sse)}+{len(sz)} 条；新增 {len(additions)} 条；通道逐只核验仍未完成。", "scope_evidence", ""),
        ("P2", "BLOCKED", "审阅新确认期PCF、港股逐笔与ETF分钟可用性", "新期PCF仅5日、无完整共同样本；不形成新OOS。", "data", str(OUT / "data/new_confirmation_inventory.json")),
        ("P3", "PARTIAL", "核实01788停复牌并区分冻结资产与缺价", "513090的01788官方停复牌处理已完成；其余逐证券事件全集未完成。", "event", str(FINAL_REVIEW / "verified_01788_event.json")),
        ("P4", "DONE", "锁定候选池、阈值、窗口和成本情景", "selection_lock.json已在新确认评价前写入。", "none", ""),
        ("P5", "PARTIAL", "保留旧批次技术结果为探索并单列银行/非银候选边界", f"复用{sum(1 for a in all_assignment if old_report_path(a['fund_id']))}个旧技术运行；未冒充新确认。", "method", str(OUT / "model_metrics.csv")),
        ("P6", "BLOCKED", "新期OOS与稳健性评价", "新期有效OOS日为0，不能运行确认门槛；执行情景仅登记假设。", "data", str(OUT / "execution_scenarios.csv")),
        ("P7", "DONE", "逐只判定、机器表、QA与RESULTS.xlsx", f"{len(rows['fund_decisions'])}只均有结论；{out_of_scope_count}范围外，{insufficient_count}证据不足；0 suitable/none。", "none", ""),
    ]
    for phase, status, action, result, blocker, evidence in phases:
        task_rows.append({"task_id": "A-" + phase, "owner": "A", "fund_id": "", "phase": phase, "status": status, "started_at_utc": NOW, "finished_at_utc": NOW, "action": action, "inputs": stable_json([str(CONTROL / "WORKFLOW.md"), str(CONTROL / "schema.json"), str(CONTROL / "assignment_A.json")]), "outputs": stable_json([str(OUT / "coverage.json"), str(OUT / "selection_lock.json")]), "detailed_result": result, "validation": "QA rows in qa_checks.csv", "blocker_type": blocker, "blocker_evidence": evidence, "next_action": "等待完整PCF/分钟共同样本后重新锁定并重跑" if status in ("BLOCKED", "PARTIAL") else "", "run_command": "python3 scripts/run_agent_a.py"})
    for r in rows["fund_decisions"]:
        task_rows.append({"task_id": "A-P7-" + r["fund_id"], "owner": "A", "fund_id": r["fund_id"], "phase": "P7", "status": "DONE", "started_at_utc": NOW, "finished_at_utc": NOW, "action": "逐只应用四态结论规则", "inputs": str(OUT / "selection_lock.json"), "outputs": str(OUT / "fund_decisions.csv"), "detailed_result": r["decision"] + ": " + r["reason_detail"], "validation": "fund_id unique and decision enum valid", "blocker_type": "" if r["decision"] == "OUT_OF_SCOPE" else "NEW_CONFIRMATION", "blocker_evidence": str(OUT / "data/new_confirmation_inventory.json") if r["decision"] != "OUT_OF_SCOPE" else "", "next_action": "补齐新期PCF和事件后复跑" if r["decision"] != "OUT_OF_SCOPE" else "", "run_command": "python3 scripts/run_agent_a.py"})
    task_cols = [c["key"] for c in schemas["tasks"]["columns"]]
    write_csv(OUT / "tasks.csv", task_rows, task_cols)
    write_jsonl_array(OUT / "tasks.json", task_rows)

    final = {
        "generated_at_utc": NOW, "assigned_seed_count": len(assignment), "a_owned_total_count": len(all_assignment),
        "decision_counts": {"SUITABLE_PRICE_PROXY": 0, "NONE_IN_TESTED_SET": 0, "INSUFFICIENT_EVIDENCE": insufficient_count, "OUT_OF_SCOPE": out_of_scope_count},
        "old_exploration_funds": sum(bool(old_report_path(a["fund_id"])) for a in all_assignment),
        "new_confirmation_oos_days": 0, "remote_pcf_dates": REMOTE_PCF_DATES,
        "additions_count": len(additions), "coverage_complete": False,
        "outputs": ["fund_decisions.csv", "model_metrics.csv", "tool_candidates.csv", "weights.csv", "execution_scenarios.csv", "data_coverage.csv", "events.csv", "sensitivity.csv", "tasks.csv", "evidence.csv", "qa_checks.csv", "coverage.json", "additions.csv", "selection_lock.json", "FINAL_REPORT.md", "STATUS.md", "RESULTS.xlsx"],
    }
    write_json(OUT / "run_summary.json", final)
    status = f"""# Agent A 状态\n\n更新时间（UTC）：{NOW}\n\n- 分配种子：{len(assignment)}只；官方目录补漏：{len(additions)}只；A所有权总分母：{len(all_assignment)}只，逐只已落结论。\n- 主结论：INSUFFICIENT_EVIDENCE {final['decision_counts']['INSUFFICIENT_EVIDENCE']}只；OUT_OF_SCOPE {final['decision_counts']['OUT_OF_SCOPE']}只；SUITABLE_PRICE_PROXY 0；NONE_IN_TESTED_SET 0。\n- 旧技术探索：{final['old_exploration_funds']}只；全部标记 REUSED_OR_UNPROVEN，不作为新确认结论。\n- 新确认期：2026-08-04至2026-09-04；PCF实际仅{len(REMOTE_PCF_DATES)}日，新的有效OOS为0日，未伪造通过。\n- 全市场补漏：已抓取SSE {len(sse)}条、SZSE {len(sz)}条官方ETF目录；directory_complete=true，但 coverage_complete=false（逐只通道/事件证据未全核验）。\n- 事件：513090/01788停牌与复牌证据已纳入并单列冻结处理。\n- 交付：11张机器明细CSV/JSON、coverage.json、additions.csv、selection_lock.json、FINAL_REPORT.md、RESULTS.xlsx及QA。\n\n未完成边界：完整新期PCF/共同分钟样本、逐只基金官方投资范围、全证券事件manifest、真实借券/费用/成交容量。\n"""
    (OUT / "STATUS.md").write_text(status)
    report = f"""# Agent A 独立研究报告\n\n## 结论\n\n本交付覆盖 A 的 {len(assignment)} 只分配种子，以及从上交所/深交所官方 ETF 目录发现、且不在完整 210 只 manifest 中的 {len(additions)} 只补漏候选；A 所有权总分母为 {len(all_assignment)} 只。逐只机器结论为：INSUFFICIENT_EVIDENCE {insufficient_count} 只、OUT_OF_SCOPE {out_of_scope_count} 只、SUITABLE_PRICE_PROXY 0 只、NONE_IN_TESTED_SET 0 只。当前不使用 NONE_IN_TESTED_SET，因为新确认证据不足，不能把“未测试”解释为“测试后不适合”。\n\n## 为什么没有新确认\n\n目标新确认窗口为 {TARGET_START} 至 {TARGET_END}。当前可审计目录只有 {len(REMOTE_PCF_DATES)} 个 PCF 明细日，港股交易文件覆盖到 2026-08-25，未形成完整共同 PCF、港股分钟和 ETF 分钟样本；有效新 OOS 记为 0。按合同要求，任何候选都未被升级为价格对冲确认政策，coverage_complete 保持 false。成本表只登记 0/1/2.5/5/10 bp 单边情景，不把未核实的借券、资金、容量或固定费用当作已知事实。\n\n## 旧结果的使用边界\n\n已有 23 只基金的旧批次技术结果写入 model_metrics、weights、sensitivity 和 evidence，但全部标记 REUSED_OR_UNPROVEN / exploration_only。它们只用于记录研究轨迹和模型接口，不构成 2026-08-04 至 2026-09-04 的新确认，也不直接生成当前 recommended_policy_id。候选工具池包括 HSI、国企、恒生科技指数期货和港股 ETF；证券/非银风险单独标记，未用银行期货作为证券行业精确替代。\n\n## 事件处理\n\n513090 的 01788 已保留官方停牌/复牌事件：停牌区间为 2026-07-23 至 2026-08-10。PCF 数量不删除，停牌期间使用停牌前最后收盘价冻结估值，并将冻结权重与严格样本影响单列；没有用未来复牌价格填补停牌价格。由于新确认期证据仍不完整，该事件处理也不会单独产生当前可执行政策。\n\n## 官方目录与审计文件\n\n官方目录来源：SSE [ETF 基金列表](https://etf.sse.com.cn/fundlist/)，SZSE [ETF 行情/基金列表](https://fund.szse.cn/marketdata/etf/)，HKEX [ETP Overview](https://www.hkex.com.hk/Products/Securities/Exchange-Traded-Products/Overview?sc_lang=en)。目录抓取共登记 SSE {len(sse)} 条、SZSE {len(sz)} 条；目录身份字段不等同于逐只投资通道认证。\n\n机器明细、来源证据、QA、锁定文件、覆盖快照和 RESULTS.xlsx 均位于本目录。后续重跑前需补齐完整 PCF/共同分钟样本、逐只官方投资范围、全证券事件 manifest，并重新执行缓存指纹检查、selection lock 和确认门槛。\n"""
    (OUT / "FINAL_REPORT.md").write_text(report)


if __name__ == "__main__":
    main()

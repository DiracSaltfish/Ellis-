"""Reproducible 197-ETF PCF valuation validation.

The script deliberately keeps source dates and missingness explicit. It uses
the T-day estimated cash component, unadjusted T-day constituent closes, and
the fixed SAFE HKD/CNY midpoint specified by WORKFLOW.md. It never uses T+1
final cash to construct the primary T-day estimate.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path

getcontext().prec = 40
ROOT = Path(__file__).resolve().parents[1]
TARGET_ISO = "2026-09-08"
TARGET = "20260908"
HISTORY_DATES = ["2026-09-07", "2026-09-04", "2026-09-03", "2026-09-02"]
ALL_DATES = [TARGET_ISO] + HISTORY_DATES
FX_HKD_CNY = Decimal("0.86482")
PUBLISHED_NAV_ROUNDING = Decimal("0.00005")

RAW_NAV = ROOT / "raw" / "nav"
RAW_HK_DAILY = ROOT / "raw" / "hk_daily"
RAW_HK_MINUTE = ROOT / "raw" / "hk"
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)


def dec(value: object, default: Decimal | None = None) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return default
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return default


def json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(type(value).__name__)


def iso_from_compact(value: str | None) -> str | None:
    if value and re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def scalar_children(root: ET.Element) -> dict[str, str]:
    return {n.tag.split("}")[-1]: (n.text or "").strip() for n in root if len(n) == 0}


def read_pcf(path: Path, market: str, requested_date: str) -> dict[str, object]:
    root = ET.parse(path).getroot()
    top = scalar_children(root)
    if market == "SZ":
        code = top.get("SecurityID", "")
        day = top.get("TradingDay", "")
        unit = dec(top.get("CreationRedemptionUnit"))
        estimate_cash = dec(top.get("EstimateCashComponent"))
        previous_cash = dec(top.get("CashComponent"))
        nav_per_unit = dec(top.get("NAVperCU"))
        nav_field = dec(top.get("NAV"))
        components: list[dict[str, object]] = []
        for node in root.iter():
            if node.tag.split("}")[-1] != "Component":
                continue
            raw = {n.tag.split("}")[-1]: (n.text or "").strip() for n in node}
            components.append(
                {
                    "code": (raw.get("UnderlyingSecurityID") or "").zfill(5),
                    "name": raw.get("UnderlyingSymbol", ""),
                    "quantity": dec(raw.get("ComponentShare"), Decimal(0)),
                    "flag": raw.get("SubstituteFlag", ""),
                    "market_source": raw.get("UnderlyingSecurityIDSource", ""),
                    "fixed_cash_cny": dec(raw.get("CreationCashSubstitute"), Decimal(0)),
                    "redemption_fixed_cash_cny": dec(raw.get("RedemptionCashSubstitute"), Decimal(0)),
                    "raw": raw,
                }
            )
        schema = "SZSE_PCF_XML"
        name = top.get("Symbol", "")
        manager = top.get("FundManagementCompany", "")
        cash_component_key = "159900"
    else:
        code = top.get("FundInstrumentID", "")
        day = top.get("TradingDay", "")
        unit = dec(top.get("CreationRedemptionUnit"))
        estimate_cash = dec(top.get("EstimatedCashComponent"))
        previous_cash = dec(top.get("PreCashComponent"))
        nav_per_unit = dec(top.get("NAVperCU"))
        nav_field = dec(top.get("NAV"))
        components = []
        for node in root.iter():
            if node.tag.split("}")[-1] != "Component":
                continue
            raw = {n.tag.split("}")[-1]: (n.text or "").strip() for n in node}
            components.append(
                {
                    "code": (raw.get("InstrumentID") or "").zfill(5),
                    "name": raw.get("InstrumentName", ""),
                    "quantity": dec(raw.get("Quantity"), Decimal(0)),
                    "flag": raw.get("SubstitutionFlag", ""),
                    "market_source": raw.get("UnderlyingSecurityID", ""),
                    "fixed_cash_cny": dec(raw.get("SubstitutionCashAmount"), Decimal(0)),
                    "redemption_fixed_cash_cny": dec(raw.get("SubstitutionCashAmount"), Decimal(0)),
                    "raw": raw,
                }
            )
        schema = "SSE_PCF_XML"
        name = ""
        manager = ""
        cash_component_key = None
    return {
        "path": str(path.relative_to(ROOT)),
        "market": market,
        "schema": schema,
        "fund_code": code,
        "trading_day": iso_from_compact(day),
        "previous_trading_day": iso_from_compact(top.get("PreTradingDay")),
        "requested_date": requested_date,
        "identity_ok": code == path.name.split("_")[0] and day == requested_date.replace("-", ""),
        "fund_name_from_pcf": name,
        "manager_from_pcf": manager,
        "creation_unit": unit,
        "estimate_cash_cny": estimate_cash,
        "previous_cash_cny": previous_cash,
        "pcf_nav_per_unit_cny": nav_per_unit,
        "pcf_nav_field": nav_field,
        "component_count": len(components),
        "cash_placeholder_code": cash_component_key,
        "components": components,
        "top_raw": top,
    }


def load_navs() -> dict[str, dict[str, Decimal]]:
    navs: dict[str, dict[str, Decimal]] = {}
    for path in sorted(RAW_NAV.glob("*.json")):
        code = path.stem
        doc = json.loads(path.read_text())
        rows = (doc.get("Data") or {}).get("LSJZList") or []
        navs[code] = {}
        for row in rows:
            value = dec(row.get("DWJZ"))
            if row.get("FSRQ") and value is not None:
                navs[code][row["FSRQ"]] = value
    return navs


def load_daily(code: str) -> dict[str, object]:
    path = RAW_HK_DAILY / f"hk{code}_day.json"
    if not path.exists():
        return {"path": str(path.relative_to(ROOT)), "rows": [], "qt": []}
    doc = json.loads(path.read_text())
    series = (doc.get("data") or {}).get(f"hk{code}", {})
    rows: list[tuple[str, Decimal]] = []
    for row in series.get("day") or []:
        if isinstance(row, list) and len(row) >= 3 and dec(row[2]) is not None:
            rows.append((str(row[0]), dec(row[2])))
    qt = series.get("qt", {}).get(f"hk{code}") or []
    return {"path": str(path.relative_to(ROOT)), "rows": rows, "qt": qt}


def price_for_date(code: str, target_date: str, daily_cache: dict[str, dict[str, object]]) -> dict[str, object]:
    info = daily_cache.setdefault(code, load_daily(code))
    rows: list[tuple[str, Decimal]] = info["rows"]  # type: ignore[assignment]
    exact = [value for date, value in rows if date == target_date]
    if exact:
        return {"price_hkd": exact[0], "price_date": target_date, "quality": "exact_close", "source_path": info["path"]}
    prior = [(date, value) for date, value in rows if date <= target_date]
    if prior:
        date, value = prior[-1]
        return {"price_hkd": value, "price_date": date, "quality": "stale_last_close", "source_path": info["path"]}
    qt: list[object] = info.get("qt", [])  # type: ignore[assignment]
    if len(qt) > 3 and dec(qt[3]) not in (None, Decimal(0)):
        return {"price_hkd": dec(qt[3]), "price_date": None, "quality": "quote_fallback", "source_path": info["path"]}
    return {"price_hkd": None, "price_date": None, "quality": "missing", "source_path": info["path"]}


def round_interval_status(estimate: Decimal, published: Decimal) -> tuple[str, Decimal, Decimal]:
    direct_bp = (estimate / published - Decimal(1)) * Decimal(10000)
    lower_nav = published - PUBLISHED_NAV_ROUNDING
    upper_nav = published + PUBLISHED_NAV_ROUNDING
    possible = [
        (estimate / lower_nav - Decimal(1)) * Decimal(10000),
        (estimate / upper_nav - Decimal(1)) * Decimal(10000),
    ]
    min_abs = min(abs(value) for value in possible)
    max_abs = max(abs(value) for value in possible)
    if max_abs < Decimal(5):
        status = "DEFINITE_PASS"
    elif min_abs >= Decimal(5):
        status = "DEFINITE_FAIL"
    else:
        status = "BOUNDARY_ROUNDING"
    return status, direct_bp, max_abs


def estimate_pcf(pcf: dict[str, object], target_date: str, daily_cache: dict[str, dict[str, object]], contribution_sink: list[dict[str, object]] | None = None) -> dict[str, object]:
    stock_value = Decimal(0)
    fixed_cash = Decimal(0)
    priced_count = 0
    fixed_count = 0
    noncash_count = 0
    missing: list[str] = []
    stale: list[dict[str, object]] = []
    unsupported: list[dict[str, object]] = []
    amount_components: list[Decimal] = []
    valued_components: list[Decimal] = []
    for component in pcf["components"]:  # type: ignore[index]
        code = component["code"]
        quantity: Decimal = component["quantity"]
        flag = component["flag"]
        source = component["market_source"]
        fixed = component["fixed_cash_cny"] or Decimal(0)
        # SZ's synthetic 159900 row is already represented by T-day estimated cash.
        is_cash_placeholder = pcf["market"] == "SZ" and code == "159900"
        if is_cash_placeholder:
            if contribution_sink is not None:
                contribution_sink.append({"fund_code": pcf["fund_code"], "date": target_date, "component_code": code, "component_name": component["name"], "quantity": quantity, "flag": flag, "valuation_method": "excluded_cash_placeholder", "amount_cny": Decimal(0), "price_hkd": None, "price_date": None, "source_path": pcf["path"]})
            continue
        noncash_count += 1
        if source not in ("103",):
            unsupported.append({"code": code, "name": component["name"], "market_source": source, "quantity": quantity})
            if contribution_sink is not None:
                contribution_sink.append({"fund_code": pcf["fund_code"], "date": target_date, "component_code": code, "component_name": component["name"], "quantity": quantity, "flag": flag, "valuation_method": "unsupported_market", "amount_cny": None, "price_hkd": None, "price_date": None, "source_path": pcf["path"]})
            continue
        # Flag 2 means mandatory cash substitution: use the fixed cash amount,
        # not both the quote and the substitution amount.
        if flag == "2":
            fixed_cash += fixed
            fixed_count += 1
            amount_components.append(abs(fixed))
            valued_components.append(abs(fixed))
            if contribution_sink is not None:
                contribution_sink.append({"fund_code": pcf["fund_code"], "date": target_date, "component_code": code, "component_name": component["name"], "quantity": quantity, "flag": flag, "valuation_method": "mandatory_cash_substitute", "amount_cny": fixed, "price_hkd": None, "price_date": None, "source_path": pcf["path"]})
            continue
        quote = price_for_date(code, target_date, daily_cache)
        price = quote["price_hkd"]
        expected = quantity * price * FX_HKD_CNY if price is not None else None
        amount_components.append(abs(quantity * (price if price is not None else Decimal(0)) * FX_HKD_CNY))
        if price is None:
            missing.append(code)
            amount = None
        else:
            amount = expected
            stock_value += expected
            priced_count += 1
            if quote["quality"] != "exact_close":
                stale.append({"code": code, "price_date": quote["price_date"], "quality": quote["quality"]})
            valued_components.append(abs(expected))
        if contribution_sink is not None:
            contribution_sink.append({"fund_code": pcf["fund_code"], "date": target_date, "component_code": code, "component_name": component["name"], "quantity": quantity, "flag": flag, "valuation_method": "unadjusted_close_times_fixed_fx", "amount_cny": amount, "price_hkd": price, "price_date": quote["price_date"], "source_path": quote["source_path"]})
    estimate = None
    if pcf["creation_unit"] and pcf["estimate_cash_cny"] is not None and not missing and not unsupported:
        estimate = (stock_value + fixed_cash + pcf["estimate_cash_cny"]) / pcf["creation_unit"]  # type: ignore[operator]
    denominator = sum(amount_components, Decimal(0))
    coverage = (sum(valued_components, Decimal(0)) / denominator * Decimal(100)) if denominator else Decimal(100)
    return {
        "stock_value_cny": stock_value,
        "fixed_cash_cny": fixed_cash,
        "priced_component_count": priced_count,
        "fixed_cash_component_count": fixed_count,
        "noncash_component_count": noncash_count,
        "missing_components": sorted(set(missing)),
        "stale_components": stale,
        "unsupported_components": unsupported,
        "amount_coverage_pct": coverage,
        "estimated_nav": estimate,
        "estimate_cash_cny": pcf["estimate_cash_cny"],
        "creation_unit": pcf["creation_unit"],
    }


def source_url(pcf: dict[str, object]) -> str:
    if pcf["market"] == "SZ":
        return f"https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_{pcf['fund_code']}_{str(pcf['trading_day']).replace('-', '')}.xml"
    return f"https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode={pcf['fund_code']}"


def pcf_path(code: str, market: str, date: str) -> Path | None:
    if date == TARGET_ISO:
        path = ROOT / ("raw/pcf" if market == "SZ" else "raw/pcf_sse") / f"{code}_{TARGET}.xml"
    else:
        path = ROOT / "raw" / "pcf_history" / f"{code}_{date.replace('-', '')}.xml"
    return path if path.exists() else None


def get_fund_labels() -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {}
    info_path = ROOT / "fund_info_manifest.json"
    if info_path.exists():
        for row in json.loads(info_path.read_text()):
            labels[row["code"]] = {"fund_name": row.get("fund_name", ""), "manager": row.get("manager", "")}
    return labels


def build_normalized_pcf(pcf_records: dict[str, list[dict[str, object]]]) -> None:
    (OUT / "normalized_pcf.json").write_text(json.dumps(pcf_records, ensure_ascii=False, indent=2, default=json_default))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json_default(v) if isinstance(v, Decimal) else (json.dumps(v, ensure_ascii=False, default=json_default) if isinstance(v, (list, dict)) else v) for k, v in row.items()})


def minute_prices(code: str) -> tuple[dict[str, Decimal], str]:
    path = RAW_HK_MINUTE / f"hk{code}_minute.json"
    # The pre-existing 513090 control evidence contains the same Tencent minute
    # schema for its basket. Use it as a local fallback when the union fetch was
    # blocked, while preserving the exact source path in the output.
    source_path = path
    if not source_path.exists():
        baseline = ROOT / "baseline_513090" / f"tencent_hk{code}_minute.json"
        if baseline.exists():
            source_path = baseline
    if not source_path.exists():
        return {}, str(path.relative_to(ROOT))
    try:
        doc = json.loads(source_path.read_text())
        series = (doc.get("data") or {}).get(f"hk{code}", {}).get("data", {})
        result: dict[str, Decimal] = {}
        for text in series.get("data") or []:
            parts = str(text).split()
            if len(parts) >= 2 and dec(parts[1]) is not None:
                result[parts[0]] = dec(parts[1])
        return result, str(source_path.relative_to(ROOT))
    except Exception:
        return {}, str(source_path.relative_to(ROOT))


def build_intraday(target_pcfs: dict[str, dict[str, object]], daily_cache: dict[str, dict[str, object]], published_navs: dict[str, dict[str, Decimal]]) -> dict[str, object]:
    minute_cache: dict[str, dict[str, Decimal]] = {}
    minute_paths: dict[str, str] = {}
    fund_source_paths: dict[str, list[str]] = {}
    rows: list[dict[str, object]] = []
    eligible: list[str] = []
    for code, pcf in target_pcfs.items():
        if pcf["market"] not in ("SZ", "SH"):
            continue
        required: list[str] = []
        fixed = Decimal(0)
        unsupported = False
        for c in pcf["components"]:
            if pcf["market"] == "SZ" and c["code"] == "159900":
                continue
            if c["market_source"] != "103":
                unsupported = True
                continue
            if c["flag"] == "2":
                fixed += c["fixed_cash_cny"] or Decimal(0)
            elif c["quantity"]:
                required.append(c["code"])
        for x in required:
            if x not in minute_cache:
                minute_cache[x], minute_paths[x] = minute_prices(x)
        if unsupported or not required or any(not minute_cache[x] for x in required):
            continue
        for x in required:
            minute_paths[x] = minute_paths.get(x, str((RAW_HK_MINUTE / f"hk{x}_minute.json").relative_to(ROOT)))
        times = set(minute_cache[required[0]])
        for x in required[1:]:
            times &= set(minute_cache[x])
        if not times:
            continue
        eligible.append(code)
        minute_source_paths = sorted({minute_paths[x] for x in required})
        fund_source_paths[code] = minute_source_paths
        for time_label in sorted(times):
            stock = sum(c["quantity"] * minute_cache[c["code"]][time_label] * FX_HKD_CNY for c in pcf["components"] if c["market_source"] == "103" and c["flag"] != "2" and c["quantity"])
            estimate = (stock + fixed + pcf["estimate_cash_cny"]) / pcf["creation_unit"]
            rows.append({"fund_code": code, "fund_market": pcf["market"], "date": TARGET_ISO, "time": time_label, "estimated_nav": estimate, "stock_value_cny": stock, "fixed_cash_cny": fixed, "estimated_cash_cny": pcf["estimate_cash_cny"], "creation_unit": pcf["creation_unit"], "published_nav": published_navs.get(code, {}).get(TARGET_ISO), "minute_component_count": len(required), "minute_source_ref": f"minute:{code}", "minute_quote_source": "Tencent minute; exact common timestamp intersection"})
    write_csv(OUT / "intraday_iopv_20260908.csv", rows, ["fund_code", "fund_market", "date", "time", "estimated_nav", "stock_value_cny", "fixed_cash_cny", "estimated_cash_cny", "creation_unit", "published_nav", "minute_component_count", "minute_source_ref", "minute_quote_source"])
    return {"rows": len(rows), "eligible_funds": sorted(eligible), "eligible_count": len(eligible), "minute_source_count": len(minute_cache), "minute_source_paths": minute_paths, "fund_source_paths": fund_source_paths}


def main() -> None:
    symbols: list[str] = json.loads((ROOT / "inputs" / "symbols.json").read_text())
    labels = get_fund_labels()
    navs = load_navs()
    daily_cache: dict[str, dict[str, object]] = {}
    normalized: dict[str, list[dict[str, object]]] = {}
    target_pcfs: dict[str, dict[str, object]] = {}
    target_contributions: list[dict[str, object]] = []
    full_rows: list[dict[str, object]] = []
    multi_rows: list[dict[str, object]] = []
    status_counter: Counter[str] = Counter()
    for order, symbol in enumerate(symbols, 1):
        code, market = symbol.split(".")
        target_path = pcf_path(code, market, TARGET_ISO)
        label = labels.get(code, {})
        base = {"input_order": order, "symbol": symbol, "code": code, "market": market, "fund_name": label.get("fund_name", ""), "manager": label.get("manager", ""), "pcf_source": "", "pcf_path": "", "nav_source": f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=30&startDate=2026-08-28&endDate=2026-09-08", "nav_path": str((RAW_NAV / f"{code}.json").relative_to(ROOT)), "nav_date": TARGET_ISO, "published_nav": navs.get(code, {}).get(TARGET_ISO), "single_day_status": "BLOCKED_DATA", "single_day_reason": "", "multi_day_status": "NOT_RUN", "multi_day_sample_count": 0, "multi_day_max_abs_bp": None, "multi_day_dates": []}
        if target_path is None:
            base.update(single_day_reason="target-date PCF file missing")
            full_rows.append(base)
            status_counter[base["single_day_status"]] += 1
            continue
        pcf = read_pcf(target_path, market, TARGET_ISO)
        target_pcfs[code] = pcf
        normalized.setdefault(code, []).append(pcf)
        base.update(pcf_source=source_url(pcf), pcf_path=pcf["path"], pcf_date=pcf["trading_day"], fund_name=base["fund_name"] or pcf["fund_name_from_pcf"], manager=base["manager"] or pcf["manager_from_pcf"], asset_type="other_overseas" if any(c["market_source"] != "103" and not (market == "SZ" and c["code"] == "159900") for c in pcf["components"]) else "HK_equities", creation_unit=pcf["creation_unit"], estimate_cash_cny=pcf["estimate_cash_cny"], pcf_component_count=pcf["component_count"], pcf_identity_ok=pcf["identity_ok"])
        estimate = estimate_pcf(pcf, TARGET_ISO, daily_cache, target_contributions)
        base.update(estimate)
        published = base["published_nav"]
        if not pcf["identity_ok"]:
            base.update(single_day_reason="PCF fund-code/date identity check failed")
        elif published is None:
            base.update(single_day_reason="published unit NAV for target date missing")
        elif estimate["unsupported_components"]:
            base.update(single_day_reason="unsupported non-HK underlying asset; independent price/FX rule not established")
            base["single_day_status"] = "NOT_APPLICABLE"
        elif estimate["missing_components"]:
            base.update(single_day_reason="missing price for constituent(s): " + ",".join(estimate["missing_components"]))
        elif estimate["estimated_nav"] is not None:
            rounding_status, error_bp, max_rounding_abs = round_interval_status(estimate["estimated_nav"], published)
            direct_abs = abs(error_bp)
            base.update(estimated_nav=estimate["estimated_nav"], error_bp=error_bp, abs_error_bp=direct_abs, rounding_status=rounding_status, published_nav_rounding_interval_bp=f"±{max_rounding_abs}")
            if rounding_status == "DEFINITE_PASS":
                base["single_day_status"] = "PASS_SINGLE_DAY"
                base["single_day_reason"] = "independent T-day close valuation is strictly inside 5 bp after published-NAV rounding interval check"
            elif rounding_status == "BOUNDARY_ROUNDING":
                base["single_day_status"] = "BOUNDARY_ROUNDING"
                base["single_day_reason"] = "direct published-NAV comparison is near 5 bp; four-decimal NAV rounding interval overlaps threshold"
            else:
                base["single_day_status"] = "FAIL_5BP"
                base["single_day_reason"] = "independent T-day close valuation is not within strict 5 bp"
        else:
            base["single_day_reason"] = "calculation did not produce a complete estimate"
        if base["single_day_status"] == "PASS_SINGLE_DAY":
            if market == "SH":
                base.update(multi_day_status="SH_SINGLE_DAY_ONLY", multi_day_sample_count=1, multi_day_max_abs_bp=base.get("abs_error_bp"), multi_day_dates=[TARGET_ISO])
                multi_rows.append({"code": code, "date": TARGET_ISO, "status": "SH_SINGLE_DAY_ONLY", "error_bp": base.get("error_bp"), "estimated_nav": base.get("estimated_nav"), "published_nav": published, "pcf_path": pcf["path"]})
            else:
                sample_errors: list[Decimal] = []
                sample_dates: list[str] = []
                for date in ALL_DATES:
                    path = pcf_path(code, market, date)
                    if path is None:
                        multi_rows.append({"code": code, "date": date, "status": "PCF_MISSING", "pcf_path": ""})
                        continue
                    hist = pcf if date == TARGET_ISO else read_pcf(path, market, date)
                    normalized.setdefault(code, []).append(hist)
                    hist_contrib: list[dict[str, object]] = []
                    est = estimate_pcf(hist, date, daily_cache, hist_contrib)
                    hist_pub = navs.get(code, {}).get(date)
                    if est["estimated_nav"] is None or hist_pub is None:
                        multi_rows.append({"code": code, "date": date, "status": "CALCULATION_INCOMPLETE", "pcf_path": hist["path"], "published_nav": hist_pub})
                        continue
                    err = (est["estimated_nav"] / hist_pub - Decimal(1)) * Decimal(10000)
                    sample_errors.append(abs(err)); sample_dates.append(date)
                    multi_rows.append({"code": code, "date": date, "status": "CALCULATED", "estimated_nav": est["estimated_nav"], "published_nav": hist_pub, "error_bp": err, "abs_error_bp": abs(err), "pcf_path": hist["path"], "stale_components": est["stale_components"]})
                base["multi_day_sample_count"] = len(sample_errors)
                base["multi_day_max_abs_bp"] = max(sample_errors) if sample_errors else None
                base["multi_day_dates"] = sample_dates
                if len(sample_errors) == len(ALL_DATES):
                    base["multi_day_status"] = "MULTI_DAY_5D_PASS" if all(x < Decimal(5) for x in sample_errors) else "MULTI_DAY_5D_NOT_STABLE"
                elif sample_errors:
                    base["multi_day_status"] = "MULTI_DAY_PARTIAL"
                else:
                    base["multi_day_status"] = "MULTI_DAY_BLOCKED"
        status_counter[base["single_day_status"]] += 1
        full_rows.append(base)
    build_normalized_pcf(normalized)
    write_csv(OUT / "watchlist_validation.csv", full_rows, ["input_order", "symbol", "code", "market", "fund_name", "manager", "asset_type", "pcf_date", "pcf_source", "pcf_path", "pcf_identity_ok", "nav_date", "nav_source", "nav_path", "creation_unit", "estimate_cash_cny", "pcf_component_count", "noncash_component_count", "priced_component_count", "fixed_cash_component_count", "amount_coverage_pct", "stock_value_cny", "fixed_cash_cny", "missing_components", "stale_components", "unsupported_components", "estimated_nav", "published_nav", "error_bp", "abs_error_bp", "rounding_status", "published_nav_rounding_interval_bp", "single_day_status", "single_day_reason", "multi_day_status", "multi_day_sample_count", "multi_day_max_abs_bp", "multi_day_dates"])
    write_csv(OUT / "component_contributions_20260908.csv", target_contributions, ["fund_code", "date", "component_code", "component_name", "quantity", "flag", "valuation_method", "amount_cny", "price_hkd", "price_date", "source_path"])
    write_csv(OUT / "multi_day_validation.csv", multi_rows, ["code", "date", "status", "estimated_nav", "published_nav", "error_bp", "abs_error_bp", "pcf_path", "stale_components"])
    intraday_summary = build_intraday(target_pcfs, daily_cache, navs)
    # Source/hash inventory is intentionally metadata only; no raw response is duplicated here.
    manifests = ["prefetch_manifest.json", "pcf_identity_checks.json", "hk_prefetch_manifest.json", "sse_pcf_manifest.json", "sz_history_manifest.json", "hk_all_minute_manifest.json", "hk_daily_manifest.json", "fund_info_manifest.json", "baseline_513090/minute_iopv_20260908.csv", "baseline_513090/source_notes.md"]
    source_inventory = []
    for name in manifests:
        path = ROOT / name
        if path.exists():
            body = path.read_bytes()
            source_inventory.append({"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
    summary = {"target_date": TARGET_ISO, "input_count": len(symbols), "single_day_status_counts": dict(status_counter), "single_day_status_total": sum(status_counter.values()), "pass_for_multiday_count": sum(r.get("single_day_status") == "PASS_SINGLE_DAY" for r in full_rows), "multi_day_status_counts": dict(Counter(r.get("multi_day_status") for r in full_rows)), "pcf_target_count": len(target_pcfs), "hk_union_count": len(json.loads((ROOT / "inputs" / "hk_union_all_pcf.json").read_text())), "fixed_fx_hkd_cny": FX_HKD_CNY, "threshold_bp": Decimal(5), "strict_inequality": "abs(error_bp) < 5", "formula": "(sum(allowed-cash-substitute quantity × unadjusted close HKD × 0.86482) + mandatory fixed cash substitutes + T-day estimated cash) / creation unit", "excluded_cash_placeholder": "SZ 159900 申赎现金 rows excluded; their values are not added to T-day estimated cash", "t_plus_one_final_cash_used_for_primary_estimate": False, "intraday": intraday_summary, "source_inventory": source_inventory, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    (OUT / "validation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default))
    print(json.dumps(summary, ensure_ascii=False, default=json_default))


if __name__ == "__main__":
    main()

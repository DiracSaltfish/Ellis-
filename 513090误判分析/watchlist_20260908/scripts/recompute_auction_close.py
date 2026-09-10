"""Recompute 2026-09-08 watchlist validation at the common closing-auction minute.

The saved Tencent minute snapshots expose a common final label of 1608 on the
target date.  This script deliberately does not substitute the daily close or
fabricate a 1600 observation for funds without that minute evidence.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from collections import Counter
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_watchlist as vw  # noqa: E402


TARGET_TIME = "1608"
TARGET_TIME_DISPLAY = "16:08"
OUTPUT = ROOT / "outputs"
RESULT_PATH = OUTPUT / "watchlist_validation_auction_1608.csv"
SUMMARY_PATH = OUTPUT / "auction_close_summary.json"
CFETS_EVIDENCE_PATH = OUTPUT / "cfets_fx_20260908.json"
CFETS_HISTORY_ENDPOINT = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            converted: dict[str, object] = {}
            for key, value in row.items():
                converted[key] = "" if value is None else str(value)
            writer.writerow(converted)


def load_target_pcfs(symbols: list[str]) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for symbol in symbols:
        code, market = symbol.split(".")
        path = vw.pcf_path(code, market, vw.TARGET_ISO)
        if path is not None:
            records[code] = vw.read_pcf(path, market, vw.TARGET_ISO)
    return records


def fetch_cfets_hkd_cny(target_date: str) -> tuple[Decimal, str, dict[str, object]]:
    params = {
        "startDate": target_date,
        "endDate": target_date,
        "currency": "HKD/CNY",
        "pageNum": 1,
        "pageSize": 10,
    }
    url = CFETS_HISTORY_ENDPOINT + "?" + urlencode(params)
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.chinamoney.com.cn/chinese/bkccpr/index.html?tab=2",
        },
    )
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    head = payload.get("data", {}).get("head", [])
    records = payload.get("records", [])
    if "HKD/CNY" not in head or not records:
        raise RuntimeError("CFETS history response did not contain HKD/CNY")
    record = next((row for row in records if row.get("date") == target_date), None)
    if record is None:
        raise RuntimeError(f"CFETS history response did not contain {target_date}")
    values = record.get("values", [])
    # With a currency filter the endpoint keeps the full header but returns a
    # one-column values array; without the filter it returns the full row.
    if len(values) == 1 and payload.get("data", {}).get("currency") == "HKD/CNY":
        rate_value = values[0]
    else:
        rate_value = values[head.index("HKD/CNY")]
    rate = Decimal(str(rate_value))
    if rate <= 0:
        raise RuntimeError(f"invalid CFETS HKD/CNY rate: {rate}")
    return rate, url, payload


def required_minute_codes(pcf: dict[str, object]) -> tuple[list[str], Decimal, bool, str]:
    required: list[str] = []
    fixed = Decimal(0)
    unsupported = False
    unsupported_sources: set[str] = set()
    for component in pcf["components"]:  # type: ignore[index]
        if pcf["market"] == "SZ" and component["code"] == "159900":
            continue
        if component["market_source"] != "103":
            unsupported = True
            unsupported_sources.add(str(component["market_source"]))
        elif component["flag"] == "2":
            fixed += component["fixed_cash_cny"] or Decimal(0)
        elif component["quantity"]:
            required.append(component["code"])
    source_note = ",".join(sorted(unsupported_sources))
    return required, fixed, unsupported, source_note


def auction_availability(
    pcf: dict[str, object],
    minute_cache: dict[str, dict[str, Decimal]],
) -> tuple[bool, str, int]:
    if pcf["market"] not in ("SZ", "SH"):
        return False, "exchange_not_supported", 0
    required, _fixed, unsupported, source_note = required_minute_codes(pcf)
    if unsupported:
        return False, f"unsupported_market_source:{source_note}", len(required)
    if not required:
        return False, "no_priced_components", 0
    missing = [code for code in required if not minute_cache.get(code)]
    if missing:
        return False, "missing_minute_data:" + ",".join(sorted(missing)), len(required)
    common = set(minute_cache[required[0]])
    for code in required[1:]:
        common &= set(minute_cache[code])
    if TARGET_TIME not in common:
        return False, "common_minute_1608_missing", len(required)
    return True, "available", len(required)


def main() -> None:
    symbols = json.loads((ROOT / "inputs" / "symbols.json").read_text())
    old_rows = read_csv(OUTPUT / "watchlist_validation.csv")
    old_by_code = {row["code"]: row for row in old_rows}
    cfets_rate, cfets_url, cfets_payload = fetch_cfets_hkd_cny(vw.TARGET_ISO)
    original_rate = vw.FX_HKD_CNY
    vw.FX_HKD_CNY = cfets_rate
    navs = vw.load_navs()
    target_pcfs = load_target_pcfs(symbols)

    # Rebuild the minute IOPV source from the target-date PCFs and the saved
    # raw minute snapshots before selecting the common auction-close label.
    vw.build_intraday(target_pcfs, {}, navs)
    intraday_rows = read_csv(OUTPUT / "intraday_iopv_20260908.csv")
    auction_rows = {row["fund_code"]: row for row in intraday_rows if row.get("time") == TARGET_TIME}

    minute_cache: dict[str, dict[str, Decimal]] = {}
    for pcf in target_pcfs.values():
        required, _fixed, _unsupported, _source_note = required_minute_codes(pcf)
        for code in required:
            if code not in minute_cache:
                minute_cache[code], _source = vw.minute_prices(code)

    output_rows: list[dict[str, object]] = []
    status_counts: Counter[str] = Counter()
    old_status_counts: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    for old in old_rows:
        code = old["code"]
        pcf = target_pcfs.get(code)
        auction = auction_rows.get(code)
        if pcf is None:
            available, reason, component_count = False, "target_date_pcf_missing", 0
        else:
            available, reason, component_count = auction_availability(pcf, minute_cache)
        published = Decimal(auction["published_nav"]) if auction and auction.get("published_nav") else None
        estimate = Decimal(auction["estimated_nav"]) if auction and auction.get("estimated_nav") else None
        old_estimate = Decimal(old["estimated_nav"]) if old.get("estimated_nav") else None
        old_error = Decimal(old["error_bp"]) if old.get("error_bp") else None
        auction_status = "AUCTION_PRICE_UNAVAILABLE"
        auction_error = None
        delta = None
        if available and auction is not None and estimate is not None and published is not None:
            rounding_status, auction_error, _max_rounding_abs = vw.round_interval_status(estimate, published)
            auction_status = {
                "DEFINITE_PASS": "AUCTION_PASS",
                "DEFINITE_FAIL": "AUCTION_FAIL_5BP",
                "BOUNDARY_ROUNDING": "AUCTION_BOUNDARY_ROUNDING",
            }[rounding_status]
            delta = auction_error - old_error if old_error is not None else None
            status_counts[auction_status] += 1
        else:
            status_counts[auction_status] += 1
        old_status_counts[old["single_day_status"]] += 1
        transitions[f"{old['single_day_status']} -> {auction_status}"] += 1
        output_rows.append(
            {
                "input_order": old.get("input_order"),
                "symbol": old.get("symbol"),
                "code": code,
                "market": old.get("market"),
                "fund_name": old.get("fund_name"),
                "auction_close_time": TARGET_TIME_DISPLAY if available else None,
                "auction_close_time_label": TARGET_TIME if available else None,
                "auction_price_policy": "common final minute from saved 2026-09-08 Tencent minute data; CFETS HKD/CNY central parity",
                "minute_component_count": auction.get("minute_component_count") if auction else component_count,
                "fx_hkd_cny_cfets": cfets_rate,
                "published_nav": published,
                "auction_estimated_nav": estimate,
                "old_estimated_nav": old_estimate,
                "old_error_bp": old_error,
                "auction_error_bp": auction_error,
                "delta_error_bp": delta,
                "old_single_day_status": old["single_day_status"],
                "auction_close_status": auction_status,
                "auction_close_reason": reason,
                "minute_quote_source": auction.get("minute_quote_source") if auction else None,
            }
        )

    fields = [
        "input_order",
        "symbol",
        "code",
        "market",
        "fund_name",
        "auction_close_time",
        "auction_close_time_label",
        "auction_price_policy",
        "minute_component_count",
        "fx_hkd_cny_cfets",
        "published_nav",
        "auction_estimated_nav",
        "old_estimated_nav",
        "old_error_bp",
        "auction_error_bp",
        "delta_error_bp",
        "old_single_day_status",
        "auction_close_status",
        "auction_close_reason",
        "minute_quote_source",
    ]
    write_csv(RESULT_PATH, output_rows, fields)

    available_rows = [row for row in output_rows if row["auction_close_status"] != "AUCTION_PRICE_UNAVAILABLE"]
    remaining = [
        row
        for row in available_rows
        if row["auction_close_status"] in ("AUCTION_FAIL_5BP", "AUCTION_BOUNDARY_ROUNDING")
    ]
    remaining.sort(key=lambda row: abs(Decimal(str(row["auction_error_bp"]))), reverse=True)
    unavailable = [row for row in output_rows if row["auction_close_status"] == "AUCTION_PRICE_UNAVAILABLE"]
    unavailable_reason_counts = Counter(row["auction_close_reason"] for row in unavailable)
    summary = {
        "target_date": vw.TARGET_ISO,
        "auction_close_time": TARGET_TIME_DISPLAY,
        "auction_close_time_label": TARGET_TIME,
        "price_policy": "Use the exact common 16:08 minute label present in every required constituent snapshot; do not use daily close and do not fabricate 16:00.",
        "formula": f"(sum(PCF quantity × 16:08 constituent price HKD × {cfets_rate}) + mandatory fixed cash substitutes + T-day estimated cash) / creation unit",
        "cfets_hkd_cny": cfets_rate,
        "previous_fixed_hkd_cny": original_rate,
        "cfets_rate_matches_previous_fixed": cfets_rate == original_rate,
        "cfets_source_page": "https://www.chinamoney.com.cn/chinese/bkccpr/index.html?tab=2",
        "cfets_history_endpoint": cfets_url,
        "threshold_bp": 5,
        "strict_inequality": "abs(error_bp) < 5",
        "input_count": len(old_rows),
        "auction_price_available_count": len(available_rows),
        "auction_price_unavailable_count": len(unavailable),
        "auction_status_counts": dict(status_counts),
        "old_status_counts": dict(old_status_counts),
        "status_transitions": dict(transitions),
        "remaining_noncompliant_count": len(remaining),
        "remaining_noncompliant_codes": [row["code"] for row in remaining],
        "unavailable_reason_counts": dict(unavailable_reason_counts),
        "unavailable_codes": [row["code"] for row in unavailable],
        "source_csv": str((OUTPUT / "intraday_iopv_20260908.csv").relative_to(ROOT)),
        "result_csv": str(RESULT_PATH.relative_to(ROOT)),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    CFETS_EVIDENCE_PATH.write_text(
        json.dumps(
            {
                "target_date": vw.TARGET_ISO,
                "currency": "HKD/CNY",
                "rate": cfets_rate,
                "endpoint": cfets_url,
                "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "response": cfets_payload,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

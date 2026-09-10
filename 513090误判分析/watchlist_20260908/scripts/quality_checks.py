"""Independent quality gates for the 197-fund PCF validation outputs."""
from __future__ import annotations

import csv
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_watchlist import (  # noqa: E402
    ALL_DATES,
    FX_HKD_CNY,
    TARGET_ISO,
    TARGET,
    dec,
    read_pcf,
)


def load_json(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def compact(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(type(value).__name__)


def main() -> None:
    symbols = load_json("inputs/symbols.json")
    unique_symbols = set(symbols)
    checks: dict[str, object] = {
        "generated_for": TARGET_ISO,
        "input_count": len(symbols),
        "input_unique_count": len(unique_symbols),
        "input_duplicate_count": len(symbols) - len(unique_symbols),
        "fx_hkd_cny": FX_HKD_CNY,
        "history_policy": {
            "SZ": ALL_DATES,
            "SH": "target date only; historical PCF revalidation intentionally disabled",
        },
    }
    failures: list[str] = []
    if len(symbols) != 197:
        failures.append("input_count_not_197")
    if len(unique_symbols) != len(symbols):
        failures.append("duplicate_input_symbols")

    nav_dates: dict[str, set[str]] = {}
    for path in sorted((ROOT / "raw/nav").glob("*.json")):
        doc = json.loads(path.read_text())
        nav_dates[path.stem] = {
            row.get("FSRQ") for row in ((doc.get("Data") or {}).get("LSJZList") or []) if row.get("FSRQ")
        }
    target_nav_missing = [s for s in symbols if s.split(".")[0] not in nav_dates or TARGET_ISO not in nav_dates[s.split(".")[0]]]
    checks["target_nav"] = {
        "files": len(nav_dates),
        "covered": len(symbols) - len(target_nav_missing),
        "missing_symbols": target_nav_missing,
    }
    if target_nav_missing:
        failures.append("target_nav_missing")

    pcfs: dict[str, dict[str, object]] = {}
    pcf_parse_issues: list[dict[str, str]] = []
    component_stats = Counter()
    bad_component_rows: list[dict[str, object]] = []
    for symbol in symbols:
        code, market = symbol.split(".")
        directory = ROOT / ("raw/pcf" if market == "SZ" else "raw/pcf_sse")
        path = directory / f"{code}_{TARGET}.xml"
        if not path.exists():
            pcf_parse_issues.append({"symbol": symbol, "issue": "target_pcf_missing"})
            continue
        try:
            pcf = read_pcf(path, market, TARGET_ISO)
            pcfs[symbol] = pcf
            if not pcf["identity_ok"]:
                pcf_parse_issues.append({"symbol": symbol, "issue": "code_or_date_identity_failed"})
            if pcf["trading_day"] != TARGET_ISO:
                pcf_parse_issues.append({"symbol": symbol, "issue": "trading_day_not_target"})
            if not pcf["components"]:
                pcf_parse_issues.append({"symbol": symbol, "issue": "no_components"})
            for component in pcf["components"]:
                component_stats["total_components"] += 1
                flag = str(component["flag"])
                if flag == "2":
                    component_stats["mandatory_cash_substitute_rows"] += 1
                elif flag == "1":
                    component_stats["priced_rows"] += 1
                else:
                    component_stats["other_flag_rows"] += 1
                    bad_component_rows.append({"symbol": symbol, "component": component["code"], "flag": flag})
                quantity = component["quantity"]
                if quantity is None or quantity < 0:
                    bad_component_rows.append({"symbol": symbol, "component": component["code"], "issue": "quantity_invalid", "quantity": quantity})
                if market == "SZ" and component["code"] == "15900":
                    component_stats["placeholder_code_15900_rows"] += 1
                if market == "SZ" and component["code"] == "159900":
                    component_stats["cash_placeholder_159900_rows"] += 1
        except Exception as exc:  # keep all issues in the evidence file
            pcf_parse_issues.append({"symbol": symbol, "issue": f"parse_error:{type(exc).__name__}:{exc}"})
    checks["target_pcf"] = {
        "parsed": len(pcfs),
        "expected": len(symbols),
        "parse_issues": pcf_parse_issues,
        "component_stats": dict(component_stats),
        "bad_component_rows": bad_component_rows,
    }
    if len(pcfs) != len(symbols) or pcf_parse_issues or bad_component_rows:
        failures.append("target_pcf_quality_gate_failed")

    manifest_specs = {
        "SSE PCF": ("sse_pcf_manifest.json", "TARGET_DATE"),
        "HK minute": ("hk_all_minute_manifest.json", "TARGET_DATE"),
        "HK daily": ("hk_daily_manifest.json", "TARGET_DATE"),
        "SZ history": ("sz_history_manifest.json", "TARGET_DATE"),
        "fund info": ("fund_info_manifest.json", "PARSED"),
    }
    manifest_summary: dict[str, object] = {}
    for label, (filename, good_status) in manifest_specs.items():
        rows = load_json(filename)
        counts = Counter(row.get("status") for row in rows)
        manifest_summary[label] = {
            "rows": len(rows),
            "status_counts": dict(counts),
            "good_status": good_status,
        }
    checks["source_manifests"] = manifest_summary

    hk_union = load_json("inputs/hk_union_all_pcf.json")
    hk_union = [str(code).zfill(5) for code in hk_union]
    daily_manifest = {row["code"]: row for row in load_json("hk_daily_manifest.json")}
    minute_manifest = {row["code"]: row for row in load_json("hk_all_minute_manifest.json")}
    stale_daily = [code for code in hk_union if daily_manifest.get(code, {}).get("status") == "TARGET_DATE_MISSING"]
    exact_daily = [code for code in hk_union if daily_manifest.get(code, {}).get("status") == "TARGET_DATE"]
    target_minutes = [code for code in hk_union if minute_manifest.get(code, {}).get("status") == "TARGET_DATE"]
    checks["constituent_market_data"] = {
        "hk_union_count": len(hk_union),
        "daily_exact_target_count": len(exact_daily),
        "daily_stale_target_count": len(stale_daily),
        "daily_stale_codes": stale_daily,
        "minute_target_count": len(target_minutes),
        "minute_missing_or_error_count": len(hk_union) - len(target_minutes),
        "minute_target_row_counts": dict(Counter(minute_manifest.get(code, {}).get("rows") for code in target_minutes)),
    }
    if len(hk_union) != 646:
        failures.append("hk_union_count_unexpected")
    if len(exact_daily) != 643 or len(stale_daily) != 3:
        failures.append("daily_target_coverage_unexpected")

    with (ROOT / "outputs/watchlist_validation.csv").open(newline="", encoding="utf-8-sig") as handle:
        validation = list(csv.DictReader(handle))
    with (ROOT / "outputs/component_contributions_20260908.csv").open(newline="", encoding="utf-8-sig") as handle:
        contributions = list(csv.DictReader(handle))
    with (ROOT / "outputs/multi_day_validation.csv").open(newline="", encoding="utf-8-sig") as handle:
        multi = list(csv.DictReader(handle))
    status_counts = Counter(row.get("single_day_status") for row in validation)
    multi_status_counts = Counter()
    by_code = {}
    for row in validation:
        multi_status_counts[row.get("multi_day_status")] += 1
        by_code[row.get("code")] = row
    output_issues = []
    if len(validation) != 197 or len({row.get("code") for row in validation}) != 197:
        output_issues.append("validation_output_not_197_unique_rows")
    if len(contributions) < len(pcfs):
        output_issues.append("component_contributions_suspiciously_short")
    for row in validation:
        status = row.get("single_day_status")
        has_error = row.get("error_bp", "") != ""
        if status in {"PASS_SINGLE_DAY", "FAIL_5BP", "BOUNDARY_ROUNDING"} and not has_error:
            output_issues.append(f"missing_error_for_{row.get('code')}")
        if status == "NOT_APPLICABLE" and row.get("asset_type") != "other_overseas":
            output_issues.append(f"not_applicable_asset_type_{row.get('code')}")
        if row.get("market") == "SH" and row.get("multi_day_status") == "SH_SINGLE_DAY_ONLY" and row.get("multi_day_sample_count") != "1":
            output_issues.append(f"sh_sample_not_one_{row.get('code')}")
    checks["outputs"] = {
        "validation_rows": len(validation),
        "validation_unique_codes": len({row.get("code") for row in validation}),
        "single_day_status_counts": dict(status_counts),
        "multi_day_status_counts": dict(multi_status_counts),
        "component_contribution_rows": len(contributions),
        "multi_day_rows": len(multi),
        "output_issues": output_issues,
    }
    if output_issues:
        failures.append("output_quality_gate_failed")

    control = by_code.get("513090")
    control_check = {
        "present": control is not None,
        "estimated_nav": control.get("estimated_nav") if control else None,
        "published_nav": control.get("published_nav") if control else None,
        "error_bp": control.get("error_bp") if control else None,
        "status": control.get("single_day_status") if control else None,
        "expected_estimated_nav": "1.8432886185282",
        "expected_published_nav": "1.8433",
        "expected_error_bp": "-0.06174508652959366",
    }
    if not control or control.get("estimated_nav") != control_check["expected_estimated_nav"] or control.get("published_nav") != control_check["expected_published_nav"]:
        failures.append("513090_control_mismatch")
    checks["control_513090"] = control_check
    checks["failures"] = failures
    checks["status"] = "PASS" if not failures else "FAIL"
    (ROOT / "outputs/quality_checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2, default=compact))
    print(json.dumps(checks, ensure_ascii=False, indent=2, default=compact))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

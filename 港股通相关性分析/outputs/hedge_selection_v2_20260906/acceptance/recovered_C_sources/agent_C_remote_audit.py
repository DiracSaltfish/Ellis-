#!/usr/bin/env python3
"""Read-only audit of the remote Stocksdata inventory for agent C.

This script is streamed to machome and never writes to the remote source tree.
It checks target-window file availability and PCF quantity completeness for the
55 assigned funds.  It deliberately does not infer prices or fill missing PCF.
"""
import argparse
import csv
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def date_files(folder, pattern):
    out = {}
    for p in folder.glob(pattern):
        m = re.match(r"(\d{8})", p.name)
        if m:
            out[m.group(1)] = p
    return out


def audit(root, fund_ids, start, end):
    cn = date_files(root / "基金_分钟数据/ETF_分钟数据/1分钟_按月归档", "*/*.zip")
    hk = date_files(root / "港股_分笔成交/港股_分笔成交_按月归档", "*/*.zip")
    pcf = date_files(root / "PCF导出CSV", "*_明细.csv")
    dates = [d for d in sorted(set(cn) | set(hk) | set(pcf)) if start <= d <= end]
    pcf_by_fund = defaultdict(list)
    pcf_checks = []
    for d in dates:
        detail = pcf.get(d)
        if not detail:
            continue
        counts = defaultdict(list)
        for row in csv_rows(detail):
            fid = row.get("基金代码", "")
            if fid in fund_ids:
                counts[fid].append(row)
        header = root / "PCF导出CSV" / f"{d}_主表.csv"
        headers = {}
        if header.exists():
            for row in csv_rows(header):
                if row.get("基金代码", "") in fund_ids:
                    headers[row["基金代码"]] = row
        for fid in fund_ids:
            rows = counts.get(fid, [])
            if not rows:
                continue
            codes = [r.get("成分股代码", "") for r in rows]
            missing_qty = sum(not r.get("数量股", "").strip() for r in rows)
            missing_cash = sum(not r.get("预估现金差额", "").strip() for r in rows)
            record = {
                "date": d,
                "fund_code": fid,
                "rows": len(rows),
                "distinct_components": len(set(codes)),
                "duplicate_components": len(codes) - len(set(codes)),
                "missing_quantities": missing_qty,
                "missing_cash_difference": missing_cash,
                "header_component_count": headers.get(fid, {}).get("成分股数量只"),
                "header_min_creation_unit": headers.get(fid, {}).get("最小申购赎回单位份"),
                "header_path": str(header.relative_to(root)) if header.exists() else None,
                "detail_path": str(detail.relative_to(root)),
            }
            pcf_checks.append(record)
            pcf_by_fund[fid].append(record)
    common = [d for d in dates if d in cn and d in hk and d in pcf]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "window": [start, end],
        "fund_count": len(fund_ids),
        "file_counts_in_window": {
            "cn_etf_1m": sum(d in cn for d in dates),
            "hk_trades": sum(d in hk for d in dates),
            "pcf_detail": sum(d in pcf for d in dates),
            "all_three": len(common),
        },
        "available_dates": {
            "cn_etf_1m": [d for d in dates if d in cn],
            "hk_trades": [d for d in dates if d in hk],
            "pcf_detail": [d for d in dates if d in pcf],
            "all_three": common,
        },
        "pcf_fund_coverage": {
            fid: {
                "observed_dates": len(pcf_by_fund.get(fid, [])),
                "dates": [r["date"] for r in pcf_by_fund.get(fid, [])],
                "missing_quantity_rows": sum(r["missing_quantities"] for r in pcf_by_fund.get(fid, [])),
                "rows_total": sum(r["rows"] for r in pcf_by_fund.get(fid, [])),
            }
            for fid in fund_ids
        },
        "pcf_checks": pcf_checks,
        "note": "File availability only; no missing prices or PCF quantities were filled.",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/Volumes/EllisFiles/Stocksdata")
    ap.add_argument("--start", default="20260804")
    ap.add_argument("--end", default="20260904")
    ap.add_argument("--funds", required=True, help="comma-separated six-digit fund codes")
    args = ap.parse_args()
    out = audit(Path(args.root), set(args.funds.split(",")), args.start, args.end)
    print(json.dumps(out, ensure_ascii=False, indent=2))

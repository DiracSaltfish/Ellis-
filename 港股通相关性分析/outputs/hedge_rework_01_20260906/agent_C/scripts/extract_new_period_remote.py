#!/usr/bin/env python3
"""Stream PCF components and HK minute LAST marks for assigned C funds.

The source tree is read-only.  The caller is expected to compress stdout
locally.  No CN ETF prices are required for the basket-risk calculation, and
none are synthesized when the source archive is absent.
"""
import argparse
import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def hk_bars(archive, member, day):
    by_minute = {}
    with archive.open(member) as stream:
        for row in csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig")):
            stamp = row.get("时间", "").replace("/", "-")
            dt = datetime.fromisoformat(stamp)
            if dt.strftime("%Y%m%d") != day:
                raise ValueError(f"wrong date in {member}: {stamp}")
            minute = dt.hour * 60 + dt.minute
            if not (570 <= minute <= 960):
                continue
            px = float(row["价格"])
            if px <= 0:
                continue
            by_minute[minute] = px
    return [[m, p] for m, p in sorted(by_minute.items())]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/Volumes/EllisFiles/Stocksdata")
    ap.add_argument("--start", default="20260804")
    ap.add_argument("--end", default="20260904")
    ap.add_argument("--funds", required=True)
    args = ap.parse_args()
    root = Path(args.root)
    funds = set(args.funds.split(","))
    cnroot = root / "基金_分钟数据/ETF_分钟数据/1分钟_按月归档"
    hkroot = root / "港股_分笔成交/港股_分笔成交_按月归档"
    pcfroot = root / "PCF导出CSV"
    pcf_paths = sorted(p for p in pcfroot.glob("*_明细.csv") if args.start <= p.name[:8] <= args.end)
    for detail in pcf_paths:
        d = detail.name[:8]
        month = d[:4] + "-" + d[4:6]
        hkpath = hkroot / month / (d + ".zip")
        headerpath = pcfroot / (d + "_主表.csv")
        if not hkpath.exists():
            continue
        headers = {r.get("基金代码", ""): r for r in rows(headerpath) if r.get("基金代码", "") in funds} if headerpath.exists() else {}
        grouped = defaultdict(list)
        for r in rows(detail):
            if r.get("基金代码", "") in funds:
                grouped[r["基金代码"]].append(r)
        with zipfile.ZipFile(hkpath) as z:
            names = set(z.namelist())
            for fid in sorted(grouped):
                components = grouped[fid]
                # Core locked ETF proxies are included for a partial new-period
                # price-risk diagnostic.  Futures are probed separately via TWS.
                codes = sorted({r.get("成分股代码", "") for r in components} | {"02800", "02828", "03032", "03033", "02845"})
                hk = {}
                missing = []
                for code in codes:
                    member = f"{int(code):05d}.HK.csv"
                    if member in names:
                        hk[code] = hk_bars(z, member, d)
                    else:
                        missing.append(code)
                record = {
                    "date": d,
                    "fund_id": fid,
                    "header": headers.get(fid),
                    "components": [
                        {
                            "code": r.get("成分股代码", ""),
                            "name": r.get("成分股名称", ""),
                            "qty": r.get("数量股", ""),
                            "cash_substitution_flag": r.get("现金替代标志", ""),
                        }
                        for r in components
                    ],
                    "hk": hk,
                    "missing_members": missing,
                    "sources": [
                        {"path": str(p.relative_to(root)), "bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
                        for p in [detail, headerpath, hkpath] if p.exists()
                    ],
                }
                sys.stdout.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    main()

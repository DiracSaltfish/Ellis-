#!/usr/bin/env python3
"""Filter ETF 1-minute daily ZIP archives for a fixed watchlist.

This script is intended to run on machome, where the Stocksdata volume is
mounted. It reads archives sequentially and writes one UTF-8-SIG CSV per
target symbol, then creates a compressed bundle and an audit manifest.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import zipfile
from collections import OrderedDict
from datetime import date
from pathlib import Path


DEFAULT_HEADER = [
    "时间",
    "代码",
    "名称",
    "开盘价",
    "收盘价",
    "最高价",
    "最低价",
    "成交量",
    "成交额",
    "涨幅",
    "振幅",
]
ARCHIVE_RE = re.compile(r"(?P<day>\d{8})_1min\.zip$")


def parse_day(path: Path) -> date | None:
    match = ARCHIVE_RE.search(path.name)
    if not match:
        return None
    try:
        return date.fromisoformat(
            f"{match.group('day')[:4]}-{match.group('day')[4:6]}-{match.group('day')[6:]}"
        )
    except ValueError:
        return None


def csv_member_map(archive: zipfile.ZipFile) -> dict[str, str]:
    """Map basename to ZIP member name, tolerating an optional inner folder."""
    result: dict[str, str] = {}
    for name in archive.namelist():
        if name.lower().endswith(".csv"):
            result.setdefault(Path(name).name, name)
    return result


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: filter_etf_1min_watchlist.py WATCHLIST_JSON OUTPUT_DIR START_DATE",
            file=sys.stderr,
        )
        return 2

    watchlist_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    start_day = date.fromisoformat(sys.argv[3])
    source_dir = Path(
        "/Volumes/EllisFiles/Stocksdata/基金_分钟数据/ETF_分钟数据/1分钟_按月归档"
    )

    watchlist = json.loads(watchlist_path.read_text(encoding="utf-8"))
    symbols = watchlist.get("symbols")
    if not isinstance(symbols, list) or any(not isinstance(s, str) for s in symbols):
        raise ValueError("watchlist JSON must contain a string array named symbols")
    if len(symbols) != 202 or len(set(symbols)) != 202:
        raise ValueError(f"expected 202 unique symbols, got {len(symbols)}")
    symbols = list(OrderedDict.fromkeys(symbols))

    archives: list[tuple[date, Path]] = []
    for path in source_dir.rglob("*_1min.zip"):
        day = parse_day(path)
        if day is not None and day >= start_day:
            archives.append((day, path))
    archives.sort(key=lambda item: (item[0], str(item[1])))
    if not archives:
        raise FileNotFoundError(f"no 1-minute archives found from {start_day} under {source_dir}")

    latest_day = archives[-1][0]
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_dir = output_dir / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    writers: dict[str, tuple[io.TextIOWrapper, csv.writer]] = {}
    stats = {
        symbol: {
            "rows": 0,
            "archive_days_present": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "out_of_order_rows": 0,
        }
        for symbol in symbols
    }
    canonical_header = DEFAULT_HEADER
    processed_archives = 0
    archive_days: list[str] = []
    missing_by_archive: dict[str, list[str]] = {}

    try:
        for day, archive_path in archives:
            processed_archives += 1
            archive_days.append(day.isoformat())
            present: set[str] = set()
            with zipfile.ZipFile(archive_path) as archive:
                members = csv_member_map(archive)
                for symbol in symbols:
                    member_name = members.get(f"{symbol}.csv")
                    if member_name is None:
                        continue
                    present.add(symbol)
                    if symbol not in writers:
                        handle = (csv_dir / f"{symbol}.csv").open(
                            "w", encoding="utf-8-sig", newline=""
                        )
                        writer = csv.writer(handle, lineterminator="\n")
                        writers[symbol] = (handle, writer)
                    handle, writer = writers[symbol]
                    with archive.open(member_name) as raw:
                        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                        reader = csv.reader(text)
                        try:
                            header = next(reader)
                        except StopIteration:
                            continue
                        if header:
                            canonical_header = header
                        if stats[symbol]["rows"] == 0:
                            writer.writerow(header)
                        for row in reader:
                            if not row:
                                continue
                            timestamp = row[0].strip() if row else ""
                            # Daily archives use YYYY/MM/DD while some summary
                            # sources use YYYY-MM-DD; compare normalized dates.
                            row_day = timestamp[:10].replace("/", "-")
                            if row_day < start_day.isoformat() or row_day > latest_day.isoformat():
                                continue
                            writer.writerow(row)
                            stats[symbol]["rows"] += 1
                            if stats[symbol]["first_timestamp"] is None:
                                stats[symbol]["first_timestamp"] = timestamp
                            previous = stats[symbol]["last_timestamp"]
                            if previous is not None and timestamp < previous:
                                stats[symbol]["out_of_order_rows"] += 1
                            stats[symbol]["last_timestamp"] = timestamp
                    stats[symbol]["archive_days_present"] += 1
            if len(present) != len(symbols):
                missing_by_archive[day.isoformat()] = [s for s in symbols if s not in present]
            if processed_archives == 1 or processed_archives % 25 == 0 or processed_archives == len(archives):
                print(
                    f"processed {processed_archives}/{len(archives)} archives through {day.isoformat()}",
                    flush=True,
                )
    finally:
        for handle, _writer in writers.values():
            handle.close()

    for symbol in symbols:
        path = csv_dir / f"{symbol}.csv"
        if not path.exists():
            path.write_text(",".join(canonical_header) + "\n", encoding="utf-8-sig")

    manifest = {
        "dataset": "ETF 1-minute K data",
        "watchlist_source": str(watchlist_path),
        "source_root": str(source_dir),
        "start_date_inclusive": start_day.isoformat(),
        "latest_archive_date": latest_day.isoformat(),
        "archive_count": processed_archives,
        "archive_dates": archive_days,
        "target_count": len(symbols),
        "target_symbols": symbols,
        "source_format": "one trading-day ZIP containing code.SH.csv/code.SZ.csv members",
        "encoding": "UTF-8 with BOM",
        "columns": canonical_header,
        "symbol_stats": stats,
        "archives_with_missing_targets": missing_by_archive,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    bundle_path = output_dir.with_suffix(".zip")
    with zipfile.ZipFile(
        bundle_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as bundle:
        for symbol in symbols:
            bundle.write(csv_dir / f"{symbol}.csv", arcname=f"csv/{symbol}.csv")
        bundle.write(manifest_path, arcname="manifest.json")

    print(json.dumps({
        "bundle": str(bundle_path),
        "manifest": str(manifest_path),
        "csv_dir": str(csv_dir),
        "latest_archive_date": latest_day.isoformat(),
        "archive_count": processed_archives,
        "target_count": len(symbols),
        "symbols_with_rows": sum(1 for item in stats.values() if item["rows"] > 0),
        "total_rows": sum(item["rows"] for item in stats.values()),
        "archives_with_missing_targets": len(missing_by_archive),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

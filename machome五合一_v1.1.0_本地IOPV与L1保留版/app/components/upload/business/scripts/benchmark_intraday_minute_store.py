#!/usr/bin/env python3
import argparse
import csv
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))

from intraday_minute_store import IntradayMinuteArchive  # noqa: E402
from sina_quote_uploader import (  # noqa: E402
    fetch_required_symbols,
    fetch_sina_quotes,
    is_true,
    load_env_file,
)


BEIJING = ZoneInfo("Asia/Shanghai")
DEFAULT_INTERVAL_SECONDS = 5.0
DEFAULT_SESSION_MINUTES = 240


def main() -> int:
    load_env_file(".sina-uploader.env")
    parser = argparse.ArgumentParser(description="Benchmark local 1-minute intraday archive overhead.")
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument(
        "--origin-tls-insecure",
        action="store_true",
        default=is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")),
    )
    parser.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", "8")))
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--session-minutes", type=int, default=DEFAULT_SESSION_MINUTES)
    parser.add_argument("--scale", type=int, default=1, help="Duplicate the live symbol set for stress testing.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols. Skip live required-symbol fetch when provided.")
    args = parser.parse_args()

    sample_quotes = load_sample_quotes(args)
    if not sample_quotes:
        raise SystemExit("no sample quotes available for benchmark")

    cycles = max(1, int(args.session_minutes * 60 / max(args.interval_seconds, 0.1)))
    session_start = datetime(2026, 6, 4, 9, 30, 0, tzinfo=BEIJING)

    with tempfile.TemporaryDirectory(prefix="intraday-minute-bench-") as root:
        archive = IntradayMinuteArchive(root)
        cycle_ms: list[float] = []
        for index in range(cycles):
            observed_at = session_start + timedelta(seconds=index * args.interval_seconds)
            t0 = time.perf_counter()
            archive.record(sample_quotes, observed_at)
            cycle_ms.append((time.perf_counter() - t0) * 1000.0)
        flush_at = session_start + timedelta(seconds=cycles * args.interval_seconds + 60)
        flush_t0 = time.perf_counter()
        written = archive.flush_due(flush_at, force=True)
        flush_ms = (time.perf_counter() - flush_t0) * 1000.0

        csv_path = Path(root) / "20260604" / "minute_quotes.csv"
        row_count = 0
        if csv_path.exists():
            with csv_path.open(newline="", encoding="utf-8") as handle:
                row_count = sum(1 for _ in csv.DictReader(handle))
        file_size = csv_path.stat().st_size if csv_path.exists() else 0

    avg_ms = statistics.mean(cycle_ms)
    p95_ms = percentile(cycle_ms, 95)
    max_ms = max(cycle_ms)
    budget_ms = args.interval_seconds * 1000.0
    print(f"sample_symbols={len(sample_quotes)} cycles={cycles} rows={row_count} last_flush_rows={written}")
    print(f"avg_cycle_ms={avg_ms:.3f} p95_cycle_ms={p95_ms:.3f} max_cycle_ms={max_ms:.3f} flush_ms={flush_ms:.3f}")
    print(f"interval_budget_ms={budget_ms:.1f} p95_budget_pct={p95_ms / budget_ms * 100:.3f}%")
    print(f"output_file_bytes={file_size}")
    if p95_ms >= budget_ms:
        print("result=FAIL reason=p95_exceeds_interval_budget")
        return 1
    print("result=PASS")
    return 0


def load_sample_quotes(args) -> list[dict]:
    if args.symbols.strip():
        symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    else:
        if not args.token.strip():
            raise SystemExit("--token or NNN_UPLOAD_TOKEN is required when --symbols is not provided")
        symbols = fetch_required_symbols(
            args.server,
            args.token,
            args.timeout,
            args.origin_ip,
            args.origin_tls_insecure,
            args.origin_ca_file,
        )
    quotes = list(fetch_sina_quotes(symbols, args.timeout).values())
    if args.scale <= 1:
        return quotes
    expanded: list[dict] = []
    for multiplier in range(args.scale):
        for quote in quotes:
            copied = dict(quote)
            copied["symbol"] = scaled_symbol(str(quote.get("symbol") or ""), multiplier)
            copied["name"] = str(quote.get("name") or copied["symbol"])
            copied["source_symbol"] = str(quote.get("source_symbol") or copied["symbol"])
            expanded.append(copied)
    return expanded


def scaled_symbol(symbol: str, multiplier: int) -> str:
    if multiplier <= 0:
        return symbol
    suffix = f"_B{multiplier}"
    if len(symbol) + len(suffix) <= 16:
        return symbol + suffix
    return f"{symbol[: max(1, 16 - len(suffix))]}{suffix}"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


if __name__ == "__main__":
    raise SystemExit(main())

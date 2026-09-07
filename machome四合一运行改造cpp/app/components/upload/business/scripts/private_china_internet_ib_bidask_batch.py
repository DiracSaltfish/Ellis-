#!/usr/bin/env python3
"""Collect a local, auditable three-week IBKR BID/ASK cache without uploading it.

The default universe is the current official PCF of SH513220.  It selects the
most recent fifteen trading dates exposed by that ETF's public minute-history API,
then requests one historical BID series and one historical ASK series for every
unique HK/US constituent.  The results are written only below ``--output-dir``;
this program has no private-input or history-import HTTP write path.

The current-PCF universe is deliberately labelled as a coverage cache.  A
later exact historical valuation still has to use the PCF belonging to each
historical date; this cache must not be treated as proof that constituents were
unchanged.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import signal
import socket
import sys
import tempfile
import time
import urllib.error
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from ib_insync import IB

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_china_internet_history_backfill as history  # noqa: E402
import private_china_internet_valuation_uploader as valuation  # noqa: E402
import private_valuation_uploader as common  # noqa: E402


DEFAULT_SYMBOL = "SH513220"
DEFAULT_TRADING_DAYS = 15
DEFAULT_OUTPUT_DIR = "scripts/.runtime/private_china_internet/tmp_ib_bidask_3w"
DEFAULT_REQUEST_DELAY = 11.0
DEFAULT_WAIT_INTERVAL = 30.0
STOP = False


def request_stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def recent_public_trading_days(server: str, symbol: str, timeout: float, count: int) -> list[date]:
    if count < 1:
        raise ValueError("trading-day count must be positive")
    values = history.public_days(server, symbol, timeout, None, None)
    if len(values) < count:
        raise history.SourceUnavailableError(f"{symbol} has only {len(values)} public minute-history dates, need {count}")
    return values[-count:]


def fetch_current_pcf(config: valuation.FundConfig, timeout: float, pcf_file: Path | None) -> tuple[valuation.PCF, bytes]:
    if pcf_file is not None:
        raw = pcf_file.expanduser().read_bytes()
        source_url = pcf_file.expanduser().resolve().as_uri()
    else:
        source_url = config.source_url(date.today())
        raw = bytes(common._http_fetch(source_url, None, common.SOURCE_HEADERS, timeout))
    return valuation.parse_pcf(config, raw, source_url), raw


def unique_overseas_components(components: Iterable[valuation.Component]) -> list[valuation.Component]:
    values: dict[tuple[str, str], valuation.Component] = {}
    for component in components:
        if component.market not in {"HK", "US"}:
            continue
        existing = values.get(component.key)
        if existing is not None and existing.currency != component.currency:
            raise history.SourceUnavailableError(f"component collision: {component.key}")
        values[component.key] = component
    if not values:
        raise history.SourceUnavailableError("PCF has no overseas components to query through IBKR")
    return [values[key] for key in sorted(values)]


def series_path(output_dir: Path, component: valuation.Component, side: str) -> Path:
    return output_dir / "series" / f"{component.market}_{component.symbol}_{side}.csv"


def write_series(path: Path, series: dict[tuple[date, str], float], component: valuation.Component, side: str) -> int:
    rows = [
        {
            "trading_day": trading_day.isoformat(),
            "minute": minute,
            "market": component.market,
            "symbol": component.symbol,
            "currency": component.currency,
            "side": side,
            "price": f"{price:.10g}",
            "source": history.HISTORICAL_IB_SOURCE,
            "market_data_type": history.HISTORICAL_MARKET_DATA_TYPE,
        }
        for (trading_day, minute), price in sorted(series.items())
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", delete=False, newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["trading_day", "minute", "market", "symbol", "currency", "side", "price", "source", "market_data_type"])
        writer.writeheader()
        writer.writerows(rows)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return len(rows)


FetchSeries = Callable[[Any, Any], dict[tuple[date, str], float]]


def pull_bid_ask(
    ib: IB,
    components: Iterable[valuation.Component],
    days: Iterable[date],
    output_dir: Path,
    request_delay: float,
    *,
    fetch_series: Callable[..., dict[tuple[date, str], float]] = history.fetch_bar_series,
) -> dict[str, int]:
    """Fetch every unique raw component/side exactly once and persist it locally."""

    result: dict[str, int] = {}
    requested_days = tuple(sorted(set(days)))
    for index, component in enumerate(unique_overseas_components(components), 1):
        contract, use_rth = history.contract_for_component(component)
        for side in ("BID", "ASK"):
            path = series_path(output_dir, component, side)
            if path.is_file() and path.stat().st_size > 0:
                result[str(path.relative_to(output_dir))] = -1
                continue
            values = fetch_series(ib, contract, what=side, use_rth=use_rth, days=requested_days, request_delay=request_delay)
            if not values:
                raise history.SourceUnavailableError(f"IB returned no {side} bars for {component.market}:{component.symbol}")
            count = write_series(path, values, component, side)
            result[str(path.relative_to(output_dir))] = count
            print(f"IB {index} {component.market}:{component.symbol} {side} rows={count}", flush=True)
    return result


def wait_for_ib(host: str, port: int, interval: float) -> None:
    while not STOP:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            print(f"waiting for IBKR API {host}:{port}", flush=True)
            time.sleep(max(1.0, interval))
    raise InterruptedError("stopped while waiting for IBKR API")


def build_manifest(symbol: str, pcf: valuation.PCF, days: list[date], raw: bytes, output_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "purpose": "local_raw_ib_bidask_coverage_cache_only",
        "status": "pending_ib",
        "fund_symbol": symbol,
        "pcf": pcf.to_payload(),
        "pcf_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "pcf_scope_warning": "This is the current-PCF coverage universe. Historical valuation must still use each date's PCF.",
        "public_minute_history_days": [value.isoformat() for value in days],
        "output_dir": str(output_dir),
        "generated_at": common.iso_timestamp(datetime.now(common.SHANGHAI)),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--symbol", default=DEFAULT_SYMBOL, choices=sorted(valuation.FUND_BY_SYMBOL))
    result.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    result.add_argument("--trading-days", type=int, default=DEFAULT_TRADING_DAYS)
    result.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    result.add_argument("--pcf-file", type=Path, help="optional audited current-PCF XML; otherwise fetch the official current PCF")
    result.add_argument("--ib-host", default=os.getenv("NNN_PRIVATE_CHINA_INTERNET_IB_HOST", os.getenv("NNN_IB_HOST", "127.0.0.1")))
    result.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_PRIVATE_CHINA_INTERNET_IB_PORT", os.getenv("NNN_IB_PORT", "7496"))))
    result.add_argument("--ib-client-id", type=int, default=513222)
    result.add_argument("--ib-request-delay", type=float, default=DEFAULT_REQUEST_DELAY)
    result.add_argument("--wait-interval", type=float, default=DEFAULT_WAIT_INTERVAL)
    result.add_argument("--timeout", type=float, default=60)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.trading_days < 1 or args.ib_request_delay < 0 or args.wait_interval <= 0:
        raise SystemExit("--trading-days must be positive, --ib-request-delay non-negative, and --wait-interval positive")
    config = valuation.FUND_BY_SYMBOL[args.symbol]
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pcf, raw = fetch_current_pcf(config, args.timeout, args.pcf_file)
    days = recent_public_trading_days(args.server, config.symbol, args.timeout, args.trading_days)
    manifest_path = output_dir / "manifest.json"
    manifest = build_manifest(config.symbol, pcf, days, raw, output_dir)
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("pcf_raw_sha256") != manifest["pcf_raw_sha256"] or existing.get("public_minute_history_days") != manifest["public_minute_history_days"]:
            raise SystemExit(f"{output_dir} already contains a different PCF/date set; choose a new --output-dir")
    atomic_write_bytes(output_dir / "pcf.xml", raw)
    atomic_write_json(manifest_path, manifest)
    print(f"prepared {output_dir} fund={config.symbol} public_days={days[0]}..{days[-1]} overseas_components={len(unique_overseas_components(pcf.components))}", flush=True)
    wait_for_ib(args.ib_host, args.ib_port, args.wait_interval)
    ib = IB()
    try:
        ib.connect(args.ib_host, args.ib_port, clientId=args.ib_client_id, timeout=args.timeout, readonly=True)
        if not ib.isConnected():
            raise history.SourceUnavailableError("TWS API socket did not become connected")
        series = pull_bid_ask(ib, pcf.components, days, output_dir, args.ib_request_delay)
    finally:
        ib.disconnect()
    manifest["status"] = "completed"
    manifest["completed_at"] = common.iso_timestamp(datetime.now(common.SHANGHAI))
    manifest["series"] = series
    atomic_write_json(manifest_path, manifest)
    print(f"completed local IB cache files={len(series)} output={output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        raise SystemExit(main())
    except (history.SourceUnavailableError, urllib.error.URLError, OSError) as exc:
        print(f"IB BID/ASK batch failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

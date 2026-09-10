#!/usr/bin/env python3
"""Repair today's private SZ162411 minutes with audited IBKR XOP BID/ASK bars.

This intentionally supports only the current Shanghai trading day.  It keeps
the domestic prices already stored by the private history endpoint, reuses the
currently audited NAV/SAFE/XOP-close model seed, and replaces the XOP quote in
each row with an exact IBKR HistoricalBidAsk one-minute observation.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any

from ib_insync import IB, Stock, util

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_valuation_uploader as common  # noqa: E402


SYMBOL = "SZ162411"
REFERENCE_SYMBOL = "XOP"
SOURCE = "mac-home-private-162411-history-backfill"
HISTORICAL_SOURCE = "IBKR_TWS_HISTORICAL_BID_ASK_1M"
HISTORICAL_MARKET_DATA_TYPE = "HistoricalBidAsk"
HISTORICAL_QUOTE_SESSION = "us_overnight_historical_bid_ask"
SHANGHAI = common.SHANGHAI


class SourceUnavailableError(RuntimeError):
    """A mandatory source or audit check failed."""


def parse_day(value: str) -> date:
    return datetime.strptime(value.strip().replace("-", ""), "%Y%m%d").date()


def day_key(value: date) -> str:
    return value.strftime("%Y%m%d")


def positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def in_china_session(value: datetime) -> bool:
    local = value.astimezone(SHANGHAI)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    return 9 * 60 + 30 <= minute <= 11 * 60 + 30 or 13 * 60 <= minute <= 15 * 60


def source_json(server: str, path: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(server.rstrip("/") + path, headers=common.SOURCE_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SourceUnavailableError(f"{path} request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise SourceUnavailableError(f"{path} returned {type(payload).__name__}, not an object")
    return payload


def current_input(server: str, day: date, timeout: float) -> dict[str, Any]:
    payload = source_json(server, f"/api/v1/private/funds/{SYMBOL}", timeout)
    value = payload.get("input")
    if not isinstance(value, dict) or str(value.get("symbol") or "").upper() != SYMBOL:
        raise SourceUnavailableError("current private SZ162411 input is missing")
    if str(value.get("valuation_kind") or "") != "lof_weighted_anchor":
        raise SourceUnavailableError("current SZ162411 input is not the weighted-anchor model")
    lof = value.get("lof")
    if not isinstance(lof, dict):
        raise SourceUnavailableError("current SZ162411 LOF audit input is missing")
    current_fx = lof.get("current_fx")
    if not isinstance(current_fx, dict) or current_fx.get("trading_day") != day.isoformat():
        raise SourceUnavailableError("current SAFE parity does not match the replay day")
    base_reference = lof.get("base_reference")
    if not isinstance(base_reference, dict) or base_reference.get("price_basis") != "regular_session_close":
        raise SourceUnavailableError("base XOP reference is not an audited regular-session close")
    return value


def existing_market_prices(server: str, day: date, timeout: float) -> dict[str, float]:
    payload = source_json(
        server,
        f"/api/v1/private/funds/{SYMBOL}/minute-history?date={day_key(day)}",
        timeout,
    )
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise SourceUnavailableError("private SZ162411 minute rows are missing")
    values: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        minute = str(row.get("minute") or "")
        try:
            observed_at = datetime.fromisoformat(minute.replace("Z", "+00:00")).astimezone(SHANGHAI)
        except ValueError:
            continue
        price = positive(row.get("market_price"))
        if observed_at.date() == day and in_china_session(observed_at) and price is not None:
            values[observed_at.strftime("%H:%M")] = price
    if not values:
        raise SourceUnavailableError("private SZ162411 history has no domestic prices to preserve")
    return values


def xop_contract() -> Stock:
    # Historical overnight routing must be resolved from the descriptor.  A
    # SMART conId pinned onto OVERNIGHT can return IB error 366/no data.
    return Stock(REFERENCE_SYMBOL, "OVERNIGHT", "USD", primaryExchange="ARCA")


def historical_prices(ib: IB, contract: Stock, day: date, what: str, timeout: float) -> dict[str, float]:
    end = datetime.combine(day, clock_time(15, 0), SHANGHAI)
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=end,
        durationStr="1 D",
        barSizeSetting="1 min",
        whatToShow=what,
        useRTH=False,
        formatDate=2,
        keepUpToDate=False,
        timeout=timeout,
    )
    values: dict[str, float] = {}
    for bar in bars:
        observed_at = getattr(bar, "date", None)
        if isinstance(observed_at, str):
            observed_at = util.parseIBDatetime(observed_at)
        if not isinstance(observed_at, datetime):
            continue
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        observed_at = observed_at.astimezone(SHANGHAI).replace(second=0, microsecond=0)
        price = positive(getattr(bar, "close", None))
        if observed_at.date() == day and in_china_session(observed_at) and price is not None:
            values[observed_at.strftime("%H:%M")] = price
    return values


def build_rows(
    day: date,
    template: dict[str, Any],
    market_prices: dict[str, float],
    bids: dict[str, float],
    asks: dict[str, float],
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for minute, market_price in sorted(market_prices.items()):
        bid, ask = bids.get(minute), asks.get(minute)
        if bid is None or ask is None or ask < bid:
            missing.append(minute)
            continue
        timestamp = datetime.combine(day, clock_time.fromisoformat(minute), SHANGHAI)
        value = copy.deepcopy(template)
        value.pop("received_at", None)
        value.pop("pcf", None)
        value.pop("fx", None)
        value["ib"] = {
            "symbol": REFERENCE_SYMBOL,
            "contract": "XOP STK OVERNIGHT/ARCA",
            "bid": bid,
            "ask": ask,
            "last": None,
            "market_data_type": HISTORICAL_MARKET_DATA_TYPE,
            "quote_session": HISTORICAL_QUOTE_SESSION,
            "source": HISTORICAL_SOURCE,
            "observed_at": common.iso_timestamp(timestamp),
        }
        value["source"] = SOURCE
        value["generated_at"] = common.iso_timestamp(timestamp)
        rows.append({
            "minute": common.iso_timestamp(timestamp),
            "market_price": market_price,
            "input": value,
        })
    return rows, missing


def upload(args: argparse.Namespace, rows: list[dict[str, Any]]) -> int:
    raw = json.dumps(
        {"rows": rows, "replace_day": True},
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    payload = gzip.compress(raw, compresslevel=6, mtime=0)
    request = urllib.request.Request(
        args.server.rstrip("/") + f"/api/v1/private/funds/{SYMBOL}/minute-history/import",
        data=payload,
        method="POST",
        headers=common.gzip_server_headers(args.server, args.token),
    )
    try:
        with common.open_server_request(
            request,
            args.timeout,
            args.origin_ip,
            args.origin_tls_insecure,
            args.origin_ca_file,
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SourceUnavailableError(f"historical import HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise SourceUnavailableError(f"historical import was not acknowledged: {result}")
    return int(result.get("imported") or 0)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--day", type=parse_day, default=datetime.now(SHANGHAI).date())
    result.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument(
        "--origin-tls-insecure",
        action="store_true",
        default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")),
    )
    result.add_argument("--env-file", default=".sina-uploader.env")
    result.add_argument("--ib-host", default=os.getenv("NNN_PRIVATE_162411_IB_HOST", "192.168.1.111"))
    result.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_PRIVATE_162411_IB_PORT", "7496")))
    result.add_argument("--ib-client-id", type=int, default=262411)
    result.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", "40")))
    result.add_argument("--request-delay", type=float, default=0.5)
    result.add_argument("--min-coverage", type=float, default=0.98)
    result.add_argument("--dry-run", action="store_true")
    return result


def run(args: argparse.Namespace) -> int:
    today = datetime.now(SHANGHAI).date()
    if args.day != today:
        raise SourceUnavailableError(f"this repair is current-day only: requested {args.day}, today {today}")
    if not 0 < args.min_coverage <= 1 or args.request_delay < 0:
        raise SourceUnavailableError("invalid coverage or request-delay setting")
    common.load_env_file(args.env_file)
    if not args.token:
        args.token = os.getenv("NNN_UPLOAD_TOKEN", "")
    if args.server == "https://1navs.com":
        args.server = os.getenv("NNN_SERVER_URL", args.server)
    if not args.origin_ip:
        args.origin_ip = os.getenv("NNN_ORIGIN_IP", "")
    if not args.origin_ca_file:
        args.origin_ca_file = os.getenv("NNN_ORIGIN_CA_FILE", "")
    if not args.origin_tls_insecure:
        args.origin_tls_insecure = common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", ""))
    if not args.dry_run and not args.token:
        raise SourceUnavailableError("NNN_UPLOAD_TOKEN or --token is required")

    template = current_input(args.server, args.day, args.timeout)
    market_prices = existing_market_prices(args.server, args.day, args.timeout)
    ib = IB()
    try:
        print(
            f"connecting TWS {args.ib_host}:{args.ib_port} client_id={args.ib_client_id} timeout={args.timeout:.0f}s",
            flush=True,
        )
        ib.wrapper.clientId = args.ib_client_id
        ib.client.connect(args.ib_host, args.ib_port, args.ib_client_id, timeout=args.timeout)
        if not ib.isConnected():
            raise SourceUnavailableError("TWS API did not become connected")
        contract = xop_contract()
        bids = historical_prices(ib, contract, args.day, "BID", args.timeout)
        if args.request_delay:
            time.sleep(args.request_delay)
        asks = historical_prices(ib, contract, args.day, "ASK", args.timeout)
    finally:
        ib.disconnect()

    rows, missing = build_rows(args.day, template, market_prices, bids, asks)
    coverage = len(rows) / len(market_prices)
    unique_bids = len({row["input"]["ib"]["bid"] for row in rows})
    unique_asks = len({row["input"]["ib"]["ask"] for row in rows})
    print(
        f"day={args.day} domestic={len(market_prices)} bid={len(bids)} ask={len(asks)} "
        f"matched={len(rows)} coverage={coverage:.1%} unique_bid={unique_bids} unique_ask={unique_asks} "
        f"missing={','.join(missing) or 'none'}",
        flush=True,
    )
    if coverage < args.min_coverage:
        raise SourceUnavailableError(
            f"exact historical BID/ASK coverage {coverage:.1%} is below {args.min_coverage:.1%}"
        )
    if unique_bids < 2 or unique_asks < 2:
        raise SourceUnavailableError("historical XOP BID/ASK is unexpectedly flat")
    if args.dry_run:
        print("dry-run: no history was changed", flush=True)
        return 0
    imported = upload(args, rows)
    print(f"imported={imported} replace_day=true", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run(parser().parse_args()))
    except SourceUnavailableError as exc:
        print(f"ERROR {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)

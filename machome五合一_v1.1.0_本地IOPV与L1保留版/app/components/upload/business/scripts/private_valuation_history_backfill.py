#!/usr/bin/env python3
"""Replay private SZ159518 minute valuation from public prices and IB Overnight.

This is deliberately separate from the live private uploader.  It reads only
the public fund's stored minute *price*, replays the dated PCF/CFETS inputs,
and obtains XOP's separately requested BID and ASK streams from IBKR's
``OVERNIGHT`` contract.  The resulting rows go solely to the private history
import endpoint and can safely overwrite an earlier replay for the same minute.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any, Iterable

from ib_insync import IB, Stock, util

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_valuation_uploader as private_uploader  # noqa: E402


HISTORY_SOURCE = "mac-home-private-history-backfill"
HISTORY_PATH = "/api/v1/private/funds/SZ159518/minute-history/import"


def parse_day(value: str) -> date:
    normalized = value.strip().replace("-", "")
    return datetime.strptime(normalized, "%Y%m%d").date()


def day_key(value: date) -> str:
    return value.strftime("%Y%m%d")


def is_china_session(value: datetime) -> bool:
    local = value.astimezone(private_uploader.SHANGHAI)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    return (9 * 60 + 30 <= minute <= 11 * 60 + 30) or (13 * 60 <= minute <= 15 * 60)


def positive(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def public_json(server: str, path: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        server.rstrip("/") + path,
        headers=private_uploader.SOURCE_HEADERS,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"public API returned {type(payload).__name__}, not an object")
    return payload


def historical_days(server: str, timeout: float, start: date | None, end: date | None) -> list[date]:
    payload = public_json(server, "/api/v1/funds/SZ159518/minute-history/dates?limit=120", timeout)
    values = payload.get("dates")
    if not isinstance(values, list):
        raise RuntimeError("public minute-history dates are missing")
    days: set[date] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            candidate = parse_day(value)
        except ValueError:
            continue
        if candidate.weekday() >= 5:
            continue
        if start is not None and candidate < start:
            continue
        if end is not None and candidate > end:
            continue
        days.add(candidate)
    return sorted(days)


def public_prices(server: str, day: date, timeout: float) -> dict[str, float]:
    payload = public_json(server, f"/api/v1/funds/SZ159518/minute-history?date={day_key(day)}", timeout)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("public minute-history rows are missing")
    prices: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        minute = str(row.get("min") or "").strip()
        if len(minute) != 5 or minute[2] != ":":
            continue
        try:
            timestamp = datetime.combine(day, clock_time.fromisoformat(minute), private_uploader.SHANGHAI)
        except ValueError:
            continue
        if not is_china_session(timestamp):
            continue
        market_price = positive(row.get("mkp"))
        # Public history occasionally carries a placeholder zero.  It is not a
        # tradable point and the server correctly rejects it, so omit just that
        # minute rather than discarding the complete date's batch.
        if market_price is not None:
            prices[minute] = market_price
    return prices


def overnight_xop_contract() -> Stock:
    # Keep the exchange and primary venue pinned, but deliberately leave conId
    # empty. IBKR's historical service resolves OVERNIGHT from this contract
    # descriptor; forcing the SMART conId onto an OVERNIGHT request can produce
    # error 366 (no data) even though a qualified OVERNIGHT descriptor works.
    return Stock(
        private_uploader.REFERENCE_SYMBOL,
        "OVERNIGHT",
        private_uploader.XOP_CURRENCY,
        primaryExchange=private_uploader.XOP_PRIMARY_EXCHANGE,
    )


def ib_bar_prices(ib: IB, contract, day: date, what: str, timeout: float) -> dict[str, float]:
    # The Chinese day is fully contained in the 24-hour history window ending
    # at 15:00 Shanghai.  useRTH=False is required: XOP's dynamic China-time
    # points are its US overnight/pre-market stream, not SMART RTH history.
    end = datetime.combine(day, clock_time(15, 0), private_uploader.SHANGHAI)
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
    prices: dict[str, float] = {}
    for bar in bars:
        raw_time = getattr(bar, "date", None)
        if isinstance(raw_time, str):
            raw_time = util.parseIBDatetime(raw_time)
        if not isinstance(raw_time, datetime):
            continue
        if raw_time.tzinfo is None:
            raw_time = raw_time.replace(tzinfo=timezone.utc)
        timestamp = raw_time.astimezone(private_uploader.SHANGHAI).replace(second=0, microsecond=0)
        if timestamp.date() != day or not is_china_session(timestamp):
            continue
        price = positive(getattr(bar, "close", None))
        if price is not None:
            prices[timestamp.strftime("%H:%M")] = price
    return prices


def upload_rows(args: argparse.Namespace, rows: list[dict[str, Any]]) -> int:
    body = json.dumps({"rows": rows}, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(
        args.server.rstrip("/") + HISTORY_PATH,
        data=body,
        method="POST",
        headers=private_uploader.server_headers(args.server, args.token),
    )
    try:
        with private_uploader.open_server_request(
            request,
            args.timeout,
            args.origin_ip,
            args.origin_tls_insecure,
            args.origin_ca_file,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"private history upload HTTP {exc.code}: {detail}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"private history upload was not acknowledged: {payload}")
    imported = payload.get("imported")
    if not isinstance(imported, int):
        raise RuntimeError(f"private history upload omitted imported count: {payload}")
    return imported


def chunks(values: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def build_rows(
    day: date,
    market_prices: dict[str, float],
    bid_prices: dict[str, float],
    ask_prices: dict[str, float],
    pcf: private_uploader.PCFInput,
    fx: private_uploader.CFETSQuote,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    for minute, market_price in sorted(market_prices.items()):
        bid = bid_prices.get(minute)
        ask = ask_prices.get(minute)
        if bid is None or ask is None or ask < bid:
            skipped += 1
            continue
        timestamp = datetime.combine(day, clock_time.fromisoformat(minute), private_uploader.SHANGHAI)
        ib = private_uploader.IBQuote(
            bid=bid,
            ask=ask,
            last=None,
            market_data_type="Live",
            observed_at=timestamp,
        )
        private_input = private_uploader.build_private_payload(
            pcf,
            fx,
            ib,
            generated_at=timestamp,
            source=HISTORY_SOURCE,
        )
        rows.append({
            "minute": private_uploader.iso_timestamp(timestamp),
            "market_price": market_price,
            "input": private_input,
        })
    return rows, skipped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument("--origin-tls-insecure", action="store_true", default=private_uploader.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    parser.add_argument("--runtime-dir", default=os.getenv("NNN_PRIVATE_RUNTIME_DIR", "scripts/.runtime/private_valuation"))
    parser.add_argument("--ib-host", default=os.getenv("NNN_PRIVATE_IB_HOST", "127.0.0.1"))
    parser.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_PRIVATE_IB_PORT", "7496")))
    # Keep ad-hoc history replays away from the persistent XOP family stream
    # (159519), cutover canary (159520), and daily calibration client (159521).
    parser.add_argument("--ib-client-id", type=int, default=int(os.getenv("NNN_PRIVATE_HISTORY_IB_CLIENT_ID", "159522")))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--pcf-interval", type=float, default=10.0, help="minimum seconds between PCF requests")
    parser.add_argument("--day-delay", type=float, default=20.0, help="wait after one BID+ASK date replay")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--start", type=parse_day, default=None, help="inclusive YYYYMMDD")
    parser.add_argument("--end", type=parse_day, default=None, help="inclusive YYYYMMDD")
    parser.add_argument("--env-file", default=".sina-uploader.env")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    private_uploader.load_env_file(args.env_file)
    if args.server == "https://1navs.com":
        args.server = os.getenv("NNN_SERVER_URL", args.server)
    if not args.token:
        args.token = os.getenv("NNN_UPLOAD_TOKEN", "")
    if not args.origin_ip:
        args.origin_ip = os.getenv("NNN_ORIGIN_IP", "")
    if not args.origin_ca_file:
        args.origin_ca_file = os.getenv("NNN_ORIGIN_CA_FILE", "")
    if not args.origin_tls_insecure:
        args.origin_tls_insecure = private_uploader.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", ""))
    if not args.token:
        raise SystemExit("NNN_UPLOAD_TOKEN is required (pass --env-file or export it)")
    if args.batch_size < 1 or args.batch_size > 500:
        raise SystemExit("--batch-size must be between 1 and 500")
    if args.start and args.end and args.start > args.end:
        raise SystemExit("--start must not be after --end")

    days = historical_days(args.server, args.timeout, args.start, args.end)
    if not days:
        print("no public SZ159518 minute-history dates in the requested interval", flush=True)
        return 0
    pcf_client = private_uploader.PCFClient(
        args.runtime_dir,
        lookback_days=0,
        request_pacer=private_uploader.PCFRequestPacer(args.pcf_interval),
    )
    cfets_client = private_uploader.CFETSClient(lookback_days=0)
    ib = IB()
    imported_total = 0
    failed_days: list[str] = []
    try:
        print(
            f"connecting to TWS {args.ib_host}:{args.ib_port} for {len(days)} replay dates",
            flush=True,
        )
        # This process needs only market-data history.  Call the low-level
        # client connect (as the live private collector does) rather than
        # IB.connect(): the latter starts account/portfolio synchronisation,
        # which can block historical replay when TWS has a slow account API.
        ib.wrapper.clientId = args.ib_client_id
        ib.client.connect(args.ib_host, args.ib_port, args.ib_client_id, timeout=args.timeout)
        if not ib.isConnected():
            raise RuntimeError("TWS API socket did not become connected")
        print("TWS API connected; using pinned XOP OVERNIGHT/ARCA contract", flush=True)
        contract = overnight_xop_contract()
        print(f"replaying {len(days)} dates with XOP {contract.exchange}/{contract.primaryExchange}", flush=True)
        for index, day in enumerate(days, start=1):
            try:
                pcf = pcf_client.fetch_latest(day)
                fx = cfets_client.fetch_latest(day)
                if pcf.trading_day != day or fx.trading_day != day:
                    raise RuntimeError(
                        f"dated inputs unavailable: pcf={pcf.trading_day.isoformat()} fx={fx.trading_day.isoformat()}"
                    )
                market_prices = public_prices(args.server, day, args.timeout)
                bid_prices = ib_bar_prices(ib, contract, day, "BID", args.timeout)
                ask_prices = ib_bar_prices(ib, contract, day, "ASK", args.timeout)
                rows, skipped = build_rows(day, market_prices, bid_prices, ask_prices, pcf, fx)
                imported = sum(upload_rows(args, batch) for batch in chunks(rows, args.batch_size))
                imported_total += imported
                print(
                    f"{index}/{len(days)} {day_key(day)} imported={imported} skipped={skipped} "
                    f"public={len(market_prices)} bid={len(bid_prices)} ask={len(ask_prices)} total={imported_total}",
                    flush=True,
                )
            except Exception as exc:  # Keep later trade dates replayable after one bad source day.
                failed_days.append(day_key(day))
                print(f"{index}/{len(days)} {day_key(day)} SKIP {exc}", file=sys.stderr, flush=True)
            if index < len(days) and args.day_delay > 0:
                time.sleep(args.day_delay)
    finally:
        ib.disconnect()
    print(f"finished imported={imported_total} failed={','.join(failed_days) or 'none'}", flush=True)
    return 1 if failed_days else 0


if __name__ == "__main__":
    raise SystemExit(main())

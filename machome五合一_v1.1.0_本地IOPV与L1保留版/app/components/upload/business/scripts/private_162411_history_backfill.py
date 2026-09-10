#!/usr/bin/env python3
"""Replay private SZ162411 minute valuation on the SAFE central-parity basis.

The live 162411 collector publishes the SAFE daily USD/CNY central parity as
the current FX.  Minutes persisted before the 2026-09-04 SAFE cutover used the
shared CFETS spot and therefore cannot be compared with the public weighted
anchor page.  This script rebuilds those minutes exactly as the SAFE model
would have produced them:

- official NAV: the latest Eastmoney official NAV whose publication day is
  strictly before the replay day (T 日晚 ~22:31 发布，所以 D 日盘中基准 = D-1 交易日官方净值)
- XOP anchor: the last completed US RTH 16:00 close on or before the base NAV
  date (TRADES 1-minute bars, last bar close)
- base SAFE parity: SAFE USD/CNY central parity of the base NAV date
- current SAFE parity: SAFE USD/CNY central parity of the replay day
- XOP current quote: IBKR OVERNIGHT BID/ASK 1-minute bars for the China session
- domestic price: public SZ162411 minute price from the public minute API

Every replay day is uploaded with ``replace_day`` so the private history chart
contains only SAFE-basis points for that day.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, time as clock_time, timezone
from pathlib import Path
from typing import Any, Iterable

from ib_insync import IB, Stock, util

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_162411_valuation_uploader as lof162411  # noqa: E402
import private_nasdaq_valuation_uploader as safe_common  # noqa: E402
import private_valuation_uploader as common  # noqa: E402


SYMBOL = lof162411.SYMBOL
HISTORY_SOURCE = "mac-home-private-162411-safe-history-backfill"
HISTORY_PATH = "/api/v1/private/funds/SZ162411/minute-history/import"
NAV_HISTORY_URL = "https://api.fund.eastmoney.com/f10/lsjz"
NEW_YORK = lof162411.NEW_YORK


def parse_day(value: str) -> date:
    normalized = value.strip().replace("-", "")
    return datetime.strptime(normalized, "%Y%m%d").date()


def day_key(value: date) -> str:
    return value.strftime("%Y%m%d")


def is_china_session(value: datetime) -> bool:
    local = value.astimezone(common.SHANGHAI)
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
        headers=common.SOURCE_HEADERS,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"public API returned {type(payload).__name__}, not an object")
    return payload


def historical_days(server: str, timeout: float, start: date | None, end: date | None) -> list[date]:
    payload = public_json(server, f"/api/v1/funds/{SYMBOL}/minute-history/dates?limit=120", timeout)
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
    payload = public_json(server, f"/api/v1/funds/{SYMBOL}/minute-history?date={day_key(day)}", timeout)
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
            timestamp = datetime.combine(day, clock_time.fromisoformat(minute), common.SHANGHAI)
        except ValueError:
            continue
        if not is_china_session(timestamp):
            continue
        market_price = positive(row.get("mkp"))
        if market_price is not None:
            prices[minute] = market_price
    return prices


def public_official_est(server: str, day: date, timeout: float) -> float:
    """Official (base) NAV the public engine actually used on that day."""
    payload = public_json(server, f"/api/v1/funds/{SYMBOL}/minute-history?date={day_key(day)}", timeout)
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"public minute-history rows missing for {day_key(day)}")
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("min") or "").strip() == "09:30":
            value = positive(row.get("oest"))
            if value is not None:
                return value
    return 0.0


def fetch_official_navs(start: date, end: date, timeout: float) -> dict[date, float]:
    """Eastmoney lsjz caps a page at 20 newest records; paginate until covered."""
    navs: dict[date, float] = {}
    page = 1
    while True:
        query = urllib.parse.urlencode({
            "fundCode": "162411", "pageIndex": str(page), "pageSize": "20",
            "startDate": start.isoformat(), "endDate": end.isoformat(),
        })
        request = urllib.request.Request(
            NAV_HISTORY_URL + "?" + query,
            headers={
                **common.SOURCE_HEADERS,
                "Referer": "https://fundf10.eastmoney.com/jjjz_162411.html",
                "Accept": "application/json,text/plain,*/*",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Eastmoney 162411 NAV history unavailable: {exc}") from exc
        records = payload.get("Data", {}).get("LSJZList")
        if not isinstance(records, list) or not records:
            break
        added = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            day = parse_day(str(record.get("FSRQ") or ""))
            value = positive(record.get("DWJZ"))
            if value is not None:
                navs.setdefault(day, value)
                added += 1
        if added < 20:
            break
        page += 1
        if page > 50:
            break
    return navs


def base_nav_for(
    navs: dict[date, float],
    official_est: float,
    replay_day: date,
    trading_days: list[date],
) -> tuple[date, float]:
    """Latest NAV date whose official value the public engine used on replay_day.

    SZ162411 NAVs are published the evening of the following trading day
    (T+1 22:31), so a NAV date N is visible during the replay-day session only
    when the next trading day after N is strictly before replay_day.  The
    public minute chart exposes ``oest`` = the base NAV the live engine used,
    which pins the exact value; the date is resolved from the publication rule
    and must be unique (0.940700-style duplicate values resolve to the later
    date that was already visible).
    """
    candidates: list[date] = []
    for index, day in enumerate(trading_days):
        if day >= replay_day:
            break
        if index + 1 >= len(trading_days):
            break
        next_day = trading_days[index + 1]
        if next_day < replay_day and abs(navs[day] - official_est) < 1e-6:
            candidates.append(day)
    if not candidates:
        raise RuntimeError(
            f"no published NAV matches live official_est {official_est:.4f} "
            f"before {replay_day.isoformat()}"
        )
    base_day = max(candidates)
    return base_day, navs[base_day]


def overnight_xop_contract() -> Stock:
    return Stock(
        common.REFERENCE_SYMBOL,
        "OVERNIGHT",
        common.XOP_CURRENCY,
        primaryExchange=common.XOP_PRIMARY_EXCHANGE,
    )


def smart_xop_contract() -> Stock:
    return Stock(
        common.REFERENCE_SYMBOL,
        "SMART",
        common.XOP_CURRENCY,
        primaryExchange=common.XOP_PRIMARY_EXCHANGE,
    )


def session_close_anchor(ib: IB, base_day: date, timeout: float) -> lof162411.BaseReference:
    """XOP 16:00 ET RTH close on the base NAV date (carry ≤ 7 days).

    The 16:00 ET close of ``base_day`` happens at 04:00 Beijing time on the
    next calendar day, so the history window must end the following morning.
    Using a cutoff on the base day itself would return the previous session.
    """
    cutoff = datetime(
        base_day.year, base_day.month, base_day.day, 8, 30, tzinfo=common.SHANGHAI
    ) + timedelta(days=1)
    contract = smart_xop_contract()
    bars = ib.reqHistoricalData(
        contract, cutoff, "10 D", "1 min", "TRADES", True, 2, False, [], timeout
    )
    if not bars:
        raise RuntimeError(f"no XOP RTH TRADES history on or before {base_day.isoformat()}")
    by_day: dict[date, list[Any]] = {}
    for bar in bars:
        raw_time = getattr(bar, "date", None)
        if isinstance(raw_time, str):
            raw_time = util.parseIBDatetime(raw_time)
        if not isinstance(raw_time, datetime):
            continue
        if raw_time.tzinfo is None:
            raw_time = raw_time.replace(tzinfo=timezone.utc)
        session_day = raw_time.astimezone(NEW_YORK).date()
        by_day.setdefault(session_day, []).append(bar)
    candidates = sorted(day for day in by_day if day <= base_day)
    if not candidates:
        raise RuntimeError(f"no completed XOP RTH session on or before {base_day.isoformat()}")
    session_day = candidates[-1]
    if (base_day - session_day).days > 7:
        raise RuntimeError(f"XOP anchor carry {session_day}->{base_day} exceeds seven days")
    last_bar = by_day[session_day][-1]
    close = positive(getattr(last_bar, "close", None))
    if close is None:
        raise RuntimeError(f"XOP RTH close missing for {session_day.isoformat()}")
    raw_time = getattr(last_bar, "date", None)
    if isinstance(raw_time, str):
        raw_time = util.parseIBDatetime(raw_time)
    if raw_time.tzinfo is None:
        raw_time = raw_time.replace(tzinfo=timezone.utc)
    observed_at = raw_time.astimezone(common.SHANGHAI)
    target_at = datetime(
        session_day.year, session_day.month, session_day.day, 16, 0, tzinfo=NEW_YORK
    ).astimezone(common.SHANGHAI)
    return lof162411.BaseReference(
        price=close,
        anchor_day=session_day,
        target_at=target_at,
        observed_at=observed_at,
        source="ibkr_rth_trades_1m:" + day_key(session_day),
        capture_status="history_backfill",
    )


def ib_bar_prices(ib: IB, contract, day: date, what: str, timeout: float) -> dict[str, float]:
    end = datetime.combine(day, clock_time(15, 0), common.SHANGHAI)
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
        timestamp = raw_time.astimezone(common.SHANGHAI).replace(second=0, microsecond=0)
        if timestamp.date() != day or not is_china_session(timestamp):
            continue
        price = positive(getattr(bar, "close", None))
        if price is not None:
            prices[timestamp.strftime("%H:%M")] = price
    return prices


def build_rows(
    day: date,
    market_prices: dict[str, float],
    bid_prices: dict[str, float],
    ask_prices: dict[str, float],
    base_nav: float,
    base_day: date,
    base_reference: lof162411.BaseReference,
    base_fx: Any,
    current_fx: Any,
    source: str,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    for minute, market_price in sorted(market_prices.items()):
        bid = bid_prices.get(minute)
        ask = ask_prices.get(minute)
        if bid is None or ask is None or ask < bid:
            skipped += 1
            continue
        timestamp = datetime.combine(day, clock_time.fromisoformat(minute), common.SHANGHAI)
        ib = common.IBQuote(
            bid=bid,
            ask=ask,
            last=None,
            market_data_type="Live",
            observed_at=timestamp,
        )
        seed = lof162411.PublicModelSeed(
            base_nav=base_nav,
            base_nav_date=base_day,
            base_nav_source="eastmoney",
            fetched_at=timestamp,
            base_reference=base_reference,
            effective_ratio=lof162411.DEFAULT_EFFECTIVE_RATIO,
            ratio_source="weighted_anchor",
        )
        private_input = lof162411.build_payload(
            seed,
            base_fx,
            current_fx,
            ib,
            timestamp,
            source=source,
            contract="XOP STK OVERNIGHT/ARCA conId=413951498",
            quote_session="us_overnight_live",
        )
        rows.append({
            "minute": common.iso_timestamp(timestamp),
            "market_price": market_price,
            "input": private_input,
        })
    return rows, skipped


def upload_day(args: argparse.Namespace, rows: list[dict[str, Any]]) -> int:
    body = json.dumps(
        {"rows": rows, "replace_day": True}, ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    request = urllib.request.Request(
        args.server.rstrip("/") + HISTORY_PATH,
        data=body,
        method="POST",
        headers=common.server_headers(args.server, args.token),
    )
    try:
        with common.open_server_request(
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    parser.add_argument("--ib-host", default=os.getenv("NNN_PRIVATE_IB_HOST", "192.168.1.111"))
    parser.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_PRIVATE_IB_PORT", "7496")))
    parser.add_argument("--ib-client-id", type=int, default=int(os.getenv("NNN_PRIVATE_162411_HISTORY_IB_CLIENT_ID", "159523")))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--day-delay", type=float, default=15.0, help="wait after one day replay")
    parser.add_argument("--start", type=parse_day, default=None, help="inclusive YYYYMMDD")
    parser.add_argument("--end", type=parse_day, default=None, help="inclusive YYYYMMDD")
    parser.add_argument("--env-file", default=".sina-uploader.env")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    common.load_env_file(args.env_file)
    if args.server == "https://1navs.com":
        args.server = os.getenv("NNN_SERVER_URL", args.server)
    if not args.token:
        args.token = os.getenv("NNN_UPLOAD_TOKEN", "")
    if not args.origin_ip:
        args.origin_ip = os.getenv("NNN_ORIGIN_IP", "")
    if not args.origin_ca_file:
        args.origin_ca_file = os.getenv("NNN_ORIGIN_CA_FILE", "")
    if not args.origin_tls_insecure:
        args.origin_tls_insecure = common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", ""))
    if not args.token:
        raise SystemExit("NNN_UPLOAD_TOKEN is required (pass --env-file or export it)")
    if args.start and args.end and args.start > args.end:
        raise SystemExit("--start must not be after --end")

    days = historical_days(args.server, args.timeout, args.start, args.end)
    if not days:
        print(f"no public {SYMBOL} minute-history dates in the requested interval", flush=True)
        return 0
    nav_start = (min(days) - timedelta(days=10)) if days else date.today()
    navs = fetch_official_navs(nav_start, max(days), args.timeout)
    if not navs:
        raise SystemExit("Eastmoney official NAV history is empty")
    trading_days = sorted(set(navs) | set(days))

    ib = IB()
    imported_total = 0
    failed_days: list[str] = []
    try:
        print(
            f"connecting to TWS {args.ib_host}:{args.ib_port} for {len(days)} replay dates",
            flush=True,
        )
        ib.wrapper.clientId = args.ib_client_id
        ib.client.connect(args.ib_host, args.ib_port, args.ib_client_id, timeout=args.timeout)
        if not ib.isConnected():
            raise RuntimeError("TWS API socket did not become connected")
        print("TWS API connected; using pinned XOP OVERNIGHT/ARCA + SMART/ARCA contracts", flush=True)
        overnight = overnight_xop_contract()
        for index, day in enumerate(days, start=1):
            try:
                official_est = public_official_est(args.server, day, args.timeout)
                if official_est <= 0:
                    raise RuntimeError(f"public official_est missing for {day_key(day)}")
                base_day, base_nav = base_nav_for(navs, official_est, day, trading_days)
                base_reference = session_close_anchor(ib, base_day, args.timeout)
                base_fx = safe_common.fetch_safe_central_parity(
                    base_day, args.timeout, safe_common.NASDAQ_FAMILY
                )
                current_fx = safe_common.fetch_safe_central_parity(
                    day, args.timeout, safe_common.NASDAQ_FAMILY
                )
                market_prices = public_prices(args.server, day, args.timeout)
                bid_prices = ib_bar_prices(ib, overnight, day, "BID", args.timeout)
                ask_prices = ib_bar_prices(ib, overnight, day, "ASK", args.timeout)
                rows, skipped = build_rows(
                    day, market_prices, bid_prices, ask_prices,
                    base_nav, base_day, base_reference, base_fx, current_fx,
                    HISTORY_SOURCE,
                )
                if not rows:
                    raise RuntimeError(f"no replay rows could be built (base {base_day.isoformat()})")
                imported = upload_day(args, rows)
                imported_total += imported
                print(
                    f"{index}/{len(days)} {day_key(day)} base={day_key(base_day)} "
                    f"nav={base_nav:.4f} oest={official_est:.4f} anchor={base_reference.price:.4f} "
                    f"safe={base_fx.rate:.4f}/{current_fx.rate:.4f} "
                    f"imported={imported} skipped={skipped} "
                    f"public={len(market_prices)} bid={len(bid_prices)} ask={len(ask_prices)} "
                    f"total={imported_total}",
                    flush=True,
                )
            except Exception as exc:
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
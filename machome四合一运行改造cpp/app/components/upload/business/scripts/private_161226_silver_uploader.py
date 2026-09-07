#!/usr/bin/env python3
"""Upload SZ161226 dual AG estimates and optionally backfill today + N days.

Live collection is intentionally limited to Shanghai weekdays
09:15-10:15, 10:30-11:30, and 13:30-15:00. Night-session rows are never uploaded.
The live AG quote is a persistent Eastmoney SSE stream; legacy Sina endpoints are
retained only for the explicit historical backfill mode.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as clock_time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_valuation_uploader as common  # noqa: E402
import upload_monitor_status as upload_health  # noqa: E402


SYMBOL = "SZ161226"
MODEL_VERSION = "private.cn-future.ag-settlement.v1"
VALUATION_KIND = "silver_settlement"
SELECTION_VERSION = "shfe-ag-even-month-roll-day10.v1"
PREVIOUS_SETTLEMENT_SOURCE = "SINA_SHFE_DAILY_SETTLEMENT"
MINLINE_AVERAGE_BASIS = "sina_minline_cumulative_average"
DAILY_SETTLEMENT_BASIS = "sina_shfe_official_daily_settlement"
EASTMONEY_FUTURES_SOURCE = "EASTMONEY_FUTURES_SSE"
EASTMONEY_SETTLEMENT_SOURCE = "EASTMONEY_FUTURES_SSE_PREVIOUS_SETTLEMENT"
EASTMONEY_AVERAGE_BASIS = "eastmoney_futures_sse_cumulative_turnover_vwap"
EASTMONEY_FUTURES_SSE_ENDPOINT = "https://futsseapi.eastmoney.com"
EASTMONEY_FUTURES_TOKEN = "1101ffec61617c99be287c1bec3085ff"
EASTMONEY_FUTURES_FIELDS = ("name", "sc", "dm", "p", "utime", "zjsj", "cje", "vol", "j")
EASTMONEY_FUTURES_MARKET = "113"
AG_CONTRACT_MULTIPLIER = 15.0
MAX_QUOTE_AGE_SECONDS = 180.0
LIVE_SOURCE = "mac-home-private-161226-silver-uploader"
BACKFILL_SOURCE = "mac-home-private-161226-silver-backfill"
EASTMONEY_URL = "https://api.fund.eastmoney.com/f10/lsjz?fundCode=161226&pageIndex=1&pageSize=40"
SINA_DAILY_URL = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20agDaily=/InnerFuturesNewService.getDailyKLine?symbol={}"
SINA_MINLINE_URL = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20agMinute=/InnerFuturesNewService.getMinLine?symbol={}"
SHANGHAI = common.SHANGHAI
STOP = False


class SourceUnavailableError(RuntimeError):
    """A required source or cross-source audit check failed."""


def positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def in_collection_window(value: datetime) -> bool:
    local = value.astimezone(SHANGHAI)
    if local.weekday() >= 5:
        return False
    second = local.hour * 3600 + local.minute * 60 + local.second
    return (
        9 * 3600 + 15 * 60 <= second < 10 * 3600 + 15 * 60
        or 10 * 3600 + 30 * 60 <= second < 11 * 3600 + 30 * 60
        or 13 * 3600 + 30 * 60 <= second < 15 * 3600
    )


def ag_contract(value: date) -> str:
    year, month = value.year, value.month
    if month % 2:
        month += 1
    elif value.day >= 10:
        month += 2
    if month > 12:
        month -= 12
        year += 1
    return f"AG{year % 100:02d}{month:02d}"


def request_bytes(url: str, timeout: float, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers=headers or common.SOURCE_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise SourceUnavailableError(f"request failed {url}: {exc}") from exc


def request_json(url: str, timeout: float, headers: dict[str, str] | None = None) -> Any:
    try:
        return json.loads(request_bytes(url, timeout, headers).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceUnavailableError(f"invalid JSON from {url}: {exc}") from exc


def request_jsonp_array(url: str, timeout: float) -> list[Any]:
    """Read a legacy Sina response used only by explicit backfill operations."""
    text = request_bytes(url, timeout, {"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}).decode("utf-8", errors="replace")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise SourceUnavailableError(f"Sina JSONP array missing from {url}")
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise SourceUnavailableError(f"invalid Sina JSONP from {url}: {exc}") from exc
    if not isinstance(payload, list):
        raise SourceUnavailableError(f"Sina JSONP from {url} is not an array")
    return payload


def fetch_navs(timeout: float) -> list[tuple[date, float]]:
    payload = request_json(
        EASTMONEY_URL,
        timeout,
        {"Referer": "https://fundf10.eastmoney.com/jjjz_161226.html", "User-Agent": "Mozilla/5.0"},
    )
    rows = payload.get("Data", {}).get("LSJZList", []) if isinstance(payload, dict) else []
    result: list[tuple[date, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            nav_day = datetime.strptime(str(row.get("FSRQ") or ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        nav = positive(row.get("DWJZ"))
        if nav is not None:
            result.append((nav_day, nav))
    result.sort(reverse=True)
    if not result:
        raise SourceUnavailableError("Eastmoney returned no positive SZ161226 NAV")
    return result


def base_nav_for_day(navs: list[tuple[date, float]], trading_day: date) -> tuple[date, float]:
    for nav_day, nav in navs:
        if nav_day < trading_day:
            return nav_day, nav
    raise SourceUnavailableError(f"no published NAV before {trading_day}")


def fetch_daily(contract: str, timeout: float) -> dict[date, dict[str, float]]:
    """Fetch legacy Sina daily rows for explicit backfill operations."""
    result: dict[date, dict[str, float]] = {}
    for row in request_jsonp_array(SINA_DAILY_URL.format(contract), timeout):
        if not isinstance(row, dict):
            continue
        try:
            day = datetime.strptime(str(row.get("d") or ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        close, settlement = positive(row.get("c")), positive(row.get("s"))
        if close is not None and settlement is not None:
            result[day] = {"close": close, "settlement": settlement}
    if not result:
        raise SourceUnavailableError(f"Sina returned no daily AG rows for {contract}")
    return result


def fetch_minline(contract: str, timeout: float) -> dict[str, dict[str, float]]:
    """Fetch legacy Sina minute rows for explicit backfill operations."""
    result: dict[str, dict[str, float]] = {}
    for row in request_jsonp_array(SINA_MINLINE_URL.format(contract), timeout):
        if not isinstance(row, list) or len(row) < 3:
            continue
        minute = str(row[0] or "")[:5]
        try:
            parsed = clock_time.fromisoformat(minute)
        except ValueError:
            continue
        price, average = positive(row[1]), positive(row[2])
        volume = positive(row[3]) if len(row) > 3 else None
        if price is not None and average is not None:
            result[minute] = {"price": price, "average": average, "volume": volume or 0}
    if not result:
        raise SourceUnavailableError(f"Sina returned no minute AG rows for {contract}")
    return result


def source_json(args: argparse.Namespace, path: str) -> dict[str, Any]:
    request = urllib.request.Request(args.server.rstrip("/") + path, headers=common.SOURCE_HEADERS)
    try:
        with common.open_server_request(request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SourceUnavailableError(f"website source failed {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SourceUnavailableError(f"website source {path} is not an object")
    return payload


def public_trading_dates(args: argparse.Namespace, limit: int) -> list[date]:
    payload = source_json(args, f"/api/v1/funds/{SYMBOL}/minute-history/dates?limit={limit}")
    result: list[date] = []
    for value in payload.get("dates", []):
        try:
            result.append(datetime.strptime(str(value), "%Y%m%d").date())
        except ValueError:
            continue
    if not result:
        raise SourceUnavailableError("website returned no SZ161226 trading dates")
    return result


def public_market_prices(args: argparse.Namespace, day: date) -> dict[str, float]:
    payload = source_json(args, f"/api/v1/funds/{SYMBOL}/minute-history?date={day:%Y%m%d}")
    result: dict[str, float] = {}
    for row in payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        minute = str(row.get("min") or "")[:5]
        price = positive(row.get("mkp"))
        if price is not None:
            result[minute] = price
    if not result:
        raise SourceUnavailableError(f"website returned no SZ161226 market prices for {day}")
    return result


def build_input(
    *, trading_day: date, observed_at: datetime, contract: str, base_nav_day: date,
    base_nav: float, previous_settlement: float, futures_price: float,
    intraday_average: float, average_basis: str, source: str, generated_at: datetime,
    previous_settlement_source: str = PREVIOUS_SETTLEMENT_SOURCE,
    futures_source: str = "SINA_INNER_FUTURES_NEW_SERVICE",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "symbol": SYMBOL,
        "model_version": MODEL_VERSION,
        "valuation_kind": VALUATION_KIND,
        "silver": {
            "base_nav": base_nav,
            "base_nav_date": base_nav_day.isoformat(),
            "base_nav_source": "EASTMONEY_F10_LSJZ",
            "base_nav_fetched_at": common.iso_timestamp(datetime.now(SHANGHAI)),
            "trading_day": trading_day.isoformat(),
            "contract": contract,
            "contract_selection_version": SELECTION_VERSION,
            "previous_settlement": previous_settlement,
            "previous_settlement_date": base_nav_day.isoformat(),
            "previous_settlement_source": previous_settlement_source,
            "futures_price": futures_price,
            "intraday_average": intraday_average,
            "intraday_average_basis": average_basis,
            "observed_at": common.iso_timestamp(observed_at),
            "source": futures_source,
        },
        "source": source,
        "generated_at": common.iso_timestamp(generated_at),
    }


def post_input(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source") or LIVE_SOURCE).strip()
    request = urllib.request.Request(
        args.server.rstrip("/") + f"/api/v1/private/inputs/{SYMBOL}",
        data=common.gzip_json_body(payload),
        method="POST",
        headers=common.gzip_server_headers(args.server, args.token),
    )
    try:
        with common.open_server_request(request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error = SourceUnavailableError(f"private input HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}")
        upload_health.record_failure(source, stage="private_input_upload", error=error)
        raise error from exc
    except Exception as exc:
        upload_health.record_failure(source, stage="private_input_upload", error=exc)
        raise
    if not isinstance(result, dict) or result.get("ok") is not True:
        error = SourceUnavailableError(f"private input was not acknowledged: {result}")
        upload_health.record_failure(source, stage="private_input_ack", error=error)
        raise error
    upload_health.record_success(source, stage="private_input_ack", accepted=1, symbols=[SYMBOL])
    return result


def post_history(args: argparse.Namespace, rows: list[dict[str, Any]]) -> int:
    body = gzip.compress(json.dumps({"rows": rows, "replace_day": True}, ensure_ascii=False, allow_nan=False).encode("utf-8"), compresslevel=6, mtime=0)
    request = urllib.request.Request(
        args.server.rstrip("/") + f"/api/v1/private/funds/{SYMBOL}/minute-history/import",
        data=body,
        method="POST",
        headers=common.gzip_server_headers(args.server, args.token),
    )
    try:
        with common.open_server_request(request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SourceUnavailableError(f"history import HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise SourceUnavailableError(f"history import was not acknowledged: {result}")
    return int(result.get("imported") or 0)


def eastmoney_sse_url(contract: str, endpoint: str = EASTMONEY_FUTURES_SSE_ENDPOINT) -> str:
    """Build the single, stable Eastmoney SSE endpoint for an SHFE contract."""
    query = urllib.parse.urlencode(
        {"token": EASTMONEY_FUTURES_TOKEN, "field": ",".join(EASTMONEY_FUTURES_FIELDS)}
    )
    return f"{endpoint.rstrip('/')}/sse/{EASTMONEY_FUTURES_MARKET}_{contract.lower()}_qt?{query}"


def iter_sse_data(lines: Any) -> Any:
    """Yield complete SSE data fields from a byte- or text-line iterator."""
    parts: list[str] = []
    for raw_line in lines:
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace")
        else:
            line = str(raw_line)
        line = line.rstrip("\r\n")
        if not line:
            if parts:
                yield "\n".join(parts)
                parts = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field != "data":
            continue
        if separator and value.startswith(" "):
            value = value[1:]
        parts.append(value)
    if parts:
        yield "\n".join(parts)


def eastmoney_quote_stream(contract: str, timeout: float, endpoint: str) -> Any:
    """Connect to one fixed SSE host and yield merged full/delta quote snapshots."""
    url = eastmoney_sse_url(contract, endpoint)
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache", "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "text/event-stream" not in content_type:
                raise SourceUnavailableError(f"Eastmoney SSE returned unexpected content type {content_type!r}")
            state: dict[str, Any] = {}
            received = False
            for data in iter_sse_data(response):
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise SourceUnavailableError(f"invalid Eastmoney SSE JSON: {exc}") from exc
                quote = event.get("qt") if isinstance(event, dict) else None
                if not isinstance(quote, dict):
                    continue
                state.update(quote)
                received = True
                yield dict(state)
            detail = "ended before a quote" if not received else "ended unexpectedly"
            raise SourceUnavailableError(f"Eastmoney SSE stream {detail}")
    except SourceUnavailableError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise SourceUnavailableError(f"Eastmoney SSE request failed {url}: {exc}") from exc


def payload_from_eastmoney_quote(
    args: argparse.Namespace, quote: dict[str, Any], navs: list[tuple[date, float]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a merged SSE quote and convert it to the existing private-input schema."""
    generated_at = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    contract = ag_contract(generated_at.date())
    market = str(quote.get("sc") or "")
    code = str(quote.get("dm") or "").upper()
    if market != EASTMONEY_FUTURES_MARKET or code != contract:
        raise SourceUnavailableError(
            f"Eastmoney SSE contract mismatch: expected {EASTMONEY_FUTURES_MARKET}.{contract}, got {market}.{code}"
        )
    try:
        observed_at = datetime.fromtimestamp(int(quote.get("utime")), SHANGHAI)
    except (TypeError, ValueError, OSError) as exc:
        raise SourceUnavailableError("Eastmoney SSE quote has no valid utime") from exc
    age = (generated_at - observed_at).total_seconds()
    if observed_at.date() != generated_at.date() or not in_collection_window(observed_at):
        raise SourceUnavailableError(f"Eastmoney SSE quote time {common.iso_timestamp(observed_at)} is outside today's collection window")
    if age < -30 or age > MAX_QUOTE_AGE_SECONDS:
        raise SourceUnavailableError(
            f"Eastmoney SSE quote {common.iso_timestamp(observed_at)} is stale or future-dated (age={age:.0f}s)"
        )
    futures_price = positive(quote.get("p"))
    previous_settlement = positive(quote.get("zjsj"))
    turnover = positive(quote.get("cje"))
    volume = positive(quote.get("vol"))
    if None in (futures_price, previous_settlement, turnover, volume):
        raise SourceUnavailableError("Eastmoney SSE quote is missing positive p/zjsj/cje/vol fields")
    intraday_average = turnover / (volume * AG_CONTRACT_MULTIPLIER)
    displayed_average = positive(quote.get("j"))
    if displayed_average is not None and abs(intraday_average - displayed_average) > 2:
        raise SourceUnavailableError(
            f"Eastmoney SSE turnover VWAP {intraday_average:.4f} disagrees with displayed average {displayed_average:.4f}"
        )
    base_day, base_nav = base_nav_for_day(navs, generated_at.date())
    return build_input(
        trading_day=generated_at.date(), observed_at=observed_at, contract=contract,
        base_nav_day=base_day, base_nav=base_nav,
        previous_settlement=previous_settlement, futures_price=futures_price,
        intraday_average=intraday_average, average_basis=EASTMONEY_AVERAGE_BASIS,
        source=args.source, generated_at=generated_at,
        previous_settlement_source=EASTMONEY_SETTLEMENT_SOURCE,
        futures_source=EASTMONEY_FUTURES_SOURCE,
    )


def live_payloads(args: argparse.Namespace) -> Any:
    """Yield valid live payloads from one persistent Eastmoney SSE connection."""
    now = datetime.now(SHANGHAI)
    if not in_collection_window(now):
        raise SourceUnavailableError("outside 09:15-10:15, 10:30-11:30, or 13:30-15:00 Shanghai collection window")
    contract = ag_contract(now.date())
    navs = fetch_navs(args.timeout)
    for quote in eastmoney_quote_stream(contract, args.timeout, args.sse_endpoint):
        current = datetime.now(SHANGHAI)
        if not in_collection_window(current):
            return
        try:
            yield payload_from_eastmoney_quote(args, quote, navs, current)
        except SourceUnavailableError:
            # A new connection may initially replay the previous session. Keep the
            # same stream open until its first complete, current quote arrives.
            continue


def live_payload(args: argparse.Namespace) -> dict[str, Any]:
    for payload in live_payloads(args):
        return payload
    raise SourceUnavailableError("Eastmoney SSE produced no valid current-session quote")


def backfill(args: argparse.Namespace) -> int:
    dates = public_trading_dates(args, args.backfill_days + 3)[:args.backfill_days + 1]
    navs = fetch_navs(args.timeout)
    today = datetime.now(SHANGHAI).date()
    daily_cache: dict[str, dict[date, dict[str, float]]] = {}
    minute_cache: dict[str, dict[str, dict[str, float]]] = {}
    total = 0
    for trading_day in reversed(dates):
        contract = ag_contract(trading_day)
        daily = daily_cache.setdefault(contract, fetch_daily(contract, args.timeout))
        base_day, base_nav = base_nav_for_day(navs, trading_day)
        if base_day not in daily:
            raise SourceUnavailableError(f"{contract} has no previous settlement for NAV date {base_day}")
        market = public_market_prices(args, trading_day)
        rows: list[dict[str, Any]] = []
        if trading_day == today:
            minutes = minute_cache.setdefault(contract, fetch_minline(contract, args.timeout))
            for minute, market_price in sorted(market.items()):
                future = minutes.get(minute)
                if future is None:
                    continue
                observed_at = datetime.combine(trading_day, clock_time.fromisoformat(minute), SHANGHAI)
                if not in_collection_window(observed_at):
                    continue
                value = build_input(
                    trading_day=trading_day, observed_at=observed_at, contract=contract,
                    base_nav_day=base_day, base_nav=base_nav,
                    previous_settlement=daily[base_day]["settlement"],
                    futures_price=future["price"], intraday_average=future["average"],
                    average_basis=MINLINE_AVERAGE_BASIS, source=BACKFILL_SOURCE, generated_at=observed_at,
                )
                rows.append({"minute": common.iso_timestamp(observed_at), "market_price": market_price, "input": value})
        else:
            target = daily.get(trading_day)
            if target is None:
                raise SourceUnavailableError(f"{contract} has no daily close/settlement for {trading_day}")
            minute = "15:00" if "15:00" in market else max(market)
            observed_at = datetime.combine(trading_day, clock_time.fromisoformat(minute), SHANGHAI)
            value = build_input(
                trading_day=trading_day, observed_at=observed_at, contract=contract,
                base_nav_day=base_day, base_nav=base_nav,
                previous_settlement=daily[base_day]["settlement"],
                futures_price=target["close"], intraday_average=target["settlement"],
                average_basis=DAILY_SETTLEMENT_BASIS, source=BACKFILL_SOURCE, generated_at=observed_at,
            )
            rows.append({"minute": common.iso_timestamp(observed_at), "market_price": market[minute], "input": value})
        if not rows:
            raise SourceUnavailableError(f"no matched backfill rows for {trading_day}")
        if args.dry_run:
            print(f"dry-run day={trading_day} contract={contract} rows={len(rows)} base={base_day} nav={base_nav}", flush=True)
            continue
        imported = post_history(args, rows)
        total += imported
        print(f"backfilled day={trading_day} contract={contract} rows={imported} base={base_day} previous_settlement={daily[base_day]['settlement']}", flush=True)
        if trading_day == today:
            post_input(args, rows[-1]["input"])
            print(f"seeded current private input from {rows[-1]['minute']}", flush=True)
    return total


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    result.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", "40")))
    result.add_argument("--interval", type=float, default=float(os.getenv("NNN_PRIVATE_161226_UPLOAD_INTERVAL", "10")))
    result.add_argument(
        "--reconnect-interval", type=float,
        default=float(os.getenv("NNN_PRIVATE_161226_SSE_RECONNECT_INTERVAL", "5")),
    )
    result.add_argument(
        "--sse-endpoint",
        default=os.getenv("NNN_PRIVATE_161226_SSE_ENDPOINT", EASTMONEY_FUTURES_SSE_ENDPOINT),
        help="Fixed Eastmoney SSE origin. No numbered-node rotation is performed.",
    )
    result.add_argument("--source", default=os.getenv("NNN_PRIVATE_161226_SOURCE", LIVE_SOURCE))
    result.add_argument("--once", action="store_true")
    result.add_argument("--backfill-days", type=int, default=0, help="Backfill today plus this many prior trading days.")
    result.add_argument("--dry-run", action="store_true")
    return result


def stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def main() -> int:
    common.load_env_file(".sina-uploader.env")
    args = parser().parse_args()
    if not args.token:
        args.token = os.getenv("NNN_UPLOAD_TOKEN", "")
    if not args.token and not args.dry_run:
        print("NNN_UPLOAD_TOKEN or --token is required", file=sys.stderr)
        return 2
    if args.backfill_days < 0 or args.backfill_days > 20:
        print("--backfill-days must be between 0 and 20", file=sys.stderr)
        return 2
    if args.backfill_days:
        imported = backfill(args)
        print(f"backfill complete imported={imported}", flush=True)
        return 0
    if args.once:
        payload = live_payload(args)
        if args.dry_run:
            print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
        else:
            post_input(args, payload)
            print(f"uploaded {payload['silver']['contract']} at {payload['silver']['observed_at']}", flush=True)
        return 0
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not STOP:
        now = datetime.now(SHANGHAI)
        if not in_collection_window(now):
            time.sleep(30)
            continue
        try:
            last_upload = 0.0
            last_observed_at = ""
            for payload in live_payloads(args):
                if STOP:
                    break
                observed_at = str(payload["silver"]["observed_at"])
                elapsed = time.monotonic() - last_upload
                if observed_at == last_observed_at or (last_upload and elapsed < max(args.interval, 1)):
                    continue
                try:
                    post_input(args, payload)
                except Exception as exc:
                    print(
                        f"{common.iso_timestamp(datetime.now(SHANGHAI))} ERROR upload failed: {exc}; "
                        f"reconnect={args.reconnect_interval:.0f}s",
                        file=sys.stderr, flush=True,
                    )
                    time.sleep(max(args.reconnect_interval, 1))
                    break
                last_upload = time.monotonic()
                last_observed_at = observed_at
                print(
                    f"{common.iso_timestamp(datetime.now(SHANGHAI))} uploaded "
                    f"contract={payload['silver']['contract']} price={payload['silver']['futures_price']} "
                    f"average={payload['silver']['intraday_average']} "
                    f"prev_settle={payload['silver']['previous_settlement']} source={EASTMONEY_FUTURES_SOURCE}",
                    flush=True,
                )
        except Exception as exc:  # launchd must keep retrying transient source failures
            if in_collection_window(datetime.now(SHANGHAI)):
                upload_health.record_failure(args.source, stage="eastmoney_sse", error=exc)
                print(
                    f"{common.iso_timestamp(datetime.now(SHANGHAI))} ERROR {exc}; "
                    f"reconnect={args.reconnect_interval:.0f}s",
                    file=sys.stderr, flush=True,
                )
                time.sleep(max(args.reconnect_interval, 1))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SourceUnavailableError as exc:
        print(f"ERROR {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)

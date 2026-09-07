#!/usr/bin/env python3
"""Backfill auditable one-minute private valuation history for SH513350/SZ159605.

The script deliberately replays only source data that is independently
available for the historical Chinese trading minute:

* public ETF minute prices already stored by the website;
* the official dated SSE/SZSE PCF and CFETS USD/CNY 16:30 spot close; and
* IBKR TWS historical BID and ASK bars.

It writes only ``private_valuation_snapshots`` through the protected private
history import API.  It never replaces public history or a fund's current
private input.  Every stored row uses a real one-minute IBKR historical BID
and ASK bar; values are never interpolated or forward-filled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ib_insync import Contract, IB, Stock, util

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_159605_valuation_uploader as multi  # noqa: E402
import private_513350_spot_close as sh_spot_close  # noqa: E402
import private_valuation_uploader as xop  # noqa: E402


SH513350 = "SH513350"
SZ159605 = "SZ159605"
SUPPORTED = (SH513350, SZ159605)
SOURCE = "mac-local-private-month-history-backfill"
HISTORY_PATH = "/api/v1/private/funds/{symbol}/minute-history/import"
FULLGOAL_PCF_API = "https://wap.fullgoal.com.cn/ws-business-server/fund/getFundSg"
SH_MODEL_VERSION = "private.total-basket.xop-cfets-pcf.sh513350.v1"
SH_REDEMPTION_UNIT = 1_000_000.0
SH_FIXED_XOP_EQUIVALENT_SHARES = 1_046.0
HISTORICAL_MARKET_DATA_TYPE = "HistoricalBidAsk"
HISTORICAL_IB_SOURCE = "IBKR_TWS_HISTORICAL_BID_ASK"
HISTORY_BAR_SIZE = "1 min"


class SourceUnavailableError(RuntimeError):
    """A dated source cannot safely construct a private historical row."""


@dataclass(frozen=True)
class SHPCF:
    trading_day: date
    pre_trading_day: date | None
    redemption: str
    redemption_unit: float
    estimated_cash_cny: float
    nav_per_cu: float
    component_count: int
    source_url: str
    sha256: str

    def to_payload(self, xop_equivalent_shares: float) -> dict[str, Any]:
        return {
            "security_id": "513350",
            "trading_day": self.trading_day.isoformat(),
            "pre_trading_day": self.pre_trading_day.isoformat() if self.pre_trading_day else None,
            "redemption": self.redemption,
            "creation_redemption_unit": self.redemption_unit,
            "estimate_cash_component_cny": self.estimated_cash_cny,
            "nav_per_cu": self.nav_per_cu,
            "component_count": self.component_count,
            "xop_equivalent_shares": xop_equivalent_shares,
            "source_url": self.source_url,
            "sha256": self.sha256,
        }


def parse_day(value: str) -> date:
    return datetime.strptime(str(value).strip().replace("-", ""), "%Y%m%d").date()


def day_key(value: date) -> str:
    return value.strftime("%Y%m%d")


def positive(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def shanghai_timestamp(day: date, minute: str) -> datetime:
    return datetime.combine(day, clock_time.fromisoformat(minute), xop.SHANGHAI)


def is_china_session(value: datetime) -> bool:
    local = value.astimezone(xop.SHANGHAI)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    return (9 * 60 + 30 <= minute <= 11 * 60 + 30) or (13 * 60 <= minute <= 15 * 60)


def source_json(server: str, path: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(server.rstrip("/") + path, headers=xop.SOURCE_HEADERS)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == 2:
                raise SourceUnavailableError(f"{path} request failed after retries: {exc}") from exc
            time.sleep(1 + attempt)
    else:  # pragma: no cover - loop either breaks or raises
        raise SourceUnavailableError(f"{path} request failed: {last_error}")
    if not isinstance(value, dict):
        raise SourceUnavailableError(f"{path} returned {type(value).__name__}, not an object")
    return value


def public_days(
    server: str,
    symbol: str,
    timeout: float,
    start: date | None,
    end: date | None,
    skip_dates: set[date] | None = None,
) -> list[date]:
    payload = source_json(server, f"/api/v1/funds/{symbol}/minute-history/dates?limit=120", timeout)
    values = payload.get("dates")
    if not isinstance(values, list):
        raise SourceUnavailableError(f"{symbol} public minute-history dates are missing")
    available: set[date] = set()
    for raw in values:
        try:
            candidate = parse_day(raw)
        except (TypeError, ValueError):
            continue
        if candidate.weekday() < 5:
            available.add(candidate)
    if not available:
        return []
    resolved_end = end or max(available)
    resolved_start = start or (resolved_end - timedelta(days=29))
    excluded = skip_dates or set()
    return [candidate for candidate in sorted(available) if resolved_start <= candidate <= resolved_end and candidate not in excluded]


def public_prices(server: str, symbol: str, day: date, timeout: float) -> dict[str, float]:
    payload = source_json(server, f"/api/v1/funds/{symbol}/minute-history?date={day_key(day)}", timeout)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise SourceUnavailableError(f"{symbol} public minute rows are missing for {day_key(day)}")
    prices: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        minute = str(row.get("min") or "").strip()
        if len(minute) != 5 or minute[2] != ":":
            continue
        try:
            timestamp = shanghai_timestamp(day, minute)
        except ValueError:
            continue
        price = positive(row.get("mkp"))
        if price is not None and is_china_session(timestamp):
            prices[minute] = price
    return prices


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xml_text(root: ET.Element, field: str, *, required: bool = True) -> str:
    values = [str(child.text or "").strip() for child in root if local_name(child.tag) == field]
    if len(values) > 1 or (required and (len(values) != 1 or not values[0])):
        raise SourceUnavailableError(f"SSE PCF {field} must appear exactly once")
    return values[0] if values else ""


def parse_sh_pcf(raw: bytes, source_url: str, expected_day: date) -> SHPCF:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceUnavailableError(f"invalid SSE PCF XML: {exc}") from exc
    if local_name(root.tag) != "SSEPortfolioCompositionFile":
        raise SourceUnavailableError("SSE PCF has an unexpected root element")
    if xml_text(root, "FundInstrumentID") != "513350":
        raise SourceUnavailableError("SSE PCF FundInstrumentID is not 513350")
    trading_day = parse_day(xml_text(root, "TradingDay"))
    if trading_day != expected_day:
        raise SourceUnavailableError(f"SSE PCF day {trading_day} does not match {expected_day}")
    pre_text = xml_text(root, "PreTradingDay", required=False)
    try:
        unit = float(xml_text(root, "CreationRedemptionUnit"))
        cash = float(xml_text(root, "EstimatedCashComponent"))
        nav = float(xml_text(root, "NAVperCU"))
        count = int(float(xml_text(root, "RecordNumber")))
    except ValueError as exc:
        raise SourceUnavailableError(f"invalid numeric SSE PCF field: {exc}") from exc
    if unit != SH_REDEMPTION_UNIT or not math.isfinite(cash) or nav <= 0 or count <= 0:
        raise SourceUnavailableError("SSE PCF unit/cash/NAV/component count is invalid")
    # SSE exposes a single creation/redemption switch.  A non-zero switch means
    # both directions are enabled for the dated basket; zero is retained as N
    # so the historical snapshot remains descriptive rather than actionable.
    redemption = "Y" if xml_text(root, "CreationRedemptionSwitch") != "0" else "N"
    return SHPCF(
        trading_day=trading_day,
        pre_trading_day=parse_day(pre_text) if pre_text else None,
        redemption=redemption,
        redemption_unit=unit,
        estimated_cash_cny=cash,
        nav_per_cu=nav,
        component_count=count,
        source_url=source_url,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def fetch_sh_pcf(day: date, timeout: float) -> SHPCF:
    # The SSE file-download endpoint intentionally exposes only the current
    # PCF, even when a date is supplied.  The fund manager's official endpoint
    # retains the daily primary-market fields needed for this XOP-proxy model.
    source_url = FULLGOAL_PCF_API + "?" + urllib.parse.urlencode({
        "productCode": "513350", "tradeDate": day.isoformat(),
    })
    request = urllib.request.Request(source_url, headers=xop.SOURCE_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceUnavailableError(f"Fullgoal PCF fetch failed for {day_key(day)}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
        data = payload["data"]
    except (UnicodeDecodeError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise SourceUnavailableError("Fullgoal PCF response is not a data object") from exc
    if not isinstance(data, dict) or int(payload.get("code", -1)) != 0:
        raise SourceUnavailableError(f"Fullgoal PCF request was not acknowledged: {payload}")
    try:
        trading_day = parse_day(data.get("tradingDay") or data.get("tradeDate"))
        pre_text = str(data.get("ptradeDate") or "").strip()
        unit = float(data["minShdy"])
        cash = float(data["minYgcash"])
        nav = float(data["minShnav"])
        count = int(float(data["recordNumber"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceUnavailableError(f"Fullgoal dated PCF fields are incomplete: {exc}") from exc
    if trading_day != day or unit != SH_REDEMPTION_UNIT or not math.isfinite(cash) or nav <= 0 or count <= 0:
        raise SourceUnavailableError("Fullgoal dated PCF has an invalid day/unit/cash/NAV/component count")
    if_sh = str(data.get("ifSh") or "")
    return SHPCF(
        trading_day=trading_day,
        pre_trading_day=parse_day(pre_text) if pre_text else None,
        redemption="Y" if "赎回" in if_sh else "N",
        redemption_unit=unit,
        estimated_cash_cny=cash,
        nav_per_cu=nav,
        component_count=count,
        source_url=source_url,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def contract_for_component(component: multi.Component):
    if component.market == "HK":
        return Contract(symbol=multi.ib_contract_symbol(component), secType="STK", exchange="SEHK", currency="HKD"), True
    if component.market == "US":
        return Stock(component.symbol, "OVERNIGHT", "USD"), False
    raise SourceUnavailableError(f"unsupported component market {component.market}")


def normalize_bar_timestamp(value: Any) -> datetime | None:
    if isinstance(value, str):
        value = util.parseIBDatetime(value)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(xop.SHANGHAI).replace(second=0, microsecond=0)


def fetch_bar_series(
    ib: IB,
    contract: Any,
    *,
    end: datetime,
    what: str,
    use_rth: bool,
    start: date,
    request_delay: float,
) -> dict[tuple[date, str], float]:
    qualified = ib.qualifyContracts(contract)
    if len(qualified) != 1:
        raise SourceUnavailableError(f"IB could not uniquely qualify {contract}")
    bars = ib.reqHistoricalData(
        qualified[0], end, "1 M", HISTORY_BAR_SIZE, what, use_rth, 2, False, [], 60
    )
    if request_delay > 0:
        time.sleep(request_delay)
    values: dict[tuple[date, str], float] = {}
    for bar in bars:
        timestamp = normalize_bar_timestamp(getattr(bar, "date", None))
        price = positive(getattr(bar, "close", None))
        if timestamp is None or price is None or timestamp.date() < start or not is_china_session(timestamp):
            continue
        values[(timestamp.date(), timestamp.strftime("%H:%M"))] = price
    return values


def fx_for_day(day: date, timeout: float) -> sh_spot_close.SpotCloseQuote:
    # Historical SH513350 replay is intentionally after-close: use the dated
    # 16:30 USD/CNY spot close, never the later 18:00 hourly reference rate.
    # Keep timeout in the signature to avoid changing the backfill call shape.
    del timeout
    try:
        return sh_spot_close.quote_for_day(day)
    except sh_spot_close.SpotCloseRateError as exc:
        raise SourceUnavailableError(str(exc)) from exc


def chunks(values: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def upload_rows(args: argparse.Namespace, symbol: str, rows: list[dict[str, Any]]) -> int:
    imported = 0
    for batch in chunks(rows, args.batch_size):
        body = json.dumps({"rows": batch}, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            args.server.rstrip("/") + HISTORY_PATH.format(symbol=symbol),
            data=body,
            method="POST",
            headers=xop.server_headers(args.server, args.token),
        )
        try:
            with xop.open_server_request(
                request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{symbol} historical upload HTTP {exc.code}: {detail}") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError(f"{symbol} historical upload was not acknowledged: {payload}")
        imported += int(payload.get("imported") or 0)
    return imported


def sh_payload(day: date, minute: str, market_price: float, pcf: SHPCF, fx: sh_spot_close.SpotCloseQuote, bid: float, ask: float, shares: float) -> dict[str, Any]:
    timestamp = shanghai_timestamp(day, minute)
    return {
        "schema_version": 1,
        "symbol": SH513350,
        "model_version": SH_MODEL_VERSION,
        "pcf": pcf.to_payload(shares),
        "fx": {
            "pair": "USD/CNY", "rate": fx.rate, "trading_day": day.isoformat(),
            "quote_time": fx.quote_time, "source": fx.source,
            "fetched_at": xop.iso_timestamp(fx.observed_at),
        },
        "ib": {
            "symbol": "XOP", "bid": bid, "ask": ask, "last": None,
            "market_data_type": HISTORICAL_MARKET_DATA_TYPE,
            "source": HISTORICAL_IB_SOURCE, "observed_at": xop.iso_timestamp(timestamp),
        },
        "source": SOURCE,
        "generated_at": xop.iso_timestamp(timestamp),
    }


def build_sh_rows(
    day: date, prices: dict[str, float], pcf: SHPCF, fx: sh_spot_close.SpotCloseQuote,
    bids: dict[tuple[date, str], float], asks: dict[tuple[date, str], float],
) -> tuple[list[dict[str, Any]], int]:
    common_minutes = sorted(
        minute for minute in prices if (day, minute) in bids and (day, minute) in asks and asks[(day, minute)] >= bids[(day, minute)]
    )
    if not common_minutes:
        raise SourceUnavailableError(f"SH513350 has no common public/XOP BID/ASK minutes for {day}")
    shares = SH_FIXED_XOP_EQUIVALENT_SHARES
    rows = [
        {"minute": xop.iso_timestamp(shanghai_timestamp(day, minute)), "market_price": prices[minute],
         "input": sh_payload(day, minute, prices[minute], pcf, fx, bids[(day, minute)], asks[(day, minute)], shares)}
        for minute in common_minutes
    ]
    return rows, len(prices) - len(rows)


def multi_payload(day: date, minute: str, pcf: multi.PCFInput, rates: tuple[multi.FXQuote, ...], component_prices: dict[tuple[str, str], tuple[float, float]], market_price: float) -> dict[str, Any]:
    timestamp = shanghai_timestamp(day, minute)
    historical_rates = tuple(
        multi.FXQuote(rate.pair, rate.rate, rate.trading_day, rate.quote_time, timestamp) for rate in rates
    )
    quotes = tuple(
        multi.MarketQuote(
            symbol=component.symbol, market=component.market, currency=component.currency,
            bid=component_prices[(component.market, component.symbol)][0],
            ask=component_prices[(component.market, component.symbol)][1], last=None,
            market_data_type=HISTORICAL_MARKET_DATA_TYPE, observed_at=timestamp,
        )
        for component in pcf.components
    )
    return multi.build_private_payload(pcf, historical_rates, quotes, generated_at=timestamp, source=SOURCE)


def build_multi_rows(
    day: date, prices: dict[str, float], pcf: multi.PCFInput, rates: tuple[multi.FXQuote, ...],
    series: dict[tuple[str, str, str], dict[tuple[date, str], float]],
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    for minute, market_price in sorted(prices.items()):
        component_prices: dict[tuple[str, str], tuple[float, float]] = {}
        for component in pcf.components:
            key = (component.market, component.symbol)
            bid = series.get((component.market, component.symbol, "BID"), {}).get((day, minute))
            ask = series.get((component.market, component.symbol, "ASK"), {}).get((day, minute))
            if bid is None or ask is None or ask < bid:
                component_prices = {}
                break
            component_prices[key] = (bid, ask)
        if not component_prices:
            skipped += 1
            continue
        timestamp = shanghai_timestamp(day, minute)
        rows.append({
            "minute": xop.iso_timestamp(timestamp),
            "market_price": market_price,
            "input": multi_payload(day, minute, pcf, rates, component_prices, market_price),
        })
    return rows, skipped


def backfill_sh(args: argparse.Namespace, ib: IB, days: list[date], end_at: datetime) -> tuple[int, list[str]]:
    if not days:
        return 0, []
    start = days[0]
    xop_contract = Stock("XOP", "OVERNIGHT", "USD", primaryExchange="ARCA")
    bids = fetch_bar_series(ib, xop_contract, end=end_at, what="BID", use_rth=False, start=start, request_delay=args.ib_request_delay)
    asks = fetch_bar_series(ib, xop_contract, end=end_at, what="ASK", use_rth=False, start=start, request_delay=args.ib_request_delay)
    imported_total, failed = 0, []
    for index, day in enumerate(days, 1):
        try:
            rows, skipped = build_sh_rows(day, public_prices(args.server, SH513350, day, args.timeout), fetch_sh_pcf(day, args.timeout), fx_for_day(day, args.timeout), bids, asks)
            imported = 0 if args.dry_run else upload_rows(args, SH513350, rows)
            imported_total += imported
            print(f"{SH513350} {index}/{len(days)} {day_key(day)} rows={len(rows)} skipped={skipped} imported={imported}", flush=True)
        except Exception as exc:
            failed.append(day_key(day))
            print(f"{SH513350} {index}/{len(days)} {day_key(day)} SKIP {exc}", file=sys.stderr, flush=True)
    return imported_total, failed


def backfill_multi(args: argparse.Namespace, ib: IB, days: list[date], end_at: datetime) -> tuple[int, list[str]]:
    if not days:
        return 0, []
    start = days[0]
    pacer = xop.PCFRequestPacer(args.pcf_request_delay, state_path=args.pcf_pacer_state)
    pcf_client = multi.PCFClient(args.runtime_dir, timeout=args.timeout, refresh_seconds=0, lookback_days=0, request_pacer=pacer)
    configs: dict[date, tuple[multi.PCFInput, tuple[multi.FXQuote, ...], dict[str, float]]] = {}
    components: dict[tuple[str, str], multi.Component] = {}
    failed: list[str] = []
    for index, day in enumerate(days, 1):
        try:
            pcf = pcf_client.fetch_latest(day, force_refresh=True)
            if pcf.trading_day != day:
                raise SourceUnavailableError(f"PCF date {pcf.trading_day} does not match {day}")
            rates = multi.CFETSClient(timeout=args.timeout).fetch_for_day(day)
            configs[day] = (pcf, rates, public_prices(args.server, SZ159605, day, args.timeout))
            components.update({(component.market, component.symbol): component for component in pcf.components})
            print(f"{SZ159605} source {index}/{len(days)} {day_key(day)} components={len(pcf.components)}", flush=True)
        except Exception as exc:
            failed.append(day_key(day))
            print(f"{SZ159605} source {index}/{len(days)} {day_key(day)} SKIP {exc}", file=sys.stderr, flush=True)
    series: dict[tuple[str, str, str], dict[tuple[date, str], float]] = {}
    for index, component in enumerate(components.values(), 1):
        contract, use_rth = contract_for_component(component)
        for what in ("BID", "ASK"):
            try:
                series[(component.market, component.symbol, what)] = fetch_bar_series(
                    ib, contract, end=end_at, what=what, use_rth=use_rth, start=start,
                    request_delay=args.ib_request_delay,
                )
                print(f"{SZ159605} IB {index}/{len(components)} {component.market}:{component.symbol} {what}", flush=True)
            except Exception as exc:
                print(f"{SZ159605} IB {component.market}:{component.symbol} {what} unavailable: {exc}", file=sys.stderr, flush=True)
    imported_total = 0
    for index, day in enumerate(days, 1):
        if day not in configs:
            continue
        try:
            pcf, rates, prices = configs[day]
            rows, skipped = build_multi_rows(day, prices, pcf, rates, series)
            if not rows:
                raise SourceUnavailableError("no minute has all 30 historical BID/ASK component prices")
            imported = 0 if args.dry_run else upload_rows(args, SZ159605, rows)
            imported_total += imported
            print(f"{SZ159605} {index}/{len(days)} {day_key(day)} rows={len(rows)} skipped={skipped} imported={imported}", flush=True)
        except Exception as exc:
            failed.append(day_key(day))
            print(f"{SZ159605} {index}/{len(days)} {day_key(day)} SKIP {exc}", file=sys.stderr, flush=True)
    return imported_total, sorted(set(failed))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument("--origin-tls-insecure", action="store_true", default=xop.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    parser.add_argument("--symbols", default=",".join(SUPPORTED))
    parser.add_argument("--start", type=parse_day)
    parser.add_argument("--end", type=parse_day)
    parser.add_argument("--skip-dates", default="", help="comma-separated historical dates to skip, YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", type=int, default=7496)
    parser.add_argument("--ib-client-id", type=int, default=159696)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--ib-request-delay", type=float, default=11.0, help="seconds between IB historical requests")
    parser.add_argument("--pcf-request-delay", type=float, default=10.0)
    parser.add_argument("--pcf-pacer-state", default="/tmp/newnavnav-private-pcf-pacer.json")
    parser.add_argument("--runtime-dir", default="scripts/.runtime/private_month_history_backfill")
    parser.add_argument("--batch-size", type=int, default=300)
    parser.add_argument("--env-file", default=".sina-uploader.env")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    xop.load_env_file(args.env_file)
    if not args.token:
        args.token = os.getenv("NNN_UPLOAD_TOKEN", "")
    if not args.origin_ip:
        args.origin_ip = os.getenv("NNN_ORIGIN_IP", "")
    if not args.origin_ca_file:
        args.origin_ca_file = os.getenv("NNN_ORIGIN_CA_FILE", "")
    if not args.origin_tls_insecure:
        args.origin_tls_insecure = xop.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", ""))
    selected = tuple(item.strip().upper() for item in args.symbols.replace("，", ",").split(",") if item.strip())
    if not selected or any(item not in SUPPORTED for item in selected):
        raise SystemExit(f"--symbols must use only {', '.join(SUPPORTED)}")
    if args.start and args.end and args.start > args.end:
        raise SystemExit("--start must not be after --end")
    try:
        skip_dates = {parse_day(item) for item in args.skip_dates.replace("，", ",").split(",") if item.strip()}
    except ValueError as exc:
        raise SystemExit(f"--skip-dates contains an invalid date: {exc}") from exc
    if args.batch_size < 1 or args.batch_size > 500:
        raise SystemExit("--batch-size must be between 1 and 500")
    if args.ib_request_delay < 0:
        raise SystemExit("--ib-request-delay must be non-negative")
    if not args.dry_run and not args.token:
        raise SystemExit("NNN_UPLOAD_TOKEN is required unless --dry-run is used")

    days_by_symbol = {
        symbol: public_days(args.server, symbol, args.timeout, args.start, args.end, skip_dates)
        for symbol in selected
    }
    available_days = [day for days in days_by_symbol.values() for day in days]
    if not available_days:
        print("no public minute history in the requested interval", flush=True)
        return 0
    end_at = datetime.combine(max(available_days), clock_time(15, 0), xop.SHANGHAI)
    print(f"history window={min(available_days)}..{max(available_days)} cadence={HISTORY_BAR_SIZE} dry_run={args.dry_run}", flush=True)
    ib = IB()
    failed: dict[str, list[str]] = {}
    imported: dict[str, int] = {}
    try:
        ib.wrapper.clientId = args.ib_client_id
        ib.client.connect(args.ib_host, args.ib_port, args.ib_client_id, timeout=args.timeout)
        if not ib.isConnected():
            raise SourceUnavailableError("TWS API socket did not become connected")
        if SH513350 in selected:
            imported[SH513350], failed[SH513350] = backfill_sh(args, ib, days_by_symbol[SH513350], end_at)
        if SZ159605 in selected:
            imported[SZ159605], failed[SZ159605] = backfill_multi(args, ib, days_by_symbol[SZ159605], end_at)
    finally:
        ib.disconnect()
    print("finished " + " ".join(f"{symbol}=imported:{imported.get(symbol, 0)} failed:{','.join(failed.get(symbol, [])) or 'none'}" for symbol in selected), flush=True)
    return 1 if any(failed.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())

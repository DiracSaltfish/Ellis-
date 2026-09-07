#!/usr/bin/env python3
"""Backfill exact one-minute PCF valuations for private China-internet ETFs.

The replay starts from the dates exposed by each public ETF's minute-history
chart.  It reads the PCF archived for *that* day, fetches each unique overseas
component's historical BID and ASK only once across every selected fund, and
imports only minutes for which the full dated basket is available.  It never
forward-fills a quote or substitutes a later PCF.

SH513220 additionally needs an audited source of historical A-share one-minute
Bid/Ask.  Pass ``--cn-1m-csv`` exported from QMT (or the equivalent raw venue
feed); the script deliberately refuses to backfill that fund without it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timezone
from pathlib import Path
from typing import Any, Iterable

from ib_insync import Contract, IB, Stock, util

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_159605_valuation_uploader as fx_source  # noqa: E402
import private_china_internet_valuation_uploader as valuation  # noqa: E402
import private_valuation_uploader as common  # noqa: E402


HISTORY_PATH = "/api/v1/private/funds/{symbol}/minute-history/import"
SOURCE = "mac-local-private-china-internet-pcf-history-backfill"
HISTORICAL_IB_SOURCE = "IBKR_TWS_HISTORICAL_BID_ASK"
HISTORICAL_CN_SOURCE = "QMT_HISTORICAL_BID_ASK"
HISTORICAL_MARKET_DATA_TYPE = "HistoricalBidAsk"
HISTORY_BAR_SIZE = "1 min"
HISTORY_REQUEST_WINDOW_DAYS = 28
HISTORY_RETRY_WINDOW_DAYS = 7


class SourceUnavailableError(RuntimeError):
    """An auditable source required for one historical minute is unavailable."""


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
    return datetime.combine(day, clock_time.fromisoformat(minute), common.SHANGHAI)


def is_china_session(value: datetime) -> bool:
    local = value.astimezone(common.SHANGHAI)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    return (9 * 60 + 30 <= minute <= 11 * 60 + 30) or (13 * 60 <= minute <= 15 * 60)


def source_json(server: str, path: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(server.rstrip("/") + path, headers=common.SOURCE_HEADERS)
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


def public_days(server: str, symbol: str, timeout: float, start: date | None, end: date | None) -> list[date]:
    payload = source_json(server, f"/api/v1/funds/{symbol}/minute-history/dates?limit=120", timeout)
    raw_dates = payload.get("dates")
    if not isinstance(raw_dates, list):
        raise SourceUnavailableError(f"{symbol} public minute-history dates are missing")
    available: set[date] = set()
    for raw in raw_dates:
        try:
            candidate = parse_day(str(raw))
        except ValueError:
            continue
        if candidate.weekday() < 5:
            available.add(candidate)
    if not available:
        return []
    lower, upper = start or min(available), end or max(available)
    return [candidate for candidate in sorted(available) if lower <= candidate <= upper]


def public_prices(server: str, symbol: str, day: date, timeout: float) -> dict[str, float]:
    payload = source_json(server, f"/api/v1/funds/{symbol}/minute-history?date={day_key(day)}", timeout)
    values = payload.get("rows")
    if not isinstance(values, list):
        raise SourceUnavailableError(f"{symbol} public minute rows are missing for {day_key(day)}")
    prices: dict[str, float] = {}
    for row in values:
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


def direct_text(node: ET.Element, name: str, *, required: bool = True) -> str:
    values = [str(child.text or "").strip() for child in node if local_name(child.tag) == name]
    if len(values) > 1 or (required and (len(values) != 1 or not values[0])):
        raise SourceUnavailableError(f"PCF {name} must appear exactly once")
    return values[0] if values else ""


def parse_manager_513050_pcf(config: valuation.FundConfig, raw: bytes, path: Path, expected_day: date) -> valuation.PCF:
    """Parse the archived full manager PCF transformed into an XML envelope.

    The cache retains the manager endpoint and explicit data grade in the raw
    file.  It is accepted only when that grade asserts a complete PCF; the
    similar 513220 historical NAV mapping is intentionally rejected.
    """

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceUnavailableError(f"{path} is not XML") from exc
    if local_name(root.tag) != "SSEPortfolioCompositionFile":
        raise SourceUnavailableError(f"{path} has an unexpected PCF root")
    if direct_text(root, "FundInstrumentID") != config.security_id:
        raise SourceUnavailableError(f"{path} has a mismatched fund id")
    if direct_text(root, "BackfillDataGrade") != "基金公司完整 PCF":
        raise SourceUnavailableError(f"{config.symbol} archive is not a complete fund-manager PCF")
    source_url = direct_text(root, "BackfillSourceURL")
    if not source_url.startswith("https://"):
        raise SourceUnavailableError(f"{config.symbol} manager source URL is invalid")
    trading_day = valuation.as_date(direct_text(root, "TradingDay"), "TradingDay")
    if trading_day != expected_day:
        raise SourceUnavailableError(f"{config.symbol} PCF day {trading_day} does not match {expected_day}")
    pre_day = direct_text(root, "PreTradingDay", required=False)
    unit = valuation.finite(direct_text(root, "CreationRedemptionUnit"), "CreationRedemptionUnit", positive=True)
    cash = valuation.finite(direct_text(root, "EstimatedCashComponent"), "EstimatedCashComponent")
    nav = valuation.finite(direct_text(root, "NAVperCU"), "NAVperCU", positive=True)
    declared = int(valuation.finite(direct_text(root, "RecordNum"), "RecordNum", positive=True))
    if unit != 1_000_000.0:
        raise SourceUnavailableError(f"{config.symbol} PCF redemption unit is not 1,000,000")
    components: list[valuation.Component] = []
    for node in root.iter():
        if local_name(node.tag) != "Component":
            continue
        fields = {local_name(child.tag): str(child.text or "").strip() for child in node}
        quantity = valuation.finite(fields.get("ComponentVolume", ""), "ComponentVolume", positive=True)
        market_label, symbol = fields.get("Market", ""), fields.get("SecurityID", "")
        if market_label == "香港联合交易所":
            normalized, market, currency = valuation.market_for("103", symbol)
        elif market_label == "其他":
            normalized, market, currency = valuation.market_for("9999", symbol)
        else:
            raise SourceUnavailableError(f"{config.symbol} unsupported manager PCF market {market_label!r}")
        components.append(valuation.Component(normalized, fields.get("SecurityName", "").strip() or normalized, market, currency, quantity))
    if declared != len(components) or not components or len({component.key for component in components}) != len(components):
        raise SourceUnavailableError(f"{config.symbol} manager PCF component list is incomplete or duplicate")
    creation = "Y" if valuation.finite(direct_text(root, "Creation"), "Creation") != 0 else "N"
    redemption = "Y" if valuation.finite(direct_text(root, "Redemption"), "Redemption") != 0 else "N"
    return valuation.PCF(
        config, trading_day, valuation.as_date(pre_day, "PreTradingDay") if pre_day else None,
        creation, redemption, unit, cash, nav, tuple(components), source_url, hashlib.sha256(raw).hexdigest(),
    )


def load_archived_pcf(root: Path, config: valuation.FundConfig, day: date) -> valuation.PCF:
    path = root / config.security_id / f"{day:%Y%m%d}.xml"
    if not path.is_file():
        raise SourceUnavailableError(f"{config.symbol} lacks an archived dated PCF: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SourceUnavailableError(f"cannot read {path}: {exc}") from exc
    if config.symbol == "SH513050":
        return parse_manager_513050_pcf(config, raw, path, day)
    if config.symbol == "SH513220":
        raise SourceUnavailableError("SH513220 archive has no dated complete PCF; historical NAV mapping is not an eligible substitute")
    return valuation.parse_pcf(config, raw, config.source_url(day), day, enforce_expected_composition=False)


def contract_for_component(component: valuation.Component) -> tuple[Any, bool]:
    if component.market == "HK":
        return Contract(symbol=component.symbol.lstrip("0") or "0", secType="STK", exchange="SEHK", currency="HKD"), True
    if component.market == "US":
        return Stock(component.symbol, "OVERNIGHT", "USD"), False
    raise SourceUnavailableError(f"IB historical source does not support {component.market}:{component.symbol}")


def normalize_bar_timestamp(value: Any) -> datetime | None:
    if isinstance(value, str):
        value = util.parseIBDatetime(value)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(common.SHANGHAI).replace(second=0, microsecond=0)


def historical_request_windows(days: Iterable[date], window_days: int = HISTORY_REQUEST_WINDOW_DAYS) -> list[list[date]]:
    if window_days < 1:
        raise ValueError("window_days must be positive")
    values, windows, current = sorted(set(days)), [], []
    for day in values:
        if current and (day - current[0]).days >= window_days:
            windows.append(current)
            current = []
        current.append(day)
    if current:
        windows.append(current)
    return windows


def fetch_bar_series(ib: IB, contract: Any, *, what: str, use_rth: bool, days: Iterable[date], request_delay: float) -> dict[tuple[date, str], float]:
    qualified = ib.qualifyContracts(contract)
    if len(qualified) != 1:
        raise SourceUnavailableError(f"IB could not uniquely qualify {contract}")

    def request_window(window: list[date], duration_days: int) -> dict[tuple[date, str], float]:
        end = datetime.combine(window[-1], clock_time(15, 0), common.SHANGHAI)
        try:
            bars = ib.reqHistoricalData(qualified[0], end, f"{duration_days} D", HISTORY_BAR_SIZE, what, use_rth, 2, False, [], 60)
        except Exception:
            return {}
        if request_delay:
            time.sleep(request_delay)
        requested_days, result = set(window), {}
        for bar in bars:
            timestamp, price = normalize_bar_timestamp(getattr(bar, "date", None)), positive(getattr(bar, "close", None))
            if timestamp is None or price is None or timestamp.date() not in requested_days or not is_china_session(timestamp):
                continue
            result[(timestamp.date(), timestamp.strftime("%H:%M"))] = price
        return result

    series: dict[tuple[date, str], float] = {}
    for window in historical_request_windows(days):
        result = request_window(window, HISTORY_REQUEST_WINDOW_DAYS)
        if not result and len(window) > 1:
            for retry in historical_request_windows(window, HISTORY_RETRY_WINDOW_DAYS):
                result.update(request_window(retry, HISTORY_RETRY_WINDOW_DAYS))
        series.update(result)
    return series


def csv_component_key(row: dict[str, str]) -> tuple[date, str, str]:
    raw_day = row.get("date") or row.get("trading_day") or ""
    raw_minute = row.get("minute") or row.get("time") or ""
    raw_symbol = (row.get("symbol") or row.get("security_id") or "").strip().upper()
    try:
        day = parse_day(raw_day)
    except ValueError as exc:
        raise SourceUnavailableError(f"CN history has invalid date {raw_day!r}") from exc
    minute = raw_minute.strip()[:5]
    if not __import__("re").fullmatch(r"\d{2}:\d{2}", minute) or not raw_symbol.isdigit() or len(raw_symbol) != 6:
        raise SourceUnavailableError("CN history requires date, minute and six-digit symbol columns")
    return day, minute, raw_symbol


def load_cn_bid_ask_csv(path: Path | None) -> dict[tuple[date, str, str], tuple[float, float]]:
    if path is None:
        return {}
    if not path.is_file():
        raise SourceUnavailableError(f"CN one-minute BID/ASK file does not exist: {path}")
    result: dict[tuple[date, str, str], tuple[float, float]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SourceUnavailableError("CN one-minute CSV has no header")
        for row in reader:
            key = csv_component_key(row)
            bid, ask = positive(row.get("bid")), positive(row.get("ask"))
            if bid is None or ask is None or ask < bid:
                raise SourceUnavailableError(f"CN history has invalid BID/ASK at {key}")
            if key in result:
                raise SourceUnavailableError(f"CN history has duplicate one-minute quote at {key}")
            result[key] = (bid, ask)
    return result


def load_ib_bid_ask_cache(root: Path | None) -> tuple[dict[tuple[str, str, str], dict[tuple[date, str], float]], dict[str, set[date]]]:
    """Load a completed local IB cache and identify venue-wide closed dates.

    A cache is only trusted after its manifest reaches ``completed``.  A day is
    considered a known market closure only when every cached series for that
    market lacks it, and only for dates explicitly covered by the cache's own
    manifest.  This prevents a short or illiquid individual series from being
    mistaken for a holiday.
    """

    if root is None:
        return {}, {}
    root = root.expanduser().resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise SourceUnavailableError(f"IB cache manifest does not exist: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceUnavailableError(f"IB cache manifest is invalid: {manifest_path}") from exc
    if manifest.get("status") != "completed":
        raise SourceUnavailableError(f"IB cache is not complete: {root}")
    raw_days = manifest.get("public_minute_history_days")
    if not isinstance(raw_days, list):
        raise SourceUnavailableError(f"IB cache manifest has no date coverage: {root}")
    try:
        covered_days = {parse_day(str(value)) for value in raw_days}
    except ValueError as exc:
        raise SourceUnavailableError(f"IB cache manifest contains an invalid date: {root}") from exc
    result: dict[tuple[str, str, str], dict[tuple[date, str], float]] = {}
    by_market: dict[str, list[dict[tuple[date, str], float]]] = {}
    for path in sorted((root / "series").glob("*.csv")):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise SourceUnavailableError(f"cannot read IB cache series: {path}") from exc
        if not rows:
            raise SourceUnavailableError(f"IB cache series is empty: {path}")
        first = rows[0]
        market, symbol, side = str(first.get("market") or ""), str(first.get("symbol") or ""), str(first.get("side") or "")
        key = market, symbol, side
        if market not in {"HK", "US"} or side not in {"BID", "ASK"} or not symbol or key in result:
            raise SourceUnavailableError(f"IB cache series identity is invalid or duplicate: {path}")
        series: dict[tuple[date, str], float] = {}
        for row in rows:
            if (str(row.get("market") or ""), str(row.get("symbol") or ""), str(row.get("side") or "")) != key:
                raise SourceUnavailableError(f"IB cache series contains mixed identities: {path}")
            try:
                trading_day = parse_day(str(row.get("trading_day") or ""))
            except ValueError as exc:
                raise SourceUnavailableError(f"IB cache series has an invalid day: {path}") from exc
            minute, price = str(row.get("minute") or "").strip(), positive(row.get("price"))
            if len(minute) != 5 or minute[2] != ":" or price is None:
                raise SourceUnavailableError(f"IB cache series has an invalid minute/price: {path}")
            series[(trading_day, minute)] = price
        result[key] = series
        by_market.setdefault(market, []).append(series)
    closed_days: dict[str, set[date]] = {}
    for market, series_list in by_market.items():
        closed_days[market] = {
            day for day in covered_days
            if all(not any(series_day == day for series_day, _minute in series) for series in series_list)
        }
    return result, closed_days


def make_payload(pcf: valuation.PCF, rates: tuple[fx_source.FXQuote, ...], quote_values: dict[tuple[str, str], tuple[float, float]], timestamp: datetime) -> dict[str, Any]:
    quotes: dict[tuple[str, str], valuation.Quote] = {}
    for component in pcf.components:
        bid_ask = quote_values.get(component.key)
        if bid_ask is None:
            raise SourceUnavailableError(f"{pcf.fund.symbol} lacks {component.market}:{component.symbol} at {timestamp:%F %R}")
        source = HISTORICAL_CN_SOURCE if component.market == "CN" else HISTORICAL_IB_SOURCE
        quotes[component.key] = valuation.Quote(component, bid_ask[0], bid_ask[1], None, source, HISTORICAL_MARKET_DATA_TYPE, timestamp)
    historical_rates = tuple(fx_source.FXQuote(rate.pair, rate.rate, rate.trading_day, rate.quote_time, timestamp) for rate in rates)
    return valuation.payload(pcf, historical_rates, quotes, timestamp, SOURCE)


def chunks(values: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def upload_rows(args: argparse.Namespace, symbol: str, rows: list[dict[str, Any]]) -> int:
    imported = 0
    for batch in chunks(rows, args.batch_size):
        request = urllib.request.Request(
            args.server.rstrip("/") + HISTORY_PATH.format(symbol=symbol),
            data=json.dumps({"rows": batch}, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            method="POST", headers=common.server_headers(args.server, args.token),
        )
        try:
            with common.open_server_request(request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{symbol} historical upload HTTP {exc.code}: {detail}") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError(f"{symbol} historical upload was not acknowledged: {payload}")
        imported += int(payload.get("imported") or 0)
    return imported


@dataclass(frozen=True)
class DayInput:
    pcf: valuation.PCF
    rates: tuple[fx_source.FXQuote, ...]
    prices: dict[str, float]


def resolve_sources(args: argparse.Namespace, days_by_symbol: dict[str, list[date]]) -> tuple[dict[tuple[str, date], DayInput], dict[tuple[str, str], valuation.Component], dict[str, list[str]]]:
    pcf_root = Path(args.pcf_cache_root).expanduser().resolve()
    cfets = fx_source.CFETSClient(timeout=args.timeout)
    values: dict[tuple[str, date], DayInput] = {}
    components: dict[tuple[str, str], valuation.Component] = {}
    failed: dict[str, list[str]] = {symbol: [] for symbol in days_by_symbol}
    for symbol, days in days_by_symbol.items():
        config = valuation.FUND_BY_SYMBOL[symbol]
        for index, day in enumerate(days, 1):
            try:
                pcf = load_archived_pcf(pcf_root, config, day)
                rates = cfets.fetch_for_day(day)
                prices = public_prices(args.server, symbol, day, args.timeout)
                if not prices:
                    raise SourceUnavailableError("public minute chart has no usable prices")
                values[(symbol, day)] = DayInput(pcf, rates, prices)
                components.update({component.key: component for component in pcf.components})
                print(f"{symbol} source {index}/{len(days)} {day_key(day)} components={len(pcf.components)}", flush=True)
            except Exception as exc:
                failed[symbol].append(day_key(day))
                print(f"{symbol} source {index}/{len(days)} {day_key(day)} SKIP {exc}", file=sys.stderr, flush=True)
    return values, components, failed


def collect_overseas_series(
    args: argparse.Namespace,
    ib: IB,
    components: dict[tuple[str, str], valuation.Component],
    target_days: Iterable[date],
    cached_series: dict[tuple[str, str, str], dict[tuple[date, str], float]] | None = None,
    known_market_closed_days: dict[str, set[date]] | None = None,
) -> dict[tuple[str, str, str], dict[tuple[date, str], float]]:
    result: dict[tuple[str, str, str], dict[tuple[date, str], float]] = {}
    overseas = [component for component in components.values() if component.market in {"HK", "US"}]
    requested_days = set(target_days)
    cached_series, known_market_closed_days = cached_series or {}, known_market_closed_days or {}
    for index, component in enumerate(sorted(overseas, key=lambda item: item.key), 1):
        contract, use_rth = contract_for_component(component)
        for what in ("BID", "ASK"):
            key = component.market, component.symbol, what
            values = dict(cached_series.get(key, {}))
            cached_days = {trading_day for trading_day, _minute in values}
            missing_days = sorted(requested_days - cached_days - known_market_closed_days.get(component.market, set()))
            try:
                if missing_days:
                    values.update(fetch_bar_series(ib, contract, what=what, use_rth=use_rth, days=missing_days, request_delay=args.ib_request_delay))
                    print(f"IB {index}/{len(overseas)} {component.market}:{component.symbol} {what} cache_days={len(cached_days)} requested_days={len(missing_days)}", flush=True)
                else:
                    print(f"IB {index}/{len(overseas)} {component.market}:{component.symbol} {what} cache-hit", flush=True)
                result[key] = values
            except Exception as exc:
                print(f"IB {component.market}:{component.symbol} {what} unavailable: {exc}", file=sys.stderr, flush=True)
    return result


def rows_for_day(symbol: str, day: date, input_value: DayInput, overseas: dict[tuple[str, str, str], dict[tuple[date, str], float]], cn: dict[tuple[date, str, str], tuple[float, float]]) -> tuple[list[dict[str, Any]], int]:
    rows, skipped = [], 0
    for minute, market_price in sorted(input_value.prices.items()):
        quote_values: dict[tuple[str, str], tuple[float, float]] = {}
        for component in input_value.pcf.components:
            if component.market == "CN":
                bid_ask = cn.get((day, minute, component.symbol))
            else:
                bid = overseas.get((component.market, component.symbol, "BID"), {}).get((day, minute))
                ask = overseas.get((component.market, component.symbol, "ASK"), {}).get((day, minute))
                bid_ask = (bid, ask) if bid is not None and ask is not None and ask >= bid else None
            if bid_ask is None:
                quote_values = {}
                break
            quote_values[component.key] = bid_ask
        if not quote_values:
            skipped += 1
            continue
        timestamp = shanghai_timestamp(day, minute)
        rows.append({"minute": common.iso_timestamp(timestamp), "market_price": market_price, "input": make_payload(input_value.pcf, input_value.rates, quote_values, timestamp)})
    if not rows:
        raise SourceUnavailableError(f"{symbol} has no one-minute row with a complete dated PCF basket")
    return rows, skipped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    parser.add_argument("--symbols", default=",".join(fund.symbol for fund in valuation.FUNDS))
    parser.add_argument("--start", type=parse_day)
    parser.add_argument("--end", type=parse_day)
    parser.add_argument("--pcf-cache-root", default="scripts/.runtime/private_china_internet/pcf")
    parser.add_argument("--ib-cache-dir", type=Path, help="completed local BID/ASK cache to reuse before requesting IBKR")
    parser.add_argument("--cn-1m-csv", type=Path, help="QMT CSV: date,minute,symbol,bid,ask (required only for SH513220)")
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", type=int, default=7496)
    parser.add_argument("--ib-client-id", type=int, default=513221)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--ib-request-delay", type=float, default=11.0)
    parser.add_argument("--batch-size", type=int, default=300)
    parser.add_argument("--env-file", default=".sina-uploader.env")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    common.load_env_file(args.env_file)
    args.token = args.token or os.getenv("NNN_UPLOAD_TOKEN", "")
    if not args.origin_ip:
        args.origin_ip = os.getenv("NNN_ORIGIN_IP", "")
    if not args.origin_ca_file:
        args.origin_ca_file = os.getenv("NNN_ORIGIN_CA_FILE", "")
    if not args.origin_tls_insecure:
        args.origin_tls_insecure = common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", ""))
    if args.start and args.end and args.start > args.end:
        raise SystemExit("--start must not be after --end")
    if args.ib_request_delay < 0 or not 1 <= args.batch_size <= 500:
        raise SystemExit("--ib-request-delay must be non-negative and --batch-size must be 1..500")
    selected = tuple(item.strip().upper() for item in args.symbols.replace("，", ",").split(",") if item.strip())
    if not selected or any(symbol not in valuation.FUND_BY_SYMBOL for symbol in selected):
        raise SystemExit("--symbols must use only " + ", ".join(valuation.FUND_BY_SYMBOL))
    if "SH513220" in selected and args.cn_1m_csv is None:
        raise SystemExit("SH513220 requires --cn-1m-csv with exact historical A-share BID/ASK; no synthetic backfill is allowed")
    if not args.dry_run and not args.token:
        raise SystemExit("NNN_UPLOAD_TOKEN is required unless --dry-run is used")

    days_by_symbol = {symbol: public_days(args.server, symbol, args.timeout, args.start, args.end) for symbol in selected}
    all_days = sorted({day for days in days_by_symbol.values() for day in days})
    if not all_days:
        print("no public minute history in the requested interval", flush=True)
        return 0
    print(f"history window={all_days[0]}..{all_days[-1]} cadence={HISTORY_BAR_SIZE} dry_run={args.dry_run}", flush=True)
    sources, components, failed = resolve_sources(args, days_by_symbol)
    cn_series = load_cn_bid_ask_csv(args.cn_1m_csv)
    imported: dict[str, int] = {symbol: 0 for symbol in selected}
    if components:
        ib = IB()
        try:
            ib.connect(args.ib_host, args.ib_port, clientId=args.ib_client_id, timeout=args.timeout, readonly=True)
            if not ib.isConnected():
                raise SourceUnavailableError("TWS API socket did not become connected")
            cached_series, known_market_closed_days = load_ib_bid_ask_cache(args.ib_cache_dir)
            overseas = collect_overseas_series(args, ib, components, all_days, cached_series, known_market_closed_days)
        finally:
            ib.disconnect()
    else:
        overseas = {}
    for symbol, days in days_by_symbol.items():
        for index, day in enumerate(days, 1):
            source = sources.get((symbol, day))
            if source is None:
                continue
            try:
                rows, skipped = rows_for_day(symbol, day, source, overseas, cn_series)
                count = 0 if args.dry_run else upload_rows(args, symbol, rows)
                imported[symbol] += count
                print(f"{symbol} {index}/{len(days)} {day_key(day)} rows={len(rows)} skipped={skipped} imported={count}", flush=True)
            except Exception as exc:
                failed[symbol].append(day_key(day))
                print(f"{symbol} {index}/{len(days)} {day_key(day)} SKIP {exc}", file=sys.stderr, flush=True)
    print("finished " + " ".join(f"{symbol}=imported:{imported[symbol]} failed:{','.join(sorted(set(failed[symbol]))) or 'none'}" for symbol in selected), flush=True)
    return 1 if any(failed.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())

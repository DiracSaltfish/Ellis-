#!/usr/bin/env python3
"""Backfill audited one-minute Private history for index QDII ETFs.

The index families deliberately share only their tradable China-session
reference data: one concrete NQ, ES, N225M or FDXM future contract, its
historical one-minute BID/ASK bars, and its family-specific calibration close.
Each fund still uses
its own dated PCF (or an auditable fixed latest SSE PCF), SAFE parity, dated
CFETS exchange rate and public ETF minute price.  Nothing is forward-filled.

Raw inputs are cached beneath ``--runtime-dir``.  A cache key contains the
reference, concrete IB conId, contract month, Shanghai date and BID/ASK side,
so a rerun never asks IBKR again for a day/side already collected for another
fund in the same family.  Historical SSE PCFs are intentionally fail-closed:
when no dated file is already in the local PCF archive, the day is recorded as
``pcf_unavailable`` rather than substituting today's PCF.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import gzip
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ib_insync import Future, IB, util

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import private_513350_spot_close as spot_close  # noqa: E402
import private_nasdaq_valuation_uploader as index  # noqa: E402
import private_valuation_uploader as common  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
HISTORY_PATH = "/api/v1/private/funds/{symbol}/minute-history/import"
SOURCE = "mac-local-private-us-index-history-backfill"
HISTORICAL_MARKET_DATA_TYPE = "HistoricalBidAsk"
HISTORICAL_IB_SOURCE = "IBKR_TWS_HISTORICAL_BID_ASK"
HISTORY_BAR_SIZE = "1 min"
REPLACE_DAY_MAX_COMPRESSED_BYTES = 1 << 20
REPLACE_DAY_MAX_DECOMPRESSED_BYTES = 4 << 20
# A one-day request is intentionally used for expired CME contracts.  IBKR
# serves their China-session BID and ASK bars promptly at this granularity,
# while multi-day requests have proved prone to a 162 timeout.  The on-disk
# cache still guarantees each contract/day/side is fetched at most once.
HISTORY_WINDOW_DAYS = 1
RAW_CACHE_SCHEMA = 2
DEFAULT_RUNTIME_DIR = SCRIPT_DIR / ".runtime" / "private_us_index_history_backfill"
DEFAULT_SPOT_CLOSE_PATH = SCRIPT_DIR / "data" / "cfets_usd_cny_spot_close_1630.csv"


class SourceUnavailableError(RuntimeError):
    """A required dated source cannot safely construct a history row."""


@dataclass(frozen=True)
class ResolvedFuture:
    reference: str
    contract_month: str
    con_id: int
    local_symbol: str
    expiry: str
    multiplier: float
    exchange: str = "CME"
    currency: str = "USD"
    timezone: str = "America/New_York"

    def to_payload(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "contract_month": self.contract_month,
            "con_id": self.con_id,
            "local_symbol": self.local_symbol,
            "expiry": self.expiry,
            "multiplier": self.multiplier,
            "exchange": self.exchange,
            "currency": self.currency,
            "timezone": self.timezone,
        }


@dataclass(frozen=True)
class PreparedDay:
    family: index.FamilyConfig
    pcf: index.PCF
    day: date
    market_prices: dict[str, float]
    fx: "HistoricalFXQuote"
    parity: index.CentralParity
    contract: ResolvedFuture
    fixed_proxy: bool = False
    fixed_contracts: float | None = None
    fixed_calibration_close: float | None = None


@dataclass(frozen=True)
class FixedProxyCalibration:
    pcf: index.PCF
    parity: index.CentralParity
    contracts: float
    calibration_close: float


@dataclass(frozen=True)
class HistoricalFXQuote:
    pair: str
    rate: float
    trading_day: date
    quote_time: str
    source: str
    observed_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "rate": self.rate,
            "trading_day": self.trading_day.isoformat(),
            "quote_time": self.quote_time,
            "source": self.source,
            "fetched_at": common.iso_timestamp(self.observed_at),
        }


def day_key(value: date) -> str:
    return value.strftime("%Y%m%d")


def parse_day(value: str) -> date:
    normalized = str(value).strip().replace("-", "")
    return datetime.strptime(normalized, "%Y%m%d").date()


def positive(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def atomic_json(path: Path, value: Any) -> None:
    common._atomic_write_bytes(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))


def load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def shanghai_timestamp(day: date, minute: str) -> datetime:
    return datetime.combine(day, clock_time.fromisoformat(minute), SHANGHAI)


def is_china_session(value: datetime) -> bool:
    local = value.astimezone(SHANGHAI)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    return (9 * 60 + 30 <= minute <= 11 * 60 + 30) or (13 * 60 <= minute <= 15 * 60)


def is_proxy_session(value: datetime, family: index.FamilyConfig) -> bool:
    """Return China-listed minutes for which the selected future can price a basket."""
    if not is_china_session(value):
        return False
    if family is not index.NIKKEI225_FAMILY:
        return True
    local = value.astimezone(SHANGHAI)
    # N225M's OSE day session closes at 15:45 JST / 14:45 Shanghai time.
    return local.hour * 60 + local.minute <= 14 * 60 + 45


def normalize_bar_timestamp(value: Any) -> datetime | None:
    if isinstance(value, str):
        value = util.parseIBDatetime(value)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SHANGHAI).replace(second=0, microsecond=0)


def source_json(server: str, path: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(server.rstrip("/") + path, headers=common.SOURCE_HEADERS)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload
            raise SourceUnavailableError(f"{path} returned {type(payload).__name__}, not an object")
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise SourceUnavailableError(f"{path} request failed after retries: {last_error}")


def cached_public_prices(runtime: Path, server: str, symbol: str, day: date, timeout: float, family: index.FamilyConfig) -> dict[str, float]:
    path = runtime / "public_minutes" / family.key / symbol / f"{day_key(day)}.json"
    cached = load_json(path)
    if isinstance(cached, dict) and cached.get("day") == day.isoformat() and isinstance(cached.get("prices"), dict):
        values: dict[str, float] = {}
        for minute, raw in cached["prices"].items():
            price = positive(raw)
            if isinstance(minute, str) and price is not None:
                values[minute] = price
        if values:
            return values
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
        if price is not None and is_proxy_session(timestamp, family):
            prices[minute] = price
    source = "public_minute_history"
    if not prices and family is index.DAX_FAMILY:
        # The Private chart retains two early Germany days after the public
        # minute store's date index has expired them.  Recover only the
        # domestic market price from those rows; never reuse the old basket
        # NAV or premium, which are exactly what this replay replaces.
        private_payload = source_json(
            server, f"/api/v1/private/funds/{symbol}/minute-history?date={day_key(day)}", timeout,
        )
        private_rows = private_payload.get("rows")
        if isinstance(private_rows, list):
            for row in private_rows:
                if not isinstance(row, dict):
                    continue
                try:
                    timestamp = datetime.fromisoformat(str(row.get("minute") or "")).astimezone(SHANGHAI)
                except (TypeError, ValueError):
                    continue
                price = positive(row.get("market_price"))
                if timestamp.date() == day and price is not None and is_proxy_session(timestamp, family):
                    prices[timestamp.strftime("%H:%M")] = price
        source = "private_history_market_price_recovery"
    if not prices:
        raise SourceUnavailableError(f"{symbol} has no usable public minute rows for {day_key(day)}")
    atomic_json(path, {"symbol": symbol, "day": day.isoformat(), "source": source, "prices": prices})
    return prices


def public_history_days(server: str, symbol: str, timeout: float, start: date | None, end: date | None, limit: int) -> list[date]:
    payload = source_json(server, f"/api/v1/funds/{symbol}/minute-history/dates?limit={limit}", timeout)
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
    lower = start or min(available)
    upper = end or max(available)
    return [candidate for candidate in sorted(available) if lower <= candidate <= upper]


def private_history_days(server: str, symbol: str, timeout: float) -> set[date]:
    payload = source_json(server, f"/api/v1/private/funds/{symbol}/minute-history/dates", timeout)
    values = payload.get("dates")
    if not isinstance(values, list):
        return set()
    result: set[date] = set()
    for raw in values:
        try:
            result.add(parse_day(raw))
        except (TypeError, ValueError):
            continue
    return result


def replay_target_days(
    family: index.FamilyConfig,
    public_days: list[date],
    existing: set[date],
    start: date | None,
    end: date | None,
    replace_existing: bool,
) -> list[date]:
    if not replace_existing:
        return [item for item in public_days if item not in existing]
    available = set(public_days)
    if family is index.DAX_FAMILY:
        available.update(existing)
    return [
        item for item in sorted(available)
        if (start is None or item >= start) and (end is None or item <= end)
    ]


def third_friday(year: int, month: int) -> date:
    candidate = date(year, month, 1)
    while candidate.weekday() != 4:
        candidate += timedelta(days=1)
    return candidate + timedelta(days=14)


def calendar_front_month(day: date) -> str:
    """Return the reproducible quarterly CME front contract for a China day."""
    for month in (3, 6, 9, 12):
        expiry = third_friday(day.year, month)
        if day <= expiry:
            return f"{day.year:04d}{month:02d}"
    return f"{day.year + 1:04d}03"


def nikkei_last_trading_day(year: int, month: int) -> date:
    """OSE Nikkei 225 mini trades through the business day before the second Friday."""
    second_friday = third_friday(year, month) - timedelta(days=7)
    candidate = second_friday - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def front_contract_month(day: date, family: index.FamilyConfig) -> str:
    if family is not index.NIKKEI225_FAMILY:
        return calendar_front_month(day)
    year, month = day.year, day.month
    if day > nikkei_last_trading_day(year, month):
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return f"{year:04d}{month:02d}"


def resolved_contract_path(runtime: Path, reference: str, month: str) -> Path:
    return runtime / "contracts" / reference / f"{month}.json"


def future_from_spec(spec: ResolvedFuture) -> Future:
    return Future(
        symbol=spec.reference,
        lastTradeDateOrContractMonth=spec.contract_month,
        exchange=spec.exchange,
        currency=spec.currency,
        multiplier=str(int(spec.multiplier)),
        includeExpired=True,
    )


def resolve_future(ib: IB, runtime: Path, family: index.FamilyConfig, day: date) -> ResolvedFuture:
    month = front_contract_month(day, family)
    path = resolved_contract_path(runtime, family.reference_symbol, month)
    cached = load_json(path)
    if isinstance(cached, dict):
        try:
            spec = ResolvedFuture(
                reference=str(cached["reference"]), contract_month=str(cached["contract_month"]),
                con_id=int(cached["con_id"]), local_symbol=str(cached["local_symbol"]),
                expiry=str(cached["expiry"]), multiplier=float(cached["multiplier"]),
                exchange=str(cached.get("exchange") or family.future_exchange),
                currency=str(cached.get("currency") or family.future_currency),
                timezone=str(cached.get("timezone") or family.future_timezone),
            )
        except (KeyError, TypeError, ValueError):
            spec = None
        if (
            spec is not None and spec.reference == family.reference_symbol
            and spec.multiplier == family.futures_multiplier_usd_per_point
            and spec.exchange == family.future_exchange and spec.currency == family.future_currency
        ):
            return spec
    contract = Future(
        symbol=family.reference_symbol,
        lastTradeDateOrContractMonth=month,
        exchange=family.future_exchange,
        currency=family.future_currency,
        multiplier=str(int(family.futures_multiplier_usd_per_point)),
        includeExpired=True,
    )
    qualified = ib.qualifyContracts(contract)
    if len(qualified) != 1:
        raise SourceUnavailableError(f"IBKR cannot qualify {family.reference_symbol} {month}")
    item = qualified[0]
    con_id = int(getattr(item, "conId", 0) or 0)
    if con_id <= 0:
        raise SourceUnavailableError(f"IBKR returned no conId for {family.reference_symbol} {month}")
    spec = ResolvedFuture(
        reference=family.reference_symbol,
        contract_month=month,
        con_id=con_id,
        local_symbol=str(getattr(item, "localSymbol", "") or ""),
        expiry=str(getattr(item, "lastTradeDateOrContractMonth", "") or month),
        multiplier=family.futures_multiplier_usd_per_point,
        exchange=family.future_exchange,
        currency=family.future_currency,
        timezone=family.future_timezone,
    )
    atomic_json(path, spec.to_payload())
    return spec


def concrete_future(ib: IB, spec: ResolvedFuture) -> Any:
    contract = future_from_spec(spec)
    qualified = ib.qualifyContracts(contract)
    if len(qualified) != 1:
        raise SourceUnavailableError(f"IBKR cannot re-qualify {spec.reference} {spec.contract_month}")
    item = qualified[0]
    if int(getattr(item, "conId", 0) or 0) != spec.con_id:
        raise SourceUnavailableError(f"IBKR conId drift for {spec.reference} {spec.contract_month}")
    return item


def futures_cache_path(runtime: Path, spec: ResolvedFuture, day: date, kind: str) -> Path:
    return runtime / "ib_futures" / spec.reference / str(spec.con_id) / spec.contract_month / f"{day_key(day)}-{kind}.json"


def decode_minute_series(payload: Any, day: date) -> dict[str, float] | None:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != RAW_CACHE_SCHEMA
        or payload.get("day") != day.isoformat()
        or payload.get("request_window_days") != HISTORY_WINDOW_DAYS
        or not isinstance(payload.get("rows"), dict)
    ):
        return None
    rows: dict[str, float] = {}
    for minute, raw in payload["rows"].items():
        price = positive(raw)
        if isinstance(minute, str) and len(minute) == 5 and price is not None:
            rows[minute] = price
    return rows


def request_windows(days: Iterable[date], window_days: int) -> list[list[date]]:
    if window_days < 1:
        raise ValueError("window_days must be positive")
    values = sorted(set(days))
    windows: list[list[date]] = []
    current: list[date] = []
    for day in values:
        if current and (day - current[0]).days >= window_days:
            windows.append(current)
            current = []
        current.append(day)
    if current:
        windows.append(current)
    return windows


def request_future_bars(ib: IB, contract: Any, days: Iterable[date], what: str, request_delay: float, family: index.FamilyConfig) -> dict[date, dict[str, float]]:
    collected: dict[date, dict[str, float]] = defaultdict(dict)

    def request(window: list[date], duration_days: int) -> dict[date, dict[str, float]]:
        end = datetime.combine(window[-1], clock_time(15, 0), SHANGHAI)
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end,
                durationStr=f"{duration_days} D",
                barSizeSetting=HISTORY_BAR_SIZE,
                whatToShow=what,
                useRTH=False,
                formatDate=2,
                keepUpToDate=False,
                timeout=20,
            )
        except Exception:
            return {}
        if request_delay > 0:
            time.sleep(request_delay)
        wanted = set(window)
        values: dict[date, dict[str, float]] = defaultdict(dict)
        for bar in bars:
            timestamp = normalize_bar_timestamp(getattr(bar, "date", None))
            price = positive(getattr(bar, "close", None))
            if timestamp is None or price is None or timestamp.date() not in wanted or not is_proxy_session(timestamp, family):
                continue
            values[timestamp.date()][timestamp.strftime("%H:%M")] = price
        return values

    for window in request_windows(days, HISTORY_WINDOW_DAYS):
        result = request(window, HISTORY_WINDOW_DAYS)
        for item_day, rows in result.items():
            collected[item_day].update(rows)
    return dict(collected)


def cached_future_series(ib: IB, runtime: Path, spec: ResolvedFuture, days: Iterable[date], what: str, request_delay: float, family: index.FamilyConfig = index.NASDAQ_FAMILY) -> dict[date, dict[str, float]]:
    values: dict[date, dict[str, float]] = {}
    missing: list[date] = []
    for day in sorted(set(days)):
        cached = decode_minute_series(load_json(futures_cache_path(runtime, spec, day, what)), day)
        if cached is None:
            missing.append(day)
        else:
            values[day] = cached
    if not missing:
        return values
    contract = concrete_future(ib, spec)
    requested = request_future_bars(ib, contract, missing, what, request_delay, family)
    for day in missing:
        rows = requested.get(day, {})
        atomic_json(futures_cache_path(runtime, spec, day, what), {
            "schema_version": RAW_CACHE_SCHEMA,
            "reference": spec.reference,
            "contract": spec.to_payload(),
            "day": day.isoformat(),
            "what": what,
            "bar_size": HISTORY_BAR_SIZE,
            "request_window_days": HISTORY_WINDOW_DAYS,
            "use_rth": False,
            "rows": rows,
        })
        values[day] = rows
    return values


def close_cache_path(
    runtime: Path, spec: ResolvedFuture, day: date,
    family: index.FamilyConfig = index.NASDAQ_FAMILY,
) -> Path:
    suffix = "XETRA_1735_CLOSE_V2" if family.calibration_anchor_kind == "xetra_close_auction_1735" else "RTH_CLOSE"
    return runtime / "ib_futures" / spec.reference / str(spec.con_id) / spec.contract_month / f"{day_key(day)}-{suffix}.json"


def parse_daily_bar_day(value: Any, market_timezone: ZoneInfo = NEW_YORK) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.astimezone(market_timezone).date() if value.tzinfo else value.date()
    try:
        return util.parseIBDatetime(value).astimezone(market_timezone).date()
    except Exception:
        return None


def future_regular_close(ib: IB, runtime: Path, spec: ResolvedFuture, day: date, request_delay: float, family: index.FamilyConfig = index.NASDAQ_FAMILY) -> float:
    """Load the family calibration close; Germany uses Xetra 17:35, not a daily futures close."""
    path = close_cache_path(runtime, spec, day, family)
    cached = load_json(path)
    if isinstance(cached, dict) and cached.get("day") == day.isoformat():
        value = positive(cached.get("close"))
        if value is not None:
            return value
    contract = concrete_future(ib, spec)
    if family.calibration_anchor_kind == "xetra_close_auction_1735":
        anchor = index.xetra_close_auction_anchor(ib, contract, day)
        if request_delay > 0:
            time.sleep(request_delay)
        close = anchor.close_1735
        atomic_json(path, {
            "schema_version": RAW_CACHE_SCHEMA,
            "reference": spec.reference,
            "contract": spec.to_payload(),
            "day": day.isoformat(),
            "close": close,
            "anchor": anchor.to_payload(),
        })
        return close
    market_timezone = ZoneInfo(family.future_timezone)
    end = datetime.combine(day, clock_time(family.regular_close_hour, family.regular_close_minute), market_timezone)
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=end,
        durationStr="4 D",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=2,
        keepUpToDate=False,
        timeout=20,
    )
    if request_delay > 0:
        time.sleep(request_delay)
    close: float | None = None
    for bar in reversed(bars):
        if parse_daily_bar_day(getattr(bar, "date", None), market_timezone) != day:
            continue
        close = positive(getattr(bar, "close", None))
        if close is not None:
            break
    if close is None:
        raise SourceUnavailableError(f"IBKR has no {spec.reference} regular-session close on {day_key(day)}")
    atomic_json(path, {"reference": spec.reference, "contract": spec.to_payload(), "day": day.isoformat(), "close": close})
    return close


def parity_cache_path(runtime: Path, day: date) -> Path:
    return runtime / "safe_parity" / f"{day_key(day)}.json"


def cached_safe_parity(runtime: Path, day: date, timeout: float, family: index.FamilyConfig = index.NASDAQ_FAMILY) -> index.CentralParity:
    path = runtime / "safe_parity" / family.key / f"{day_key(day)}.json"
    cached = load_json(path)
    if isinstance(cached, dict) and cached.get("trading_day") == day.isoformat():
        rate = positive(cached.get("rate"))
        if rate is not None:
            return index.CentralParity(rate, day, datetime.now(SHANGHAI))
    parity = index.fetch_safe_central_parity(day, timeout, family)
    atomic_json(path, {"trading_day": day.isoformat(), "rate": parity.rate, "source": index.SAFE_SOURCE})
    return parity


def load_dated_pcf(store: index.PCFStore, family: index.FamilyConfig, fund: index.FundConfig, day: date) -> index.PCF:
    path = store.path(fund, day)
    if fund.exchange == "SSE" and day < datetime.now(SHANGHAI).date() and not path.is_file():
        raise SourceUnavailableError("SSE 历史 PCF 不可获取，且本地归档不存在")
    # A dated PCF can legitimately have a different constituent count after an
    # index reconstitution or a cash-substitution rule change.  Keep its full
    # audited basket and count; the server's definition will retain the
    # non-actionable warning.  The live collector remains strict because it
    # protects the current trading-day feed from unexpected basket drift.
    historical_fund = replace(fund, component_count=0)
    return store.fetch(historical_fund, day, family)


def load_latest_sse_pcf(store: index.PCFStore, family: index.FamilyConfig, fund: index.FundConfig) -> index.PCF:
    """Fetch one current SSE PCF and retain it as an explicit fixed calibration.

    SSE cannot provide a dated archive.  This is intentionally separate from
    ``load_dated_pcf``: the returned PCF keeps its actual latest TradingDay
    and is never presented as if it were the historical chart date.
    """
    if fund.exchange != "SSE":
        raise SourceUnavailableError("latest fixed PCF policy is only valid for SSE funds")
    source_url = fund.source_url(datetime.now(SHANGHAI).date())
    store.pacer.wait()
    raw = bytes(common._http_fetch(source_url, None, common.SOURCE_HEADERS, store.timeout))
    try:
        root = ET.fromstring(raw)
        trading_day = index.parse_day(index.direct_text(root, "TradingDay"), "TradingDay")
    except (ET.ParseError, index.SourceUnavailableError) as exc:
        raise SourceUnavailableError(f"{fund.symbol} latest SSE PCF has no valid TradingDay: {exc}") from exc
    pcf = index.parse_pcf(replace(fund, component_count=0), raw, source_url, trading_day, family)
    common._atomic_write_bytes(store.path(fund, pcf.trading_day), raw)
    return pcf


def fixed_proxy_calibration(ib: IB, runtime: Path, family: index.FamilyConfig, pcf: index.PCF, timeout: float, request_delay: float) -> FixedProxyCalibration:
    parity = cached_safe_parity(runtime, pcf.pre_trading_day, timeout, family)
    contract = resolve_future(ib, runtime, family, pcf.trading_day)
    close = future_regular_close(ib, runtime, contract, pcf.pre_trading_day, request_delay, family)
    contracts = index.nq_contract_equivalent(pcf, parity, close, family)
    return FixedProxyCalibration(pcf, parity, contracts, close)


def historical_fx_for_day(runtime: Path, family: index.FamilyConfig, day: date, timeout: float, spot_close_path: str) -> HistoricalFXQuote:
    if family is index.NASDAQ_FAMILY or family is index.SP500_FAMILY:
        quote = spot_close.quote_for_day(day, spot_close_path)
        return HistoricalFXQuote(
            "USD/CNY", quote.rate, quote.trading_day, quote.quote_time, quote.source, quote.observed_at,
        )
    if family is index.DAX_FAMILY:
        path = runtime / "cfets_eur_cny_reference" / f"{day_key(day)}-1600.json"
        cached = load_json(path)
        if isinstance(cached, dict) and cached.get("trading_day") == day.isoformat() and cached.get("quote_time") == "16:00":
            rate = positive(cached.get("rate"))
            if rate is not None:
                observed_at = datetime.combine(day, clock_time(16, 0), SHANGHAI)
                return HistoricalFXQuote("EUR/CNY", rate, day, "16:00", "CFETS_REFERENCE_RATE", observed_at)
        quote = index.EURCFETSClient(timeout, lookback_days=0).fetch_latest(day, max_hour=16)
        if quote.trading_day != day or quote.quote_time != "16:00":
            raise SourceUnavailableError(f"CFETS EUR/CNY requires exact {day.isoformat()} 16:00 reference quote")
        atomic_json(path, {"trading_day": day.isoformat(), "quote_time": quote.quote_time, "rate": quote.rate})
        observed_at = datetime.combine(day, clock_time(16, 0), SHANGHAI)
        return HistoricalFXQuote("EUR/CNY", quote.rate, day, quote.quote_time, "CFETS_REFERENCE_RATE", observed_at)
    path = runtime / "cfets_jpy_cny_reference" / f"{day_key(day)}-1600.json"
    cached = load_json(path)
    if isinstance(cached, dict) and cached.get("trading_day") == day.isoformat() and cached.get("quote_time") == "16:00":
        rate = positive(cached.get("rate"))
        if rate is not None:
            observed_at = datetime.combine(day, clock_time(16, 0), SHANGHAI)
            return HistoricalFXQuote("JPY/CNY", rate, day, "16:00", "CFETS_REFERENCE_RATE", observed_at)
    quote = index.JPYCFETSClient(timeout, lookback_days=0).fetch_latest(day, max_hour=16)
    if quote.trading_day != day or quote.quote_time != "16:00":
        raise SourceUnavailableError(f"CFETS JPY/CNY requires exact {day.isoformat()} 16:00 reference quote")
    atomic_json(path, {"trading_day": day.isoformat(), "quote_time": quote.quote_time, "rate": quote.rate})
    observed_at = datetime.combine(day, clock_time(16, 0), SHANGHAI)
    return HistoricalFXQuote("JPY/CNY", quote.rate, day, quote.quote_time, "CFETS_REFERENCE_RATE", observed_at)


def quality_check(prices: dict[str, float], bids: dict[str, float], asks: dict[str, float], threshold: float, max_gap: int) -> tuple[list[str], int, float]:
    common_minutes = sorted(minute for minute in prices if minute in bids and minute in asks and asks[minute] >= bids[minute])
    if not prices:
        return [], 0, 0.0
    missing_run = longest_missing_run(sorted(prices), set(common_minutes))
    coverage = len(common_minutes) / len(prices)
    if not common_minutes or coverage < threshold or missing_run > max_gap:
        raise SourceUnavailableError(
            f"shared futures BID/ASK coverage={coverage:.1%}, max_gap={missing_run}; requires >= {threshold:.1%} and <= {max_gap}"
        )
    return common_minutes, missing_run, coverage


def longest_missing_run(expected: list[str], present: set[str]) -> int:
    maximum = current = 0
    for minute in expected:
        if minute in present:
            current = 0
        else:
            current += 1
            maximum = max(maximum, current)
    return maximum


def historical_payload(prepared: PreparedDay, minute: str, bid: float, ask: float, contracts: float) -> dict[str, Any]:
    timestamp = shanghai_timestamp(prepared.day, minute)
    pcf = prepared.pcf.to_payload(contracts)
    if prepared.fixed_proxy:
        pcf["historical_fixed_proxy"] = True
    return {
        "schema_version": common.INPUT_SCHEMA_VERSION,
        "symbol": prepared.pcf.fund.symbol,
        "model_version": prepared.family.model_version,
        "pcf": pcf,
        "fx": prepared.fx.to_payload(),
        "ib": {
            "symbol": prepared.family.reference_symbol,
            "bid": bid,
            "ask": ask,
            "last": None,
            "market_data_type": HISTORICAL_MARKET_DATA_TYPE,
            "source": HISTORICAL_IB_SOURCE,
            "observed_at": common.iso_timestamp(timestamp),
        },
        "source": SOURCE,
        "generated_at": common.iso_timestamp(timestamp),
    }


def build_rows(prepared: PreparedDay, bids: dict[str, float], asks: dict[str, float], contracts: float, coverage: float, max_gap: int, threshold: float) -> tuple[list[dict[str, Any]], int]:
    minutes, missing_run, verified_coverage = quality_check(prepared.market_prices, bids, asks, threshold, max_gap)
    if abs(coverage - verified_coverage) > 1e-12:
        raise SourceUnavailableError("shared BID/ASK cache coverage changed during build")
    rows = [
        {
            "minute": common.iso_timestamp(shanghai_timestamp(prepared.day, minute)),
            "market_price": prepared.market_prices[minute],
            "input": historical_payload(prepared, minute, bids[minute], asks[minute], contracts),
        }
        for minute in minutes
    ]
    return rows, missing_run


def payload_chunks(values: list[dict[str, Any]], row_limit: int, byte_limit: int) -> Iterable[list[dict[str, Any]]]:
    """Keep every JSON request below the reverse-proxy body limit.

    A private history row carries a full PCF.  Therefore 240 rows can be only
    a fraction of the server's 500-row API limit but still exceed nginx's body
    cap.  Measure the actual JSON envelope rather than assuming a row count.
    """
    current: list[dict[str, Any]] = []
    for value in values:
        candidate = current + [value]
        encoded_size = len(json.dumps({"rows": candidate}, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        if current and (len(candidate) > row_limit or encoded_size > byte_limit):
            yield current
            current = [value]
            encoded_size = len(json.dumps({"rows": current}, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        else:
            current = candidate
        if encoded_size > byte_limit:
            raise SourceUnavailableError(f"one historical row is {encoded_size} bytes, exceeding --max-payload-bytes={byte_limit}")
    if current:
        yield current


def upload_rows(args: argparse.Namespace, symbol: str, rows: list[dict[str, Any]]) -> int:
    if args.replace_existing:
        raw_payload = json.dumps(
            {"rows": rows, "replace_day": True},
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(raw_payload) > REPLACE_DAY_MAX_DECOMPRESSED_BYTES:
            raise SourceUnavailableError(
                f"{symbol} complete-day replacement is {len(raw_payload)} bytes, "
                f"exceeding {REPLACE_DAY_MAX_DECOMPRESSED_BYTES}"
            )
        request_payload = gzip.compress(raw_payload, compresslevel=6, mtime=0)
        if len(request_payload) > REPLACE_DAY_MAX_COMPRESSED_BYTES:
            raise SourceUnavailableError(
                f"{symbol} compressed complete-day replacement is {len(request_payload)} bytes, "
                f"exceeding {REPLACE_DAY_MAX_COMPRESSED_BYTES}"
            )
        headers = common.server_headers(args.server, args.token)
        headers["Content-Encoding"] = "gzip"
        request = urllib.request.Request(
            args.server.rstrip("/") + HISTORY_PATH.format(symbol=symbol),
            data=request_payload,
            method="POST",
            headers=headers,
        )
        return upload_request(args, symbol, request)

    imported = 0
    for chunk in payload_chunks(rows, args.batch_size, args.max_payload_bytes):
        request = urllib.request.Request(
            args.server.rstrip("/") + HISTORY_PATH.format(symbol=symbol),
            data=json.dumps({"rows": chunk}, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            method="POST",
            headers=common.server_headers(args.server, args.token),
        )
        imported += upload_request(args, symbol, request)
    return imported


def upload_request(args: argparse.Namespace, symbol: str, request: urllib.request.Request) -> int:
    try:
        with common.open_server_request(request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{symbol} historical upload HTTP {exc.code}: {detail}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"{symbol} historical upload was not acknowledged: {payload}")
    return int(payload.get("imported") or 0)


def selected_funds(args: argparse.Namespace) -> tuple[index.FamilyConfig, ...]:
    requested = {item.strip().lower() for item in args.families.replace("，", ",").split(",") if item.strip()}
    if not requested or requested == {"all"}:
        return tuple(family for family in index.FAMILIES.values() if family.supports_historical_replay)
    unknown = requested.difference(index.FAMILIES)
    if unknown:
        raise SourceUnavailableError(f"unknown --families: {', '.join(sorted(unknown))}")
    families = tuple(index.FAMILIES[name] for name in sorted(requested))
    unsupported = [family.key for family in families if not family.supports_historical_replay]
    if unsupported:
        raise SourceUnavailableError(
            "historical replay is not yet supported for: " + ", ".join(unsupported)
        )
    return families


def selected_symbol_set(args: argparse.Namespace) -> set[str] | None:
    if not args.symbols.strip():
        return None
    return {item.strip().upper() for item in args.symbols.replace("，", ",").split(",") if item.strip()}


def write_manifest(runtime: Path, records: list[dict[str, Any]]) -> Path:
    path = runtime / "manifests" / f"run-{datetime.now(SHANGHAI):%Y%m%dT%H%M%S}.json"
    atomic_json(path, {"schema_version": 1, "source": SOURCE, "generated_at": common.iso_timestamp(datetime.now(SHANGHAI)), "records": records})
    return path


def run(args: argparse.Namespace) -> int:
    if args.batch_size < 1 or args.batch_size > 500:
        raise SourceUnavailableError("--batch-size must be between 1 and 500")
    if args.max_payload_bytes < 50_000 or args.max_payload_bytes > 3_000_000:
        raise SourceUnavailableError("--max-payload-bytes must be between 50000 and 3000000")
    if not 0 < args.min_coverage <= 1:
        raise SourceUnavailableError("--min-coverage must be in (0, 1]")
    if args.max_gap < 0:
        raise SourceUnavailableError("--max-gap must be non-negative")
    if args.sse_history_policy not in {"skip", "latest-fixed"}:
        raise SourceUnavailableError("--sse-history-policy must be skip or latest-fixed")
    if not args.dry_run and not args.token.strip():
        raise SourceUnavailableError("NNN_UPLOAD_TOKEN or --token is required unless --dry-run is used")
    runtime = Path(args.runtime_dir).expanduser().resolve()
    selector = selected_symbol_set(args)
    families = selected_funds(args)
    configs: list[tuple[index.FamilyConfig, index.FundConfig]] = []
    for family in families:
        for fund in family.funds:
            if selector is None or fund.symbol in selector:
                configs.append((family, fund))
    if not configs:
        raise SourceUnavailableError("no selected funds")
    known = {fund.symbol for _, fund in configs}
    if selector is not None and selector.difference(known):
        raise SourceUnavailableError(f"unsupported --symbols: {', '.join(sorted(selector.difference(known)))}")

    records: list[dict[str, Any]] = []
    planned: list[tuple[index.FamilyConfig, index.FundConfig, list[date]]] = []
    for family, fund in configs:
        try:
            public_days = public_history_days(args.server, fund.symbol, args.timeout, args.start, args.end, args.public_date_limit)
            existing = private_history_days(args.server, fund.symbol, args.timeout)
            target = replay_target_days(
                family, public_days, existing, args.start, args.end,
                bool(getattr(args, "replace_existing", False)),
            )
            if args.max_days > 0:
                target = target[:args.max_days]
            planned.append((family, fund, target))
            print(f"{fund.symbol} plan public={len(public_days)} existing_private={len(existing)} target={len(target)}", flush=True)
        except Exception as exc:
            records.append({"symbol": fund.symbol, "status": "plan_unavailable", "error": str(exc)})
            print(f"{fund.symbol} PLAN SKIP {exc}", file=sys.stderr, flush=True)

    ib: IB | None = None
    pcf_store = index.PCFStore(runtime / "pcf", args.timeout, common.PCFRequestPacer(args.pcf_request_delay, state_path=args.pcf_pacer_state))
    prepared: list[PreparedDay] = []
    try:
        ib = IB()
        ib.connect(args.ib_host, args.ib_port, clientId=args.ib_client_id, timeout=args.timeout, readonly=True)
        if not ib.isConnected():
            raise SourceUnavailableError("TWS API socket did not become connected")
        ib.reqMarketDataType(1)
        for family, fund, days in planned:
            fixed: FixedProxyCalibration | None = None
            if days and fund.exchange == "SSE" and args.sse_history_policy == "latest-fixed":
                try:
                    fixed = fixed_proxy_calibration(
                        ib, runtime, family, load_latest_sse_pcf(pcf_store, family, fund), args.timeout, args.ib_request_delay,
                    )
                    print(f"{fund.symbol} fixed PCF={fixed.pcf.trading_day.isoformat()} {family.reference_symbol} contracts={fixed.contracts:.6f}", flush=True)
                except Exception as exc:
                    for item_day in days:
                        records.append({
                            "symbol": fund.symbol, "family": family.key, "day": item_day.isoformat(),
                            "status": "fixed_pcf_unavailable", "error": str(exc),
                        })
                        print(f"{fund.symbol} {day_key(item_day)} SKIP fixed PCF {exc}", file=sys.stderr, flush=True)
                    continue
            for item_day in days:
                record: dict[str, Any] = {"symbol": fund.symbol, "family": family.key, "day": item_day.isoformat()}
                try:
                    pcf = fixed.pcf if fixed is not None else load_dated_pcf(pcf_store, family, fund, item_day)
                    fx = historical_fx_for_day(runtime, family, item_day, args.timeout, args.spot_close_path)
                    parity = fixed.parity if fixed is not None else cached_safe_parity(runtime, pcf.pre_trading_day, args.timeout, family)
                    contract = resolve_future(ib, runtime, family, item_day)
                    prices = cached_public_prices(runtime, args.server, fund.symbol, item_day, args.timeout, family)
                    prepared.append(PreparedDay(
                        family, pcf, item_day, prices, fx, parity, contract,
                        fixed_proxy=fixed is not None, fixed_contracts=fixed.contracts if fixed is not None else None,
                        fixed_calibration_close=fixed.calibration_close if fixed is not None else None,
                    ))
                    record.update({
                        "status": "prepared", "contract": contract.to_payload(), "public_minutes": len(prices), "pcf_sha256": pcf.sha256,
                        "pcf_strategy": "latest_fixed" if fixed is not None else "dated",
                    })
                    print(f"{fund.symbol} {day_key(item_day)} PREPARED {family.reference_symbol}={contract.contract_month}/{contract.con_id}", flush=True)
                except Exception as exc:
                    record.update({"status": "pcf_unavailable" if "SSE 历史 PCF" in str(exc) else "source_unavailable", "error": str(exc)})
                    print(f"{fund.symbol} {day_key(item_day)} SKIP {exc}", file=sys.stderr, flush=True)
                records.append(record)

        grouped: dict[tuple[str, int, str], tuple[ResolvedFuture, set[date]]] = {}
        for item in prepared:
            key = (item.family.reference_symbol, item.contract.con_id, item.contract.contract_month)
            if key not in grouped:
                grouped[key] = (item.contract, set())
            grouped[key][1].add(item.day)
        series: dict[tuple[str, int, str, str, date], dict[str, float]] = {}
        for (reference, con_id, month), (contract, days) in sorted(grouped.items()):
            for what in ("BID", "ASK"):
                family = next(item.family for item in prepared if item.contract == contract)
                values = cached_future_series(ib, runtime, contract, days, what, args.ib_request_delay, family)
                for item_day, rows in values.items():
                    series[(reference, con_id, month, what, item_day)] = rows
                print(f"{reference} {month}/{con_id} {what} cached_days={len(values)}", flush=True)

        imported_total = 0
        for item in prepared:
            matching = next(record for record in records if record.get("symbol") == item.pcf.fund.symbol and record.get("day") == item.day.isoformat())
            try:
                bids = series[(item.family.reference_symbol, item.contract.con_id, item.contract.contract_month, "BID", item.day)]
                asks = series[(item.family.reference_symbol, item.contract.con_id, item.contract.contract_month, "ASK", item.day)]
                _, _, coverage = quality_check(item.market_prices, bids, asks, args.min_coverage, args.max_gap)
                close = item.fixed_calibration_close
                if item.fixed_contracts is None:
                    close = future_regular_close(ib, runtime, item.contract, item.pcf.pre_trading_day, args.ib_request_delay, item.family)
                    contracts = index.nq_contract_equivalent(item.pcf, item.parity, close, item.family)
                else:
                    contracts = item.fixed_contracts
                rows, missing_run = build_rows(item, bids, asks, contracts, coverage, args.max_gap, args.min_coverage)
                imported = 0 if args.dry_run else upload_rows(args, item.pcf.fund.symbol, rows)
                imported_total += imported
                matching.update({
                    "status": "dry_run_ready" if args.dry_run else "uploaded",
                    "rows": len(rows), "imported": imported, "coverage": coverage,
                    "max_missing_gap": missing_run, "contracts": contracts,
                    "calibration_close": close,
                    "calibration_anchor": item.family.calibration_anchor_kind,
                    "pcf_strategy": "latest_fixed" if item.fixed_proxy else "dated",
                })
                print(f"{item.pcf.fund.symbol} {day_key(item.day)} rows={len(rows)} coverage={coverage:.1%} contracts={contracts:.6f} imported={imported}", flush=True)
            except Exception as exc:
                matching.update({"status": "reference_unavailable", "error": str(exc)})
                print(f"{item.pcf.fund.symbol} {day_key(item.day)} SKIP {exc}", file=sys.stderr, flush=True)
        manifest = write_manifest(runtime, records)
        ready = sum(1 for record in records if record.get("status") in {"dry_run_ready", "uploaded"})
        skipped = len(records) - ready
        print(f"summary ready={ready} skipped={skipped} imported={imported_total} manifest={manifest}", flush=True)
        return 0 if ready else 2
    finally:
        if ib is not None:
            try:
                ib.disconnect()
            except Exception:
                pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--server", default=os.getenv("NNN_PRIVATE_SERVER", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    result.add_argument("--families", default="all", help="all, nasdaq, sp500, nikkei225, germany, or a comma-separated subset")
    result.add_argument("--symbols", default="", help="optional comma-separated subset of the selected family")
    result.add_argument("--start", type=parse_day)
    result.add_argument("--end", type=parse_day)
    result.add_argument(
        "--replace-existing",
        action="store_true",
        help="replay requested dates even when Private history already contains partial rows",
    )
    result.add_argument("--max-days", type=int, default=0, help="for validation, keep the earliest N planned days per symbol")
    result.add_argument("--public-date-limit", type=int, default=45)
    result.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR))
    result.add_argument("--spot-close-path", default=str(DEFAULT_SPOT_CLOSE_PATH))
    result.add_argument("--ib-host", default=os.getenv("NNN_IB_HOST", "127.0.0.1"))
    result.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_IB_PORT", "7496")))
    result.add_argument("--ib-client-id", type=int, default=int(os.getenv("NNN_PRIVATE_US_INDEX_HISTORY_IB_CLIENT_ID", "159697")))
    result.add_argument("--timeout", type=float, default=60)
    result.add_argument("--ib-request-delay", type=float, default=1.5)
    result.add_argument("--pcf-request-delay", type=float, default=10.0)
    result.add_argument("--pcf-pacer-state", default="/tmp/newnavnav-private-pcf-pacer.json")
    result.add_argument("--sse-history-policy", choices=("skip", "latest-fixed"), default="skip")
    result.add_argument("--min-coverage", type=float, default=0.98)
    result.add_argument("--max-gap", type=int, default=3)
    result.add_argument("--batch-size", type=int, default=500)
    result.add_argument("--max-payload-bytes", type=int, default=750_000)
    result.add_argument("--env-file", default=".sina-uploader.env")
    result.add_argument("--dry-run", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    common.load_env_file(args.env_file)
    if not args.token:
        args.token = os.getenv("NNN_UPLOAD_TOKEN", "")
    if not args.origin_ip:
        args.origin_ip = os.getenv("NNN_ORIGIN_IP", "")
    if not args.origin_ca_file:
        args.origin_ca_file = os.getenv("NNN_ORIGIN_CA_FILE", "")
    if not args.origin_tls_insecure:
        args.origin_tls_insecure = common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", ""))
    if args.start and args.end and args.start > args.end:
        raise SystemExit("--start must not be after --end")
    try:
        return run(args)
    except (SourceUnavailableError, RuntimeError, urllib.error.URLError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

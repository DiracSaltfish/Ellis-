#!/usr/bin/env python3
"""Publish the private SZ164824 T-2 multi-market INDA valuation input.

This is deliberately an indicative-valuation collector, not an order router.
It uses the latest released 164824 NAV as its dated base, the corresponding
SAFE USD/CNY central parity and four INDA observations at the overseas closing
windows represented in the fund's quarterly portfolio report.  The current
INDA bid/ask and T-day SAFE central parity are then posted to /private.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import private_nasdaq_valuation_uploader as index_common
import private_valuation_uploader as common
import upload_monitor_status as upload_health


SYMBOL = "SZ164824"
REFERENCE_SYMBOL = "INDA"
NIFTY_SYMBOL = "NIFTY"
NIFTY_EXCHANGE = "SGX"
MODEL_VERSION = "private.t2-multimarket.inda-safe.v1"
SAFE_SOURCE = "SAFE_CENTRAL_PARITY"
SOURCE = "mac-home-private-164824-t2-inda-uploader"
SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
EASTMONEY_NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz"
PORTFOLIO_AS_OF = "2026-06-30"
PORTFOLIO_SOURCE = "工银瑞信印度市场证券投资基金（LOF）2026年第2季度报告，第5.9节"
INVESTMENT_RATIO = 0.8937
STATIC_RATIO = 0.1063
NIFTY_ROLL_CAPTURE_START_MINUTE = 12 * 60 + 28
NIFTY_ROLL_CAPTURE_END_MINUTE = 12 * 60 + 32
NIFTY_CONTRACT_SELECTION_VERSION = "sgx-monthly-effective-last-tuesday.v2"
BRIDGE_CAPTURE_START_MINUTE = 15 * 60 + 49
BRIDGE_CAPTURE_END_MINUTE = 15 * 60 + 51
BRIDGE_REFERENCE_MINUTE = 15 * 60 + 50
TWS_DEFAULT_OPEN_TIME = clock_time(9, 0)
HISTORICAL_BID_ASK_MARKET_DATA_TYPE = "HistoricalBidAsk"
HISTORICAL_BID_ASK_SOURCE = "IBKR_TWS_HISTORICAL_BID_ASK_1M"


@dataclass(frozen=True)
class AnchorSpec:
    key: str
    label: str
    weight: float
    timezone: str
    close_time: clock_time
    preferred_exchange: str


# Q2 report weights: US 52.60%, Europe 31.65%, Japan 4.25%, HK 0.87%,
# normalised inside the 89.37% overseas risk sleeve.  The proxy is INDA at all
# four stamps; capture status makes any off-session carry-forward explicit.
ANCHORS: tuple[AnchorSpec, ...] = (
    AnchorSpec("jp_close", "日本收盘", 0.0475551079780687, "Asia/Tokyo", clock_time(15, 0), "OVERNIGHT"),
    AnchorSpec("hk_close", "香港收盘", 0.0097348103390399, "Asia/Hong_Kong", clock_time(16, 0), "SMART"),
    AnchorSpec("eu_close", "欧洲收盘", 0.3541456864719705, "Europe/Berlin", clock_time(17, 30), "SMART"),
    AnchorSpec("us_close", "美国收盘", 0.5885643952109209, "America/New_York", clock_time(16, 0), "SMART"),
)


class SourceUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class OfficialNAV:
    value: float
    trading_day: date


@dataclass(frozen=True)
class AnchorObservation:
    key: str
    label: str
    weight: float
    price: float
    target_at: str
    observed_at: str
    source: str
    capture_status: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    contract: str
    bid: float
    ask: float
    last: float | None
    observed_at: datetime
    market_data_type: str
    source: str = "IBKR_TWS"

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "contract": self.contract,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "market_data_type": self.market_data_type,
            "source": self.source,
            "observed_at": common.iso_timestamp(self.observed_at),
        }


@dataclass(frozen=True)
class NiftyRollAdjustment:
    """Auditable old/new contract basis used only when contract legs differ."""

    roll_date: date
    captured_at: datetime
    old_contract: str
    new_contract: str
    direction: str
    bid_factor: float
    ask_factor: float

    def to_payload(self) -> dict[str, Any]:
        return {
            "roll_date": self.roll_date.isoformat(),
            "captured_at": common.iso_timestamp(self.captured_at),
            "old_contract": self.old_contract,
            "new_contract": self.new_contract,
            "direction": self.direction,
            "bid_factor": self.bid_factor,
            "ask_factor": self.ask_factor,
            "source": "IBKR_TWS_LIVE_SGX_NIFTY_ROLL_1230_BJT",
        }


def target_at(base_day: date, spec: AnchorSpec) -> datetime:
    return datetime.combine(base_day, spec.close_time, ZoneInfo(spec.timezone))


def parse_shanghai_clock_time(value: str) -> clock_time:
    """Parse a configurable local TWS opening time without accepting a date."""
    try:
        parsed = clock_time.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("time must be HH:MM or HH:MM:SS") from exc
    if parsed.tzinfo is not None:
        raise argparse.ArgumentTypeError("time must not contain a timezone")
    return parsed


def tws_collection_open(now: datetime, open_time: clock_time) -> bool:
    """Whether the remote TWS service is expected to be callable.

    The Mac-hosted collector is deliberately quiet before the user-operated
    remote TWS opens at 09:00 BJT.  In particular, it must not turn overnight
    connection failures into missing valuation data or a launchd restart loop.
    """
    local = now.astimezone(SHANGHAI)
    local_time = local.time().replace(tzinfo=None)
    return local.weekday() < 5 and open_time <= local_time < clock_time(15, 0)


def latest_completed_bridge_target(now: datetime) -> datetime:
    """Return the most recent completed weekday 15:50 ET bridge midpoint."""
    local = now.astimezone(NEW_YORK)
    candidate_day = local.date()
    candidate = datetime.combine(candidate_day, clock_time(BRIDGE_REFERENCE_MINUTE // 60, BRIDGE_REFERENCE_MINUTE % 60), NEW_YORK)
    if local >= candidate and candidate_day.weekday() < 5:
        return candidate
    candidate_day -= timedelta(days=1)
    while candidate_day.weekday() >= 5:
        candidate_day -= timedelta(days=1)
    return datetime.combine(candidate_day, clock_time(BRIDGE_REFERENCE_MINUTE // 60, BRIDGE_REFERENCE_MINUTE % 60), NEW_YORK)


def last_tuesday_of_month(value: date) -> date:
    """Return the calendar last Tuesday for value's month in Shanghai time."""
    if value.month == 12:
        next_month = date(value.year + 1, 1, 1)
    else:
        next_month = date(value.year, value.month + 1, 1)
    last_day = next_month - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - 1) % 7)


def select_nifty_monthly_contract(candidates: list[tuple[date, Any]], local_day: date) -> Any:
    """Use next month from its last-trading Tuesday onward.

    For example, the July 28 valuation date uses the August contract, and
    August 25 uses September. This is a calendar rule, not a post-close rule.
    """
    ordered = sorted((item for item in candidates if item[0] >= local_day), key=lambda item: item[0])
    if not ordered:
        raise SourceUnavailableError("TWS has no unexpired SGX NIFTY monthly contract")
    roll_day = last_tuesday_of_month(local_day)
    if local_day >= roll_day:
        post_roll = [contract for expiry, contract in ordered if expiry > roll_day]
        if post_roll:
            return post_roll[0]
    return ordered[0][1]


def positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def fetch_latest_official_nav(timeout: float, as_of: date) -> OfficialNAV:
    query = urllib.parse.urlencode({
        "fundCode": "164824", "pageIndex": "1", "pageSize": "40",
        "startDate": (as_of - timedelta(days=120)).isoformat(), "endDate": as_of.isoformat(),
    })
    request = urllib.request.Request(
        EASTMONEY_NAV_URL + "?" + query,
        headers={
            **common.SOURCE_HEADERS,
            "Referer": "https://fundf10.eastmoney.com/jjjz_164824.html",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise SourceUnavailableError(f"Eastmoney 164824 NAV unavailable: {exc}") from exc
    rows = ((payload.get("Data") or {}).get("LSJZList") or []) if isinstance(payload, dict) else []
    candidates: list[OfficialNAV] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            trading_day = date.fromisoformat(str(row.get("FSRQ") or ""))
        except ValueError:
            continue
        value = positive(row.get("DWJZ"))
        if value is not None and trading_day <= as_of:
            candidates.append(OfficialNAV(value, trading_day))
    if not candidates:
        raise SourceUnavailableError("Eastmoney returned no usable 164824 unit NAV")
    return max(candidates, key=lambda item: item.trading_day)


def safe_payload(parity: Any) -> dict[str, Any]:
    return {
        "pair": "USD/CNY",
        "rate": parity.rate,
        "trading_day": parity.trading_day.isoformat(),
        # SAFE fixes a daily central parity rather than an intraday quote; a
        # stable documented time avoids pretending it is a live FX tick.
        "quote_time": "09:15",
        "source": SAFE_SOURCE,
        "fetched_at": common.iso_timestamp(parity.fetched_at),
    }


def spot_payload(quote: common.CFETSSpotQuote) -> dict[str, Any]:
    return quote.to_payload()


class INDAMarket:
    def __init__(self, host: str, port: int, client_id: int, timeout: float) -> None:
        self.host, self.port, self.client_id, self.timeout = host, port, client_id, timeout
        self.ib: Any = None
        self.contracts: dict[str, Any] = {}
        self.tickers: dict[str, Any] = {}
        self.ticker_contracts: dict[str, Any] = {}
        self.shared_quotes: dict[str, Any] = {}
        self.nifty_candidates_day: date | None = None
        self.nifty_candidates: list[tuple[date, Any]] = []

    def connect(self) -> None:
        if self.ib is not None and self.ib.isConnected():
            return
        try:
            from ib_insync import IB
        except ImportError as exc:
            raise SourceUnavailableError("ib_insync is required for 164824 private valuation") from exc
        self.disconnect_direct()
        self.ib = IB()
        try:
            self.ib.connect(
                self.host, self.port, clientId=self.client_id,
                timeout=self.timeout, readonly=True,
            )
            if not self.ib.isConnected():
                raise SourceUnavailableError("TWS API socket did not become connected")
        except Exception:
            self.disconnect_direct()
            raise

    def disconnect_direct(self) -> None:
        """Close the low-frequency resolver/history session, never shared quotes."""
        if self.ib is not None:
            try:
                self.ib.disconnect()
            except Exception:
                pass
        self.ib = None

    def contract(self, exchange: str) -> Any:
        if exchange in self.contracts:
            return self.contracts[exchange]
        self.connect()
        try:
            from ib_insync import Stock
            candidates = self.ib.qualifyContracts(Stock(REFERENCE_SYMBOL, exchange, "USD"))
        except Exception:
            self.disconnect_direct()
            raise
        if not candidates:
            self.disconnect_direct()
            raise SourceUnavailableError(f"TWS cannot qualify {REFERENCE_SYMBOL} {exchange}")
        self.contracts[exchange] = candidates[0]
        return candidates[0]

    def nifty_contract_candidates(self, as_of: datetime) -> list[tuple[date, Any]]:
        today = as_of.astimezone(SHANGHAI).date()
        if self.nifty_candidates_day == today and self.nifty_candidates:
            return list(self.nifty_candidates)
        self.connect()
        try:
            from ib_insync import Future
            details = self.ib.reqContractDetails(
                Future(NIFTY_SYMBOL, exchange=NIFTY_EXCHANGE, currency="USD")
            )
        except Exception:
            self.disconnect_direct()
            raise
        candidates: list[tuple[date, Any]] = []
        for detail in details:
            contract = getattr(detail, "contract", None)
            expiry = str(getattr(contract, "lastTradeDateOrContractMonth", ""))[:8]
            try:
                expiry_day = datetime.strptime(expiry, "%Y%m%d").date()
            except ValueError:
                continue
            if expiry_day >= today:
                candidates.append((expiry_day, contract))
        if not candidates:
            self.disconnect_direct()
            raise SourceUnavailableError("TWS cannot resolve an unexpired SGX NIFTY future")
        self.nifty_candidates_day = today
        self.nifty_candidates = sorted(candidates, key=lambda item: item[0])
        return list(self.nifty_candidates)

    def nifty_contract(self, as_of: datetime) -> Any:
        candidates = self.nifty_contract_candidates(as_of)
        today = as_of.astimezone(SHANGHAI).date()
        return select_nifty_monthly_contract(candidates, today)

    def nifty_roll_pair(self, as_of: datetime) -> tuple[date, Any, Any] | None:
        local_day = as_of.astimezone(SHANGHAI).date()
        roll_day = last_tuesday_of_month(local_day)
        if local_day != roll_day - timedelta(days=1):
            return None
        try:
            candidates = self.nifty_contract_candidates(as_of)
            old = next((contract for expiry, contract in candidates if expiry == roll_day), None)
            new = next((contract for expiry, contract in candidates if expiry > roll_day), None)
        finally:
            self.disconnect_direct()
        if old is None or new is None:
            return None
        return roll_day, old, new

    def quote(self, key: str, contract: Any, symbol: str) -> MarketQuote | None:
        from machome_ibkr_bridge_client import ContractSubscription, SharedSingleQuoteStream
        if key not in self.shared_quotes:
            if key == "INDA":
                subscription = ContractSubscription.create(
                    subscription_id="INDA.SMART",
                    symbol=REFERENCE_SYMBOL,
                    security_type="STK",
                    exchange="SMART",
                    primary_exchange="ARCA",
                    currency="USD",
                )
                contract_name = REFERENCE_SYMBOL
            else:
                subscription = ContractSubscription.create(
                    symbol=str(getattr(contract, "symbol", "") or symbol),
                    security_type=str(getattr(contract, "secType", "") or "FUT"),
                    exchange=str(getattr(contract, "exchange", "") or NIFTY_EXCHANGE),
                    primary_exchange=str(getattr(contract, "primaryExchange", "") or ""),
                    currency=str(getattr(contract, "currency", "") or "USD"),
                    con_id=int(getattr(contract, "conId", 0) or 0),
                    expiry=str(getattr(contract, "lastTradeDateOrContractMonth", "") or ""),
                    multiplier=str(getattr(contract, "multiplier", "") or ""),
                    trading_class=str(getattr(contract, "tradingClass", "") or ""),
                )
                contract_name = str(
                    getattr(contract, "localSymbol", "") or getattr(contract, "symbol", symbol)
                )
            stream = SharedSingleQuoteStream(subscription, timeout=self.timeout)
            try:
                stream.connect()
            except Exception as exc:
                stream.close()
                raise SourceUnavailableError(f"native IBKR bridge unavailable: {exc}") from exc
            self.shared_quotes[key] = (stream, contract_name)
        stream, contract_name = self.shared_quotes[key]
        # A newly subscribed TWS ticker may need several event-loop turns
        # before its first live bid/ask arrives.  Do not misclassify that
        # subscription warm-up as an unavailable bridge reference.
        for _ in range(10):
            try:
                value = stream.poll(0.1)
            except Exception as exc:
                raise SourceUnavailableError(f"native IBKR bridge poll failed: {exc}") from exc
            if value is None:
                continue
            return MarketQuote(
                symbol, contract_name, value.bid, value.ask, value.last,
                value.observed_at.astimezone(SHANGHAI), value.market_data_type,
            )
        return None

    def inda_quote(self) -> MarketQuote | None:
        return self.quote("INDA", None, REFERENCE_SYMBOL)

    def nifty_quote_for_contract(self, contract: Any) -> MarketQuote | None:
        contract_id = str(getattr(contract, "localSymbol", "") or getattr(contract, "conId", "") or NIFTY_SYMBOL)
        return self.quote(f"{NIFTY_SYMBOL}:{contract_id}", contract, NIFTY_SYMBOL)

    def nifty_quote(self, as_of: datetime) -> MarketQuote | None:
        try:
            contract = self.nifty_contract(as_of)
        finally:
            self.disconnect_direct()
        return self.nifty_quote_for_contract(contract)

    def historical_bid_ask_window(
        self,
        contract: Any,
        symbol: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[MarketQuote]:
        """Return exact common one-minute historical BID/ASK observations.

        IBKR's TWS session on mac-home begins after the US close.  Retrieving
        the completed 15:49-15:51 ET bars after 09:00 BJT preserves the
        intended simultaneous bridge reference without requiring an overnight
        TWS connection.  BID and ASK are deliberately requested separately;
        a combined BID_ASK bar is not a two-sided executable quote.
        """
        if window_start.tzinfo is None or window_end.tzinfo is None or window_end < window_start:
            raise SourceUnavailableError("historical bridge window must be timezone-aware and ordered")
        series: dict[str, dict[datetime, float]] = {}
        self.connect()
        try:
            for what in ("BID", "ASK"):
                bars = self.ib.reqHistoricalData(
                    contract,
                    endDateTime=window_end + timedelta(minutes=1),
                    durationStr="1 D",
                    barSizeSetting="1 min",
                    whatToShow=what,
                    useRTH=False,
                    formatDate=2,
                    keepUpToDate=False,
                    timeout=self.timeout,
                )
                values: dict[datetime, float] = {}
                for bar in bars:
                    observed_at = getattr(bar, "date", None)
                    if not isinstance(observed_at, datetime):
                        continue
                    if observed_at.tzinfo is None:
                        observed_at = observed_at.replace(tzinfo=NEW_YORK)
                    observed_at = observed_at.astimezone(NEW_YORK).replace(
                        second=0, microsecond=0
                    )
                    value = positive(getattr(bar, "close", None))
                    if value is not None and window_start <= observed_at <= window_end:
                        values[observed_at] = value
                series[what] = values
        finally:
            self.disconnect_direct()
        contract_name = str(getattr(contract, "localSymbol", "") or getattr(contract, "symbol", symbol))
        quotes: list[MarketQuote] = []
        for observed_at in sorted(set(series["BID"]).intersection(series["ASK"])):
            bid, ask = series["BID"][observed_at], series["ASK"][observed_at]
            if ask >= bid:
                quotes.append(MarketQuote(
                    symbol, contract_name, bid, ask, None, observed_at,
                    HISTORICAL_BID_ASK_MARKET_DATA_TYPE, HISTORICAL_BID_ASK_SOURCE,
                ))
        return quotes

    def historical_bridge_samples(self, target: datetime) -> list[tuple[datetime, MarketQuote, MarketQuote]]:
        """Read the completed synchronous INDA/NIFTY bridge from TWS history."""
        target = target.astimezone(NEW_YORK).replace(second=0, microsecond=0)
        if not bridge_capture_window(target):
            raise SourceUnavailableError("historical bridge target must be within 15:49-15:51 ET")
        window_start = target.replace(hour=BRIDGE_CAPTURE_START_MINUTE // 60, minute=BRIDGE_CAPTURE_START_MINUTE % 60)
        window_end = target.replace(hour=BRIDGE_CAPTURE_END_MINUTE // 60, minute=BRIDGE_CAPTURE_END_MINUTE % 60)
        inda = self.historical_bid_ask_window(self.contract("SMART"), REFERENCE_SYMBOL, window_start, window_end)
        nifty = self.historical_bid_ask_window(self.nifty_contract(target), NIFTY_SYMBOL, window_start, window_end)
        common = sorted({item.observed_at for item in inda}.intersection(item.observed_at for item in nifty))
        if not common:
            raise SourceUnavailableError("TWS history has no common INDA/NIFTY BID/ASK minute in the 15:49-15:51 ET bridge window")
        by_india = {item.observed_at: item for item in inda}
        by_nifty = {item.observed_at: item for item in nifty}
        # The caller records all observations, then the existing median reader
        # turns them into the audit-friendly bridge reference.
        return [(observed_at, by_india[observed_at], by_nifty[observed_at]) for observed_at in common]

    def anchor_for(self, base_day: date, spec: AnchorSpec) -> AnchorObservation:
        target = target_at(base_day, spec)
        self.connect()
        try:
            contract = self.contract(spec.preferred_exchange)
            # Search a broad window. A close-price proxy at the Japan/HK stamp may
            # legitimately be the latest tradable INDA overnight observation; its
            # status and actual time are retained instead of fabricating a tick.
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime=target + timedelta(minutes=2),
                durationStr="1 D",
                barSizeSetting="1 min",
                whatToShow="BID_ASK",
                useRTH=False,
                formatDate=2,
                keepUpToDate=False,
                timeout=self.timeout,
            )
        finally:
            self.disconnect_direct()
        prior: list[tuple[datetime, float]] = []
        following: list[tuple[datetime, float]] = []
        for bar in bars:
            bar_at = getattr(bar, "date", None)
            if not isinstance(bar_at, datetime):
                continue
            if bar_at.tzinfo is None:
                bar_at = bar_at.replace(tzinfo=ZoneInfo("America/New_York"))
            price = positive(getattr(bar, "close", None))
            if price is None:
                continue
            if bar_at <= target:
                prior.append((bar_at, price))
            else:
                following.append((bar_at, price))
        status = "exact_1m"
        if prior:
            observed_at, price = max(prior, key=lambda item: item[0])
            delta = (target - observed_at).total_seconds()
            if delta > 180:
                status = "prior_tradable_carry"
        elif following:
            observed_at, price = min(following, key=lambda item: item[0])
            status = "next_tradable_fallback"
        else:
            raise SourceUnavailableError(f"TWS returned no INDA BID_ASK minute bar for {spec.key} {base_day}")
        return AnchorObservation(
            spec.key, spec.label, spec.weight, price,
            target.isoformat(), observed_at.isoformat(),
            f"IBKR_TWS_INDA_{spec.preferred_exchange}_BID_ASK_1M", status,
        )

    def close(self) -> None:
        self.disconnect_direct()
        for stream, _contract_name in self.shared_quotes.values():
            stream.close()
        self.shared_quotes = {}
        self.contracts = {}
        self.tickers = {}
        self.ticker_contracts = {}
        self.nifty_candidates_day = None
        self.nifty_candidates = []


def cache_path(runtime: Path, base_day: date) -> Path:
    return runtime / "anchors" / f"{base_day:%Y%m%d}.json"


def load_cached_anchors(path: Path) -> list[AnchorObservation] | None:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
        rows = decoded.get("anchors") if isinstance(decoded, dict) else None
        if not isinstance(rows, list) or len(rows) != len(ANCHORS):
            return None
        anchors = [AnchorObservation(**row) for row in rows]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return anchors if {item.key for item in anchors} == {spec.key for spec in ANCHORS} else None


def anchors_for(market: INDAMarket, runtime: Path, base_day: date) -> list[AnchorObservation]:
    path = cache_path(runtime, base_day)
    cached = load_cached_anchors(path)
    if cached is not None:
        return cached
    anchors = [market.anchor_for(base_day, spec) for spec in ANCHORS]
    payload = json.dumps({"base_nav_date": base_day.isoformat(), "anchors": [item.to_payload() for item in anchors]}, ensure_ascii=False).encode("utf-8")
    common._atomic_write_bytes(path, payload)
    return anchors


def bridge_capture_window(now: datetime) -> bool:
    new_york = now.astimezone(NEW_YORK)
    if new_york.weekday() >= 5:
        return False
    minute = new_york.hour * 60 + new_york.minute
    return BRIDGE_CAPTURE_START_MINUTE <= minute <= BRIDGE_CAPTURE_END_MINUTE


def nifty_roll_capture_window(now: datetime) -> bool:
    """Liquid Monday 12:28–12:32 BJT window before the monthly last Tuesday."""
    local = now.astimezone(SHANGHAI)
    if local.weekday() != 0 or local.date() + timedelta(days=1) != last_tuesday_of_month(local.date()):
        return False
    minute = local.hour * 60 + local.minute
    return NIFTY_ROLL_CAPTURE_START_MINUTE <= minute <= NIFTY_ROLL_CAPTURE_END_MINUTE


def bridge_cache_path(runtime: Path, captured_at: datetime) -> Path:
    return runtime / "nifty_bridge" / f"{captured_at.astimezone(SHANGHAI):%Y%m%d}.json"


def nifty_roll_cache_path(runtime: Path, roll_day: date) -> Path:
    return runtime / "nifty_rolls" / f"{roll_day:%Y%m}.json"


def quote_from_payload(payload: Any) -> MarketQuote | None:
    if not isinstance(payload, dict):
        return None
    try:
        observed_at = datetime.fromisoformat(str(payload.get("observed_at") or ""))
    except ValueError:
        return None
    bid, ask = positive(payload.get("bid")), positive(payload.get("ask"))
    if bid is None or ask is None or ask < bid or observed_at.tzinfo is None:
        return None
    symbol = str(payload.get("symbol") or "").strip().upper()
    contract = str(payload.get("contract") or symbol).strip()
    market_data_type = str(payload.get("market_data_type") or "").strip()
    source = str(payload.get("source") or "").strip()
    if not symbol or not contract or not market_data_type or not source:
        return None
    return MarketQuote(symbol, contract, bid, ask, positive(payload.get("last")), observed_at, market_data_type, source)


def record_nifty_roll_sample(
    runtime: Path,
    captured_at: datetime,
    roll_day: date,
    old: MarketQuote,
    new: MarketQuote,
) -> None:
    if not nifty_roll_capture_window(captured_at):
        raise SourceUnavailableError("NIFTY roll basis must be sampled on the stated Monday 12:28-12:32 BJT window")
    if old.market_data_type != "Live" or new.market_data_type != "Live":
        raise SourceUnavailableError("NIFTY roll basis requires live old/new SGX bid/ask quotes")
    if old.contract == new.contract:
        raise SourceUnavailableError("NIFTY roll basis requires distinct expiring and next contracts")
    path = nifty_roll_cache_path(runtime, roll_day)
    samples: list[dict[str, Any]] = []
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
        cached = decoded.get("samples") if isinstance(decoded, dict) else None
        if isinstance(cached, list):
            samples = [item for item in cached if isinstance(item, dict)][-120:]
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    samples.append({
        "captured_at": common.iso_timestamp(captured_at),
        "old": old.to_payload(),
        "new": new.to_payload(),
    })
    payload = {
        "schema_version": 1,
        "contract_selection_version": NIFTY_CONTRACT_SELECTION_VERSION,
        "roll_date": roll_day.isoformat(),
        "window_timezone": "Asia/Shanghai",
        "window": "Monday 12:28-12:32 before monthly last Tuesday",
        "samples": samples[-120:],
    }
    common._atomic_write_bytes(path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def bridge_reference_quote(value: MarketQuote) -> bool:
    if value.market_data_type == "Live":
        return bool(value.source)
    return value.market_data_type == HISTORICAL_BID_ASK_MARKET_DATA_TYPE and value.source == HISTORICAL_BID_ASK_SOURCE


def record_bridge_sample(runtime: Path, captured_at: datetime, inda: MarketQuote, nifty: MarketQuote) -> None:
    if not bridge_capture_window(captured_at):
        raise SourceUnavailableError("164824 bridge reference must be sampled within 15:49-15:51 ET")
    if not bridge_reference_quote(inda) or not bridge_reference_quote(nifty):
        raise SourceUnavailableError("164824 bridge reference requires live or audited historical INDA/NIFTY BID/ASK quotes")
    if inda.market_data_type != nifty.market_data_type or inda.source != nifty.source:
        raise SourceUnavailableError("164824 bridge reference quote sources must match")
    path = bridge_cache_path(runtime, captured_at)
    samples: list[dict[str, Any]] = []
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
        cached = decoded.get("samples") if isinstance(decoded, dict) else None
        if isinstance(cached, list):
            samples = [item for item in cached if isinstance(item, dict)][-240:]
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    samples.append({
        "captured_at": common.iso_timestamp(captured_at),
        "inda": inda.to_payload(),
        "nifty": nifty.to_payload(),
    })
    payload = {
        "schema_version": 1,
        "contract_selection_version": NIFTY_CONTRACT_SELECTION_VERSION,
        "window_timezone": "America/New_York",
        "window": "15:49-15:51 ET (19-session spread/close-residual study; midpoint 15:50)",
        "capture_method": "historical_after_tws_open" if inda.market_data_type == HISTORICAL_BID_ASK_MARKET_DATA_TYPE else "live",
        "samples": samples[-240:],
    }
    common._atomic_write_bytes(path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def median_quote(samples: list[MarketQuote], symbol: str, reference_at: datetime) -> MarketQuote | None:
    if not samples:
        return None
    contracts = [item.contract for item in samples if item.contract]
    contract = max(set(contracts), key=contracts.count) if contracts else symbol
    lasts = [item.last for item in samples if item.last is not None]
    market_data_types = [item.market_data_type for item in samples if item.market_data_type]
    sources = [item.source for item in samples if item.source]
    return MarketQuote(
        symbol, contract, statistics.median(item.bid for item in samples), statistics.median(item.ask for item in samples),
        statistics.median(lasts) if lasts else None, reference_at,
        max(set(market_data_types), key=market_data_types.count) if market_data_types else "Live",
        max(set(sources), key=sources.count) if sources else "IBKR_TWS",
    )


def roll_adjusted_nifty_quote(
    runtime: Path,
    current: MarketQuote,
    reference: MarketQuote,
) -> tuple[MarketQuote, NiftyRollAdjustment | None] | None:
    """Put current NIFTY on the reference contract's scale when a roll splits them.

    When both legs already use the same contract no adjustment is applied. If
    they differ and no audited Monday sample exists, the bridge is withheld:
    returning a raw different-contract ratio would manufacture a valuation
    jump exactly when liquidity moves to the next monthly future.
    """
    if current.contract == reference.contract:
        return current, None
    directory = runtime / "nifty_rolls"
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
            roll_day = date.fromisoformat(str(decoded.get("roll_date") or "")) if isinstance(decoded, dict) else None
            rows = decoded.get("samples") if isinstance(decoded, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if (
            roll_day is None
            or not isinstance(decoded, dict)
            or decoded.get("contract_selection_version") != NIFTY_CONTRACT_SELECTION_VERSION
            or not isinstance(rows, list)
            or roll_day != last_tuesday_of_month(roll_day)
        ):
            continue
        pairs: list[tuple[datetime, MarketQuote, MarketQuote]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                captured_at = datetime.fromisoformat(str(row.get("captured_at") or ""))
            except ValueError:
                continue
            old, new = quote_from_payload(row.get("old")), quote_from_payload(row.get("new"))
            if (
                captured_at.tzinfo is None
                or old is None
                or new is None
                or old.symbol != NIFTY_SYMBOL
                or new.symbol != NIFTY_SYMBOL
                or old.market_data_type != "Live"
                or new.market_data_type != "Live"
                or old.contract == new.contract
                or not nifty_roll_capture_window(captured_at)
            ):
                continue
            pairs.append((captured_at, old, new))
        if not pairs:
            continue
        pairs.sort(key=lambda item: item[0])
        captured_at = pairs[len(pairs) // 2][0]
        old = median_quote([item[1] for item in pairs], NIFTY_SYMBOL, captured_at)
        new = median_quote([item[2] for item in pairs], NIFTY_SYMBOL, captured_at)
        if old is None or new is None:
            continue
        if reference.contract == old.contract and current.contract == new.contract:
            bid_factor, ask_factor = old.bid / new.ask, old.ask / new.bid
            direction = "new_to_old"
        elif reference.contract == new.contract and current.contract == old.contract:
            bid_factor, ask_factor = new.bid / old.ask, new.ask / old.bid
            direction = "old_to_new"
        else:
            continue
        bid, ask = current.bid * bid_factor, current.ask * ask_factor
        if bid <= 0 or ask < bid:
            continue
        adjustment = NiftyRollAdjustment(
            roll_day, captured_at, old.contract, new.contract, direction, bid_factor, ask_factor,
        )
        return MarketQuote(
            current.symbol, current.contract, bid, ask, current.last,
            current.observed_at, current.market_data_type,
        ), adjustment
    return None


def latest_bridge_reference(runtime: Path, now: datetime) -> tuple[datetime, MarketQuote, MarketQuote] | None:
    directory = runtime / "nifty_bridge"
    if not directory.is_dir():
        return None
    cutoff = now - timedelta(days=4)
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
            rows = decoded.get("samples") if isinstance(decoded, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(decoded, dict) or decoded.get("contract_selection_version") != NIFTY_CONTRACT_SELECTION_VERSION or not isinstance(rows, list):
            continue
        pairs: list[tuple[datetime, MarketQuote, MarketQuote]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                captured_at = datetime.fromisoformat(str(row.get("captured_at") or ""))
            except ValueError:
                continue
            inda, nifty = quote_from_payload(row.get("inda")), quote_from_payload(row.get("nifty"))
            if (
                captured_at.tzinfo is None
                or inda is None
                or nifty is None
                or inda.symbol != REFERENCE_SYMBOL
                or nifty.symbol != NIFTY_SYMBOL
                or not bridge_reference_quote(inda)
                or not bridge_reference_quote(nifty)
                or inda.market_data_type != nifty.market_data_type
                or inda.source != nifty.source
            ):
                continue
            if captured_at > now or captured_at < cutoff or not bridge_capture_window(captured_at):
                continue
            pairs.append((captured_at, inda, nifty))
        if not pairs:
            continue
        pairs.sort(key=lambda item: item[0])
        reference_at = pairs[len(pairs) // 2][0]
        inda = median_quote([item[1] for item in pairs], REFERENCE_SYMBOL, reference_at)
        nifty = median_quote([item[2] for item in pairs], NIFTY_SYMBOL, reference_at)
        if inda is not None and nifty is not None:
            return reference_at, inda, nifty
    return None


def bridge_reference_matches_target(
    reference: tuple[datetime, MarketQuote, MarketQuote] | None,
    target: datetime,
) -> bool:
    if reference is None:
        return False
    return abs((reference[0].astimezone(NEW_YORK) - target.astimezone(NEW_YORK)).total_seconds()) <= 5 * 60


def build_payload(nav: OfficialNAV, base_fx: Any, current_fx: Any, quote: MarketQuote, anchors: list[AnchorObservation], generated_at: datetime, nifty: MarketQuote | None = None, bridge_reference: tuple[datetime, MarketQuote, MarketQuote] | None = None, nifty_beta: float = 1.0, nifty_roll_adjustment: NiftyRollAdjustment | None = None) -> dict[str, Any]:
    if len(anchors) != len(ANCHORS) or abs(sum(item.weight for item in anchors) - 1) > 1e-9:
        raise SourceUnavailableError("164824 anchor set is incomplete or has invalid weights")
    india_payload: dict[str, Any] = {
        "base_nav": nav.value,
        "base_nav_date": nav.trading_day.isoformat(),
        "base_fx": safe_payload(base_fx),
        "current_fx": spot_payload(current_fx),
        "investment_ratio": INVESTMENT_RATIO,
        "static_ratio": STATIC_RATIO,
        "anchors": [item.to_payload() for item in anchors],
        "portfolio_as_of": PORTFOLIO_AS_OF,
        "portfolio_source": PORTFOLIO_SOURCE,
    }
    if nifty is not None and bridge_reference is not None:
        reference_at, inda_reference, nifty_reference = bridge_reference
        bridge_payload: dict[str, Any] = {
            "nifty": nifty.to_payload(),
            "inda_reference": inda_reference.to_payload(),
            "nifty_reference": nifty_reference.to_payload(),
            "reference_at": common.iso_timestamp(reference_at),
            "beta": nifty_beta,
            "contract_selection_version": NIFTY_CONTRACT_SELECTION_VERSION,
        }
        if nifty_roll_adjustment is not None:
            bridge_payload["roll_adjustment"] = nifty_roll_adjustment.to_payload()
        india_payload["nifty_bridge"] = bridge_payload
    ib_payload = quote.to_payload()
    ib_payload["stream_checked_at"] = common.iso_timestamp(generated_at)
    return {
        "schema_version": 1,
        "symbol": SYMBOL,
        "model_version": MODEL_VERSION,
        "india": india_payload,
        "ib": ib_payload,
        "source": SOURCE,
        "generated_at": common.iso_timestamp(generated_at),
    }


def post(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(
        args.server.rstrip("/") + "/api/v1/private/inputs/" + SYMBOL,
        data=common.gzip_json_body(payload), method="POST",
        headers=common.gzip_server_headers(args.server, args.token),
    )
    try:
        with common.open_server_request(request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        error = SourceUnavailableError(f"private 164824 upload status {exc.code}: {detail}")
        upload_health.record_failure(SOURCE, stage="private_input_upload", error=error)
        raise error from exc
    except Exception as exc:
        upload_health.record_failure(SOURCE, stage="private_input_upload", error=exc)
        raise
    upload_health.record_success(SOURCE, stage="private_input_ack", accepted=1, symbols=[SYMBOL])


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    result.add_argument("--runtime-dir", default=os.getenv("NNN_PRIVATE_164824_RUNTIME_DIR", "scripts/.runtime/private_164824"))
    result.add_argument("--ib-host", default=os.getenv("NNN_PRIVATE_164824_IB_HOST", os.getenv("NNN_IB_HOST", "127.0.0.1")))
    result.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_PRIVATE_164824_IB_PORT", os.getenv("NNN_IB_PORT", "7496"))))
    result.add_argument("--ib-client-id", type=int, default=int(os.getenv("NNN_PRIVATE_164824_IB_CLIENT_ID", "164824")))
    result.add_argument(
        "--ib-timeout",
        type=float,
        default=float(os.getenv("NNN_PRIVATE_164824_IB_TIMEOUT", "40")),
        help="TWS socket handshake timeout",
    )
    result.add_argument("--nifty-beta", type=float, default=float(os.getenv("NNN_PRIVATE_164824_NIFTY_BETA", "1")))
    result.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", "12")))
    result.add_argument("--upload-interval", type=float, default=float(os.getenv("NNN_PRIVATE_164824_UPLOAD_INTERVAL", "3")))
    result.add_argument(
        "--tws-open-time",
        type=parse_shanghai_clock_time,
        default=parse_shanghai_clock_time(os.getenv("NNN_PRIVATE_164824_TWS_OPEN_TIME", "09:00")),
        help="Asia/Shanghai time after which the remote TWS API may be contacted (default: 09:00)",
    )
    result.add_argument("--once", action="store_true")
    result.add_argument("--force", action="store_true", help="allow one diagnostic upload outside the China session")
    return result


def run(args: argparse.Namespace) -> int:
    if not str(args.token).strip():
        raise SourceUnavailableError("NNN_UPLOAD_TOKEN or --token is required")
    if not math.isclose(args.nifty_beta, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise SourceUnavailableError("164824 NIFTY bridge beta is fixed at 1 until an out-of-sample calibration is approved")
    runtime = Path(args.runtime_dir).expanduser().resolve()
    market = INDAMarket(args.ib_host, args.ib_port, args.ib_client_id, args.ib_timeout)
    spot_client = common.PrivateCFETSSpotClient(
        args.server,
        args.token,
        timeout=args.timeout,
        origin_ip=args.origin_ip,
        origin_tls_insecure=args.origin_tls_insecure,
        origin_ca_file=args.origin_ca_file,
    )
    nav: OfficialNAV | None = None
    base_fx: Any = None
    anchors: list[AnchorObservation] = []
    current_fx: Any = None
    next_nav_refresh = next_fx_refresh = 0.0
    bridge_target: datetime | None = None
    next_bridge_reference_attempt = 0.0
    try:
        while not common.STOP_EVENT.is_set():
            now = datetime.now(SHANGHAI)
            if not args.force and not tws_collection_open(now, args.tws_open_time):
                # Do not even open a socket before the remote, user-operated
                # TWS is expected to be available.  launchd keeps the process
                # alive, so this is a quiet wait rather than a failed run.
                market.close()
                common.STOP_EVENT.wait(30.0)
                if args.once:
                    return 0
                continue
            in_china_session = common.ib_collection_window(now)
            in_bridge_window = bridge_capture_window(now)
            in_roll_window = nifty_roll_capture_window(now)
            if not args.force:
                target = latest_completed_bridge_target(now)
                if target != bridge_target:
                    bridge_target = target
                    next_bridge_reference_attempt = 0.0
                if time.monotonic() >= next_bridge_reference_attempt:
                    cached = latest_bridge_reference(runtime, now)
                    if bridge_reference_matches_target(cached, target):
                        next_bridge_reference_attempt = float("inf")
                    else:
                        try:
                            for captured_at, inda_reference, nifty_reference in market.historical_bridge_samples(target):
                                record_bridge_sample(runtime, captured_at, inda_reference, nifty_reference)
                            common.log(
                                f"164824 NIFTY bridge reference backfilled after TWS open target={target.isoformat()}"
                            )
                            next_bridge_reference_attempt = float("inf")
                        except SourceUnavailableError as exc:
                            # A late TWS start should not terminate launchd or
                            # publish a made-up bridge. Retry after five minutes.
                            market.close()
                            common.log(f"164824 NIFTY bridge reference deferred: {exc}", error=True)
                            next_bridge_reference_attempt = time.monotonic() + 5 * 60
            if not args.force and not in_china_session and not in_bridge_window and not in_roll_window:
                market.close()
                common.STOP_EVENT.wait(30.0)
                if args.once:
                    return 0
                continue
            if in_bridge_window and not in_china_session:
                inda = market.inda_quote()
                nifty = market.nifty_quote(now)
                if inda is None or nifty is None:
                    raise SourceUnavailableError("164824 bridge capture needs live INDA and SGX NIFTY bid/ask")
                record_bridge_sample(runtime, now, inda, nifty)
                common.log(f"164824 NIFTY bridge sample captured INDA={inda.bid:.4f}/{inda.ask:.4f} NIFTY={nifty.bid:.1f}/{nifty.ask:.1f}")
                if args.once:
                    return 0
                common.STOP_EVENT.wait(max(0.5, args.upload_interval))
                continue
            if in_roll_window:
                pair = market.nifty_roll_pair(now)
                if pair is None:
                    raise SourceUnavailableError("NIFTY Monday roll window has no expiring/next SGX monthly contract pair")
                roll_day, old_contract, new_contract = pair
                old = market.nifty_quote_for_contract(old_contract)
                new = market.nifty_quote_for_contract(new_contract)
                if old is None or new is None:
                    raise SourceUnavailableError("NIFTY Monday roll basis needs live old/new SGX bid/ask")
                record_nifty_roll_sample(runtime, now, roll_day, old, new)
                common.log(
                    f"164824 NIFTY roll basis captured roll={roll_day} "
                    f"old={old.contract} {old.bid:.1f}/{old.ask:.1f} "
                    f"new={new.contract} {new.bid:.1f}/{new.ask:.1f}"
                )
                if args.once:
                    return 0
                common.STOP_EVENT.wait(max(0.5, args.upload_interval))
                continue
            if time.monotonic() >= next_nav_refresh:
                nav = fetch_latest_official_nav(args.timeout, now.date())
                base_fx = index_common.fetch_safe_central_parity(nav.trading_day, args.timeout, index_common.NASDAQ_FAMILY)
                anchors = anchors_for(market, runtime, nav.trading_day)
                next_nav_refresh = time.monotonic() + 15 * 60
            if time.monotonic() >= next_fx_refresh:
                current_fx = spot_client.fetch("USD/CNY", now.date())
                next_fx_refresh = time.monotonic() + 60
            quote = market.inda_quote()
            if nav is None or base_fx is None or current_fx is None or quote is None:
                raise SourceUnavailableError("164824 needs NAV, base SAFE parity, current CFETS spot, anchors and live INDA bid/ask")
            bridge_reference = latest_bridge_reference(runtime, now)
            nifty: MarketQuote | None = None
            nifty_roll_adjustment: NiftyRollAdjustment | None = None
            if bridge_reference is not None:
                raw_nifty = market.nifty_quote(now)
                if raw_nifty is not None:
                    aligned = roll_adjusted_nifty_quote(runtime, raw_nifty, bridge_reference[2])
                    if aligned is not None:
                        nifty, nifty_roll_adjustment = aligned
                    else:
                        common.log(
                            "164824 NIFTY bridge withheld: current and reference contracts differ without an audited Monday 12:30 BJT roll basis",
                            error=True,
                        )
            payload = build_payload(nav, base_fx, current_fx, quote, anchors, now, nifty, bridge_reference, args.nifty_beta, nifty_roll_adjustment)
            post(args, payload)
            bridge_status = "ready" if "nifty_bridge" in payload["india"] else "unavailable"
            roll_status = nifty_roll_adjustment.direction if nifty_roll_adjustment is not None else "none"
            common.log(
                f"164824 private input uploaded base_nav={nav.value:.4f}@{nav.trading_day} "
                f"INDA={quote.bid:.4f}/{quote.ask:.4f} NIFTY-bridge={bridge_status} roll={roll_status} anchors={len(anchors)}"
            )
            if args.once:
                return 0
            common.STOP_EVENT.wait(max(0.5, args.upload_interval))
    finally:
        market.close()
    return 0


def main() -> int:
    common.load_env_file(".sina-uploader.env")
    args = parser().parse_args()
    signal.signal(signal.SIGINT, common._request_stop)
    signal.signal(signal.SIGTERM, common._request_stop)
    try:
        return run(args)
    except (SourceUnavailableError, RuntimeError, urllib.error.URLError) as exc:
        common.log(str(exc), error=True)
        return 1


if __name__ == "__main__":  # pragma: no cover - launchd entry point
    sys.exit(main())

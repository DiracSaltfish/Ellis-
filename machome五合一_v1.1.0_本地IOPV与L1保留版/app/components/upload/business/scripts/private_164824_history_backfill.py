#!/usr/bin/env python3
"""Backfill auditable one-minute direct-INDA history for SZ164824.

Each China-session point uses the public SZ164824 minute price, the T-2
official NAV and SAFE USD/CNY parities prescribed by the live model, the four
dated INDA anchor observations, and the last real INDA BID/ASK minute before
the China session opens.  INDA is closed during the China session, so its
final US quote is intentionally held as an observed market close rather than
inventing a minute-by-minute price path.  NIFTY bridge IOPV is deliberately
not written to historical final-NAV rows.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_164824_valuation_uploader as india  # noqa: E402
import private_nasdaq_valuation_uploader as index_common  # noqa: E402
import private_valuation_uploader as common  # noqa: E402


SYMBOL = india.SYMBOL
MODEL_VERSION = india.MODEL_VERSION
HISTORY_PATH = "/api/v1/private/funds/{symbol}/minute-history/import"
SOURCE = "mac-local-private-164824-history-backfill"
HISTORICAL_IB_SOURCE = "IBKR_TWS_HISTORICAL_BID_ASK"
HISTORICAL_MARKET_DATA_TYPE = "HistoricalBidAsk"
HISTORICAL_NIFTY_SOURCE = "IBKR_TWS_HISTORICAL_NIFTY_BID_ASK_1M"
HISTORICAL_NIFTY_ROLL_SOURCE = india.HISTORICAL_BID_ASK_SOURCE
SHANGHAI = india.SHANGHAI
DEFAULT_RUNTIME_DIR = Path(__file__).resolve().parent / ".runtime" / "private_164824_history_backfill"
MAX_COMPRESSED_BYTES = 1 << 20
MAX_DECOMPRESSED_BYTES = 4 << 20


class SourceUnavailableError(RuntimeError):
    """A required dated source is unavailable or fails the audit checks."""


@dataclass(frozen=True)
class HistoricalNiftyRollAdjustment:
    """Historical old/new contract basis used to match the reference scale."""

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
            # Historical replay must not be mislabeled as a live subscription.
            "source": HISTORICAL_NIFTY_ROLL_SOURCE,
        }


@dataclass(frozen=True)
class PreparedDay:
    day: date
    nav: india.OfficialNAV
    base_fx: Any
    current_fx: Any
    anchors: tuple[india.AnchorObservation, ...]
    close_quote: india.MarketQuote
    close_source: str
    market_prices: dict[str, float]
    coverage: float
    max_gap: int
    nifty_quotes: dict[str, india.MarketQuote] | None = None
    bridge_reference: tuple[datetime, india.MarketQuote, india.MarketQuote] | None = None
    nifty_roll_adjustment: HistoricalNiftyRollAdjustment | None = None


def parse_day(value: str) -> date:
    return datetime.strptime(str(value).strip().replace("-", ""), "%Y%m%d").date()


def day_key(value: date) -> str:
    return value.strftime("%Y%m%d")


def next_month_start(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def historical_nifty_contract_month(value: date) -> str:
    """Return the monthly NIFTY future under the production calendar rule."""
    selected_month = next_month_start(value) if value >= india.last_tuesday_of_month(value) else value.replace(day=1)
    return selected_month.strftime("%Y%m")


def nifty_roll_date_between(reference_at: datetime, valuation_day: date) -> date:
    """Return the single monthly roll crossed by a reference/current pair."""
    if reference_at.tzinfo is None:
        raise SourceUnavailableError("NIFTY bridge reference_at must be timezone-aware")
    reference_day = reference_at.astimezone(SHANGHAI).date()
    if reference_day > valuation_day:
        raise SourceUnavailableError(
            f"NIFTY reference Shanghai day {reference_day} follows valuation day {valuation_day}"
        )
    candidates: list[date] = []
    candidate = reference_day + timedelta(days=1)
    while candidate <= valuation_day:
        if candidate == india.last_tuesday_of_month(candidate):
            candidates.append(candidate)
        candidate += timedelta(days=1)
    if len(candidates) != 1:
        raise SourceUnavailableError(
            "different NIFTY reference/current contracts must cross exactly one monthly last-Tuesday roll "
            f"between {reference_day} and {valuation_day}; found {len(candidates)}"
        )
    return candidates[0]


def historical_nifty_roll_adjustment_from_samples(
    roll_day: date,
    old_samples: list[india.MarketQuote],
    new_samples: list[india.MarketQuote],
    current_contract: str,
    reference_contract: str,
) -> HistoricalNiftyRollAdjustment:
    """Build the production conservative factors from synchronized TWS bars."""
    if roll_day != india.last_tuesday_of_month(roll_day):
        raise SourceUnavailableError(f"NIFTY roll date {roll_day} is not the monthly last Tuesday")
    expected_capture_day = roll_day - timedelta(days=1)

    def validate(samples: list[india.MarketQuote], leg: str) -> None:
        if not samples:
            raise SourceUnavailableError(f"NIFTY roll basis has no {leg}-contract BID/ASK samples")
        for quote in samples:
            local = quote.observed_at.astimezone(SHANGHAI) if quote.observed_at.tzinfo is not None else None
            if (
                local is None
                or local.date() != expected_capture_day
                or local.second != 0
                or local.microsecond != 0
                or not india.nifty_roll_capture_window(quote.observed_at)
            ):
                raise SourceUnavailableError(
                    "NIFTY roll basis contains a quote outside the exact Monday 12:28-12:32 BJT window"
                )
            if (
                quote.market_data_type != HISTORICAL_MARKET_DATA_TYPE
                or quote.source != HISTORICAL_NIFTY_ROLL_SOURCE
            ):
                raise SourceUnavailableError(
                    "NIFTY historical roll basis requires audited TWS HistoricalBidAsk one-minute quotes"
                )
            if quote.symbol != india.NIFTY_SYMBOL or quote.last is not None:
                raise SourceUnavailableError(
                    "NIFTY historical roll basis must contain BID/ASK only; last or midpoint is not accepted"
                )

    validate(old_samples, "old")
    validate(new_samples, "new")
    old_contracts = {quote.contract for quote in old_samples}
    new_contracts = {quote.contract for quote in new_samples}
    if len(old_contracts) != 1 or len(new_contracts) != 1:
        raise SourceUnavailableError("NIFTY roll basis contains inconsistent contracts within a leg")
    old_contract = next(iter(old_contracts))
    new_contract = next(iter(new_contracts))
    if old_contract == new_contract:
        raise SourceUnavailableError("NIFTY roll basis requires distinct old and new contracts")

    old_by_time = {quote.observed_at: quote for quote in old_samples}
    new_by_time = {quote.observed_at: quote for quote in new_samples}
    common_times = sorted(set(old_by_time).intersection(new_by_time))
    if not common_times:
        raise SourceUnavailableError(
            "NIFTY roll basis has no exact common old/new BID/ASK minute in Monday 12:28-12:32 BJT"
        )
    captured_at = common_times[len(common_times) // 2]
    old = india.median_quote([old_by_time[item] for item in common_times], india.NIFTY_SYMBOL, captured_at)
    new = india.median_quote([new_by_time[item] for item in common_times], india.NIFTY_SYMBOL, captured_at)
    if old is None or new is None:
        raise SourceUnavailableError("NIFTY roll basis cannot form synchronized old/new median quotes")

    if reference_contract == old_contract and current_contract == new_contract:
        bid_factor, ask_factor = old.bid / new.ask, old.ask / new.bid
        direction = "new_to_old"
    elif reference_contract == new_contract and current_contract == old_contract:
        bid_factor, ask_factor = new.bid / old.ask, new.ask / old.bid
        direction = "old_to_new"
    else:
        raise SourceUnavailableError(
            "NIFTY roll basis contracts do not match the current/reference contract pair: "
            f"basis={old_contract}/{new_contract}, pair={current_contract}/{reference_contract}"
        )
    if positive(bid_factor) is None or positive(ask_factor) is None:
        raise SourceUnavailableError("NIFTY roll basis produced a non-positive adjustment factor")
    return HistoricalNiftyRollAdjustment(
        roll_day,
        captured_at,
        old_contract,
        new_contract,
        direction,
        bid_factor,
        ask_factor,
    )


def apply_historical_nifty_roll_adjustment(
    quotes: dict[str, india.MarketQuote],
    adjustment: HistoricalNiftyRollAdjustment,
) -> dict[str, india.MarketQuote]:
    """Apply conservative BID/ASK factors without inventing last/mid prices."""
    expected_contract = adjustment.new_contract if adjustment.direction == "new_to_old" else adjustment.old_contract
    adjusted: dict[str, india.MarketQuote] = {}
    for minute, quote in quotes.items():
        if quote.contract != expected_contract:
            raise SourceUnavailableError(
                f"NIFTY China-session contract {quote.contract} does not match roll basis {expected_contract}"
            )
        if (
            quote.market_data_type != HISTORICAL_MARKET_DATA_TYPE
            or quote.source != HISTORICAL_NIFTY_SOURCE
            or quote.last is not None
        ):
            raise SourceUnavailableError(
                "NIFTY China-session roll adjustment requires audited BID/ASK-only historical quotes"
            )
        bid = quote.bid * adjustment.bid_factor
        ask = quote.ask * adjustment.ask_factor
        if positive(bid) is None or positive(ask) is None or ask < bid:
            raise SourceUnavailableError(f"NIFTY roll adjustment produced an invalid {minute} BID/ASK quote")
        adjusted[minute] = india.MarketQuote(
            quote.symbol,
            quote.contract,
            bid,
            ask,
            None,
            quote.observed_at,
            quote.market_data_type,
            quote.source,
        )
    return adjusted


def previous_weekday(value: datetime) -> datetime:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def shanghai_timestamp(day: date, minute: str) -> datetime:
    return datetime.combine(day, clock_time.fromisoformat(minute), SHANGHAI)


def china_session_minutes(day: date) -> list[str]:
    values: list[str] = []
    for start, end in ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60)):
        for total in range(start, end + 1):
            values.append(f"{total // 60:02d}:{total % 60:02d}")
    return values


def source_json(server: str, path: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(server.rstrip("/") + path, headers=common.SOURCE_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise SourceUnavailableError(f"{path} request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise SourceUnavailableError(f"{path} returned {type(payload).__name__}, not an object")
    return payload


def public_history_days(server: str, timeout: float, start: date | None, end: date | None) -> list[date]:
    payload = source_json(server, f"/api/v1/funds/{SYMBOL}/minute-history/dates?limit=120", timeout)
    raw_dates = payload.get("dates")
    if not isinstance(raw_dates, list):
        raise SourceUnavailableError("SZ164824 public minute-history dates are missing")
    values: set[date] = set()
    for raw in raw_dates:
        try:
            candidate = parse_day(raw)
        except (TypeError, ValueError):
            continue
        if candidate.weekday() < 5:
            values.add(candidate)
    if not values:
        raise SourceUnavailableError("SZ164824 has no public China-session history to replay")
    lower, upper = start or min(values), end or max(values)
    return [value for value in sorted(values) if lower <= value <= upper]


def private_history_days(server: str, timeout: float) -> set[date]:
    payload = source_json(server, f"/api/v1/private/funds/{SYMBOL}/minute-history/dates", timeout)
    values = payload.get("dates")
    if not isinstance(values, list):
        return set()
    parsed: set[date] = set()
    for raw in values:
        try:
            parsed.add(parse_day(raw))
        except (TypeError, ValueError):
            continue
    return parsed


def public_prices(server: str, day: date, timeout: float) -> dict[str, float]:
    payload = source_json(server, f"/api/v1/funds/{SYMBOL}/minute-history?date={day_key(day)}", timeout)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise SourceUnavailableError(f"{day_key(day)} public minute rows are missing")
    allowed = set(china_session_minutes(day))
    values: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        minute = str(row.get("min") or "").strip()
        price = positive(row.get("mkp"))
        if minute in allowed and price is not None:
            values[minute] = price
    if not values:
        raise SourceUnavailableError(f"{day_key(day)} contains no usable China-session public prices")
    return values


def minute_quality(prices: dict[str, float], day: date) -> tuple[float, int]:
    expected = china_session_minutes(day)
    present = [minute in prices for minute in expected]
    coverage = sum(present) / len(expected)
    maximum = current = 0
    for exists in present:
        if exists:
            maximum = max(maximum, current)
            current = 0
        else:
            current += 1
    return coverage, max(maximum, current)


def official_nav_series(timeout: float, start: date, end: date) -> list[india.OfficialNAV]:
    # Eastmoney silently fixes every page at 20 rows. Page through the dated
    # range rather than assuming pageSize was honoured.  Do not cap this at a
    # fixed number of pages: a one-year redemption replay additionally needs
    # the preceding T-2 anchor dates, and a fixed 11-page cap silently dropped
    # the oldest part of a year-long request.
    values: dict[date, india.OfficialNAV] = {}
    required_start = start - timedelta(days=45)
    for page_index in range(1, 200):
        query = {
            "fundCode": "164824",
            "pageIndex": str(page_index),
            "pageSize": "40",
            "startDate": (start - timedelta(days=45)).isoformat(),
            "endDate": end.isoformat(),
        }
        request = urllib.request.Request(
            india.EASTMONEY_NAV_URL + "?" + urllib.parse.urlencode(query),
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
            raise SourceUnavailableError(f"Eastmoney 164824 NAV history unavailable: {exc}") from exc
        rows = ((payload.get("Data") or {}).get("LSJZList") or []) if isinstance(payload, dict) else []
        if not isinstance(rows, list) or not rows:
            break
        previous_count = len(values)
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                nav_day = date.fromisoformat(str(row.get("FSRQ") or ""))
            except ValueError:
                continue
            value = positive(row.get("DWJZ"))
            if value is not None:
                values[nav_day] = india.OfficialNAV(value, nav_day)
        # The endpoint can report an all-history TotalCount even when a date
        # range was requested, so that count is not a safe stopping condition.
        # Stop only once the required T-2 lookback is covered, or when a page
        # adds no data (guarding against a server that repeats page one).
        if values and min(values) <= required_start:
            break
        if len(values) == previous_count:
            break
    if not values:
        raise SourceUnavailableError("Eastmoney returned no usable 164824 NAV history")
    return [values[key] for key in sorted(values)]


def t_minus_two_nav(day: date, series: list[india.OfficialNAV]) -> india.OfficialNAV:
    previous = [item for item in series if item.trading_day < day]
    if len(previous) < 2:
        raise SourceUnavailableError(f"{day_key(day)} lacks two prior official NAV trading days")
    # The model's datum is explicitly T-2, not "the last value that happens
    # to be visible when the backfill is run".  Fund NAV dates form the
    # relevant dealing-day calendar, including holiday gaps.
    return previous[-2]


def quote_payload(value: india.MarketQuote, source: str) -> dict[str, Any]:
    return {
        "symbol": india.REFERENCE_SYMBOL,
        "contract": value.contract,
        "bid": value.bid,
        "ask": value.ask,
        "last": value.last,
        "market_data_type": HISTORICAL_MARKET_DATA_TYPE,
        "source": source,
        "observed_at": common.iso_timestamp(value.observed_at),
    }


class HistoricalINDAMarket(india.INDAMarket):
    def historical_nifty_contract_for_day(self, day: date) -> Any:
        """Qualify the effective NIFTY monthly future, including expired months."""
        self.connect()
        month = historical_nifty_contract_month(day)
        cache = getattr(self, "historical_nifty_contracts", None)
        if not isinstance(cache, dict):
            cache = {}
            self.historical_nifty_contracts = cache
        if month in cache:
            return cache[month]
        from ib_insync import Future
        contracts = self.ib.qualifyContracts(Future(
            india.NIFTY_SYMBOL,
            month,
            india.NIFTY_EXCHANGE,
            currency="USD",
            includeExpired=True,
        ))
        if not contracts:
            raise SourceUnavailableError(f"TWS cannot qualify historical SGX NIFTY contract {month}")
        cache[month] = contracts[0]
        return contracts[0]

    def nifty_quotes_for_china_session(self, day: date) -> dict[str, india.MarketQuote]:
        """Read exact NIFTY BID/ASK minutes for one China session."""
        self.connect()
        contract = self.historical_nifty_contract_for_day(day)
        end_at = datetime.combine(day, clock_time(15, 5), SHANGHAI)
        allowed = set(china_session_minutes(day))
        series: dict[str, dict[str, tuple[datetime, float]]] = {}
        for what in ("BID", "ASK"):
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime=end_at,
                durationStr="1 D",
                barSizeSetting="1 min",
                whatToShow=what,
                useRTH=False,
                formatDate=2,
                keepUpToDate=False,
                timeout=self.timeout,
            )
            values: dict[str, tuple[datetime, float]] = {}
            for bar in bars:
                observed_at = getattr(bar, "date", None)
                if not isinstance(observed_at, datetime):
                    continue
                if observed_at.tzinfo is None:
                    observed_at = observed_at.replace(tzinfo=timezone.utc)
                local = observed_at.astimezone(SHANGHAI).replace(second=0, microsecond=0)
                minute = local.strftime("%H:%M")
                value = positive(getattr(bar, "close", None))
                if local.date() == day and minute in allowed and value is not None:
                    values[minute] = (local, value)
            series[what] = values
        contract_name = str(getattr(contract, "localSymbol", "") or getattr(contract, "symbol", india.NIFTY_SYMBOL))
        quotes: dict[str, india.MarketQuote] = {}
        for minute in sorted(set(series["BID"]).intersection(series["ASK"])):
            bid_time, bid = series["BID"][minute]
            ask_time, ask = series["ASK"][minute]
            if ask < bid:
                continue
            quotes[minute] = india.MarketQuote(
                india.NIFTY_SYMBOL,
                contract_name,
                bid,
                ask,
                None,
                max(bid_time, ask_time),
                HISTORICAL_MARKET_DATA_TYPE,
                HISTORICAL_NIFTY_SOURCE,
            )
        return quotes

    def nifty_bridge_reference_for_china_day(self, day: date) -> tuple[datetime, india.MarketQuote, india.MarketQuote]:
        """Rebuild the latest available preceding 15:49–15:51 ET bridge."""
        target = india.latest_completed_bridge_target(datetime.combine(day, clock_time(9, 30), SHANGHAI))
        for _ in range(5):
            # Contract selection follows the actual reference instant.  In
            # particular, a U.S. Monday holiday can move a last-Tuesday China
            # valuation back to the prior Friday and therefore the old future.
            reference_day = target.astimezone(SHANGHAI).date()
            contract = self.historical_nifty_contract_for_day(reference_day)
            window_start = target.replace(hour=india.BRIDGE_CAPTURE_START_MINUTE // 60, minute=india.BRIDGE_CAPTURE_START_MINUTE % 60)
            window_end = target.replace(hour=india.BRIDGE_CAPTURE_END_MINUTE // 60, minute=india.BRIDGE_CAPTURE_END_MINUTE % 60)
            inda = self.historical_bid_ask_window(self.contract("SMART"), india.REFERENCE_SYMBOL, window_start, window_end)
            nifty = self.historical_bid_ask_window(contract, india.NIFTY_SYMBOL, window_start, window_end)
            common_times = sorted({item.observed_at for item in inda}.intersection(item.observed_at for item in nifty))
            if common_times:
                by_inda = {item.observed_at: item for item in inda}
                by_nifty = {item.observed_at: item for item in nifty}
                inda_reference = india.median_quote([by_inda[item] for item in common_times], india.REFERENCE_SYMBOL, target)
                nifty_reference = india.median_quote([by_nifty[item] for item in common_times], india.NIFTY_SYMBOL, target)
                if inda_reference is not None and nifty_reference is not None:
                    return target, inda_reference, nifty_reference
            # A weekday can still be a U.S. market holiday.  Walk to the
            # preceding available close and reselect its Shanghai-date future.
            target = previous_weekday(target)
        raise SourceUnavailableError(f"{day_key(day)} has no common INDA/NIFTY bridge quote in the preceding five weekday close windows")

    def align_nifty_quotes_to_bridge_reference(
        self,
        day: date,
        quotes: dict[str, india.MarketQuote],
        bridge_reference: tuple[datetime, india.MarketQuote, india.MarketQuote],
    ) -> tuple[dict[str, india.MarketQuote], HistoricalNiftyRollAdjustment | None]:
        """Put China-session quotes on the reference contract's price scale."""
        if not quotes:
            raise SourceUnavailableError(f"{day_key(day)} has no NIFTY China-session BID/ASK quotes")
        current_contracts = {quote.contract for quote in quotes.values()}
        if len(current_contracts) != 1:
            raise SourceUnavailableError(f"{day_key(day)} NIFTY China-session quotes mix multiple contracts")
        current_contract = next(iter(current_contracts))
        reference_at, _, reference = bridge_reference
        if (
            reference.market_data_type != HISTORICAL_MARKET_DATA_TYPE
            or reference.source != HISTORICAL_NIFTY_ROLL_SOURCE
            or reference.last is not None
        ):
            raise SourceUnavailableError(
                f"{day_key(day)} NIFTY reference is not an audited BID/ASK-only historical quote"
            )
        if current_contract == reference.contract:
            return quotes, None

        roll_day = nifty_roll_date_between(reference_at, day)
        old_contract = self.historical_nifty_contract_for_day(roll_day - timedelta(days=1))
        new_contract = self.historical_nifty_contract_for_day(roll_day)
        capture_day = roll_day - timedelta(days=1)
        window_start = datetime.combine(
            capture_day,
            clock_time(india.NIFTY_ROLL_CAPTURE_START_MINUTE // 60, india.NIFTY_ROLL_CAPTURE_START_MINUTE % 60),
            SHANGHAI,
        )
        window_end = datetime.combine(
            capture_day,
            clock_time(india.NIFTY_ROLL_CAPTURE_END_MINUTE // 60, india.NIFTY_ROLL_CAPTURE_END_MINUTE % 60),
            SHANGHAI,
        )
        old_samples = self.historical_bid_ask_window(
            old_contract,
            india.NIFTY_SYMBOL,
            window_start,
            window_end,
        )
        new_samples = self.historical_bid_ask_window(
            new_contract,
            india.NIFTY_SYMBOL,
            window_start,
            window_end,
        )
        adjustment = historical_nifty_roll_adjustment_from_samples(
            roll_day,
            old_samples,
            new_samples,
            current_contract,
            reference.contract,
        )
        return apply_historical_nifty_roll_adjustment(quotes, adjustment), adjustment

    def _series_before(self, contract: Any, cutoff: datetime, what: str, bar_size: str, duration: str = "4 D") -> dict[datetime, float]:
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime=cutoff + timedelta(minutes=1),
            durationStr=duration,
            barSizeSetting=bar_size,
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
                observed_at = observed_at.replace(tzinfo=india.NEW_YORK)
            observed_at = observed_at.astimezone(timezone.utc).replace(second=0, microsecond=0)
            value = positive(getattr(bar, "close", None))
            if value is not None and observed_at <= cutoff.astimezone(timezone.utc):
                values[observed_at] = value
        return values

    def close_quote_before_china_session(self, day: date) -> tuple[india.MarketQuote, str]:
        self.connect()
        contract = self.contract("SMART")
        cutoff = datetime.combine(day, clock_time(9, 30), SHANGHAI)
        for bar_size, source in (("1 min", HISTORICAL_IB_SOURCE), ("5 mins", HISTORICAL_IB_SOURCE + "_5M_RETENTION_FALLBACK")):
            bids = self._series_before(contract, cutoff, "BID", bar_size)
            asks = self._series_before(contract, cutoff, "ASK", bar_size)
            common_minutes = sorted(set(bids).intersection(asks))
            if not common_minutes:
                continue
            observed_at = common_minutes[-1]
            bid, ask = bids[observed_at], asks[observed_at]
            if ask < bid:
                continue
            return india.MarketQuote(
                india.REFERENCE_SYMBOL,
                str(getattr(contract, "localSymbol", "") or getattr(contract, "symbol", india.REFERENCE_SYMBOL)),
                bid,
                ask,
                None,
                observed_at,
                HISTORICAL_MARKET_DATA_TYPE,
            ), source
        raise SourceUnavailableError(f"{day_key(day)} has no common INDA historical BID/ASK bar before China open")

    def close_quotes_before_china_sessions(self, days: list[date]) -> dict[date, tuple[india.MarketQuote, str]]:
        """Fetch multiple China-session close quotes from batched 1m IBKR bars.

        The historical API request is immutable/read-only.  A four-calendar-day
        window is an IBKR-supported range for each group of two consecutive
        Chinese business days; each day still selects its own last common
        BID/ASK minute, so batching does not interpolate or coarsen prices.
        """
        self.connect()
        contract = self.contract("SMART")
        result: dict[date, tuple[india.MarketQuote, str]] = {}
        for offset in range(0, len(days), 2):
            group = days[offset:offset + 2]
            if not group:
                continue
            cutoff = datetime.combine(max(group), clock_time(9, 30), SHANGHAI)
            bids = self._series_before(contract, cutoff, "BID", "1 min", "4 D")
            asks = self._series_before(contract, cutoff, "ASK", "1 min", "4 D")
            common_minutes = sorted(set(bids).intersection(asks))
            for day in group:
                day_cutoff = datetime.combine(day, clock_time(9, 30), SHANGHAI).astimezone(timezone.utc)
                eligible = [observed_at for observed_at in common_minutes if observed_at <= day_cutoff]
                if not eligible:
                    continue
                observed_at = eligible[-1]
                bid, ask = bids[observed_at], asks[observed_at]
                if ask < bid:
                    continue
                result[day] = (
                    india.MarketQuote(
                        india.REFERENCE_SYMBOL,
                        str(getattr(contract, "localSymbol", "") or getattr(contract, "symbol", india.REFERENCE_SYMBOL)),
                        bid,
                        ask,
                        None,
                        observed_at,
                        HISTORICAL_MARKET_DATA_TYPE,
                    ),
                    HISTORICAL_IB_SOURCE + "_BATCH_1M",
                )
        return result

    def five_minute_anchor(self, base_day: date, spec: india.AnchorSpec) -> india.AnchorObservation:
        target = india.target_at(base_day, spec)
        contract = self.contract("SMART")
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime=target + timedelta(minutes=5),
            durationStr="1 D",
            barSizeSetting="5 mins",
            whatToShow="BID_ASK",
            useRTH=False,
            formatDate=2,
            keepUpToDate=False,
            timeout=self.timeout,
        )
        prior: list[tuple[datetime, float]] = []
        following: list[tuple[datetime, float]] = []
        for bar in bars:
            observed_at = getattr(bar, "date", None)
            if not isinstance(observed_at, datetime):
                continue
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=india.NEW_YORK)
            value = positive(getattr(bar, "close", None))
            if value is None:
                continue
            if observed_at <= target:
                prior.append((observed_at, value))
            else:
                following.append((observed_at, value))
        if prior:
            observed_at, value = max(prior, key=lambda item: item[0])
            status = "historical_5m_exact" if observed_at == target else "historical_5m_prior_tradable_carry"
        elif following:
            observed_at, value = min(following, key=lambda item: item[0])
            status = "historical_5m_next_tradable_fallback"
        else:
            raise SourceUnavailableError(f"TWS returned no INDA five-minute BID_ASK bar for {spec.key} {base_day}")
        return india.AnchorObservation(
            spec.key,
            spec.label,
            spec.weight,
            value,
            target.isoformat(),
            observed_at.isoformat(),
            "IBKR_TWS_INDA_SMART_BID_ASK_5M_RETENTION_FALLBACK",
            status,
        )


def historical_anchors(market: HistoricalINDAMarket, runtime: Path, base_day: date) -> tuple[india.AnchorObservation, ...]:
    cached = india.load_cached_anchors(india.cache_path(runtime, base_day))
    if cached is not None:
        return tuple(cached)
    values: list[india.AnchorObservation] = []
    for spec in india.ANCHORS:
        candidates = [spec]
        if spec.preferred_exchange != "SMART":
            candidates.append(replace(spec, preferred_exchange="SMART"))
        observed: india.AnchorObservation | None = None
        for candidate in candidates:
            try:
                observed = market.anchor_for(base_day, candidate)
                break
            except Exception:
                continue
        if observed is None:
            # IBKR's OVERNIGHT routing can decline an expired historical
            # request even where the identical INDA BATS/SMART data is
            # available.  This is an explicit contract fallback, not a price
            # fill: a coarser, historical bar is used only after both 1m
            # contract routes fail, and its source/status say so explicitly.
            observed = market.five_minute_anchor(base_day, spec)
        elif observed.source.endswith("_SMART_BID_ASK_1M") and spec.preferred_exchange != "SMART":
            observed = replace(
                observed,
                source=observed.source + "_FALLBACK_FROM_" + spec.preferred_exchange,
                capture_status="smart_contract_fallback_" + observed.capture_status,
            )
        values.append(observed)
    payload = json.dumps({
        "base_nav_date": base_day.isoformat(),
        "anchors": [value.to_payload() for value in values],
    }, ensure_ascii=False, allow_nan=False).encode("utf-8")
    common._atomic_write_bytes(india.cache_path(runtime, base_day), payload)
    return tuple(values)


def historical_input(prepared: PreparedDay, minute: str) -> dict[str, Any]:
    timestamp = shanghai_timestamp(prepared.day, minute)
    india_payload: dict[str, Any] = {
        "base_nav": prepared.nav.value,
        "base_nav_date": prepared.nav.trading_day.isoformat(),
        "base_fx": india.safe_payload(prepared.base_fx),
        "current_fx": india.safe_payload(prepared.current_fx),
        "investment_ratio": india.INVESTMENT_RATIO,
        "static_ratio": india.STATIC_RATIO,
        "anchors": [asdict(anchor) for anchor in prepared.anchors],
        "portfolio_as_of": india.PORTFOLIO_AS_OF,
        "portfolio_source": india.PORTFOLIO_SOURCE,
    }
    nifty = prepared.nifty_quotes.get(minute) if prepared.nifty_quotes else None
    if nifty is not None and prepared.bridge_reference is not None:
        reference_at, inda_reference, nifty_reference = prepared.bridge_reference
        bridge_payload: dict[str, Any] = {
            "nifty": nifty.to_payload(),
            "inda_reference": inda_reference.to_payload(),
            "nifty_reference": nifty_reference.to_payload(),
            "reference_at": common.iso_timestamp(reference_at),
            "beta": 1.0,
            "contract_selection_version": india.NIFTY_CONTRACT_SELECTION_VERSION,
        }
        if prepared.nifty_roll_adjustment is not None:
            bridge_payload["roll_adjustment"] = prepared.nifty_roll_adjustment.to_payload()
        india_payload["nifty_bridge"] = bridge_payload
    return {
        "schema_version": 1,
        "symbol": SYMBOL,
        "model_version": MODEL_VERSION,
        "india": india_payload,
        "ib": quote_payload(prepared.close_quote, prepared.close_source),
        "source": SOURCE,
        "generated_at": common.iso_timestamp(timestamp),
    }


def historical_rows(prepared: PreparedDay) -> list[dict[str, Any]]:
    return [
        {
            "minute": common.iso_timestamp(shanghai_timestamp(prepared.day, minute)),
            "market_price": prepared.market_prices[minute],
            "input": historical_input(prepared, minute),
        }
        for minute in sorted(prepared.market_prices)
    ]


def upload_day(args: argparse.Namespace, rows: list[dict[str, Any]]) -> int:
    raw_payload = json.dumps(
        {"rows": rows, "replace_day": bool(args.replace_existing)},
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(raw_payload) > MAX_DECOMPRESSED_BYTES:
        raise SourceUnavailableError(f"{day_key(parse_day(rows[0]['minute'][:10]))} payload exceeds {MAX_DECOMPRESSED_BYTES} bytes")
    payload = gzip.compress(raw_payload, compresslevel=6, mtime=0)
    if len(payload) > MAX_COMPRESSED_BYTES:
        raise SourceUnavailableError(f"compressed historical payload exceeds {MAX_COMPRESSED_BYTES} bytes")
    request = urllib.request.Request(
        args.server.rstrip("/") + HISTORY_PATH.format(symbol=SYMBOL),
        data=payload,
        method="POST",
        headers=common.gzip_server_headers(args.server, args.token),
    )
    try:
        with common.open_server_request(request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SourceUnavailableError(f"historical import HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise SourceUnavailableError(f"historical import was not acknowledged: {result}")
    return int(result.get("imported") or 0)


def write_manifest(runtime: Path, records: list[dict[str, Any]]) -> Path:
    path = runtime / "manifests" / f"run-{datetime.now(SHANGHAI):%Y%m%dT%H%M%S}.json"
    common._atomic_write_bytes(path, json.dumps({
        "schema_version": 1,
        "source": SOURCE,
        "generated_at": common.iso_timestamp(datetime.now(SHANGHAI)),
        "records": records,
    }, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    result.add_argument("--start", type=parse_day)
    result.add_argument("--end", type=parse_day)
    result.add_argument("--max-days", type=int, default=0)
    result.add_argument("--replace-existing", action="store_true")
    result.add_argument("--include-nifty-bridge", action="store_true", help="rebuild auditable NIFTY bridge minutes alongside final INDA history")
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR))
    result.add_argument("--ib-host", default="127.0.0.1")
    result.add_argument("--ib-port", type=int, default=7496)
    result.add_argument("--ib-client-id", type=int, default=264824)
    # Old IBKR minute history is often retrieved from archive storage.  This
    # is intentionally longer than the live uploader's timeout; a timeout is
    # a missing audit source, not permission to synthesize a two-sided quote.
    result.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", "45")))
    result.add_argument("--ib-request-delay", type=float, default=0.4)
    result.add_argument("--min-coverage", type=float, default=0.98)
    result.add_argument("--max-gap", type=int, default=3)
    return result


def run(args: argparse.Namespace) -> int:
    if args.end is not None and args.start is not None and args.end < args.start:
        raise SourceUnavailableError("--end must not precede --start")
    if args.max_days < 0 or args.ib_request_delay < 0 or not 0 < args.min_coverage <= 1 or args.max_gap < 0:
        raise SourceUnavailableError("invalid coverage, gap, pacing, or max-days setting")
    if not args.dry_run and not str(args.token).strip():
        raise SourceUnavailableError("NNN_UPLOAD_TOKEN or --token is required unless --dry-run is used")
    runtime = Path(args.runtime_dir).expanduser().resolve()
    public_days = public_history_days(args.server, args.timeout, args.start, args.end)
    existing = private_history_days(args.server, args.timeout)
    targets = [day for day in public_days if args.replace_existing or day not in existing]
    if args.max_days:
        targets = targets[:args.max_days]
    print(f"{SYMBOL} plan public={len(public_days)} existing_private={len(existing)} target={len(targets)}", flush=True)
    if not targets:
        return 0
    navs = official_nav_series(args.timeout, min(targets), max(targets))
    fx_cache: dict[date, Any] = {}
    anchor_cache: dict[date, tuple[india.AnchorObservation, ...]] = {}
    records: list[dict[str, Any]] = []
    market = HistoricalINDAMarket(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
    imported_total = 0
    try:
        try:
            close_quote_cache = market.close_quotes_before_china_sessions(targets)
        except Exception as exc:
            # Preserve correctness if a long batched request is unavailable:
            # per-day retrieval below retains the prior strict bid/ask rule.
            common.log(f"164824 batch close-quote query unavailable; using per-day fallback: {exc}", error=True)
            close_quote_cache = {}
        for day in targets:
            record: dict[str, Any] = {"symbol": SYMBOL, "day": day.isoformat()}
            try:
                prices = public_prices(args.server, day, args.timeout)
                coverage, max_gap = minute_quality(prices, day)
                if coverage < args.min_coverage or max_gap > args.max_gap:
                    raise SourceUnavailableError(f"public minute coverage={coverage:.1%}, max_gap={max_gap}")
                nav = t_minus_two_nav(day, navs)
                if nav.trading_day not in fx_cache:
                    fx_cache[nav.trading_day] = index_common.fetch_safe_central_parity(nav.trading_day, args.timeout, index_common.NASDAQ_FAMILY)
                if day not in fx_cache:
                    fx_cache[day] = index_common.fetch_safe_central_parity(day, args.timeout, index_common.NASDAQ_FAMILY)
                if nav.trading_day not in anchor_cache:
                    anchor_cache[nav.trading_day] = historical_anchors(market, runtime, nav.trading_day)
                close_quote, close_source = close_quote_cache.get(day) or market.close_quote_before_china_session(day)
                nifty_quotes: dict[str, india.MarketQuote] | None = None
                bridge_reference: tuple[datetime, india.MarketQuote, india.MarketQuote] | None = None
                nifty_roll_adjustment: HistoricalNiftyRollAdjustment | None = None
                nifty_coverage: float | None = None
                nifty_max_gap: int | None = None
                if args.include_nifty_bridge:
                    try:
                        # The current leg follows the valuation day; the
                        # reference method independently selects from the
                        # actual reference_at Shanghai date.
                        nifty_quotes = market.nifty_quotes_for_china_session(day)
                        nifty_coverage, nifty_max_gap = minute_quality({minute: quote.bid for minute, quote in nifty_quotes.items()}, day)
                        if nifty_coverage < args.min_coverage or nifty_max_gap > args.max_gap:
                            raise SourceUnavailableError(f"NIFTY minute coverage={nifty_coverage:.1%}, max_gap={nifty_max_gap}")
                        bridge_reference = market.nifty_bridge_reference_for_china_day(day)
                        nifty_quotes, nifty_roll_adjustment = market.align_nifty_quotes_to_bridge_reference(
                            day,
                            nifty_quotes,
                            bridge_reference,
                        )
                    except Exception as exc:
                        # A different-contract ratio without the exact audited
                        # Monday basis would manufacture a roll jump.  Exclude
                        # the entire day's bridge and retain the reason in the
                        # run manifest instead of falling back to last/mid.
                        record["nifty_bridge_exclusion_reason"] = str(exc)
                        raise
                prepared = PreparedDay(
                    day=day,
                    nav=nav,
                    base_fx=fx_cache[nav.trading_day],
                    current_fx=fx_cache[day],
                    anchors=anchor_cache[nav.trading_day],
                    close_quote=close_quote,
                    close_source=close_source,
                    market_prices=prices,
                    coverage=coverage,
                    max_gap=max_gap,
                    nifty_quotes=nifty_quotes,
                    bridge_reference=bridge_reference,
                    nifty_roll_adjustment=nifty_roll_adjustment,
                )
                rows = historical_rows(prepared)
                imported = 0 if args.dry_run else upload_day(args, rows)
                imported_total += imported
                record.update({
                    "status": "dry_run_ready" if args.dry_run else "uploaded",
                    "rows": len(rows),
                    "imported": imported,
                    "coverage": coverage,
                    "max_gap": max_gap,
                    "base_nav": nav.value,
                    "base_nav_date": nav.trading_day.isoformat(),
                    "base_fx": fx_cache[nav.trading_day].rate,
                    "current_fx": fx_cache[day].rate,
                    "inda_bid": close_quote.bid,
                    "inda_ask": close_quote.ask,
                    "inda_observed_at": common.iso_timestamp(close_quote.observed_at),
                    "inda_source": close_source,
                    "anchor_prices": {anchor.key: anchor.price for anchor in anchor_cache[nav.trading_day]},
                })
                if bridge_reference is not None:
                    record.update({
                        "nifty_coverage": nifty_coverage,
                        "nifty_max_gap": nifty_max_gap,
                        "nifty_contract": next(iter(nifty_quotes.values())).contract if nifty_quotes else "",
                        "bridge_reference_at": common.iso_timestamp(bridge_reference[0]),
                        "nifty_reference_contract": bridge_reference[2].contract,
                    })
                    if nifty_roll_adjustment is not None:
                        record["nifty_roll_adjustment"] = nifty_roll_adjustment.to_payload()
                print(
                    f"{SYMBOL} {day_key(day)} rows={len(rows)} coverage={coverage:.1%} "
                    f"T-2={nav.trading_day} INDA={close_quote.bid:.4f}/{close_quote.ask:.4f} "
                    f"NIFTY={'bridge' if bridge_reference is not None else 'off'} imported={imported}",
                    flush=True,
                )
            except Exception as exc:
                record.update({"status": "source_unavailable", "error": str(exc)})
                print(f"{SYMBOL} {day_key(day)} SKIP {exc}", file=sys.stderr, flush=True)
            records.append(record)
            if args.ib_request_delay:
                time.sleep(args.ib_request_delay)
    finally:
        market.close()
    manifest = write_manifest(runtime, records)
    ready = sum(record.get("status") in {"dry_run_ready", "uploaded"} for record in records)
    print(f"summary ready={ready} skipped={len(records) - ready} imported={imported_total} manifest={manifest}", flush=True)
    return 0 if ready == len(records) else 2


def main() -> int:
    common.load_env_file(".sina-uploader.env")
    args = parser().parse_args()
    try:
        return run(args)
    except (SourceUnavailableError, RuntimeError, urllib.error.URLError) as exc:
        common.log(str(exc), error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

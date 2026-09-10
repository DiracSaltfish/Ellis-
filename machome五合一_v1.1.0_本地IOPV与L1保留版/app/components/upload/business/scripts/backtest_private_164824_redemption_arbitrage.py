#!/usr/bin/env python3
"""Backtest hedged SZ164824 discount-to-redemption trades from local caches.

This is a research-only, local-output script.  It does not submit orders and
does not write valuation or trading data to 1navs.  A one-contract tranche is
modelled as follows:

1. Scan every available domestic one-minute bar.  Buy one tranche at the first
   executable minute where the LOF is below the NIFTY-bridge estimate of the
   T-day NAV.  The default uses the minute high, rather than its close, as a
   conservative proxy for a marketable buy.
2. Short one effective NIFTY monthly future at the historical bid; at the
   first 09:40 New York quote, buy it back at the ask and short the equivalent
   INDA notional at the INDA bid.
3. Submit redemption on the third subsequent official fund-NAV dealing day;
   hold the INDA short through that redemption day's 15:50 New York quote and
   cover at the ask.  The cash-availability date is recorded as six further
   official dealing days, but does not need a later market quote.

The signal explicitly tracks bridge estimation error.  Besides an unadjusted
signal, the error-aware rule reduces each provisional NAV by the trailing
90th percentile of *positive* historical estimation errors that were already
observable from the T-2 information set.  This avoids a look-ahead fitted
signal.

The redemption fee defaults to 0.464%, the actual rate supplied for this
strategy. Broker commissions, securities borrow and financing are inputs, not
asserted facts: change them to the actual account schedule before relying on
any net result.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import statistics
import sys
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_164824_history_backfill as history  # noqa: E402
import private_164824_valuation_uploader as india  # noqa: E402
import private_nasdaq_valuation_uploader as fx_common  # noqa: E402


DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".runtime" / "private_164824_redemption_arbitrage_cache"
DEFAULT_RUNTIME_DIR = Path(__file__).resolve().parent / ".runtime" / "private_164824_redemption_arbitrage_backtest"
DEFAULT_DOMESTIC_ROOT = Path("/Volumes/Stocksdata/基金_分钟数据/LOF_分钟数据/1分钟_按月归档")
SYMBOL_FILE = "164824.SZ.csv"
HANDOFF_MINUTE = "09:40"
CLOSE_MINUTE = "15:50"
DEFAULT_ENTRY_START = "09:30"
DEFAULT_ENTRY_LAST_MINUTE = "14:57"
ROLL_CACHE_VERSION = "private-164824-roll-basis.v1"


class BacktestInputError(RuntimeError):
    """A source row is insufficient for a non-interpolated backtest point."""


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    observed_at: str
    contract: str = ""

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class SignalObservation:
    day: date
    entry_minute: str
    base_nav_day: date
    base_nav: float
    actual_nav: float
    base_fx: float
    target_fx: float
    estimated_nav: float
    guarded_nav: float | None
    prediction_error: float
    fund_price: float
    nifty_entry: Quote
    nifty_estimate: Quote
    nifty_bridge: Quote
    inda_bridge: Quote
    base_inda_close: Quote
    roll_adjustment: "NiftyRollAdjustment | None" = None
    redemption_fx: float | None = None


@dataclass(frozen=True)
class NiftyRollBasis:
    """Audited old/new NIFTY quote pair captured before a monthly roll."""

    roll_day: date
    captured_at: str
    old: Quote
    new: Quote
    old_bridge: Quote


@dataclass(frozen=True)
class NiftyRollAdjustment:
    roll_day: date
    captured_at: str
    old_contract: str
    new_contract: str
    direction: str
    bid_factor: float
    ask_factor: float


def parse_day(value: str) -> date:
    return datetime.strptime(str(value).strip().replace("-", ""), "%Y%m%d").date()


def day_key(value: date) -> str:
    return value.isoformat()


def positive(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BacktestInputError(f"{label} is not numeric") from exc
    if not math.isfinite(result) or result <= 0:
        raise BacktestInputError(f"{label} must be positive")
    return result


def percentile(values: Iterable[float], ratio: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * ratio
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BacktestInputError(f"cannot load {path}: {exc}") from exc


def load_tws_payload(cache_dir: Path, day: date) -> dict[str, Any]:
    path = cache_dir / "days" / f"{day_key(day)}.json.gz"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, EOFError, json.JSONDecodeError) as exc:
        raise BacktestInputError(f"cannot load TWS cache {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BacktestInputError(f"TWS cache {path} is not an object")
    return payload


def quote_from_row(row: Any, label: str) -> Quote:
    if not isinstance(row, dict):
        raise BacktestInputError(f"{label} quote is missing")
    bid, ask = positive(row.get("bid"), f"{label} bid"), positive(row.get("ask"), f"{label} ask")
    if ask < bid:
        raise BacktestInputError(f"{label} ask is below bid")
    observed_at = str(row.get("observed_at") or "")
    if not observed_at:
        raise BacktestInputError(f"{label} timestamp is missing")
    return Quote(bid, ask, observed_at, str(row.get("contract") or "").strip())


def quote_at(rows: Any, minute: str, label: str) -> Quote:
    if not isinstance(rows, list):
        raise BacktestInputError(f"{label} quote rows are missing")
    for row in rows:
        if isinstance(row, dict) and str(row.get("observed_at") or "")[11:16] == minute:
            return quote_from_row(row, label)
    raise BacktestInputError(f"{label} has no {minute} quote")


def median_quote(rows: Any, label: str) -> Quote:
    if not isinstance(rows, list) or not rows:
        raise BacktestInputError(f"{label} quotes are missing")
    # The cache preserves each actual minute.  Median BID and ASK avoids a
    # single outlier while retaining an executable two-sided quote.
    quotes = [quote_from_row(row, label) for row in rows]
    contracts = {item.contract for item in quotes if item.contract}
    if len(contracts) > 1:
        raise BacktestInputError(f"{label} mixes NIFTY contracts")
    return Quote(
        statistics.median(item.bid for item in quotes),
        statistics.median(item.ask for item in quotes),
        quotes[len(quotes) // 2].observed_at,
        next(iter(contracts), ""),
    )


def bridge_quote(payload: dict[str, Any], leg: str) -> Quote:
    bridge = payload.get("bridge")
    if not isinstance(bridge, dict):
        raise BacktestInputError("bridge cache is missing")
    return median_quote(bridge.get(leg), f"bridge {leg}")


def nifty_china_quote(payload: dict[str, Any], minute: str) -> Quote:
    section = payload.get("nifty_china_session")
    quotes = section.get("quotes") if isinstance(section, dict) else None
    if not isinstance(quotes, dict) or minute not in quotes:
        raise BacktestInputError(f"NIFTY China session has no {minute} quote")
    return quote_from_row(quotes[minute], f"NIFTY China {minute}")


def usa_quote(payload: dict[str, Any], leg: str, minute: str) -> Quote:
    session = payload.get("us_session")
    rows = session.get(leg) if isinstance(session, dict) else None
    return quote_at(rows, minute, f"US {leg}")


def domestic_zip_path(root: Path, day: date) -> Path:
    return root / f"{day:%Y-%m}" / f"{day:%Y%m%d}_1min.zip"


def domestic_bars(root: Path, day: date) -> dict[str, dict[str, float]]:
    path = domestic_zip_path(root, day)
    if not path.is_file():
        raise BacktestInputError(f"domestic minute zip is missing: {path}")
    bars: dict[str, dict[str, float]] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            with archive.open(SYMBOL_FILE) as handle:
                for raw in handle:
                    row = next(csv.reader([raw.decode("utf-8-sig").strip()]), [])
                    if len(row) < 7 or len(row[0]) < 5 or not row[0][-5:].replace(":", "").isdigit():
                        continue
                    minute = row[0][-5:]
                    bars[minute] = {
                        "close": positive(row[4], f"{day_key(day)} domestic {minute} close"),
                        "high": positive(row[5], f"{day_key(day)} domestic {minute} high"),
                    }
    except KeyError as exc:
        raise BacktestInputError(f"{path} has no {SYMBOL_FILE}") from exc
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise BacktestInputError(f"cannot read domestic minute source {path}: {exc}") from exc
    if not bars:
        raise BacktestInputError(f"{day_key(day)} domestic source has no minute bars")
    return bars


def domestic_price(root: Path, day: date, minute: str, price_field: str = "close") -> float:
    if price_field not in {"close", "high"}:
        raise BacktestInputError(f"unsupported domestic entry price field: {price_field}")
    bars = domestic_bars(root, day)
    try:
        return bars[minute][price_field]
    except KeyError as exc:
        raise BacktestInputError(f"{day_key(day)} domestic source has no {minute} {price_field}") from exc


def roll_cache_path(cache_dir: Path, roll_day: date) -> Path:
    return cache_dir / "rolls" / f"{day_key(roll_day)}.json"


def load_roll_basis(cache_dir: Path, roll_day: date) -> NiftyRollBasis:
    """Load the audited Monday basis and old-contract close for one roll.

    The regular cache deliberately selects the T-day effective contract for
    every replay request.  That is sufficient for ordinary days.  On the
    Tuesday roll, however, the live web model's bridge was captured while the
    old contract was still active.  This sidecar supplies the exact old/new
    Monday 12:28--12:32 BJT quote pair needed to reproduce that live mapping.
    """
    path = roll_cache_path(cache_dir, roll_day)
    decoded = load_json(path)
    if not isinstance(decoded, dict):
        raise BacktestInputError(f"NIFTY roll cache {path} is not an object")
    if decoded.get("cache_version") != ROLL_CACHE_VERSION:
        raise BacktestInputError(f"NIFTY roll cache {path} has an unsupported version")
    try:
        cached_day = parse_day(decoded.get("roll_day"))
    except (BacktestInputError, ValueError) as exc:
        raise BacktestInputError(f"NIFTY roll cache {path} has no valid roll day") from exc
    if cached_day != roll_day or roll_day != india.last_tuesday_of_month(roll_day):
        raise BacktestInputError(f"NIFTY roll cache {path} is not for the stated final Tuesday")
    window = str(decoded.get("window") or "")
    if window != "Monday 12:28-12:32 BJT before monthly last Tuesday":
        raise BacktestInputError(f"NIFTY roll cache {path} has an invalid capture window")
    old = median_quote(decoded.get("old"), f"NIFTY roll {roll_day} old")
    new = median_quote(decoded.get("new"), f"NIFTY roll {roll_day} new")
    old_bridge = median_quote(decoded.get("old_bridge"), f"NIFTY roll {roll_day} old bridge")
    if not old.contract or not new.contract or not old_bridge.contract:
        raise BacktestInputError(f"NIFTY roll cache {path} is missing contract identifiers")
    if old.contract == new.contract or old_bridge.contract != old.contract:
        raise BacktestInputError(f"NIFTY roll cache {path} has inconsistent old/new contracts")
    return NiftyRollBasis(roll_day, old.observed_at, old, new, old_bridge)


def align_nifty_to_reference(
    cache_dir: Path,
    current: Quote,
    reference: Quote,
    roll_basis: NiftyRollBasis | None = None,
) -> tuple[Quote, NiftyRollAdjustment | None]:
    """Put a current NIFTY quote on its bridge contract scale, fail closed.

    Bid and ask use the adverse side of the audited old/new spread.  This is
    intentionally stricter than using a midpoint basis: a missing audited
    pair excludes a roll-day observation rather than manufacturing a NAV jump.
    """
    if current.contract == reference.contract:
        return current, None
    if not current.contract or not reference.contract:
        raise BacktestInputError("NIFTY bridge/current contract is not identified")
    basis = roll_basis
    if basis is None:
        candidates: list[NiftyRollBasis] = []
        for path in sorted((cache_dir / "rolls").glob("*.json")) if (cache_dir / "rolls").is_dir() else []:
            try:
                candidates.append(load_roll_basis(cache_dir, parse_day(path.stem)))
            except BacktestInputError:
                continue
        for candidate in candidates:
            if {current.contract, reference.contract} == {candidate.old.contract, candidate.new.contract}:
                basis = candidate
                break
    if basis is None:
        raise BacktestInputError(
            f"NIFTY {current.contract}/{reference.contract} needs an audited Monday roll basis"
        )
    if current.contract == basis.new.contract and reference.contract == basis.old.contract:
        bid_factor, ask_factor, direction = basis.old.bid / basis.new.ask, basis.old.ask / basis.new.bid, "new_to_old"
    elif current.contract == basis.old.contract and reference.contract == basis.new.contract:
        bid_factor, ask_factor, direction = basis.new.bid / basis.old.ask, basis.new.ask / basis.old.bid, "old_to_new"
    else:
        raise BacktestInputError(
            f"NIFTY roll basis {basis.roll_day} does not cover {current.contract}/{reference.contract}"
        )
    adjusted = Quote(current.bid * bid_factor, current.ask * ask_factor, current.observed_at, current.contract)
    if adjusted.ask < adjusted.bid or adjusted.bid <= 0:
        raise BacktestInputError("NIFTY roll-adjusted quote is invalid")
    return adjusted, NiftyRollAdjustment(
        basis.roll_day, basis.captured_at, basis.old.contract, basis.new.contract,
        direction, bid_factor, ask_factor,
    )


def bridge_for_estimation(cache_dir: Path, day: date, payload: dict[str, Any]) -> tuple[Quote, NiftyRollBasis | None]:
    """Return the live-compatible NIFTY bridge for a China valuation day."""
    cached = bridge_quote(payload, "nifty")
    if day != india.last_tuesday_of_month(day):
        return cached, None
    basis = load_roll_basis(cache_dir, day)
    # The ordinary cache intentionally retrieves the new contract's preceding
    # close for a Tuesday replay.  The production bridge, captured before the
    # roll, used the old contract, so use the audited old close and align the
    # intraday new contract to it below.
    if cached.contract != basis.new.contract:
        raise BacktestInputError(
            f"{day_key(day)} cache expected effective new NIFTY {basis.new.contract}, got {cached.contract}"
        )
    return basis.old_bridge, basis


def available_domestic_days(root: Path, start: date, end: date) -> list[date]:
    values: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5 and domestic_zip_path(root, current).is_file():
            try:
                domestic_bars(root, current)
            except BacktestInputError:
                pass
            else:
                values.append(current)
        current = current.fromordinal(current.toordinal() + 1)
    return values


def load_nav_series(runtime: Path, start: date, end: date, timeout: float) -> list[india.OfficialNAV]:
    path = runtime / "inputs" / "official_nav.json"
    cached: list[india.OfficialNAV] = []
    if path.exists():
        raw = load_json(path)
        if isinstance(raw, list):
            for row in raw:
                try:
                    cached.append(india.OfficialNAV(positive(row.get("value"), "cached NAV"), parse_day(row.get("day"))))
                except (AttributeError, BacktestInputError, ValueError):
                    continue
    # The estimator only needs two official fund-NAV dealing days preceding
    # the requested start date.  Checking that directly avoids an unnecessary
    # network refresh around long domestic holiday closures.
    if cached and max(item.trading_day for item in cached) >= end:
        cached = sorted(cached, key=lambda item: item.trading_day)
        if sum(item.trading_day < start for item in cached) >= 2:
            return cached
    rows = history.official_nav_series(timeout, start, end)
    atomic_json(path, [{"day": item.trading_day.isoformat(), "value": item.value} for item in rows])
    return rows


def cached_fx(runtime: Path, day: date, timeout: float) -> float:
    path = runtime / "inputs" / "safe_usd_cny" / f"{day_key(day)}.json"
    if path.exists():
        try:
            row = load_json(path)
            if isinstance(row, dict) and row.get("trading_day") == day_key(day):
                return positive(row.get("rate"), f"cached SAFE {day_key(day)}")
        except BacktestInputError:
            pass
    parity = fx_common.fetch_safe_central_parity(day, timeout)
    atomic_json(path, {"trading_day": day_key(day), "rate": parity.rate, "source": fx_common.SAFE_SOURCE})
    return parity.rate


def estimate_nav(
    base_nav: float,
    base_fx: float,
    target_fx: float,
    base_inda_close: Quote,
    bridge_inda: Quote,
    bridge_nifty: Quote,
    current_nifty: Quote,
) -> float:
    synthetic_inda = bridge_inda.mid * current_nifty.mid / bridge_nifty.mid
    value = base_nav * (
        india.STATIC_RATIO
        + india.INVESTMENT_RATIO * (synthetic_inda / base_inda_close.mid) * (target_fx / base_fx)
    )
    if not math.isfinite(value) or value <= 0:
        raise BacktestInputError("provisional bridge NAV is invalid")
    return value


def get_official_redemption_day(day: date, nav_days: list[date], offset: int) -> date:
    try:
        index = nav_days.index(day)
    except ValueError as exc:
        raise BacktestInputError(f"{day_key(day)} is not an official NAV dealing day") from exc
    if index + offset >= len(nav_days):
        raise BacktestInputError(f"{day_key(day)} has no T+{offset} official NAV dealing day in the source range")
    return nav_days[index + offset]


def net_fund_buy_price(raw_price: float, fund_commission_bps: float, fund_slippage_bps: float) -> float:
    return raw_price * (1.0 + (fund_commission_bps + fund_slippage_bps) / 10_000.0)


def build_observations(
    *,
    domestic_root: Path,
    cache_dir: Path,
    runtime: Path,
    start: date,
    end: date,
    timeout: float,
    calibration_days: int,
    entry_start_minute: str,
    entry_last_minute: str,
    entry_price_field: str,
) -> tuple[list[SignalObservation], dict[date, india.OfficialNAV], list[date], dict[date, dict[str, Any]]]:
    nav_series = load_nav_series(runtime, start, end, timeout)
    nav_by_day = {item.trading_day: item for item in nav_series}
    nav_days = [item.trading_day for item in nav_series]
    status = load_json(cache_dir / "status.json")
    entries = status.get("entries") if isinstance(status, dict) else {}
    if not isinstance(entries, dict):
        raise BacktestInputError("TWS cache status has no entries")
    payloads: dict[date, dict[str, Any]] = {}
    observations: list[SignalObservation] = []
    for day in available_domestic_days(domestic_root, start, end):
        status_entry = entries.get(day_key(day))
        if not isinstance(status_entry, dict) or status_entry.get("state") != "complete":
            continue
        try:
            base = history.t_minus_two_nav(day, nav_series)
            base_payload = payloads.setdefault(base.trading_day, load_tws_payload(cache_dir, base.trading_day))
            payload = payloads.setdefault(day, load_tws_payload(cache_dir, day))
            base_close = usa_quote(base_payload, "inda", CLOSE_MINUTE)
            bridge_inda = bridge_quote(payload, "inda")
            bridge_nifty, roll_basis = bridge_for_estimation(cache_dir, day, payload)
            base_fx, target_fx = cached_fx(runtime, base.trading_day, timeout), cached_fx(runtime, day, timeout)
            actual = nav_by_day[day].value
            bars = domestic_bars(domestic_root, day)
        except (BacktestInputError, history.SourceUnavailableError, KeyError):
            continue
        for minute in sorted(bars):
            if minute < entry_start_minute or minute > entry_last_minute:
                continue
            try:
                nifty_entry = nifty_china_quote(payload, minute)
                nifty_estimate, adjustment = align_nifty_to_reference(
                    cache_dir, nifty_entry, bridge_nifty, roll_basis,
                )
                estimated = estimate_nav(
                    base.value, base_fx, target_fx, base_close, bridge_inda, bridge_nifty, nifty_estimate,
                )
                price = bars[minute][entry_price_field]
            except BacktestInputError:
                # A minute without a synchronous NIFTY bid/ask quote is not
                # tradable under this replay; never fill it from a neighbour.
                continue
            observations.append(SignalObservation(
                day=day,
                entry_minute=minute,
                base_nav_day=base.trading_day,
                base_nav=base.value,
                actual_nav=actual,
                base_fx=base_fx,
                target_fx=target_fx,
                estimated_nav=estimated,
                guarded_nav=None,
                prediction_error=estimated / actual - 1.0,
                fund_price=price,
                nifty_entry=nifty_entry,
                nifty_estimate=nifty_estimate,
                nifty_bridge=bridge_nifty,
                inda_bridge=bridge_inda,
                base_inda_close=base_close,
                roll_adjustment=adjustment,
            ))
    observations.sort(key=lambda item: (item.day, item.entry_minute))
    # Calibrate each minute against only the same-minute observations whose
    # T-day NAV would already have been published by the candidate's T-2
    # datum.  Pooling later intraday minutes here would leak future information
    # into an earlier opening-auction signal.
    by_minute: dict[str, list[SignalObservation]] = defaultdict(list)
    for item in observations:
        by_minute[item.entry_minute].append(item)
    guarded: list[SignalObservation] = []
    for minute in sorted(by_minute):
        rows = sorted(by_minute[minute], key=lambda item: item.day)
        for index, item in enumerate(rows):
            prior = [
                max(0.0, row.prediction_error)
                for row in rows[:index]
                if row.day <= item.base_nav_day
            ]
            guard = percentile(prior[-calibration_days:], 0.90) if len(prior) >= calibration_days else None
            guarded.append(replace(item, guarded_nav=item.estimated_nav * (1.0 - guard) if guard is not None else None))
    guarded.sort(key=lambda item: (item.day, item.entry_minute))
    return guarded, nav_by_day, nav_days, payloads


def simulate(
    *,
    observations: list[SignalObservation],
    nav_by_day: dict[date, india.OfficialNAV],
    nav_days: list[date],
    cache_dir: Path,
    payloads: dict[date, dict[str, Any]],
    threshold: float,
    signal_mode: str,
    nifty_multiplier: float,
    redemption_fee: float,
    fund_commission_bps: float,
    fund_slippage_bps: float,
    nifty_fee_usd: float,
    inda_commission_per_share_usd: float,
    annual_borrow_rate: float,
    annual_fund_carry_rate: float,
    entry_price_field: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    entered_days: set[date] = set()
    for item in observations:
        if item.day in entered_days:
            continue
        signal_nav = item.estimated_nav if signal_mode == "raw" else item.guarded_nav
        if signal_nav is None:
            continue
        fund_entry = net_fund_buy_price(item.fund_price, fund_commission_bps, fund_slippage_bps)
        estimated_discount = signal_nav / fund_entry - 1.0
        if estimated_discount < threshold:
            continue
        try:
            redemption_day = get_official_redemption_day(item.day, nav_days, 3)
            cash_available_day = get_official_redemption_day(item.day, nav_days, 9)
            redemption_payload = payloads.setdefault(redemption_day, load_tws_payload(cache_dir, redemption_day))
            handoff_nifty = usa_quote(payloads.setdefault(item.day, load_tws_payload(cache_dir, item.day)), "nifty", HANDOFF_MINUTE)
            handoff_inda = usa_quote(payloads[item.day], "inda", HANDOFF_MINUTE)
            redemption_inda = usa_quote(redemption_payload, "inda", CLOSE_MINUTE)
        except (BacktestInputError, history.SourceUnavailableError):
            continue
        # One NIFTY unit per eligible China day.  The first minute that passes
        # the threshold is the executable entry; later lower prints are not
        # retrospectively cherry-picked.
        entered_days.add(item.day)
        redemption_fx = item.redemption_fx
        if redemption_fx is None:
            continue
        short_notional_usd = item.nifty_entry.bid * nifty_multiplier
        fund_shares = short_notional_usd * item.target_fx / (signal_nav * india.INVESTMENT_RATIO)
        fund_cost = fund_shares * fund_entry
        redeemed_nav = nav_by_day[redemption_day].value
        redemption_cash = fund_shares * redeemed_nav * (1.0 - redemption_fee)
        cash_lock_days = max((cash_available_day - item.day).days, 0)
        fund_carry_rmb = fund_cost * annual_fund_carry_rate * cash_lock_days / 365.0
        nifty_pnl_usd = (item.nifty_entry.bid - handoff_nifty.ask) * nifty_multiplier - 2.0 * nifty_fee_usd
        inda_shares = handoff_nifty.mid * nifty_multiplier / handoff_inda.bid
        holding_days = max((redemption_day - item.day).days, 0)
        inda_borrow = inda_shares * handoff_inda.bid * annual_borrow_rate * holding_days / 365.0
        inda_pnl_usd = (
            (handoff_inda.bid - redemption_inda.ask) * inda_shares
            - 2.0 * inda_shares * inda_commission_per_share_usd
            - inda_borrow
        )
        nifty_pnl_rmb = nifty_pnl_usd * item.target_fx
        inda_pnl_rmb = inda_pnl_usd * redemption_fx
        total_pnl = redemption_cash - fund_cost + nifty_pnl_rmb + inda_pnl_rmb - fund_carry_rmb
        results.append({
            "signal_mode": signal_mode,
            "threshold": threshold,
            "entry_day": day_key(item.day),
            "entry_minute_bjt": item.entry_minute,
            "entry_price_field": entry_price_field,
            "redemption_day": day_key(redemption_day),
            "cash_available_day": day_key(cash_available_day),
            "estimated_discount": estimated_discount,
            "actual_discount_vs_T_nav": item.actual_nav / fund_entry - 1.0,
            "bridge_prediction_error": item.prediction_error,
            "fund_entry_price": fund_entry,
            "estimated_nav": item.estimated_nav,
            "signal_nav": signal_nav,
            "actual_T_nav": item.actual_nav,
            "nifty_entry_contract": item.nifty_entry.contract,
            "nifty_estimate_contract": item.nifty_estimate.contract,
            "nifty_bridge_contract": item.nifty_bridge.contract,
            "nifty_roll_adjustment": item.roll_adjustment.direction if item.roll_adjustment is not None else "none",
            "nifty_roll_day": day_key(item.roll_adjustment.roll_day) if item.roll_adjustment is not None else None,
            "nifty_roll_bid_factor": item.roll_adjustment.bid_factor if item.roll_adjustment is not None else None,
            "nifty_roll_ask_factor": item.roll_adjustment.ask_factor if item.roll_adjustment is not None else None,
            "redemption_nav": redeemed_nav,
            "fund_shares": fund_shares,
            "nifty_entry_notional_usd": short_notional_usd,
            "fund_cost_rmb": fund_cost,
            "redemption_cash_rmb": redemption_cash,
            "fund_carry_rmb": fund_carry_rmb,
            "nifty_pnl_usd": nifty_pnl_usd,
            "inda_pnl_usd": inda_pnl_usd,
            "nifty_pnl_rmb": nifty_pnl_rmb,
            "inda_pnl_rmb": inda_pnl_rmb,
            "total_pnl_rmb": total_pnl,
            "return_on_fund_cost": total_pnl / fund_cost,
            "holding_calendar_days": holding_days,
            "cash_lock_calendar_days": cash_lock_days,
        })
    return results


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}
    pnl = [float(row["total_pnl_rmb"]) for row in trades]
    cost = [float(row["fund_cost_rmb"]) for row in trades]
    cash_days = sum(
        float(row["fund_cost_rmb"])
        * max((parse_day(str(row["cash_available_day"])) - parse_day(str(row["entry_day"]))).days, 0)
        for row in trades
    )
    # A tranche consumes the fund purchase cash until redemption proceeds are
    # usable.  The hedge itself ends on redemption day, so report those two
    # overlapping-position counts separately.  This makes "one contract per
    # signal day" operationally comparable without inventing a margin rate.
    cash_events: list[tuple[date, int, float]] = []
    hedge_events: list[tuple[date, int, float]] = []
    for row in trades:
        entry_day = parse_day(str(row["entry_day"]))
        redemption_day = parse_day(str(row["redemption_day"]))
        cash_available_day = parse_day(str(row["cash_available_day"]))
        fund_cost = float(row["fund_cost_rmb"])
        # "到账可用" means the purchase cash is released before a new 14:55
        # entry on that date, so availability-day releases are processed first.
        cash_events.extend([(entry_day, 1, fund_cost), (cash_available_day, -1, -fund_cost)])
        hedge_notional = float(row["nifty_entry_notional_usd"])
        hedge_events.extend([(entry_day, 1, hedge_notional), (redemption_day + timedelta(days=1), -1, -hedge_notional)])

    def peaks(events: list[tuple[date, int, float]]) -> tuple[int, float]:
        active_count, active_value = 0, 0.0
        peak_count, peak_value = 0, 0.0
        for _, count_delta, value_delta in sorted(events, key=lambda item: (item[0], item[1])):
            active_count += count_delta
            active_value += value_delta
            peak_count = max(peak_count, active_count)
            peak_value = max(peak_value, active_value)
        return peak_count, peak_value

    max_cash_tranches, max_locked_fund_cash = peaks(cash_events)
    max_hedge_tranches, max_active_hedge_notional_usd = peaks(hedge_events)
    return {
        "trades": len(trades),
        "wins": sum(value > 0 for value in pnl),
        "win_rate": sum(value > 0 for value in pnl) / len(pnl),
        "total_pnl_rmb": sum(pnl),
        "total_fund_cost_rmb": sum(cost),
        "return_on_deployed_cost": sum(pnl) / sum(cost),
        "mean_pnl_rmb": statistics.fmean(pnl),
        "median_pnl_rmb": statistics.median(pnl),
        "worst_pnl_rmb": min(pnl),
        "mean_actual_discount": statistics.fmean(float(row["actual_discount_vs_T_nav"]) for row in trades),
        "mean_estimated_discount": statistics.fmean(float(row["estimated_discount"]) for row in trades),
        "mean_cash_lock_calendar_days": statistics.fmean(
            max((parse_day(str(row["cash_available_day"])) - parse_day(str(row["entry_day"]))).days, 0)
            for row in trades
        ),
        "annualized_return_on_cash_days": sum(pnl) * 365.0 / cash_days if cash_days else None,
        "max_concurrent_cash_tranches": max_cash_tranches,
        "max_locked_fund_cash_rmb": max_locked_fund_cash,
        "max_concurrent_hedge_tranches": max_hedge_tranches,
        "max_active_hedge_notional_usd": max_active_hedge_notional_usd,
    }


def parse_thresholds(value: str) -> list[float]:
    values = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not values or any(item < 0 for item in values):
        raise argparse.ArgumentTypeError("thresholds must be non-negative decimal ratios")
    return values


def parse_minute(value: str) -> str:
    candidate = str(value).strip()
    try:
        datetime.strptime(candidate, "%H:%M")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("minute must use HH:MM") from exc
    return candidate


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--start", type=parse_day, default=date(2026, 2, 4))
    result.add_argument("--end", type=parse_day, default=date(2026, 8, 4))
    result.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    result.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    result.add_argument("--domestic-root", type=Path, default=DEFAULT_DOMESTIC_ROOT)
    result.add_argument("--timeout", type=float, default=30.0)
    result.add_argument("--entry-start-minute", type=parse_minute, default=DEFAULT_ENTRY_START)
    result.add_argument("--entry-last-minute", type=parse_minute, default=DEFAULT_ENTRY_LAST_MINUTE)
    result.add_argument(
        "--entry-price-field",
        choices=("high", "close"),
        default="high",
        help="domestic minute price used for the executable LOF buy; high is the conservative default",
    )
    result.add_argument("--thresholds", type=parse_thresholds, default=parse_thresholds("0.010,0.0125,0.015,0.0175,0.020"))
    result.add_argument("--calibration-days", type=int, default=20)
    result.add_argument("--nifty-multiplier", type=float, default=2.0)
    result.add_argument("--redemption-fee", type=float, default=0.00464)
    result.add_argument("--fund-commission-bps", type=float, default=3.0)
    result.add_argument("--fund-slippage-bps", type=float, default=5.0)
    result.add_argument("--nifty-fee-usd", type=float, default=2.0)
    result.add_argument("--inda-commission-per-share-usd", type=float, default=0.005)
    result.add_argument("--annual-borrow-rate", type=float, default=0.01)
    result.add_argument("--annual-fund-carry-rate", type=float, default=0.0)
    return result


def run(args: argparse.Namespace) -> int:
    if args.end < args.start:
        raise BacktestInputError("end must not precede start")
    if args.entry_last_minute < args.entry_start_minute:
        raise BacktestInputError("entry-last-minute must not precede entry-start-minute")
    if args.calibration_days <= 0 or args.nifty_multiplier <= 0 or args.timeout <= 0:
        raise BacktestInputError("invalid positive configuration")
    if any(value < 0 for value in (args.redemption_fee, args.fund_commission_bps, args.fund_slippage_bps, args.nifty_fee_usd, args.inda_commission_per_share_usd, args.annual_borrow_rate, args.annual_fund_carry_rate)):
        raise BacktestInputError("cost inputs must not be negative")
    observations, nav_by_day, nav_days, payloads = build_observations(
        domestic_root=args.domestic_root, cache_dir=args.cache_dir, runtime=args.runtime_dir,
        start=args.start, end=args.end, timeout=args.timeout, calibration_days=args.calibration_days,
        entry_start_minute=args.entry_start_minute,
        entry_last_minute=args.entry_last_minute,
        entry_price_field=args.entry_price_field,
    )
    # Load the FX only for redemption dates that an executable T-day could
    # reach.  It stays in the typed observation so the simulation cannot
    # accidentally substitute a close/spot FX quote.
    required_redemption_days: set[date] = set()
    for item in observations:
        try:
            required_redemption_days.add(get_official_redemption_day(item.day, nav_days, 3))
        except BacktestInputError:
            continue
    redemption_fx = {day: cached_fx(args.runtime_dir, day, args.timeout) for day in sorted(required_redemption_days)}
    enriched: list[SignalObservation] = []
    for item in observations:
        try:
            redemption_day = get_official_redemption_day(item.day, nav_days, 3)
        except BacktestInputError:
            continue
        enriched.append(replace(item, redemption_fx=redemption_fx.get(redemption_day)))
    report: dict[str, Any] = {
        "generated_at": datetime.now(india.SHANGHAI).isoformat(),
        "scope": {
            "start": day_key(args.start),
            "end": day_key(args.end),
            "entry_window_bjt": f"{args.entry_start_minute}-{args.entry_last_minute}",
            "entry_price_field": args.entry_price_field,
            "handoff_minute_ny": HANDOFF_MINUTE,
            "exit_minute_ny": CLOSE_MINUTE,
        },
        "assumptions": {
            "investment_ratio": india.INVESTMENT_RATIO,
            "static_ratio": india.STATIC_RATIO,
            "nifty_multiplier": args.nifty_multiplier,
            "redemption_fee": args.redemption_fee,
            "fund_commission_bps": args.fund_commission_bps,
            "fund_slippage_bps": args.fund_slippage_bps,
            "nifty_fee_usd_per_side": args.nifty_fee_usd,
            "inda_commission_usd_per_share_per_side": args.inda_commission_per_share_usd,
            "annual_inda_borrow_rate": args.annual_borrow_rate,
            "annual_fund_carry_rate": args.annual_fund_carry_rate,
            "calibration_days": args.calibration_days,
            "nifty_roll_policy": {
                "effective_contract": "calendar-last-tuesday-use-next-month.v2",
                "basis_capture": "Monday 12:28-12:32 BJT before the final Tuesday",
                "roll_day_bridge": "old-contract close plus adverse-side old/new basis adjustment",
                "missing_basis": "exclude roll-day observations",
            },
        },
        "observations": len(enriched),
        "prediction_error": {
            "mean_bps": statistics.fmean(item.prediction_error for item in enriched) * 10_000.0 if enriched else None,
            "mae_bps": statistics.fmean(abs(item.prediction_error) for item in enriched) * 10_000.0 if enriched else None,
            "p90_overestimate_bps": percentile([max(0.0, item.prediction_error) * 10_000.0 for item in enriched], 0.90),
        },
        "scenarios": [],
    }
    all_trades: list[dict[str, Any]] = []
    for mode in ("raw", "guarded"):
        for threshold in args.thresholds:
            trades = simulate(
                observations=enriched, nav_by_day=nav_by_day, nav_days=nav_days, cache_dir=args.cache_dir, payloads=payloads,
                threshold=threshold, signal_mode=mode, nifty_multiplier=args.nifty_multiplier,
                redemption_fee=args.redemption_fee, fund_commission_bps=args.fund_commission_bps,
                fund_slippage_bps=args.fund_slippage_bps, nifty_fee_usd=args.nifty_fee_usd,
                inda_commission_per_share_usd=args.inda_commission_per_share_usd, annual_borrow_rate=args.annual_borrow_rate,
                annual_fund_carry_rate=args.annual_fund_carry_rate, entry_price_field=args.entry_price_field,
            )
            summary = summarize(trades)
            report["scenarios"].append({"mode": mode, "threshold": threshold, **summary})
            all_trades.extend(trades)
    output = args.runtime_dir / "results" / f"run-{datetime.now(india.SHANGHAI):%Y%m%dT%H%M%S}.json"
    atomic_json(output, report)
    trades_path = output.with_suffix(".trades.csv")
    fields = sorted({key for row in all_trades for key in row}) if all_trades else []
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    with trades_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_trades)
    print(json.dumps({"report": str(output), "trades": str(trades_path), "observations": len(enriched), "scenarios": report["scenarios"]}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    try:
        return run(parser().parse_args())
    except (BacktestInputError, history.SourceUnavailableError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

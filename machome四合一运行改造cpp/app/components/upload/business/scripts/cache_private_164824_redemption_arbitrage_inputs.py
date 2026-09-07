#!/usr/bin/env python3
"""Cache six months of read-only IBKR inputs for a SZ164824 redemption study.

This program intentionally **does not** calculate an arbitrage signal, select
a discount threshold, submit an order, or write any website/valuation data.
It only builds a resumable local cache from the IBKR TWS API.  A later model
can therefore compare thresholds and staggered-entry rules without repeatedly
asking TWS for the same historical data.

For every Beijing-calendar weekday in the requested range the cache stores:

* the preceding synchronous INDA/NIFTY 15:49--15:51 New York close bridge,
  using the T-day effective NIFTY monthly contract;
* NIFTY one-minute BID/ASK throughout China's 09:30--11:30 and 13:00--15:00
  sessions, which supports entry-time and bridge-residual analysis;
* INDA and NIFTY one-minute BID/ASK from 09:30--16:00 New York, which covers
  the NIFTY-to-INDA handoff and the final US valuation/hedge leg.

The optional ``--backfill-rolls`` pass is intentionally much smaller than a
full refresh.  For each final Tuesday it stores the old/new NIFTY BID/ASK pair
from the preceding Monday 12:28--12:32 BJT window, plus the old-contract
15:49--15:51 ET close.  The replay uses those auditable quotes to remove the
monthly futures basis jump on the single Tuesday where a live bridge can still
reference the expiring contract.

The effective NIFTY contract switches to next month on the calendar month's
last Tuesday, matching the live 164824 bridge model.  Holidays and unavailable
historical data are recorded as errors rather than interpolated.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import signal
import sys
import time
from dataclasses import asdict
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_164824_history_backfill as history  # noqa: E402
import private_164824_valuation_uploader as india  # noqa: E402


DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / ".runtime" / "private_164824_redemption_arbitrage_cache"
CACHE_VERSION = "private-164824-redemption-inputs.v1"
ROLL_CACHE_VERSION = "private-164824-roll-basis.v1"
BRIDGE_START_MINUTE = clock_time(15, 49)
BRIDGE_END_MINUTE = clock_time(15, 51)
ROLL_START_MINUTE = clock_time(12, 28)
ROLL_END_MINUTE = clock_time(12, 32)
US_OPEN = clock_time(9, 30)
US_CLOSE = clock_time(16, 0)


class Interrupted(RuntimeError):
    """Stop after the current request and leave a resumable status file."""


def parse_day(value: str) -> date:
    return datetime.strptime(str(value).strip().replace("-", ""), "%Y%m%d").date()


def day_key(value: date) -> str:
    return value.isoformat()


def six_months_before(value: date) -> date:
    """Subtract calendar months without requiring a third-party dependency."""
    month_index = value.year * 12 + value.month - 1 - 6
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(value.day, last_day))


def china_weekdays(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end day must not precede start day")
    values: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return values


def monthly_roll_days(start: date, end: date) -> list[date]:
    """Return calendar final Tuesdays in range under the production rule."""
    cursor = date(start.year, start.month, 1)
    values: list[date] = []
    while cursor <= end:
        roll_day = india.last_tuesday_of_month(cursor)
        if start <= roll_day <= end:
            values.append(roll_day)
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return values


def quote_payload(quote: india.MarketQuote) -> dict[str, Any]:
    """Serialize every auditable quote field; do not collapse BID/ASK to mid."""
    row = asdict(quote)
    observed_at = row.get("observed_at")
    if isinstance(observed_at, datetime):
        row["observed_at"] = observed_at.isoformat()
    return row


def quotes_payload(quotes: Iterable[india.MarketQuote]) -> list[dict[str, Any]]:
    return [quote_payload(item) for item in quotes]


def missing_payload_segments(payload: Any) -> list[str]:
    """Return the raw cache segments that cannot support the later replay.

    A completed API request is not necessarily a usable market session: U.S.
    holidays and transient historical-feed holes can both return an empty bar
    collection.  Keep that distinction explicit so a resume run retries only
    genuinely incomplete cache files rather than blindly replaying six months.
    """
    if not isinstance(payload, dict):
        return ["payload"]
    missing: list[str] = []
    if int((payload.get("bridge") or {}).get("common_minutes") or 0) < 1:
        missing.append("bridge")
    if not isinstance((payload.get("nifty_china_session") or {}).get("quotes"), dict) or not (payload.get("nifty_china_session") or {}).get("quotes"):
        missing.append("nifty_china_session")
    us_session = payload.get("us_session") or {}
    if not isinstance(us_session.get("inda"), list) or not us_session.get("inda"):
        missing.append("us_inda")
    if not isinstance(us_session.get("nifty"), list) or not us_session.get("nifty"):
        missing.append("us_nifty")
    return missing


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_gzip_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
    os.replace(temporary, path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def roll_cache_path(cache_dir: Path, roll_day: date) -> Path:
    return cache_dir / "rolls" / f"{day_key(roll_day)}.json"


def roll_cache_complete(path: Path, roll_day: date) -> bool:
    decoded = read_json(path, {})
    if not isinstance(decoded, dict):
        return False
    if decoded.get("cache_version") != ROLL_CACHE_VERSION or decoded.get("roll_day") != day_key(roll_day):
        return False
    required = ("old", "new", "old_bridge")
    return all(isinstance(decoded.get(key), list) and decoded.get(key) for key in required)


def cached_day_missing_segments(path: Path) -> list[str]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return missing_payload_segments(json.load(handle))
    except (OSError, EOFError, json.JSONDecodeError):
        return ["payload"]


def bridge_windows(day: date) -> tuple[datetime, datetime]:
    """Initial close bridge for a China day, in the New York time zone."""
    target = india.latest_completed_bridge_target(datetime.combine(day, clock_time(9, 30), india.SHANGHAI))
    return (
        target.replace(hour=BRIDGE_START_MINUTE.hour, minute=BRIDGE_START_MINUTE.minute, second=0, microsecond=0),
        target.replace(hour=BRIDGE_END_MINUTE.hour, minute=BRIDGE_END_MINUTE.minute, second=0, microsecond=0),
    )


def previous_weekday(value: datetime) -> datetime:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def common_observed_minutes(left: list[india.MarketQuote], right: list[india.MarketQuote]) -> int:
    return len({item.observed_at for item in left}.intersection(item.observed_at for item in right))


class TWSCache:
    def __init__(self, market: history.HistoricalINDAMarket, request_interval: float) -> None:
        self.market = market
        self.request_interval = request_interval
        self.stop_requested = False

    def request_window(
        self,
        contract: Any,
        symbol: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[india.MarketQuote]:
        if self.stop_requested:
            raise Interrupted("interrupted before a new TWS historical request")
        quotes = self.market.historical_bid_ask_window(contract, symbol, start_at, end_at)
        if self.request_interval > 0:
            time.sleep(self.request_interval)
        return quotes

    def request_nifty_china_session(self, day: date) -> dict[str, india.MarketQuote]:
        if self.stop_requested:
            raise Interrupted("interrupted before a new TWS historical request")
        quotes = self.market.nifty_quotes_for_china_session(day)
        if self.request_interval > 0:
            time.sleep(self.request_interval)
        return quotes

    def preceding_bridge(
        self,
        day: date,
        nifty_contract: Any,
    ) -> tuple[datetime, list[india.MarketQuote], list[india.MarketQuote]]:
        """Use the latest available US close; preserve T-day NIFTY contract.

        A U.S. holiday cannot have a synthetic bridge.  Walking backward is
        explicit in the cache so later simulations can attach a stale-anchor
        flag or exclude the day altogether.
        """
        start_at, end_at = bridge_windows(day)
        inda_contract = self.market.contract("SMART")
        for _ in range(5):
            inda_quotes = self.request_window(inda_contract, india.REFERENCE_SYMBOL, start_at, end_at)
            nifty_quotes = self.request_window(nifty_contract, india.NIFTY_SYMBOL, start_at, end_at)
            if common_observed_minutes(inda_quotes, nifty_quotes):
                return start_at, inda_quotes, nifty_quotes
            start_at = previous_weekday(start_at).replace(
                hour=BRIDGE_START_MINUTE.hour,
                minute=BRIDGE_START_MINUTE.minute,
                second=0,
                microsecond=0,
            )
            end_at = start_at.replace(hour=BRIDGE_END_MINUTE.hour, minute=BRIDGE_END_MINUTE.minute)
        raise history.SourceUnavailableError(f"{day_key(day)} has no synchronous preceding INDA/NIFTY bridge within five weekdays")

    def cache_day(self, day: date) -> dict[str, Any]:
        nifty_contract = self.market.historical_nifty_contract_for_day(day)
        nifty_name = str(getattr(nifty_contract, "localSymbol", "") or getattr(nifty_contract, "symbol", india.NIFTY_SYMBOL))
        bridge_start, bridge_inda, bridge_nifty = self.preceding_bridge(day, nifty_contract)
        china_nifty = self.request_nifty_china_session(day)
        us_start = datetime.combine(day, US_OPEN, india.NEW_YORK)
        us_end = datetime.combine(day, US_CLOSE, india.NEW_YORK)
        us_inda = self.request_window(self.market.contract("SMART"), india.REFERENCE_SYMBOL, us_start, us_end)
        us_nifty = self.request_window(nifty_contract, india.NIFTY_SYMBOL, us_start, us_end)
        return {
            "cache_version": CACHE_VERSION,
            "china_day": day_key(day),
            "cached_at": datetime.now(india.SHANGHAI).isoformat(),
            "nifty_contract": {
                "effective_month": history.historical_nifty_contract_month(day),
                "local_symbol": nifty_name,
                "exchange": india.NIFTY_EXCHANGE,
                "roll_rule": "calendar-last-tuesday-use-next-month.v2",
            },
            "bridge": {
                "timezone": "America/New_York",
                "window": "15:49-15:51",
                "used_start": bridge_start.isoformat(),
                "used_end": bridge_start.replace(hour=BRIDGE_END_MINUTE.hour, minute=BRIDGE_END_MINUTE.minute).isoformat(),
                "common_minutes": common_observed_minutes(bridge_inda, bridge_nifty),
                "inda": quotes_payload(bridge_inda),
                "nifty": quotes_payload(bridge_nifty),
            },
            "nifty_china_session": {
                "timezone": "Asia/Shanghai",
                "window": "09:30-11:30,13:00-15:00",
                "quotes": {minute: quote_payload(quote) for minute, quote in sorted(china_nifty.items())},
            },
            "us_session": {
                "timezone": "America/New_York",
                "window": "09:30-16:00",
                "inda": quotes_payload(us_inda),
                "nifty": quotes_payload(us_nifty),
            },
        }

    def cache_roll_basis(self, roll_day: date) -> dict[str, Any]:
        """Capture the historical old/new pair and old close for one roll.

        This uses the same read-only BID/ASK endpoint as the main cache.  It
        does not infer a basis from last prices or post-roll data: each side is
        the synchronized executable quote from the specified Monday window.
        """
        if roll_day != india.last_tuesday_of_month(roll_day):
            raise history.SourceUnavailableError(f"{day_key(roll_day)} is not a monthly final Tuesday")
        monday = roll_day - timedelta(days=1)
        if monday.weekday() != 0:
            raise history.SourceUnavailableError(f"{day_key(roll_day)} has no preceding Monday roll window")
        old_contract = self.market.historical_nifty_contract_for_day(monday)
        new_contract = self.market.historical_nifty_contract_for_day(roll_day)
        old_name = str(getattr(old_contract, "localSymbol", "") or getattr(old_contract, "symbol", india.NIFTY_SYMBOL))
        new_name = str(getattr(new_contract, "localSymbol", "") or getattr(new_contract, "symbol", india.NIFTY_SYMBOL))
        if old_name == new_name:
            raise history.SourceUnavailableError(f"{day_key(roll_day)} did not select distinct old/new NIFTY contracts")
        basis_start = datetime.combine(monday, ROLL_START_MINUTE, india.SHANGHAI)
        basis_end = datetime.combine(monday, ROLL_END_MINUTE, india.SHANGHAI)
        old_quotes = self.request_window(old_contract, india.NIFTY_SYMBOL, basis_start, basis_end)
        new_quotes = self.request_window(new_contract, india.NIFTY_SYMBOL, basis_start, basis_end)
        common_basis = common_observed_minutes(old_quotes, new_quotes)
        if not common_basis:
            raise history.SourceUnavailableError(f"{day_key(roll_day)} has no synchronous Monday old/new NIFTY BID/ASK quote")
        bridge_start, bridge_end = bridge_windows(roll_day)
        old_bridge = self.request_window(old_contract, india.NIFTY_SYMBOL, bridge_start, bridge_end)
        if not old_bridge:
            raise history.SourceUnavailableError(f"{day_key(roll_day)} has no old-contract Monday 15:49-15:51 ET bridge quote")
        return {
            "cache_version": ROLL_CACHE_VERSION,
            "roll_day": day_key(roll_day),
            "monday": day_key(monday),
            "window_timezone": "Asia/Shanghai",
            "window": "Monday 12:28-12:32 BJT before monthly last Tuesday",
            "bridge_timezone": "America/New_York",
            "bridge_window": "15:49-15:51 ET on the preceding Monday",
            "cached_at": datetime.now(india.SHANGHAI).isoformat(),
            "old": quotes_payload(old_quotes),
            "new": quotes_payload(new_quotes),
            "old_bridge": quotes_payload(old_bridge),
            "common_basis_minutes": common_basis,
        }

    def close(self) -> None:
        self.market.close()


def progress_payload(
    *,
    start: date,
    end: date,
    planned: list[date],
    entries: dict[str, dict[str, Any]],
    started_at: str,
    finished: bool,
) -> dict[str, Any]:
    states = [entry.get("state") for entry in entries.values()]
    remaining = sum(1 for day in planned if not isinstance(entries.get(day_key(day)), dict) or entries[day_key(day)].get("state") != "complete")
    return {
        "cache_version": CACHE_VERSION,
        "started_at": started_at,
        "updated_at": datetime.now(india.SHANGHAI).isoformat(),
        "finished": finished,
        "range": {"start": day_key(start), "end": day_key(end)},
        "planned_weekdays": len(planned),
        "complete": states.count("complete"),
        "partial": states.count("partial"),
        "errors": states.count("error"),
        "pending": remaining,
        "entries": entries,
    }


def append_event(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    today = datetime.now(india.SHANGHAI).date()
    default_end = today - timedelta(days=1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_day, default=six_months_before(default_end))
    parser.add_argument("--end", type=parse_day, default=default_end)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", type=int, default=7496)
    parser.add_argument("--ib-client-id", type=int, default=264829)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--request-interval", type=float, default=1.5, help="seconds after each BID/ASK request pair")
    parser.add_argument("--max-days", type=int, default=0, help="limit newest planned weekdays; 0 means all")
    parser.add_argument("--retry-errors", action="store_true", help="retry dates previously recorded as errors")
    parser.add_argument("--retry-partial", action="store_true", help="retry only cache files with an empty required market segment")
    parser.add_argument(
        "--backfill-rolls",
        action="store_true",
        help="cache Monday old/new NIFTY basis and old close for each final Tuesday in range",
    )
    parser.add_argument("--status", action="store_true", help="print progress only; do not contact TWS")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.request_interval < 0 or args.timeout <= 0 or args.max_days < 0:
        raise SystemExit("timeout must be positive; request interval and max-days must not be negative")
    cache_dir: Path = args.cache_dir
    status_path = cache_dir / "status.json"
    if args.status:
        print(json.dumps({
            "days": read_json(status_path, {"error": "cache status does not exist"}),
            "rolls": read_json(cache_dir / "roll_status.json", {"error": "roll cache status does not exist"}),
        }, ensure_ascii=False, indent=2))
        return 0
    planned = china_weekdays(args.start, args.end)
    if args.max_days:
        planned = planned[-args.max_days:]
    previous = read_json(status_path, {})
    entries = previous.get("entries") if isinstance(previous, dict) and isinstance(previous.get("entries"), dict) else {}
    started_at = str(previous.get("started_at") or datetime.now(india.SHANGHAI).isoformat()) if isinstance(previous, dict) else datetime.now(india.SHANGHAI).isoformat()
    worklist = list(planned)
    if args.retry_partial:
        worklist = []
        for day in planned:
            key = day_key(day)
            prior = entries.get(key)
            path = cache_dir / "days" / f"{key}.json.gz"
            missing = cached_day_missing_segments(path) if path.exists() else ["payload"]
            if missing or (isinstance(prior, dict) and prior.get("state") == "partial"):
                worklist.append(day)
    events_path = cache_dir / "progress.jsonl"
    stop = {"requested": False}

    def request_stop(_signal: int, _frame: Any) -> None:
        stop["requested"] = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    market = history.HistoricalINDAMarket(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
    cache = TWSCache(market, args.request_interval)
    try:
        if args.backfill_rolls:
            roll_status_path = cache_dir / "roll_status.json"
            previous_rolls = read_json(roll_status_path, {})
            roll_entries = previous_rolls.get("entries") if isinstance(previous_rolls, dict) and isinstance(previous_rolls.get("entries"), dict) else {}
            roll_days = monthly_roll_days(args.start, args.end)
            for position, roll_day in enumerate(roll_days, start=1):
                key = day_key(roll_day)
                path = roll_cache_path(cache_dir, roll_day)
                prior = roll_entries.get(key)
                if roll_cache_complete(path, roll_day):
                    continue
                if isinstance(prior, dict) and prior.get("state") == "error" and not args.retry_errors:
                    continue
                if stop["requested"]:
                    break
                try:
                    payload = cache.cache_roll_basis(roll_day)
                    atomic_write_json(path, payload)
                    entry = {
                        "state": "complete",
                        "path": str(path.relative_to(cache_dir)),
                        "updated_at": datetime.now(india.SHANGHAI).isoformat(),
                        "old_contract": str((payload.get("old") or [{}])[0].get("contract") or ""),
                        "new_contract": str((payload.get("new") or [{}])[0].get("contract") or ""),
                    }
                    print(f"[{position}/{len(roll_days)}] roll complete {key}", flush=True)
                except Interrupted:
                    stop["requested"] = True
                    break
                except Exception as exc:
                    entry = {"state": "error", "error": str(exc), "updated_at": datetime.now(india.SHANGHAI).isoformat()}
                    print(f"[{position}/{len(roll_days)}] roll error {key}: {exc}", file=sys.stderr, flush=True)
                roll_entries[key] = entry
                atomic_write_json(roll_status_path, {
                    "cache_version": ROLL_CACHE_VERSION,
                    "range": {"start": day_key(args.start), "end": day_key(args.end)},
                    "updated_at": datetime.now(india.SHANGHAI).isoformat(),
                    "finished": False,
                    "entries": roll_entries,
                })
            atomic_write_json(roll_status_path, {
                "cache_version": ROLL_CACHE_VERSION,
                "range": {"start": day_key(args.start), "end": day_key(args.end)},
                "updated_at": datetime.now(india.SHANGHAI).isoformat(),
                "finished": not stop["requested"],
                "entries": roll_entries,
            })
            return 0
        for position, day in enumerate(worklist, start=1):
            key = day_key(day)
            prior = entries.get(key)
            if isinstance(prior, dict) and prior.get("state") == "complete" and not args.retry_partial:
                continue
            if isinstance(prior, dict) and prior.get("state") == "error" and not args.retry_errors:
                continue
            if stop["requested"]:
                break
            try:
                payload = cache.cache_day(day)
                path = cache_dir / "days" / f"{key}.json.gz"
                atomic_write_gzip_json(path, payload)
                missing = missing_payload_segments(payload)
                state = "partial" if missing else "complete"
                entry = {
                    "state": state,
                    "path": str(path.relative_to(cache_dir)),
                    "updated_at": datetime.now(india.SHANGHAI).isoformat(),
                }
                if missing:
                    entry["missing_segments"] = missing
                    print(f"[{position}/{len(worklist)}] partial {key}: {', '.join(missing)}", file=sys.stderr, flush=True)
                else:
                    print(f"[{position}/{len(worklist)}] complete {key}", flush=True)
            except Interrupted:
                stop["requested"] = True
                break
            except Exception as exc:  # preserve missing exchanges/holidays for later model filtering
                entry = {"state": "error", "error": str(exc), "updated_at": datetime.now(india.SHANGHAI).isoformat()}
                print(f"[{position}/{len(planned)}] error {key}: {exc}", file=sys.stderr, flush=True)
            entries[key] = entry
            append_event(events_path, {"at": entry["updated_at"], "china_day": key, **entry})
            atomic_write_json(status_path, progress_payload(
                start=args.start, end=args.end, planned=planned, entries=entries, started_at=started_at, finished=False,
            ))
    finally:
        cache.close()
        atomic_write_json(status_path, progress_payload(
            start=args.start, end=args.end, planned=planned, entries=entries, started_at=started_at, finished=not stop["requested"],
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

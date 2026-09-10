#!/usr/bin/env python3
"""Audit NIFTY's ability to bridge INDA between two China-time quote windows.

This is a read-only IBKR historical-data study.  It neither submits orders nor
writes any valuation data to the web service.  For each China date it compares
the two instruments' midpoint return between 03:49--03:51 BJT and
21:39--21:41 BJT.  The first interval is the US 15:49--15:51 close bridge and
the second is the US 09:39--09:41 open window during US daylight saving time.

The production NIFTY expiry rule is used: switch to the next monthly contract
on the calendar month's last Tuesday.  Each observation uses one contract on
both legs, so contract-roll basis cannot manufacture an intraday return.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_164824_history_backfill as history  # noqa: E402
import private_164824_valuation_uploader as india  # noqa: E402


DEFAULT_RUNTIME_DIR = Path(__file__).resolve().parent / ".runtime" / "private_164824_nifty_inda_bridge_analysis"
START_WINDOW = (clock_time(3, 49), clock_time(3, 51))
END_WINDOW = (clock_time(21, 39), clock_time(21, 41))


@dataclass(frozen=True)
class QuoteWindow:
    inda: india.MarketQuote
    nifty: india.MarketQuote
    common_minutes: int


@dataclass(frozen=True)
class Observation:
    china_day: str
    nifty_contract: str
    start_inda_mid: float
    end_inda_mid: float
    start_nifty_mid: float
    end_nifty_mid: float
    inda_return: float
    nifty_return: float
    raw_tracking_error: float
    inda_start_spread_bps: float
    inda_end_spread_bps: float
    nifty_start_spread_bps: float
    nifty_end_spread_bps: float
    start_common_minutes: int
    end_common_minutes: int

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        for field in (
            "inda_return",
            "nifty_return",
            "raw_tracking_error",
        ):
            row[f"{field}_bps"] = row[field] * 10_000.0
        return row


def parse_day(value: str) -> date:
    return datetime.strptime(str(value).strip().replace("-", ""), "%Y%m%d").date()


def day_key(value: date) -> str:
    return value.isoformat()


def midpoint(quote: india.MarketQuote) -> float:
    return (quote.bid + quote.ask) / 2.0


def spread_bps(quote: india.MarketQuote) -> float:
    mid = midpoint(quote)
    return (quote.ask - quote.bid) / mid * 10_000.0


def china_windows(day: date) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Return exact, inclusive BJT windows requested for the comparison."""
    return (
        (datetime.combine(day, START_WINDOW[0], india.SHANGHAI), datetime.combine(day, START_WINDOW[1], india.SHANGHAI)),
        (datetime.combine(day, END_WINDOW[0], india.SHANGHAI), datetime.combine(day, END_WINDOW[1], india.SHANGHAI)),
    )


def historical_window_quotes(
    market: history.HistoricalINDAMarket,
    contract: Any,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    request_interval: float,
) -> list[india.MarketQuote]:
    """Read an exact window through the production bridge query primitive.

    IBKR's ``1 D`` retention window is trading-session based for this stock
    feed, rather than a guaranteed 24 civil hours.  A request ending at
    21:41 BJT can omit the 03:50 BJT close bridge, so the two narrow windows
    must be fetched separately.
    """
    quotes = market.historical_bid_ask_window(contract, symbol, window_start, window_end)
    if request_interval > 0:
        # A helper call contains a separate historical BID and ASK request.
        time.sleep(request_interval)
    return quotes


def synchronous_quote_window(
    inda_quotes: list[india.MarketQuote],
    nifty_quotes: list[india.MarketQuote],
    reference_at: datetime,
) -> QuoteWindow | None:
    """Median each leg over minutes for which *both* instruments have B/A."""
    by_inda = {item.observed_at: item for item in inda_quotes}
    by_nifty = {item.observed_at: item for item in nifty_quotes}
    common = sorted(set(by_inda).intersection(by_nifty))
    if not common:
        return None
    inda_quote = india.median_quote([by_inda[item] for item in common], india.REFERENCE_SYMBOL, reference_at)
    nifty_quote = india.median_quote([by_nifty[item] for item in common], india.NIFTY_SYMBOL, reference_at)
    if inda_quote is None or nifty_quote is None:
        return None
    return QuoteWindow(inda_quote, nifty_quote, len(common))


def observe_day(market: history.HistoricalINDAMarket, day: date, request_interval: float) -> Observation:
    start_window, end_window = china_windows(day)
    nifty_contract = market.historical_nifty_contract_for_day(day)
    inda_contract = market.contract("SMART")
    start_inda_quotes = historical_window_quotes(market, inda_contract, india.REFERENCE_SYMBOL, *start_window, request_interval)
    start_nifty_quotes = historical_window_quotes(market, nifty_contract, india.NIFTY_SYMBOL, *start_window, request_interval)
    end_inda_quotes = historical_window_quotes(market, inda_contract, india.REFERENCE_SYMBOL, *end_window, request_interval)
    end_nifty_quotes = historical_window_quotes(market, nifty_contract, india.NIFTY_SYMBOL, *end_window, request_interval)
    start = synchronous_quote_window(
        start_inda_quotes,
        start_nifty_quotes,
        start_window[0] + timedelta(minutes=1),
    )
    end = synchronous_quote_window(
        end_inda_quotes,
        end_nifty_quotes,
        end_window[0] + timedelta(minutes=1),
    )
    if start is None or end is None:
        missing = []
        if start is None:
            missing.append("03:49-03:51 BJT")
        if end is None:
            missing.append("21:39-21:41 BJT")
        raise history.SourceUnavailableError(
            f"{day_key(day)} lacks synchronous INDA/NIFTY BID/ASK in {', '.join(missing)} "
            f"(start INDA/NIFTY minutes={len(start_inda_quotes)}/{len(start_nifty_quotes)}, "
            f"end={len(end_inda_quotes)}/{len(end_nifty_quotes)})"
        )
    start_inda, end_inda = midpoint(start.inda), midpoint(end.inda)
    start_nifty, end_nifty = midpoint(start.nifty), midpoint(end.nifty)
    inda_return = end_inda / start_inda - 1.0
    nifty_return = end_nifty / start_nifty - 1.0
    return Observation(
        china_day=day_key(day),
        nifty_contract=end.nifty.contract,
        start_inda_mid=start_inda,
        end_inda_mid=end_inda,
        start_nifty_mid=start_nifty,
        end_nifty_mid=end_nifty,
        inda_return=inda_return,
        nifty_return=nifty_return,
        raw_tracking_error=nifty_return - inda_return,
        inda_start_spread_bps=spread_bps(start.inda),
        inda_end_spread_bps=spread_bps(end.inda),
        nifty_start_spread_bps=spread_bps(start.nifty),
        nifty_end_spread_bps=spread_bps(end.nifty),
        start_common_minutes=start.common_minutes,
        end_common_minutes=end.common_minutes,
    )


def percentile(values: Iterable[float], ratio: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * ratio
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean, right_mean = statistics.fmean(left), statistics.fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def beta_no_intercept(x: Iterable[float], y: Iterable[float]) -> float | None:
    x_values, y_values = list(x), list(y)
    denominator = sum(value * value for value in x_values)
    if not x_values or denominator == 0:
        return None
    return sum(left * right for left, right in zip(x_values, y_values)) / denominator


def error_metrics(errors: Iterable[float]) -> dict[str, float | int | None]:
    values = list(errors)
    bps = [value * 10_000.0 for value in values]
    abs_bps = [abs(value) for value in bps]
    if not bps:
        return {"n": 0, "mean_bps": None, "mae_bps": None, "median_abs_bps": None, "p90_abs_bps": None, "rmse_bps": None, "max_abs_bps": None}
    return {
        "n": len(bps),
        "mean_bps": statistics.fmean(bps),
        "mae_bps": statistics.fmean(abs_bps),
        "median_abs_bps": statistics.median(abs_bps),
        "p90_abs_bps": percentile(abs_bps, 0.90),
        "rmse_bps": math.sqrt(statistics.fmean(value * value for value in bps)),
        "max_abs_bps": max(abs_bps),
    }


def rolling_beta_errors(observations: list[Observation], window: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(observations):
        previous = observations[max(0, index - window):index]
        beta = beta_no_intercept((row.nifty_return for row in previous), (row.inda_return for row in previous))
        if beta is None or len(previous) < window:
            continue
        estimate = beta * item.nifty_return
        rows.append({
            "china_day": item.china_day,
            "rolling_beta": beta,
            "inda_return": item.inda_return,
            "nifty_return": item.nifty_return,
            "adjusted_tracking_error": estimate - item.inda_return,
            "adjusted_tracking_error_bps": (estimate - item.inda_return) * 10_000.0,
        })
    return rows


def build_summary(observations: list[Observation], exclusions: list[dict[str, str]], beta_window: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    nifty_returns = [item.nifty_return for item in observations]
    inda_returns = [item.inda_return for item in observations]
    whole_beta = beta_no_intercept(nifty_returns, inda_returns)
    whole_beta_errors = [] if whole_beta is None else [whole_beta * item.nifty_return - item.inda_return for item in observations]
    rolling_rows = rolling_beta_errors(observations, beta_window)
    spreads = {
        "inda_start": [item.inda_start_spread_bps for item in observations],
        "inda_end": [item.inda_end_spread_bps for item in observations],
        "nifty_start": [item.nifty_start_spread_bps for item in observations],
        "nifty_end": [item.nifty_end_spread_bps for item in observations],
    }
    summary = {
        "method": {
            "timezone": "Asia/Shanghai",
            "start_window": "03:49-03:51 BJT (inclusive; centre 03:50)",
            "end_window": "21:39-21:41 BJT (inclusive; centre 21:40)",
            "quote": "synchronous historical 1-minute BID/ASK; each window uses medians only on common instrument minutes",
            "price": "midpoint = (bid + ask) / 2",
            "error": "NIFTY midpoint return minus INDA midpoint return, in basis points",
            "contract_selection": india.NIFTY_CONTRACT_SELECTION_VERSION,
            "note": "Fixed Beijing clock times equal US 15:49-15:51 close and 09:39-09:41 open only during US daylight saving time.",
        },
        "quality": {
            "valid_days": len(observations),
            "excluded_days": len(exclusions),
            "exclusions": exclusions,
            "common_minutes": {
                "start_min": min((item.start_common_minutes for item in observations), default=0),
                "end_min": min((item.end_common_minutes for item in observations), default=0),
            },
        },
        "raw_proxy": {
            **error_metrics(item.raw_tracking_error for item in observations),
            "return_correlation": correlation(nifty_returns, inda_returns),
        },
        "whole_sample_beta": {
            "beta": whole_beta,
            "in_sample_error": error_metrics(whole_beta_errors),
            "warning": "Descriptive only: this beta uses the entire sample and must not be treated as a tradable historical forecast.",
        },
        "rolling_beta": {
            "window_days": beta_window,
            "out_of_sample_error": error_metrics(row["adjusted_tracking_error"] for row in rolling_rows),
        },
        "quoted_spreads_bps": {
            name: {
                "mean": statistics.fmean(values) if values else None,
                "median": statistics.median(values) if values else None,
                "p90": percentile(values, 0.90),
                "max": max(values) if values else None,
            }
            for name, values in spreads.items()
        },
    }
    return summary, rolling_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="https://1navs.com", help="read-only source for available 164824 China-session dates")
    parser.add_argument("--start", type=parse_day, help="first China date, YYYYMMDD")
    parser.add_argument("--end", type=parse_day, help="last China date, YYYYMMDD")
    parser.add_argument("--max-days", type=int, default=12, help="maximum eligible China-session dates, newest first")
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", type=int, default=7496)
    parser.add_argument("--ib-client-id", type=int, default=264827)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--request-interval", type=float, default=3.0, help="seconds to wait between completed daily observations")
    parser.add_argument("--ib-request-interval", type=float, default=1.25, help="seconds to wait after each historical BID/ASK request")
    parser.add_argument("--beta-window", type=int, default=10, help="prior valid days used for the out-of-sample beta")
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    return parser.parse_args()


def save_results(runtime: Path, generated_at: datetime, observations: list[Observation], rolling_rows: list[dict[str, Any]], summary: dict[str, Any]) -> tuple[Path, Path]:
    directory = runtime / "results"
    directory.mkdir(parents=True, exist_ok=True)
    stem = generated_at.astimezone(india.SHANGHAI).strftime("bridge-%Y%m%dT%H%M%S")
    json_path, csv_path = directory / f"{stem}.json", directory / f"{stem}.csv"
    rows = [item.to_row() for item in observations]
    payload = {
        "generated_at": generated_at.astimezone(india.SHANGHAI).isoformat(),
        "summary": summary,
        "observations": rows,
        "rolling_beta_observations": rolling_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = list(rows[0]) if rows else ["china_day", "nifty_contract"]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def main() -> int:
    args = parse_args()
    if args.max_days <= 0 or args.beta_window <= 0 or args.request_interval < 0 or args.ib_request_interval < 0:
        raise SystemExit("--max-days and --beta-window must be positive; request intervals must be non-negative")
    now = datetime.now(india.SHANGHAI)
    available = history.public_history_days(args.server, args.timeout, args.start, args.end)
    days = [item for item in available if item < now.date()]
    days = days[-args.max_days:]
    if not days:
        raise SystemExit("no completed China-session dates in the requested range")
    market = history.HistoricalINDAMarket(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
    observations: list[Observation] = []
    exclusions: list[dict[str, str]] = []
    try:
        for index, day in enumerate(days, start=1):
            # A fixed 03:50 BJT timestamp on Monday maps to Sunday afternoon
            # in New York.  It is structurally not a US-close bridge, so do
            # not burn historical-data pacing capacity trying to fetch it.
            if day.weekday() == 0:
                reason = "fixed 03:49-03:51 BJT falls on Sunday in New York; use the preceding Friday close bridge in production"
                exclusions.append({"china_day": day_key(day), "reason": reason})
                print(f"[{index}/{len(days)}] excluded {day_key(day)}: {reason}", file=sys.stderr)
                continue
            try:
                observation = observe_day(market, day, args.ib_request_interval)
            except Exception as exc:  # Preserve the exact date/data-quality reason.
                exclusions.append({"china_day": day_key(day), "reason": str(exc)})
                print(f"[{index}/{len(days)}] excluded {day_key(day)}: {exc}", file=sys.stderr)
            else:
                observations.append(observation)
                print(f"[{index}/{len(days)}] valid {observation.china_day}: raw error {observation.raw_tracking_error * 10_000:+.2f} bp")
            if args.request_interval > 0 and index < len(days):
                time.sleep(args.request_interval)
    finally:
        market.close()
    summary, rolling_rows = build_summary(observations, exclusions, args.beta_window)
    generated_at = datetime.now(india.SHANGHAI)
    json_path, csv_path = save_results(args.runtime_dir, generated_at, observations, rolling_rows, summary)
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "summary": summary}, ensure_ascii=False, indent=2))
    return 0 if observations else 2


if __name__ == "__main__":
    raise SystemExit(main())

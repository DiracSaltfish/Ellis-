#!/usr/bin/env python3
"""Historical diagnostic for the ETF intraday pull-up monitor.

The input is the filtered watchlist bundle produced in the previous task.
This is deliberately dependency-free so it can be rerun on the host without
installing a scientific Python stack.
"""

from __future__ import annotations

import csv
import io
import json
import math
import statistics
import sys
import zipfile
from collections import defaultdict, deque
from pathlib import Path


FAST_THRESHOLD = 0.005  # +0.50% maximum close return in the next 15 minutes
STRONG_THRESHOLD = 0.010  # +1.00% maximum close return in the next 15 minutes
SIGNAL_GAP_BARS = 5
EVENT_LOOKBACK_BARS = 3
MIN_FUTURE_BARS = 5


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def parse_float(value: str) -> float:
    value = value.strip()
    return float(value) if value else 0.0


def normalize_timestamp(value: str) -> tuple[str, str, int]:
    stamp = value.strip().replace("/", "-")
    day = stamp[:10]
    clock = stamp[11:16]
    minute = int(clock[:2]) * 60 + int(clock[3:5])
    return stamp, day, minute


def session_for(minute: int) -> str | None:
    if 570 <= minute <= 690:  # 09:30–11:30
        return "am"
    if 780 <= minute <= 900:  # 13:00–15:00
        return "pm"
    return None


def signal_eligible(session: str, minute: int) -> bool:
    # Match the production signal windows, excluding lunch and closing auction.
    if session == "am":
        return 571 <= minute < 690  # 09:31–11:30; minute bars have no seconds
    return 781 <= minute < 897  # 13:01–14:56


def collapse_alerts(indices: list[int], gap: int = SIGNAL_GAP_BARS) -> list[int]:
    if not indices:
        return []
    result = [indices[0]]
    previous = indices[0]
    for index in indices[1:]:
        if index - previous > gap:
            result.append(index)
        previous = index
    return result


def episodes_from_condition(
    bars: list[dict[str, object]], threshold: float
) -> list[dict[str, object]]:
    """Create forward-looking event episodes used only as a benchmark label.

    A truth episode begins at the first bar that has at least `threshold`
    upside in the next 15 minutes. This is intentionally broad: it measures
    how early each rule can surface a meaningful upcoming move, not investment
    profitability or causality.
    """
    events: list[dict[str, object]] = []
    for session in ("am", "pm"):
        # Keep the benchmark inside the production signal phase. In particular,
        # do not let an afternoon event use the 14:57–15:00 closing auction as
        # its future evidence.
        indices = [
            i
            for i, bar in enumerate(bars)
            if bar["session"] == session
            and signal_eligible(session, int(bar["minute"]))
        ]
        if not indices:
            continue
        qualifying: list[int] = []
        for position, i in enumerate(indices):
            bar = bars[i]
            future = [bars[j] for j in indices[position + 1 : position + 16]]
            if len(future) < MIN_FUTURE_BARS:
                continue
            current_close = float(bar["close"])
            future_max = max(float(item["close"]) for item in future)
            if current_close > 0 and future_max / current_close - 1 >= threshold:
                qualifying.append(i)
        if not qualifying:
            continue

        current: dict[str, object] | None = None
        last_qualifying = -10_000
        positions = {index: position for position, index in enumerate(indices)}
        for i in qualifying:
            position = positions[i]
            last_position = positions.get(last_qualifying)
            if current is None or (last_position is not None and position - last_position > SIGNAL_GAP_BARS):
                if current is not None:
                    events.append(current)
                current = {
                    "session": session,
                    "start_idx": i,
                    "peak_idx": i,
                    "peak_return": 0.0,
                }
            start = int(current["start_idx"])
            start_position = positions[start]
            peak_candidates = indices[start_position : start_position + 31]
            start_close = float(bars[start]["close"])
            peak_idx = max(peak_candidates, key=lambda j: float(bars[j]["close"]))
            peak_return = float(bars[peak_idx]["close"]) / start_close - 1 if start_close > 0 else 0
            current["peak_idx"] = peak_idx
            current["peak_return"] = max(float(current["peak_return"]), peak_return)
            last_qualifying = i
        if current is not None:
            events.append(current)
    return events


def match_rule(
    alert_indices: list[int], events: list[dict[str, object]]
) -> tuple[int, int, list[int]]:
    alerts = collapse_alerts(sorted(alert_indices))
    used_alerts: set[int] = set()
    leads: list[int] = []
    for event in events:
        start = int(event["start_idx"])
        peak = int(event["peak_idx"])
        candidates = [a for a in alerts if start - EVENT_LOOKBACK_BARS <= a <= peak]
        if candidates:
            chosen = candidates[0]
            used_alerts.add(chosen)
            leads.append(max(0, peak - chosen))
    return len(used_alerts), len(alerts), leads


def forward_alert_stats(
    alert_indices: list[int],
    bars: list[dict[str, object]],
    eligible_by_session: dict[str, list[int]],
    threshold: float,
) -> tuple[int, int]:
    """Return valid forward windows and windows reaching the benchmark move."""
    alerts = collapse_alerts(sorted(alert_indices))
    positions = {
        index: position
        for session_indices in eligible_by_session.values()
        for position, index in enumerate(session_indices)
    }
    valid = 0
    hits = 0
    for index in alerts:
        session = str(bars[index]["session"])
        session_indices = eligible_by_session.get(session, [])
        position = positions.get(index)
        if position is None:
            continue
        future = [bars[j] for j in session_indices[position + 1 : position + 16]]
        if len(future) < MIN_FUTURE_BARS:
            continue
        current_close = float(bars[index]["close"])
        if current_close <= 0:
            continue
        valid += 1
        future_max = max(float(item["close"]) for item in future)
        if future_max / current_close - 1 >= threshold:
            hits += 1
    return valid, hits


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def safe_json(value: object) -> object:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {str(k): safe_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_json(v) for v in value]
    return value


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: analyze_etf_pull_model.py BUNDLE_ZIP OUTPUT_DIR", file=sys.stderr)
        return 2
    bundle_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(bundle_path) as bundle:
        manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
        symbols = manifest["target_symbols"]
        source_latest = manifest["latest_archive_date"]

        daily_rows: list[dict[str, object]] = []
        event_rows: list[dict[str, object]] = []
        symbol_rows: list[dict[str, object]] = []
        rule_counts = {
            "current_pull_proxy": {"fast": [0, 0, []], "strong": [0, 0, []]},
            "breakout_15m": {"fast": [0, 0, []], "strong": [0, 0, []]},
            "volume_confirmed": {"fast": [0, 0, []], "strong": [0, 0, []]},
            "adaptive_hybrid": {"fast": [0, 0, []], "strong": [0, 0, []]},
        }
        alert_outcomes = {
            rule: {"fast": [0, 0], "strong": [0, 0]}
            for rule in rule_counts
        }
        all_days = 0
        days_with_fast = 0
        days_with_strong = 0
        zero_row_symbols: list[str] = []

        for symbol in symbols:
            member = f"csv/{symbol}.csv"
            with bundle.open(member) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                reader = csv.reader(text)
                header = next(reader, None)
                if not header:
                    zero_row_symbols.append(symbol)
                    continue
                try:
                    time_col = header.index("时间")
                    close_col = header.index("收盘价")
                    open_col = header.index("开盘价")
                    high_col = header.index("最高价")
                    low_col = header.index("最低价")
                    volume_col = header.index("成交量")
                    amount_col = header.index("成交额")
                except ValueError as exc:
                    raise ValueError(f"{symbol}: missing expected column: {exc}") from exc

                by_day: dict[str, list[dict[str, object]]] = defaultdict(list)
                for row in reader:
                    if not row or row[0].strip() == "时间":
                        continue
                    timestamp, day, minute = normalize_timestamp(row[time_col])
                    session = session_for(minute)
                    if session is None:
                        continue
                    by_day[day].append(
                        {
                            "timestamp": timestamp,
                            "day": day,
                            "minute": minute,
                            "session": session,
                            "open": parse_float(row[open_col]),
                            "close": parse_float(row[close_col]),
                            "high": parse_float(row[high_col]),
                            "low": parse_float(row[low_col]),
                            "volume": parse_float(row[volume_col]),
                            "amount": parse_float(row[amount_col]),
                        }
                    )

            if not by_day:
                zero_row_symbols.append(symbol)
                continue

            previous_close: float | None = None
            previous_volumes: list[float] = []
            previous_amounts: list[float] = []
            cumulative_history: dict[tuple[str, int], deque[float]] = defaultdict(
                lambda: deque(maxlen=20)
            )
            prior_abs_r3: deque[float] = deque(maxlen=800)
            symbol_fast_events = 0
            symbol_strong_events = 0
            symbol_day_count = 0
            symbol_alerts = defaultdict(int)
            symbol_matched = defaultdict(int)
            symbol_leads = defaultdict(list)
            daily_amounts_for_bucket: list[float] = []
            r3_abs_values: list[float] = []
            r3_up_values: list[float] = []

            for day in sorted(by_day):
                bars = sorted(by_day[day], key=lambda item: int(item["minute"]))
                if not bars:
                    continue
                all_days += 1
                symbol_day_count += 1
                daily_volume = sum(float(bar["volume"]) for bar in bars)
                daily_amount = sum(float(bar["amount"]) for bar in bars)
                daily_amounts_for_bucket.append(daily_amount)
                first_open = float(bars[0]["open"])
                last_close = float(bars[-1]["close"])
                day_high = max(float(bar["high"]) for bar in bars)
                day_low = min(float(bar["low"]) for bar in bars)
                vol_base_5 = median(previous_volumes[-5:])
                vol_base_20 = median(previous_volumes[-20:])
                amt_base_5 = median(previous_amounts[-5:])
                amt_base_20 = median(previous_amounts[-20:])
                volume_ratio_5 = daily_volume / vol_base_5 if vol_base_5 else None
                volume_ratio_20 = daily_volume / vol_base_20 if vol_base_20 else None
                amount_ratio_5 = daily_amount / amt_base_5 if amt_base_5 else None
                amount_ratio_20 = daily_amount / amt_base_20 if amt_base_20 else None
                day_return = last_close / previous_close - 1 if previous_close and previous_close > 0 else None
                day_high_return = day_high / previous_close - 1 if previous_close and previous_close > 0 else None
                day_low_return = day_low / previous_close - 1 if previous_close and previous_close > 0 else None

                # Live-style cumulative volume ratio against prior days at the same session/bar index.
                session_positions: dict[str, int] = defaultdict(int)
                cumulative_by_session: dict[str, float] = defaultdict(float)
                for bar in bars:
                    session = str(bar["session"])
                    pos = session_positions[session]
                    session_positions[session] += 1
                    cumulative_by_session[session] += float(bar["volume"])
                    history = cumulative_history[(session, pos)]
                    prior_cum = list(history)
                    bar["cum_volume_ratio"] = (
                        cumulative_by_session[session] / statistics.median(prior_cum)
                        if prior_cum and statistics.median(prior_cum) > 0
                        else None
                    )
                    bar["session_pos"] = pos
                    history.append(cumulative_by_session[session])

                fast_events = episodes_from_condition(bars, FAST_THRESHOLD)
                strong_events = episodes_from_condition(bars, STRONG_THRESHOLD)
                symbol_fast_events += len(fast_events)
                symbol_strong_events += len(strong_events)
                if fast_events:
                    days_with_fast += 1
                if strong_events:
                    days_with_strong += 1

                # Minute-level rule features and alert candidates.
                alerts: dict[str, list[int]] = defaultdict(list)
                eligible_by_session = {
                    session: [
                        i
                        for i, bar in enumerate(bars)
                        if bar["session"] == session
                        and signal_eligible(session, int(bar["minute"]))
                    ]
                    for session in ("am", "pm")
                }
                for session in ("am", "pm"):
                    indices = [i for i, bar in enumerate(bars) if bar["session"] == session]
                    if not indices:
                        continue
                    start_i, end_i = indices[0], indices[-1] + 1
                    closes = [float(bar["close"]) for bar in bars]
                    session_start = start_i
                    for i in range(start_i, end_i):
                        bar = bars[i]
                        if i <= session_start or not signal_eligible(session, int(bar["minute"])):
                            continue
                        close = closes[i]
                        r1 = close / closes[i - 1] - 1 if closes[i - 1] > 0 else 0.0
                        r3 = None
                        r5 = None
                        r15 = None
                        if i - session_start >= 3:
                            base = min(closes[i - 3 : i])
                            r3 = close / base - 1 if base > 0 else 0.0
                        if i - session_start >= 5:
                            base = min(closes[i - 5 : i])
                            r5 = close / base - 1 if base > 0 else 0.0
                        if i - session_start >= 15:
                            base = min(closes[i - 15 : i])
                            r15 = close / base - 1 if base > 0 else 0.0
                        if r3 is not None:
                            r3_abs_values.append(abs(r3))
                            if r3 > 0:
                                r3_up_values.append(r3)
                        volume_ratio = finite(bar.get("cum_volume_ratio"))
                        current_proxy = (r3 is not None and r3 > 0.004) or (r5 is not None and r5 > 0.008)
                        breakout = (
                            r15 is not None
                            and r15 >= 0.005
                            and close > max(closes[i - 15 : i])
                        )
                        volume_confirmed = (
                            volume_ratio is not None
                            and volume_ratio >= 1.5
                            and (
                                (r3 is not None and r3 >= 0.0025)
                                or (r5 is not None and r5 >= 0.005)
                                or r1 >= 0.002
                            )
                        )
                        baseline = median(list(prior_abs_r3)[-120:])
                        adaptive_move = r3 is not None and baseline is not None and r3 >= max(0.0025, 3 * baseline)
                        adaptive_hybrid = (
                            current_proxy
                            or volume_confirmed
                            or (breakout and volume_ratio is not None and volume_ratio >= 1.2)
                            or (adaptive_move and volume_ratio is not None and volume_ratio >= 1.2)
                        )
                        if current_proxy:
                            alerts["current_pull_proxy"].append(i)
                        if breakout:
                            alerts["breakout_15m"].append(i)
                        if volume_confirmed:
                            alerts["volume_confirmed"].append(i)
                        if adaptive_hybrid:
                            alerts["adaptive_hybrid"].append(i)
                        if r3 is not None:
                            prior_abs_r3.append(abs(r3))

                for rule, indices in alerts.items():
                    fast_valid, fast_hits = forward_alert_stats(
                        indices, bars, eligible_by_session, FAST_THRESHOLD
                    )
                    strong_valid, strong_hits = forward_alert_stats(
                        indices, bars, eligible_by_session, STRONG_THRESHOLD
                    )
                    alert_outcomes[rule]["fast"][0] += fast_valid
                    alert_outcomes[rule]["fast"][1] += fast_hits
                    alert_outcomes[rule]["strong"][0] += strong_valid
                    alert_outcomes[rule]["strong"][1] += strong_hits
                    symbol_alerts[rule] += len(collapse_alerts(indices))
                    fast_matched, fast_alert_count, fast_leads = match_rule(indices, fast_events)
                    strong_matched, strong_alert_count, strong_leads = match_rule(indices, strong_events)
                    rule_counts[rule]["fast"][0] += fast_matched
                    rule_counts[rule]["fast"][1] += fast_alert_count
                    rule_counts[rule]["fast"][2].extend(fast_leads)
                    rule_counts[rule]["strong"][0] += strong_matched
                    rule_counts[rule]["strong"][1] += strong_alert_count
                    rule_counts[rule]["strong"][2].extend(strong_leads)
                    symbol_matched[(rule, "fast")] += fast_matched
                    symbol_matched[(rule, "strong")] += strong_matched
                    symbol_leads[(rule, "fast")].extend(fast_leads)
                    symbol_leads[(rule, "strong")].extend(strong_leads)

                fast_count = len(fast_events)
                strong_count = len(strong_events)
                daily_rows.append(
                    {
                        "symbol": symbol,
                        "date": day,
                        "daily_volume": round(daily_volume, 4),
                        "daily_amount": round(daily_amount, 4),
                        "volume_ratio_5": finite(volume_ratio_5),
                        "volume_ratio_20": finite(volume_ratio_20),
                        "amount_ratio_5": finite(amount_ratio_5),
                        "amount_ratio_20": finite(amount_ratio_20),
                        "day_return": finite(day_return),
                        "day_high_return": finite(day_high_return),
                        "day_low_return": finite(day_low_return),
                        "fast_event_count": fast_count,
                        "strong_event_count": strong_count,
                    }
                )
                for strength, events in (("fast", fast_events), ("strong", strong_events)):
                    for event in events:
                        start_i = int(event["start_idx"])
                        peak_i = int(event["peak_idx"])
                        event_rows.append(
                            {
                                "symbol": symbol,
                                "date": day,
                                "session": event["session"],
                                "strength": strength,
                                "start_time": bars[start_i]["timestamp"],
                                "peak_time": bars[peak_i]["timestamp"],
                                "peak_return_from_start": round(float(event["peak_return"]), 6),
                                "volume_ratio_5": finite(volume_ratio_5),
                                "volume_ratio_20": finite(volume_ratio_20),
                                "daily_amount": round(daily_amount, 4),
                                "day_return": finite(day_return),
                            }
                        )
                previous_close = last_close
                previous_volumes.append(daily_volume)
                previous_amounts.append(daily_amount)

            symbol_rows.append(
                {
                    "symbol": symbol,
                    "days": symbol_day_count,
                    "fast_events": symbol_fast_events,
                    "strong_events": symbol_strong_events,
                    "median_daily_amount": finite(median(daily_amounts_for_bucket)),
                    "p95_abs_3m_move": finite(percentile(r3_abs_values, 0.95)),
                    "p95_up_3m_move": finite(percentile(r3_up_values, 0.95)),
                    "current_fast_matched": symbol_matched[("current_pull_proxy", "fast")],
                    "current_strong_matched": symbol_matched[("current_pull_proxy", "strong")],
                    "hybrid_fast_matched": symbol_matched[("adaptive_hybrid", "fast")],
                    "hybrid_strong_matched": symbol_matched[("adaptive_hybrid", "strong")],
                    "hybrid_fast_lead_median_bars": finite(median(symbol_leads[("adaptive_hybrid", "fast")])),
                    "hybrid_strong_lead_median_bars": finite(median(symbol_leads[("adaptive_hybrid", "strong")])),
                }
            )

        # Daily volume screen metrics use complete trailing-5-day baselines.
        daily_volume_screen: list[dict[str, object]] = []
        for threshold in (1.25, 1.5, 2.0, 3.0):
            eligible = [row for row in daily_rows if row["volume_ratio_5"] is not None]
            candidate = [row for row in eligible if float(row["volume_ratio_5"]) >= threshold]
            candidate_fast = [row for row in candidate if int(row["fast_event_count"]) > 0]
            candidate_strong = [row for row in candidate if int(row["strong_event_count"]) > 0]
            all_fast_days = [row for row in eligible if int(row["fast_event_count"]) > 0]
            all_strong_days = [row for row in eligible if int(row["strong_event_count"]) > 0]
            daily_volume_screen.append(
                {
                    "threshold_ratio_5": threshold,
                    "eligible_symbol_days": len(eligible),
                    "candidate_symbol_days": len(candidate),
                    "candidate_share": len(candidate) / len(eligible) if eligible else None,
                    "fast_event_day_rate": len(candidate_fast) / len(candidate) if candidate else None,
                    "strong_event_day_rate": len(candidate_strong) / len(candidate) if candidate else None,
                    "fast_event_day_recall": len(candidate_fast) / len(all_fast_days) if all_fast_days else None,
                    "strong_event_day_recall": len(candidate_strong) / len(all_strong_days) if all_strong_days else None,
                }
            )

        rule_metrics: list[dict[str, object]] = []
        for rule, by_strength in rule_counts.items():
            for strength, (matched, alert_count, leads) in by_strength.items():
                total_events = sum(
                    int(row[f"{strength}_event_count"])
                    for row in daily_rows
                )
                rule_metrics.append(
                    {
                        "rule": rule,
                        "strength": strength,
                        "truth_events": total_events,
                        "matched_events": matched,
                        "event_recall": matched / total_events if total_events else None,
                        "alert_episodes": alert_count,
                        "matched_alert_precision": matched / alert_count if alert_count else None,
                        "valid_alerts_with_future": alert_outcomes[rule][strength][0],
                        "forward_hit_rate": (
                            alert_outcomes[rule][strength][1]
                            / alert_outcomes[rule][strength][0]
                            if alert_outcomes[rule][strength][0]
                            else None
                        ),
                        "median_lead_bars_to_peak": finite(median(leads)),
                        "p90_lead_bars_to_peak": finite(percentile(leads, 0.90)),
                    }
                )

        # Rank concrete historical examples by strong event size and volume shock.
        examples = sorted(
            [row for row in event_rows if row["strength"] == "strong"],
            key=lambda row: (
                float(row["peak_return_from_start"]),
                float(row["volume_ratio_5"] or 0),
            ),
            reverse=True,
        )[:30]

        amount_values = [float(row["median_daily_amount"]) for row in symbol_rows if row["median_daily_amount"] is not None]
        amount_low = percentile(amount_values, 1 / 3)
        amount_high = percentile(amount_values, 2 / 3)
        for row in symbol_rows:
            amount = row["median_daily_amount"]
            if amount is None or amount_low is None or amount_high is None:
                row["liquidity_bucket"] = "unknown"
            elif float(amount) <= amount_low:
                row["liquidity_bucket"] = "low"
            elif float(amount) <= amount_high:
                row["liquidity_bucket"] = "mid"
            else:
                row["liquidity_bucket"] = "high"

        report = {
            "analysis": {
                "source_bundle": str(bundle_path),
                "source_latest_date": source_latest,
                "start_date": manifest["start_date_inclusive"],
                "target_count": len(symbols),
                "symbols_with_rows": len(symbols) - len(zero_row_symbols),
                "zero_row_symbols": zero_row_symbols,
                "symbol_days": all_days,
                "fast_event_days": days_with_fast,
                "strong_event_days": days_with_strong,
                "fast_event_episodes": sum(int(row["fast_event_count"]) for row in daily_rows),
                "strong_event_episodes": sum(int(row["strong_event_count"]) for row in daily_rows),
                "fast_threshold_definition": "future maximum close return >= 0.50% within next 15 minutes, same session",
                "strong_threshold_definition": "future maximum close return >= 1.00% within next 15 minutes, same session",
                "current_proxy_definition": "price close vs prior 3-minute low > 0.40% OR prior 5-minute low > 0.80%",
                "daily_volume_definition": "daily volume / median of previous 5 available trading days for the same symbol",
                "live_volume_definition": "running session volume / median running session volume at same bar position over prior 20 days",
                "time_handling": "source timestamps normalized from YYYY/MM/DD or YYYY-MM-DD; analysis uses local exchange session minutes",
                "caveat": "historical minute K has no IOPV, bid1, order-book depth, source quality flags, or feed gaps; current premium/order-book model cannot be replayed exactly",
            },
            "daily_volume_screen": daily_volume_screen,
            "rule_metrics": rule_metrics,
            "top_strong_examples": examples,
        }

    write_csv(
        output_dir / "daily_stats.csv",
        daily_rows,
        [
            "symbol", "date", "daily_volume", "daily_amount", "volume_ratio_5", "volume_ratio_20",
            "amount_ratio_5", "amount_ratio_20", "day_return", "day_high_return", "day_low_return",
            "fast_event_count", "strong_event_count",
        ],
    )
    write_csv(
        output_dir / "event_episodes.csv",
        event_rows,
        [
            "symbol", "date", "session", "strength", "start_time", "peak_time", "peak_return_from_start",
            "volume_ratio_5", "volume_ratio_20", "daily_amount", "day_return",
        ],
    )
    write_csv(
        output_dir / "symbol_stats.csv",
        symbol_rows,
        [
            "symbol", "days", "fast_events", "strong_events", "median_daily_amount", "p95_abs_3m_move",
            "p95_up_3m_move", "current_fast_matched", "current_strong_matched", "hybrid_fast_matched",
            "hybrid_strong_matched", "hybrid_fast_lead_median_bars", "hybrid_strong_lead_median_bars",
            "liquidity_bucket",
        ],
    )
    write_csv(
        output_dir / "rule_metrics.csv",
        rule_metrics,
        [
            "rule", "strength", "truth_events", "matched_events", "event_recall", "alert_episodes",
            "matched_alert_precision", "valid_alerts_with_future", "forward_hit_rate",
            "median_lead_bars_to_peak", "p90_lead_bars_to_peak",
        ],
    )
    (output_dir / "summary.json").write_text(
        json.dumps(safe_json(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(safe_json(report["analysis"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

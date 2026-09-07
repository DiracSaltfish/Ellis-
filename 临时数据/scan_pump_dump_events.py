#!/usr/bin/env python3
"""Scan ETF minute bars for sharp intraday pump-then-dump episodes.

The event label deliberately uses future highs/lows.  The candidate warning
rules use only information available at the alert minute and are evaluated
only on the rising leg, before the labeled peak and dump.
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

sys.path.insert(0, str(Path(__file__).parent))
from analyze_etf_pull_model import (  # noqa: E402
    collapse_alerts,
    median,
    normalize_timestamp,
    parse_float,
    session_for,
    signal_eligible,
)


EXCLUDED_SYMBOLS = {"513310.SH"}
CORE_RISE = 0.008
CORE_DROP = 0.006
BROAD_RISE = 0.006
BROAD_DROP = 0.004
EXTREME_RISE = 0.015
EXTREME_DROP = 0.010
LEG_MAX = 10
EVENT_CLUSTER_GAP = 10
SIGNAL_GAP = 5


def pct(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def read_symbol(bundle: zipfile.ZipFile, symbol: str) -> dict[str, list[dict[str, object]]]:
    by_day: dict[str, list[dict[str, object]]] = defaultdict(list)
    with bundle.open(f"csv/{symbol}.csv") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        for row in reader:
            stamp, day, minute = normalize_timestamp(row.get("时间", ""))
            session = session_for(minute)
            if session is None:
                continue
            by_day[day].append({
                "timestamp": stamp,
                "minute": minute,
                "session": session,
                "open": parse_float(row.get("开盘价", "0")),
                "close": parse_float(row.get("收盘价", "0")),
                "high": parse_float(row.get("最高价", "0")),
                "low": parse_float(row.get("最低价", "0")),
                "volume": parse_float(row.get("成交量", "0")),
                "amount": parse_float(row.get("成交额", "0")),
            })
    for day in by_day:
        by_day[day].sort(key=lambda row: int(row["minute"]))
    return by_day


def eligible_session_rows(day_rows: list[dict[str, object]], session: str) -> list[dict[str, object]]:
    return [
        row for row in day_rows
        if row["session"] == session and signal_eligible(session, int(row["minute"]))
    ]


def detect_candidates(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Find local sharp-rise / sharp-drop shapes using future data for labels."""
    candidates: list[dict[str, object]] = []
    n = len(rows)
    closes = [float(row["close"]) for row in rows]
    highs = [float(row["high"]) for row in rows]
    lows = [float(row["low"]) for row in rows]
    for peak in range(3, n - 2):
        base_range = range(max(0, peak - LEG_MAX), peak - 1)
        base = min(base_range, key=lambda i: closes[i])
        rise_bars = peak - base
        if rise_bars < 2 or rise_bars > LEG_MAX or closes[base] <= 0:
            continue
        rise = highs[peak] / closes[base] - 1
        if rise < BROAD_RISE:
            continue
        trough_range = range(peak + 1, min(n, peak + LEG_MAX + 1))
        if len(list(trough_range)) < 2 or highs[peak] <= 0:
            continue
        trough = min(trough_range, key=lambda i: lows[i])
        drop = 1 - lows[trough] / highs[peak]
        if drop < BROAD_DROP:
            continue
        tier = "extreme" if rise >= EXTREME_RISE and drop >= EXTREME_DROP else (
            "core" if rise >= CORE_RISE and drop >= CORE_DROP else "broad"
        )
        candidates.append({
            "base_idx": base,
            "peak_idx": peak,
            "trough_idx": trough,
            "rise_pct": rise,
            "drop_pct": drop,
            "rise_bars": rise_bars,
            "drop_bars": trough - peak,
            "tier": tier,
        })

    # Several adjacent peak bars describe the same move. Keep the strongest
    # candidate in each cluster so the event count is usable for analysis.
    selected: list[dict[str, object]] = []
    cluster: list[dict[str, object]] = []
    last_peak = -10_000
    for item in candidates:
        peak = int(item["peak_idx"])
        if cluster and peak - last_peak > EVENT_CLUSTER_GAP:
            selected.append(max(cluster, key=lambda x: (min(float(x["rise_pct"]), float(x["drop_pct"])), float(x["rise_pct"]) + float(x["drop_pct"]))))
            cluster = []
        cluster.append(item)
        last_peak = peak
    if cluster:
        selected.append(max(cluster, key=lambda x: (min(float(x["rise_pct"]), float(x["drop_pct"])), float(x["rise_pct"]) + float(x["drop_pct"]))))
    return selected


def build_features(rows: list[dict[str, object]]) -> dict[int, dict[str, float | bool | None]]:
    """Build real-time features; every feature uses current/past bars only."""
    n = len(rows)
    closes = [float(row["close"]) for row in rows]
    highs = [float(row["high"]) for row in rows]
    lows = [float(row["low"]) for row in rows]
    volumes = [float(row["volume"]) for row in rows]
    amounts = [float(row["amount"]) for row in rows]
    prior_abs_r3: deque[float] = deque(maxlen=120)
    prior_abs_r5: deque[float] = deque(maxlen=120)
    prior_ranges: deque[float] = deque(maxlen=120)
    result: dict[int, dict[str, float | bool | None]] = {}
    cum_volume = 0.0
    cum_amount = 0.0
    for i in range(n):
        cum_volume += volumes[i]
        cum_amount += amounts[i] if amounts[i] > 0 else closes[i] * volumes[i]
        close = closes[i]
        r1 = close / closes[i - 1] - 1 if i >= 1 and closes[i - 1] > 0 else None
        r3_simple = close / closes[i - 3] - 1 if i >= 3 and closes[i - 3] > 0 else None
        r5_simple = close / closes[i - 5] - 1 if i >= 5 and closes[i - 5] > 0 else None
        r3_low = close / min(closes[i - 3:i]) - 1 if i >= 3 and min(closes[i - 3:i]) > 0 else None
        r5_low = close / min(closes[i - 5:i]) - 1 if i >= 5 and min(closes[i - 5:i]) > 0 else None
        prior_high_15 = max(closes[i - 15:i]) if i >= 15 else None
        range_pct = (highs[i] - lows[i]) / closes[i - 1] if i >= 1 and closes[i - 1] > 0 else None
        base3 = median(list(prior_abs_r3))
        base5 = median(list(prior_abs_r5))
        range_base = median(list(prior_ranges))
        adaptive_k2_5 = bool(
            (r3_low is not None and base3 is not None and r3_low >= max(0.0015, 2.5 * base3))
            or (r5_low is not None and base5 is not None and r5_low >= max(0.0025, 2.2 * base5))
        )
        fixed_momentum = bool(
            (r3_simple is not None and r3_simple >= 0.003)
            or (r5_simple is not None and r5_simple >= 0.005)
        )
        current_proxy = bool(
            (r3_low is not None and r3_low > 0.004)
            or (r5_low is not None and r5_low > 0.008)
        )
        breakout_momentum = bool(
            fixed_momentum and prior_high_15 is not None and close > prior_high_15
        )
        range_expansion = bool(
            range_pct is not None and range_base is not None
            and r1 is not None and r1 > 0
            and range_pct >= max(0.0015, 2.0 * range_base)
        )
        vwap = cum_amount / cum_volume if cum_volume > 0 else None
        result[i] = {
            "r1": r1,
            "r3_simple": r3_simple,
            "r5_simple": r5_simple,
            "r3_low": r3_low,
            "r5_low": r5_low,
            "range_pct": range_pct,
            "price_vs_vwap": close / vwap - 1 if vwap and vwap > 0 else None,
            "adaptive_k2_5": adaptive_k2_5,
            "fixed_momentum": fixed_momentum,
            "current_proxy": current_proxy,
            "breakout_momentum": breakout_momentum,
            "range_expansion": range_expansion,
            "early_pump_radar": adaptive_k2_5 or fixed_momentum or range_expansion,
            "volume": volumes[i],
            "cum_volume": cum_volume,
        }
        if r3_low is not None:
            prior_abs_r3.append(abs(r3_low))
        if r5_low is not None:
            prior_abs_r5.append(abs(r5_low))
        if range_pct is not None:
            prior_ranges.append(abs(range_pct))
    return result


def first_prepeak_alert(
    alert_indices: list[int], base_idx: int, peak_idx: int
) -> int | None:
    # Filter the event window before collapsing alerts. Otherwise an earlier
    # unrelated alert can absorb the first valid alert within this event.
    candidates = [idx for idx in sorted(alert_indices) if base_idx - 3 <= idx <= peak_idx]
    alerts = collapse_alerts(candidates, gap=SIGNAL_GAP)
    return alerts[0] if alerts else None


def event_row(
    symbol: str,
    day: str,
    session: str,
    rows: list[dict[str, object]],
    event: dict[str, object],
    features: dict[int, dict[str, float | bool | None]],
    session_volume_ratio_5: float | None,
) -> dict[str, object]:
    base = int(event["base_idx"])
    peak = int(event["peak_idx"])
    trough = int(event["trough_idx"])
    row: dict[str, object] = {
        "symbol": symbol,
        "date": day,
        "session": session,
        "start_time": rows[base]["timestamp"],
        "peak_time": rows[peak]["timestamp"],
        "trough_time": rows[trough]["timestamp"],
        "rise_pct": round(float(event["rise_pct"]), 6),
        "drop_pct": round(float(event["drop_pct"]), 6),
        "roundtrip_pct": round(float(rows[trough]["low"]) / float(rows[base]["close"]) - 1, 6),
        "rise_bars": int(event["rise_bars"]),
        "drop_bars": int(event["drop_bars"]),
        "tier": event["tier"],
        "session_volume_ratio_5": round(session_volume_ratio_5, 4) if session_volume_ratio_5 is not None else "",
    }
    for model in ("adaptive_k2_5", "fixed_momentum", "current_proxy", "breakout_momentum", "range_expansion", "early_pump_radar"):
        idxs = [i for i, f in features.items() if bool(f.get(model))]
        trigger = first_prepeak_alert(idxs, base, peak)
        row[f"trigger_{model}"] = rows[trigger]["timestamp"] if trigger is not None else ""
        row[f"trigger_offset_{model}_bars"] = base - trigger if trigger is not None else ""
        row[f"trigger_lead_{model}_bars"] = peak - trigger if trigger is not None else ""
    return row


def safe_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: scan_pump_dump_events.py BUNDLE_ZIP OUTPUT_DIR", file=sys.stderr)
        return 2
    bundle_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)
    event_rows: list[dict[str, object]] = []
    daily_rows: list[dict[str, object]] = []
    symbol_rows: list[dict[str, object]] = []
    model_names = ("adaptive_k2_5", "fixed_momentum", "current_proxy", "breakout_momentum", "range_expansion", "early_pump_radar")
    model_alerts = {model: [] for model in model_names}
    model_event_leads = {model: [] for model in model_names}
    model_event_offsets = {model: [] for model in model_names}
    model_event_matches = {model: 0 for model in model_names}
    model_raw_alert_bars = {model: 0 for model in model_names}
    model_alert_episodes = {model: 0 for model in model_names}
    source_symbol_count = 0
    source_rows = 0
    source_days = set()
    observed_symbol_days = 0

    with zipfile.ZipFile(bundle_path) as bundle:
        manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
        for symbol in manifest["target_symbols"]:
            if symbol in EXCLUDED_SYMBOLS:
                continue
            by_day = read_symbol(bundle, symbol)
            if not by_day:
                continue
            source_symbol_count += 1
            source_rows += sum(len(rows) for rows in by_day.values())
            source_days.update(by_day)
            session_history: dict[str, deque[float]] = {"am": deque(maxlen=5), "pm": deque(maxlen=5)}
            symbol_event_rows: list[dict[str, object]] = []
            for day in sorted(by_day):
                observed_symbol_days += 1
                day_rows = by_day[day]
                for session in ("am", "pm"):
                    rows = eligible_session_rows(day_rows, session)
                    if len(rows) < 10:
                        continue
                    session_volume = sum(float(row["volume"]) for row in rows)
                    history = list(session_history[session])
                    session_volume_ratio_5 = session_volume / statistics.median(history) if history and statistics.median(history) > 0 else None
                    features = build_features(rows)
                    candidates = detect_candidates(rows)
                    for candidate in candidates:
                        row = event_row(symbol, day, session, rows, candidate, features, session_volume_ratio_5)
                        event_rows.append(row)
                        symbol_event_rows.append(row)
                        for model in model_names:
                            trigger = safe_float(row.get(f"trigger_lead_{model}_bars"))
                            if row.get(f"trigger_{model}"):
                                model_event_matches[model] += 1
                                if trigger is not None:
                                    model_event_leads[model].append(trigger)
                                offset = safe_float(row.get(f"trigger_offset_{model}_bars"))
                                if offset is not None:
                                    model_event_offsets[model].append(offset)
                    for model in model_names:
                        alert_indices = [i for i, f in features.items() if bool(f.get(model))]
                        model_raw_alert_bars[model] += len(alert_indices)
                        model_alert_episodes[model] += len(collapse_alerts(alert_indices, gap=SIGNAL_GAP))
                    session_history[session].append(session_volume)
                daily_rows.append({
                    "symbol": symbol,
                    "date": day,
                    "broad_events": sum(1 for row in symbol_event_rows if row["date"] == day and row["tier"] == "broad"),
                    "core_events": sum(1 for row in symbol_event_rows if row["date"] == day and row["tier"] in {"core", "extreme"}),
                    "extreme_events": sum(1 for row in symbol_event_rows if row["date"] == day and row["tier"] == "extreme"),
                })
            if symbol_event_rows:
                core = [row for row in symbol_event_rows if row["tier"] in {"core", "extreme"}]
                symbol_rows.append({
                    "symbol": symbol,
                    "broad_events": len(symbol_event_rows),
                    "core_events": len(core),
                    "extreme_events": sum(row["tier"] == "extreme" for row in symbol_event_rows),
                    "median_rise_pct": median([float(row["rise_pct"]) for row in symbol_event_rows]),
                    "median_drop_pct": median([float(row["drop_pct"]) for row in symbol_event_rows]),
                    "median_rise_bars": median([float(row["rise_bars"]) for row in symbol_event_rows]),
                    "median_drop_bars": median([float(row["drop_bars"]) for row in symbol_event_rows]),
                })

    event_count = len(event_rows)
    core_rows = [row for row in event_rows if row["tier"] in {"core", "extreme"}]
    metrics: list[dict[str, object]] = []
    for model in model_names:
        alerts = sum(1 for row in event_rows if row.get(f"trigger_{model}"))
        matched = model_event_matches[model]
        metrics.append({
            "model": model,
            "event_scope": "broad_all" if event_count else "broad_all",
            "events": event_count,
            "matched_events": matched,
            "event_recall": matched / event_count if event_count else None,
            "alerted_event_rows": alerts,
            "raw_alert_bars": model_raw_alert_bars[model],
            "alert_episodes": model_alert_episodes[model],
            "alert_episodes_per_observed_symbol_day": model_alert_episodes[model] / observed_symbol_days if observed_symbol_days else None,
            "median_lead_to_peak_bars": median(model_event_leads[model]),
            "p10_lead_to_peak_bars": percentile(model_event_leads[model], 0.10),
            "median_offset_to_rise_start_bars": median(model_event_offsets[model]),
        })
        core_matched = sum(1 for row in core_rows if row.get(f"trigger_{model}"))
        metrics.append({
            "model": model,
            "event_scope": "core_plus_extreme",
            "events": len(core_rows),
            "matched_events": core_matched,
            "event_recall": core_matched / len(core_rows) if core_rows else None,
            "alerted_event_rows": core_matched,
            "raw_alert_bars": model_raw_alert_bars[model],
            "alert_episodes": model_alert_episodes[model],
            "alert_episodes_per_observed_symbol_day": model_alert_episodes[model] / observed_symbol_days if observed_symbol_days else None,
            "median_lead_to_peak_bars": median([float(row[f"trigger_lead_{model}_bars"]) for row in core_rows if row.get(f"trigger_{model}")]),
            "p10_lead_to_peak_bars": percentile([float(row[f"trigger_lead_{model}_bars"]) for row in core_rows if row.get(f"trigger_{model}")], 0.10),
            "median_offset_to_rise_start_bars": median([float(row[f"trigger_offset_{model}_bars"]) for row in core_rows if row.get(f"trigger_{model}")]),
        })

    def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    write_csv(output_dir / "pump_dump_events.csv", event_rows)
    write_csv(output_dir / "pump_dump_daily_summary.csv", daily_rows)
    write_csv(output_dir / "pump_dump_symbol_summary.csv", sorted(symbol_rows, key=lambda r: int(r["core_events"]), reverse=True))
    write_csv(output_dir / "realtime_model_metrics.csv", metrics)

    core_trigger_rows = [row for row in core_rows]
    write_csv(output_dir / "core_event_realtime_triggers.csv", core_trigger_rows)

    tier_counts = {tier: sum(row["tier"] == tier for row in event_rows) for tier in ("broad", "core", "extreme")}
    session_counts = {session: sum(row["session"] == session for row in event_rows) for session in ("am", "pm")}
    rise_values = [float(row["rise_pct"]) for row in event_rows]
    drop_values = [float(row["drop_pct"]) for row in event_rows]
    rise_bars = [float(row["rise_bars"]) for row in event_rows]
    drop_bars = [float(row["drop_bars"]) for row in event_rows]
    no_volume_count = sum(
        safe_float(row.get("session_volume_ratio_5")) is not None
        and safe_float(row.get("session_volume_ratio_5")) < 1.2
        for row in event_rows
    )
    report = {
        "source_bundle": str(bundle_path),
        "source_range": [manifest["start_date_inclusive"], manifest["latest_archive_date"]],
        "source_symbols_with_rows_after_exclusion": source_symbol_count,
        "source_rows_in_intraday_sessions_after_exclusion": source_rows,
        "source_days_after_exclusion": len(source_days),
        "excluded_symbols": sorted(EXCLUDED_SYMBOLS),
        "event_definition": {
            "broad": "local peak with rise >= 0.60% from the lowest close in the prior <=10 eligible minutes, then drop >= 0.40% from peak high to a low within the next <=10 eligible minutes",
            "core": "rise >= 0.80% and subsequent drop >= 0.60% under the same <=10-minute legs",
            "extreme": "rise >= 1.50% and subsequent drop >= 1.00% under the same <=10-minute legs",
            "cluster_gap_bars": EVENT_CLUSTER_GAP,
            "closing_auction_excluded": True,
        },
        "event_counts": tier_counts,
        "session_counts": session_counts,
        "shape_summary": {
            "median_rise_pct": median(rise_values),
            "p90_rise_pct": percentile(rise_values, 0.90),
            "median_drop_pct": median(drop_values),
            "p90_drop_pct": percentile(drop_values, 0.90),
            "median_rise_bars": median(rise_bars),
            "median_drop_bars": median(drop_bars),
            "fraction_with_session_volume_ratio_below_1_2": no_volume_count / event_count if event_count else None,
        },
        "realtime_rule_note": "All realtime model features use only current and prior bars; future high/low is used only to create labels and to score whether a trigger occurred before the labeled peak.",
        "models": model_names,
    }
    (output_dir / "pump_dump_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "events": event_count, "core_events": len(core_rows), "symbols": source_symbol_count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

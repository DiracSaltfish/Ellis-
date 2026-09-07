#!/usr/bin/env python3
"""Compare early-warning models and save event-level trigger timestamps."""

from __future__ import annotations

import csv
import io
import json
import statistics
import sys
import zipfile
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analyze_etf_pull_model import (  # noqa: E402
    FAST_THRESHOLD,
    MIN_FUTURE_BARS,
    SIGNAL_GAP_BARS,
    STRONG_THRESHOLD,
    collapse_alerts,
    episodes_from_condition,
    forward_alert_stats,
    match_rule,
    median,
    normalize_timestamp,
    parse_float,
    session_for,
    signal_eligible,
)


MODELS = (
    "current_proxy",
    "adaptive_k2_5",
    "early_radar_or",
    "adaptive_k3_5",
    "adaptive_k4_5",
    "two_factor_k3_5",
    "balanced_k3_5",
    "breakout_volume",
)


def first_match(alert_indices: list[int], event: dict[str, object], bars: list[dict[str, object]]) -> int | None:
    alerts = collapse_alerts(sorted(alert_indices))
    start = int(event["start_idx"])
    peak = int(event["peak_idx"])
    candidates = [a for a in alerts if start - 3 <= a <= peak]
    return candidates[0] if candidates else None


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: backtest_early_warning_models.py BUNDLE_ZIP OUTPUT_DIR", file=sys.stderr)
        return 2
    bundle_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics = {
        model: {
            strength: {"matched": 0, "alerts": 0, "valid": 0, "hits": 0, "leads": [], "offsets": []}
            for strength in ("fast", "strong")
        }
        for model in MODELS
    }
    truth_events = {"fast": 0, "strong": 0}
    event_trigger_rows: list[dict[str, object]] = []

    with zipfile.ZipFile(bundle_path) as bundle:
        manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
        symbols = manifest["target_symbols"]
        for symbol in symbols:
            if symbol == "513310.SH":
                continue
            member = f"csv/{symbol}.csv"
            with bundle.open(member) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                reader = csv.reader(text)
                header = next(reader, None)
                if not header:
                    continue
                time_col = header.index("时间")
                open_col = header.index("开盘价")
                close_col = header.index("收盘价")
                high_col = header.index("最高价")
                low_col = header.index("最低价")
                volume_col = header.index("成交量")
                by_day: dict[str, list[dict[str, object]]] = defaultdict(list)
                for row in reader:
                    if not row or row[0].strip() == "时间":
                        continue
                    timestamp, day, minute = normalize_timestamp(row[time_col])
                    session = session_for(minute)
                    if session is None:
                        continue
                    by_day[day].append({
                        "timestamp": timestamp,
                        "minute": minute,
                        "session": session,
                        "open": parse_float(row[open_col]),
                        "close": parse_float(row[close_col]),
                        "high": parse_float(row[high_col]),
                        "low": parse_float(row[low_col]),
                        "volume": parse_float(row[volume_col]),
                    })

            if not by_day:
                continue
            cumulative_history: dict[tuple[str, int], deque[float]] = defaultdict(lambda: deque(maxlen=20))
            prior_abs_r3: dict[str, deque[float]] = {"am": deque(maxlen=240), "pm": deque(maxlen=240)}
            prior_abs_r5: dict[str, deque[float]] = {"am": deque(maxlen=240), "pm": deque(maxlen=240)}

            for day in sorted(by_day):
                bars = sorted(by_day[day], key=lambda item: int(item["minute"]))
                if not bars:
                    continue
                session_positions: dict[str, int] = defaultdict(int)
                cumulative_by_session: dict[str, float] = defaultdict(float)
                for bar in bars:
                    session = str(bar["session"])
                    pos = session_positions[session]
                    session_positions[session] += 1
                    cumulative_by_session[session] += float(bar["volume"])
                    history = cumulative_history[(session, pos)]
                    prior = list(history)
                    prior_median = median(prior)
                    bar["cum_volume_ratio"] = cumulative_by_session[session] / prior_median if prior_median and prior_median > 0 else None
                    history.append(cumulative_by_session[session])

                eligible_by_session = {
                    session: [
                        i for i, bar in enumerate(bars)
                        if bar["session"] == session and signal_eligible(session, int(bar["minute"]))
                    ]
                    for session in ("am", "pm")
                }
                fast_events = episodes_from_condition(bars, FAST_THRESHOLD)
                strong_events = episodes_from_condition(bars, STRONG_THRESHOLD)
                truth_events["fast"] += len(fast_events)
                truth_events["strong"] += len(strong_events)

                alerts: dict[str, list[int]] = defaultdict(list)
                closes = [float(bar["close"]) for bar in bars]
                for session in ("am", "pm"):
                    indices = [i for i, bar in enumerate(bars) if bar["session"] == session]
                    if not indices:
                        continue
                    session_start = indices[0]
                    for i in range(session_start, indices[-1] + 1):
                        bar = bars[i]
                        if i <= session_start or not signal_eligible(session, int(bar["minute"])):
                            continue
                        close = closes[i]
                        r1 = close / closes[i - 1] - 1 if closes[i - 1] > 0 else 0.0
                        r3 = r5 = r15 = None
                        if i - session_start >= 3:
                            base = min(closes[i - 3:i])
                            r3 = close / base - 1 if base > 0 else 0.0
                        if i - session_start >= 5:
                            base = min(closes[i - 5:i])
                            r5 = close / base - 1 if base > 0 else 0.0
                        if i - session_start >= 15:
                            base = min(closes[i - 15:i])
                            r15 = close / base - 1 if base > 0 else 0.0

                        volume_ratio = bar.get("cum_volume_ratio")
                        volume_ratio = float(volume_ratio) if volume_ratio is not None else None
                        base3 = median(list(prior_abs_r3[session])[-120:])
                        base5 = median(list(prior_abs_r5[session])[-120:])
                        adaptive_k2_5 = (
                            (r3 is not None and base3 is not None and r3 >= max(0.0015, 2.5 * base3))
                            or (r5 is not None and base5 is not None and r5 >= max(0.0025, 2.2 * base5))
                        )
                        adaptive_k3_5 = (
                            (r3 is not None and base3 is not None and r3 >= max(0.0015, 3.5 * base3))
                            or (r5 is not None and base5 is not None and r5 >= max(0.0025, 3.0 * base5))
                        )
                        adaptive_k4_5 = (
                            (r3 is not None and base3 is not None and r3 >= max(0.0020, 4.5 * base3))
                            or (r5 is not None and base5 is not None and r5 >= max(0.0035, 4.0 * base5))
                        )
                        breakout = r15 is not None and r15 >= 0.003 and close > max(closes[i - 15:i])
                        volume_push = volume_ratio is not None and volume_ratio >= 1.2
                        micro_push = r1 >= 0.0015
                        current_proxy = (r3 is not None and r3 > 0.004) or (r5 is not None and r5 > 0.008)
                        early_radar_or = adaptive_k2_5 or current_proxy
                        two_factor_k3_5 = sum((adaptive_k3_5, breakout, volume_push, micro_push)) >= 2
                        balanced_k3_5 = adaptive_k3_5 or (breakout and volume_push) or (micro_push and volume_push)

                        if current_proxy:
                            alerts["current_proxy"].append(i)
                        if adaptive_k2_5:
                            alerts["adaptive_k2_5"].append(i)
                        if early_radar_or:
                            alerts["early_radar_or"].append(i)
                        if adaptive_k3_5:
                            alerts["adaptive_k3_5"].append(i)
                        if adaptive_k4_5:
                            alerts["adaptive_k4_5"].append(i)
                        if two_factor_k3_5:
                            alerts["two_factor_k3_5"].append(i)
                        if balanced_k3_5:
                            alerts["balanced_k3_5"].append(i)
                        if breakout and volume_push:
                            alerts["breakout_volume"].append(i)
                        if r3 is not None:
                            prior_abs_r3[session].append(abs(r3))
                        if r5 is not None:
                            prior_abs_r5[session].append(abs(r5))

                for model in MODELS:
                    model_alerts = alerts.get(model, [])
                    for strength, threshold, events in (("fast", FAST_THRESHOLD, fast_events), ("strong", STRONG_THRESHOLD, strong_events)):
                        matched, alert_count, leads = match_rule(model_alerts, events)
                        valid, hits = forward_alert_stats(model_alerts, bars, eligible_by_session, threshold)
                        metrics[model][strength]["matched"] += matched
                        metrics[model][strength]["alerts"] += alert_count
                        metrics[model][strength]["valid"] += valid
                        metrics[model][strength]["hits"] += hits
                        metrics[model][strength]["leads"].extend(leads)
                        for event in events:
                            trigger_index = first_match(model_alerts, event, bars)
                            if trigger_index is not None:
                                metrics[model][strength]["offsets"].append(int(event["start_idx"]) - trigger_index)

                for event in strong_events:
                    row: dict[str, object] = {
                        "symbol": symbol,
                        "date": day,
                        "session": event["session"],
                        "start_time": bars[int(event["start_idx"])]["timestamp"],
                        "peak_time": bars[int(event["peak_idx"])]["timestamp"],
                        "peak_return_from_start": round(float(event["peak_return"]), 6),
                    }
                    for model in MODELS:
                        trigger_index = first_match(alerts.get(model, []), event, bars)
                        row[f"trigger_{model}"] = bars[trigger_index]["timestamp"] if trigger_index is not None else ""
                        row[f"trigger_offset_{model}_bars"] = int(event["start_idx"]) - trigger_index if trigger_index is not None else ""
                    event_trigger_rows.append(row)

    metric_rows: list[dict[str, object]] = []
    for model in MODELS:
        for strength in ("fast", "strong"):
            item = metrics[model][strength]
            metric_rows.append({
                "model": model,
                "strength": strength,
                "truth_events": truth_events[strength],
                "matched_events": item["matched"],
                "event_recall": item["matched"] / truth_events[strength] if truth_events[strength] else None,
                "alert_episodes": item["alerts"],
                "valid_alerts_with_future": item["valid"],
                "forward_hit_rate": item["hits"] / item["valid"] if item["valid"] else None,
                "median_lead_bars_to_peak": median(item["leads"]),
                "median_trigger_offset_to_event_start_bars": median(item["offsets"]),
                "p90_trigger_offset_to_event_start_bars": percentile(item["offsets"], 0.90) if item["offsets"] else None,
            })

    with (output_dir / "model_metrics.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metric_rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(metric_rows)
    with (output_dir / "event_triggers.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(event_trigger_rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(event_trigger_rows)
    report = {
        "source_bundle": str(bundle_path),
        "models": MODELS,
        "fast_threshold": "future maximum close return >= 0.50% within next 15 eligible minutes",
        "strong_threshold": "future maximum close return >= 1.00% within next 15 eligible minutes",
        "model_features": {
            "adaptive_k2_5": "3/5-minute upward move versus prior same-session rolling absolute-move median, using 2.5/2.2x multipliers and absolute floors",
            "early_radar_or": "adaptive_k2_5 OR current_proxy; intentionally recall-first and tolerant of false positives",
            "adaptive_k3_5": "3/5-minute upward move versus prior same-session rolling absolute-move median, using 3.5/3.0x multipliers and absolute floors",
            "adaptive_k4_5": "3/5-minute upward move versus prior same-session rolling absolute-move median, using 4.5/4.0x multipliers and absolute floors",
            "breakout": "3-minute move >= 0.30% and close above prior 15-minute high",
            "volume_push": "running same-session volume / prior 20-day same-position median >= 1.20",
            "micro_push": "one-minute return >= 0.15%",
            "two_factor_k3_5": "at least two of adaptive 3.5x momentum, breakout, volume push, micro push",
            "balanced_k3_5": "adaptive 3.5x momentum OR (breakout and volume) OR (micro push and volume)",
        },
        "event_rows": len(event_trigger_rows),
    }
    (output_dir / "model_selection.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "event_rows": len(event_trigger_rows)}, ensure_ascii=False))
    return 0


def percentile(values: list[int], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate evidence-first TGW field/scale/order/latency statistics.

This tool intentionally reports observations and anomaly counts. It does not
declare a field mapping production-approved on the first live day.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator


def lines(path: Path) -> Iterator[bytes]:
    if path.suffix == ".zst":
        try:
            import zstandard
        except ImportError as exc:
            raise SystemExit("zstandard is required for .zst input; use the project .venv") from exc
        with path.open("rb") as stream:
            reader = zstandard.ZstdDecompressor().stream_reader(stream, read_across_frames=True)
            tail = b""
            while chunk := reader.read(1024 * 1024):
                tail += chunk
                parts = tail.split(b"\n")
                tail = parts.pop()
                yield from (item for item in parts if item)
            if tail:
                yield tail
    else:
        with path.open("rb") as stream:
            yield from (line.rstrip(b"\n") for line in stream if line.strip())


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]


def orig_epoch_ms(value: int) -> int | None:
    text = str(value)
    if len(text) == 13:
        return value
    if len(text) != 17:
        return None
    try:
        return int(datetime.strptime(text, "%Y%m%d%H%M%S%f").timestamp() * 1000)
    except ValueError:
        return None


def report(raw_paths: Iterable[Path], normalized_paths: Iterable[Path]) -> dict:
    result: dict = {"policy": "statistics_only_no_automatic_production_approval", "raw": {}, "normalized": {}}
    sessions: collections.Counter[str] = collections.Counter()
    tags: collections.Counter[str] = collections.Counter()
    key_sets: collections.Counter[str] = collections.Counter()
    value_types: collections.defaultdict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    delta = collections.Counter()
    sequences: dict[str, int] = {}
    market_sequence_discontinuities = 0
    core_observed_adapter_gaps = 0
    queue_high = 0
    raw_count = 0
    legacy_unwrapped = 0
    for path in raw_paths:
        for line in lines(path):
            item = json.loads(line)
            raw_count += 1
            session = str(item.get("session", ""))
            sequence = int(item.get("adapter_seq", 0))
            if sequence:
                if session in sequences and sequence != sequences[session] + 1:
                    market_sequence_discontinuities += 1
                sequences[session] = sequence
                sessions[session] += 1
            else:
                legacy_unwrapped += 1
            tags[str(item.get("tag"))] += 1
            delta["delta" if item.get("delta", item.get("is_delta", 0)) else "full"] += 1
            queue_high = max(queue_high, int(item.get("sdk_queue_depth", 0)))
            core_observed_adapter_gaps = max(core_observed_adapter_gaps, int(item.get("core_observed_adapter_gaps", 0)))
            event = item.get("event", item if "data" in item else {})
            data = event.get("data", {}) if isinstance(event, dict) else {}
            key_sets[",".join(sorted(data))] += 1
            for key, value in data.items():
                value_types[key][type(value).__name__] += 1
    result["raw"] = {"events": raw_count, "sessions": sessions, "tags": tags, "full_delta": delta,
                     "core_observed_adapter_gaps": core_observed_adapter_gaps,
                     "market_sequence_discontinuities_in_file": market_sequence_discontinuities,
                     "market_sequence_note": "adapter_seq also numbers status/control frames; file discontinuity alone is not packet loss",
                     "sdk_queue_high_water": queue_high,
                     "legacy_unwrapped_test_records": legacy_unwrapped,
                     "distinct_key_sets": len(key_sets), "top_key_sets": key_sets.most_common(20),
                     "field_json_types": {key: dict(value) for key, value in sorted(value_types.items())}}

    count = 0
    quality = collections.Counter()
    symbols = set()
    latency_ms: list[float] = []
    premium_ratios: list[float] = []
    book_order_errors = 0
    crossed_books = 0
    price_range_errors = 0
    limit_errors = 0
    orig_time_invalid = 0
    orig_time_backwards = 0
    orig_time_duplicates = 0
    update_intervals_ms: list[float] = []
    iopv_zero = 0
    iopv_unchanged_intervals: list[int] = []
    nonmonotonic = collections.Counter()
    previous: dict[tuple[str, str], tuple[int, int, int]] = {}
    previous_time: dict[tuple[str, str], int] = {}
    iopv_state: dict[tuple[str, str], tuple[int, int]] = {}
    for path in normalized_paths:
        for line in lines(path):
            item = json.loads(line)
            count += 1
            symbol = str(item.get("s", ""))
            session = str(item.get("source_session", "legacy-session"))
            identity = (session, symbol)
            symbols.add(symbol)
            quality.update(item.get("quality", []))
            receive = int(item.get("receive_wall_ns", 0))
            publish = int(item.get("publish_wall_ns", 0))
            if receive and publish >= receive:
                latency_ms.append((publish - receive) / 1_000_000)
            price, iopv = int(item.get("last_price_e6", 0)), int(item.get("iopv_e6", 0))
            if iopv <= 0:
                iopv_zero += 1
            if price > 0 and iopv > 0:
                premium_ratios.append(price / iopv)
            bids, asks = item.get("bid_prices_e6", []), item.get("ask_prices_e6", [])
            if any(a and b and b > a for a, b in zip(bids, bids[1:])) or any(a and b and b < a for a, b in zip(asks, asks[1:])):
                book_order_errors += 1
            if bids and asks and bids[0] > 0 and asks[0] > 0 and bids[0] >= asks[0]:
                crossed_books += 1
            high, low = int(item.get("high_price_e6", 0)), int(item.get("low_price_e6", 0))
            if price > 0 and ((high > 0 and price > high) or (low > 0 and price < low)):
                price_range_errors += 1
            high_limit, low_limit = int(item.get("high_limit_price_e6", 0)), int(item.get("low_limit_price_e6", 0))
            if price > 0 and ((high_limit > 0 and price > high_limit) or (low_limit > 0 and price < low_limit)):
                limit_errors += 1
            orig_time_raw = int(item.get("orig_time", 0))
            orig_time_value = orig_epoch_ms(orig_time_raw)
            if orig_time_value is None:
                orig_time_invalid += 1
            if orig_time_value is not None and identity in previous_time:
                if orig_time_value < previous_time[identity]:
                    orig_time_backwards += 1
                elif orig_time_value == previous_time[identity]:
                    orig_time_duplicates += 1
                else:
                    update_intervals_ms.append(float(orig_time_value - previous_time[identity]))
            if orig_time_value is not None:
                previous_time[identity] = orig_time_value
            if identity not in iopv_state or iopv_state[identity][0] != iopv:
                if identity in iopv_state and orig_time_value is not None:
                    iopv_unchanged_intervals.append(max(0, orig_time_value - iopv_state[identity][1]))
                if orig_time_value is not None:
                    iopv_state[identity] = (iopv, orig_time_value)
            current = (int(item.get("total_volume_e2", 0)), int(item.get("total_amount_e5", 0)), int(item.get("num_trades", 0)))
            if identity in previous:
                for index, label in enumerate(("volume", "amount", "trades")):
                    if current[index] < previous[identity][index]:
                        nonmonotonic[label] += 1
            previous[identity] = current
    result["normalized"] = {"events": count, "symbols": len(symbols), "quality_issues": quality,
                            "book_order_errors": book_order_errors, "crossed_books": crossed_books,
                            "last_outside_high_low": price_range_errors, "last_outside_limits": limit_errors,
                            "nonmonotonic": nonmonotonic, "iopv_zero": iopv_zero,
                            "orig_time": {"invalid": orig_time_invalid, "backwards": orig_time_backwards,
                                          "duplicates": orig_time_duplicates,
                                          "update_interval_ms_p50": percentile(update_intervals_ms, .5),
                                          "update_interval_ms_p99": percentile(update_intervals_ms, .99)},
                            "iopv_unchanged_interval_ms_max": max(iopv_unchanged_intervals) if iopv_unchanged_intervals else None,
                            "latency_ms": {"p50": percentile(latency_ms, .5), "p95": percentile(latency_ms, .95),
                                           "p99": percentile(latency_ms, .99), "max": max(latency_ms) if latency_ms else None},
                            "price_iopv_ratio": {"min": min(premium_ratios) if premium_ratios else None,
                                                 "median": statistics.median(premium_ratios) if premium_ratios else None,
                                                 "max": max(premium_ratios) if premium_ratios else None}}
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, action="append", default=[])
    parser.add_argument("--normalized", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = report(args.raw, args.normalized)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=dict)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

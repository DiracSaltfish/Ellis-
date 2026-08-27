#!/usr/bin/env python3
"""Compare A's 19195 output with an independent QMT1 19195 reference."""
from __future__ import annotations

import argparse
import collections
import json
import math
import socket
import statistics
import threading
import time
from pathlib import Path
from typing import Any


def subscribe(host: str, port: int, symbols: list[str]) -> tuple[socket.socket, Any]:
    sock = socket.create_connection((host, port), timeout=5)
    stream = sock.makefile("rwb")
    hello = json.loads(stream.readline())
    if hello.get("t") != "hello":
        raise RuntimeError(f"{host}:{port} did not send hello")
    stream.write(json.dumps({"v": 1, "t": "subscribe", "id": "compare", "symbols": symbols, "interval_ms": 0}).encode() + b"\n")
    stream.flush()
    return sock, stream


def collect(stream: Any, deadline: float, output: dict[str, list[dict]], error: list[str]) -> None:
    try:
        while time.monotonic() < deadline:
            line = stream.readline()
            if not line:
                return
            message = json.loads(line)
            for book in message.get("books", []):
                output.setdefault(str(book.get("s")), []).append(book)
    except Exception as exc:
        error.append(f"{type(exc).__name__}: {exc}")


def nearest(reference: list[dict], target: dict, tolerance_ms: int) -> dict | None:
    qt = int(target.get("qt", 0))
    if not reference:
        return None
    candidate = min(reference, key=lambda item: abs(int(item.get("qt", 0)) - qt))
    return candidate if qt and abs(int(candidate.get("qt", 0)) - qt) <= tolerance_ms else None


def analyze(a_books: dict[str, list[dict]], qmt_books: dict[str, list[dict]], tolerance_ms: int) -> dict:
    price_differences: list[float] = []
    quantity_ratios: list[float] = []
    exact_last = exact_price_levels = exact_quantity_levels = compared = 0
    missing = collections.Counter()
    for symbol, samples in a_books.items():
        reference = qmt_books.get(symbol, [])
        if not reference:
            missing["reference_symbol_missing"] += len(samples)
            continue
        for sample in samples:
            matched = nearest(reference, sample, tolerance_ms)
            if matched is None:
                missing["no_near_time_sample"] += 1
                continue
            compared += 1
            a_last, q_last = float(sample.get("lp", 0)), float(matched.get("lp", 0))
            price_differences.append(a_last - q_last)
            exact_last += a_last == q_last
            a_prices = list(sample.get("bp", [])) + list(sample.get("ap", []))
            q_prices = list(matched.get("bp", [])) + list(matched.get("ap", []))
            exact_price_levels += sum(left == right for left, right in zip(a_prices, q_prices))
            a_volumes = list(sample.get("bv", [])) + list(sample.get("av", []))
            q_volumes = list(matched.get("bv", [])) + list(matched.get("av", []))
            exact_quantity_levels += sum(left == right for left, right in zip(a_volumes, q_volumes))
            for left, right in zip(a_volumes, q_volumes):
                if right:
                    quantity_ratios.append(float(left) / float(right))
    return {"policy": "QMT1 validates price/five-level only; it does not validate IOPV",
            "compared_samples": compared, "missing": missing,
            "last_price_exact_rate": exact_last / compared if compared else None,
            "price_level_exact_rate": exact_price_levels / (compared * 10) if compared else None,
            "quantity_level_exact_rate": exact_quantity_levels / (compared * 10) if compared else None,
            "last_price_difference": {"min": min(price_differences) if price_differences else None,
                                      "median": statistics.median(price_differences) if price_differences else None,
                                      "max": max(price_differences) if price_differences else None},
            "quantity_ratio": {"min": min(quantity_ratios) if quantity_ratios else None,
                               "median": statistics.median(quantity_ratios) if quantity_ratios else None,
                               "max": max(quantity_ratios) if quantity_ratios else None}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a-host", required=True)
    parser.add_argument("--a-port", type=int, default=19195)
    parser.add_argument("--qmt-host", required=True)
    parser.add_argument("--qmt-port", type=int, default=19195)
    parser.add_argument("--symbols", required=True, help="comma-separated, max 256")
    parser.add_argument("--duration", type=int, default=900)
    parser.add_argument("--time-tolerance-ms", type=int, default=5000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    a_socket, a_stream = subscribe(args.a_host, args.a_port, symbols)
    q_socket, q_stream = subscribe(args.qmt_host, args.qmt_port, symbols)
    a_books: dict[str, list[dict]] = {}
    q_books: dict[str, list[dict]] = {}
    errors: list[str] = []
    deadline = time.monotonic() + args.duration
    threads = [threading.Thread(target=collect, args=(a_stream, deadline, a_books, errors), daemon=True),
               threading.Thread(target=collect, args=(q_stream, deadline, q_books, errors), daemon=True)]
    for thread in threads:
        thread.start()
    while time.monotonic() < deadline:
        time.sleep(min(1, deadline - time.monotonic()))
    a_socket.close()
    q_socket.close()
    payload = analyze(a_books, q_books, args.time_tolerance_ms)
    payload.update({"a_samples": sum(map(len, a_books.values())), "qmt_samples": sum(map(len, q_books.values())),
                    "symbols": symbols, "duration": args.duration, "reader_errors": errors,
                    "production_duration_met": args.duration >= 900})
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=dict)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

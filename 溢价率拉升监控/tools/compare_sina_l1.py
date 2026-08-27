#!/usr/bin/env python3
"""Synchronously compare A's TGW-backed 19195 stream with Sina five-level L1."""
from __future__ import annotations

import argparse
import collections
import json
import socket
import statistics
import threading
import time
from pathlib import Path
from typing import Any

from sina_l1 import SinaL1Fetcher, load_symbols


def _line(sock: socket.socket, buffer: bytearray, deadline: float) -> bytes | None:
    while time.monotonic() < deadline:
        position = buffer.find(b"\n")
        if position >= 0:
            result = bytes(buffer[:position])
            del buffer[:position + 1]
            return result
        try:
            block = sock.recv(65536)
        except socket.timeout:
            continue
        if not block:
            return None
        buffer.extend(block)
    return None


def subscribe(host: str, port: int, symbols: list[str]) -> tuple[socket.socket, bytearray, dict]:
    sock = socket.create_connection((host, port), timeout=5)
    sock.settimeout(0.5)
    buffer = bytearray()
    raw = _line(sock, buffer, time.monotonic() + 5)
    hello = json.loads(raw or b"{}")
    if hello.get("t") != "hello":
        sock.close()
        raise RuntimeError(f"{host}:{port} did not send NDJSON v1 hello")
    message = {"v": 1, "t": "subscribe", "id": "sina-quality", "symbols": symbols, "interval_ms": 0}
    sock.sendall(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    return sock, buffer, hello


def collect_tgw(sock: socket.socket, buffer: bytearray, deadline: float,
                output: dict[str, list[dict]], errors: list[str]) -> None:
    try:
        while time.monotonic() < deadline:
            raw = _line(sock, buffer, deadline)
            if raw is None:
                continue
            message = json.loads(raw)
            for book in message.get("books", []):
                if isinstance(book, dict) and book.get("s"):
                    output.setdefault(str(book["s"]).upper(), []).append(book)
    except Exception as exc:
        errors.append(f"TGW reader {type(exc).__name__}: {exc}")


def _deduplicate(items: list[dict]) -> list[dict]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict] = []
    for item in items:
        key = (item.get("qt"), item.get("lp"), tuple(item.get("bp", [])), tuple(item.get("ap", [])),
               tuple(item.get("bv", [])), tuple(item.get("av", [])))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _nearest(items: list[dict], target: dict, tolerance_ms: int) -> dict | None:
    if not items:
        return None
    qt = int(target.get("qt", 0))
    candidate = min(items, key=lambda item: abs(int(item.get("qt", 0)) - qt))
    return candidate if qt and abs(int(candidate.get("qt", 0)) - qt) <= tolerance_ms else None


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]


def _summary(values: list[float]) -> dict:
    return {"count": len(values), "min": min(values) if values else None,
            "p50": statistics.median(values) if values else None,
            "p95": _percentile(values, .95), "p99": _percentile(values, .99),
            "max": max(values) if values else None}


def _price_e6(value: Any) -> int:
    return int(round(float(value or 0) * 1_000_000))


def _book_issues(book: dict) -> list[str]:
    issues: list[str] = []
    bids = [float(value or 0) for value in book.get("bp", [])]
    asks = [float(value or 0) for value in book.get("ap", [])]
    if any(left > 0 and right > 0 and left < right for left, right in zip(bids, bids[1:])):
        issues.append("bid_not_descending")
    if any(left > 0 and right > 0 and left > right for left, right in zip(asks, asks[1:])):
        issues.append("ask_not_ascending")
    if bids and asks and bids[0] > 0 and asks[0] > 0 and bids[0] >= asks[0]:
        issues.append("crossed_or_locked")
    lp, high, low = (float(book.get(key, 0) or 0) for key in ("lp", "h", "l"))
    if lp > 0 and ((high > 0 and lp > high) or (low > 0 and lp < low)):
        issues.append("last_outside_high_low")
    return issues


def analyze(tgw_books: dict[str, list[dict]], sina_books: dict[str, list[dict]],
            tolerance_ms: int) -> dict:
    scalar_fields = ("lp", "o", "h", "l", "pc")
    scalar_equal = collections.Counter()
    scalar_total = collections.Counter()
    scalar_diff: dict[str, list[float]] = {field: [] for field in scalar_fields}
    price_ratios: list[float] = []
    level_price_total = level_price_equal = 0
    level_volume_total = level_volume_equal = 0
    level_volume_ratios: list[float] = []
    total_volume_ratios: list[float] = []
    total_amount_ratios: list[float] = []
    quote_time_delta: list[float] = []
    receive_time_delta: list[float] = []
    tgw_freshness: list[float] = []
    sina_freshness: list[float] = []
    tgw_issues = collections.Counter()
    sina_issues = collections.Counter()
    missing = collections.Counter()
    examples: list[dict] = []
    symbol_rows: list[dict] = []
    compared = 0
    for symbol in sorted(set(tgw_books) | set(sina_books)):
        tgw = _deduplicate(tgw_books.get(symbol, []))
        sina = _deduplicate(sina_books.get(symbol, []))
        for book in tgw:
            tgw_issues.update(_book_issues(book))
        for book in sina:
            sina_issues.update(_book_issues(book))
        row_compared = row_last_equal = 0
        row_max_abs_diff = 0.0
        if not tgw:
            missing["tgw_symbol_missing"] += len(sina)
        if not sina:
            missing["sina_symbol_missing"] += len(tgw)
        for reference in sina:
            sample = _nearest(tgw, reference, tolerance_ms)
            if sample is None:
                missing["no_tgw_sample_near_sina_quote_time"] += 1
                continue
            compared += 1
            row_compared += 1
            quote_time_delta.append(float(int(sample.get("qt", 0)) - int(reference.get("qt", 0))))
            receive_time_delta.append(float(int(sample.get("rt", 0)) - int(reference.get("rt", 0))))
            if sample.get("rt") and sample.get("qt"):
                tgw_freshness.append(float(int(sample["rt"]) - int(sample["qt"])))
            if reference.get("rt") and reference.get("qt"):
                sina_freshness.append(float(int(reference["rt"]) - int(reference["qt"])))
            mismatch = False
            for field in scalar_fields:
                left, right = _price_e6(sample.get(field)), _price_e6(reference.get(field))
                if left <= 0 or right <= 0:
                    continue
                scalar_total[field] += 1
                difference = (left - right) / 1_000_000
                scalar_diff[field].append(difference)
                scalar_equal[field] += left == right
                if field == "lp":
                    row_last_equal += left == right
                    row_max_abs_diff = max(row_max_abs_diff, abs(difference))
                    price_ratios.append(left / right)
                mismatch |= left != right
            for side in ("b", "a"):
                for left, right in zip(sample.get(f"{side}p", []), reference.get(f"{side}p", [])):
                    left_e6, right_e6 = _price_e6(left), _price_e6(right)
                    if left_e6 <= 0 or right_e6 <= 0:
                        continue
                    level_price_total += 1
                    level_price_equal += left_e6 == right_e6
                    price_ratios.append(left_e6 / right_e6)
                    mismatch |= left_e6 != right_e6
                for left, right in zip(sample.get(f"{side}v", []), reference.get(f"{side}v", [])):
                    left_i, right_i = int(round(float(left or 0))), int(round(float(right or 0)))
                    if left_i <= 0 or right_i <= 0:
                        continue
                    level_volume_total += 1
                    level_volume_equal += left_i == right_i
                    level_volume_ratios.append(left_i / right_i)
                    mismatch |= left_i != right_i
            for field, target in (("vol", total_volume_ratios), ("amt", total_amount_ratios)):
                left, right = float(sample.get(field, 0) or 0), float(reference.get(field, 0) or 0)
                if left > 0 and right > 0:
                    target.append(left / right)
            if mismatch and len(examples) < 100:
                examples.append({"s": symbol, "quote_time_delta_ms": quote_time_delta[-1],
                                 "tgw": sample, "sina": reference})
        symbol_rows.append({"s": symbol, "tgw_unique": len(tgw), "sina_unique": len(sina),
                            "compared": row_compared,
                            "last_exact_rate": row_last_equal / row_compared if row_compared else None,
                            "last_max_abs_difference": row_max_abs_diff if row_compared else None})
    price_ratio = _summary(price_ratios)
    known_scales = [1e-6, 1e-5, 1e-3, 1e-2, .1, 1, 10, 100, 1000, 1e5, 1e6]
    median_ratio = price_ratio["p50"]
    nearest_scale = min(known_scales, key=lambda value: abs((median_ratio or 0) - value)) if median_ratio else None
    return {
        "policy": "Sina validates public price/five-level/volume fields only; it cannot validate TGW IOPV",
        "comparison_grain": "one deduplicated Sina quote per symbol, matched to nearest TGW exchange quote time",
        "compared_samples": compared,
        "missing": dict(missing),
        "scalar_price_fields": {
            field: {"compared": scalar_total[field],
                    "exact_rate": scalar_equal[field] / scalar_total[field] if scalar_total[field] else None,
                    "difference": _summary(scalar_diff[field])}
            for field in scalar_fields
        },
        "level_price": {"compared": level_price_total,
                        "exact_rate": level_price_equal / level_price_total if level_price_total else None},
        "level_volume": {"compared": level_volume_total,
                         "exact_rate": level_volume_equal / level_volume_total if level_volume_total else None,
                         "ratio_tgw_over_sina": _summary(level_volume_ratios)},
        "total_volume_ratio_tgw_over_sina": _summary(total_volume_ratios),
        "total_amount_ratio_tgw_over_sina": _summary(total_amount_ratios),
        "price_ratio_tgw_over_sina": {**price_ratio, "nearest_known_scale": nearest_scale},
        "quote_time_delta_ms_tgw_minus_sina": _summary(quote_time_delta),
        "receive_time_delta_ms_tgw_minus_sina": _summary(receive_time_delta),
        "freshness_ms_receive_minus_quote": {"tgw": _summary(tgw_freshness), "sina": _summary(sina_freshness)},
        "book_validity": {"tgw": dict(tgw_issues), "sina": dict(sina_issues)},
        "symbols": symbol_rows,
        "mismatch_examples": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare TGW-backed A with independent Sina L1")
    parser.add_argument("--a-host", required=True)
    parser.add_argument("--a-port", type=int, default=19195)
    parser.add_argument("--symbols")
    parser.add_argument("--watchlist", type=Path)
    parser.add_argument("--duration", type=float, default=900.0)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--time-tolerance-ms", type=int, default=5000)
    parser.add_argument("--sina-timeout", type=float, default=5.0)
    parser.add_argument("--sina-chunk-size", type=int, default=80)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples-output", type=Path)
    args = parser.parse_args()
    symbols = load_symbols(args.symbols, args.watchlist)
    sock, buffer, hello = subscribe(args.a_host, args.a_port, symbols)
    deadline = time.monotonic() + max(0.0, args.duration)
    tgw_books: dict[str, list[dict]] = {}
    sina_books: dict[str, list[dict]] = {}
    errors: list[str] = []
    reader = threading.Thread(target=collect_tgw, args=(sock, buffer, deadline, tgw_books, errors), daemon=True)
    reader.start()
    fetcher = SinaL1Fetcher(args.sina_timeout, args.sina_chunk_size)
    request_metrics: list[dict] = []
    poll_errors: list[str] = []
    try:
        while True:
            started = time.monotonic()
            try:
                books, requests = fetcher.fetch(symbols)
                request_metrics.extend(requests)
                for book in books:
                    sina_books.setdefault(str(book["s"]).upper(), []).append(book)
            except Exception as exc:
                poll_errors.append(f"{type(exc).__name__}: {exc}")
            if time.monotonic() >= deadline:
                break
            time.sleep(min(max(0.0, args.interval - (time.monotonic() - started)),
                           max(0.0, deadline - time.monotonic())))
    finally:
        sock.close()
        reader.join(timeout=2)
    payload = analyze(tgw_books, sina_books, args.time_tolerance_ms)
    payload.update({"generated_at": datetime_now(), "a_source": f"{args.a_host}:{args.a_port}",
                    "a_hello": hello, "requested_symbols": len(symbols),
                    "duration_seconds": args.duration, "production_duration_met": args.duration >= 900,
                    "tgw_samples": sum(map(len, tgw_books.values())),
                    "sina_samples": sum(map(len, sina_books.values())),
                    "sina_http_requests": len(request_metrics),
                    "sina_http_latency_ms": _summary([float(item["latency_ms"]) for item in request_metrics]),
                    "reader_errors": errors, "sina_poll_errors": poll_errors})
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded + "\n", encoding="utf-8")
    if args.samples_output:
        args.samples_output.parent.mkdir(parents=True, exist_ok=True)
        args.samples_output.write_text(json.dumps({"tgw": tgw_books, "sina": sina_books},
                                                  ensure_ascii=False, separators=(",", ":")) + "\n",
                                       encoding="utf-8")
    print(json.dumps({key: payload.get(key) for key in (
        "generated_at", "requested_symbols", "duration_seconds", "tgw_samples", "sina_samples",
        "compared_samples", "missing", "price_ratio_tgw_over_sina", "level_price", "level_volume",
        "book_validity", "reader_errors", "sina_poll_errors")}, ensure_ascii=False, indent=2))
    return 0 if payload["compared_samples"] and not errors and not poll_errors else 1


def datetime_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


if __name__ == "__main__":
    raise SystemExit(main())

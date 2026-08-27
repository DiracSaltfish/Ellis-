#!/usr/bin/env python3
"""Measure LAN WebSocket stability, traffic and detail first/subsequent pushes."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path


def percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    pick = lambda p: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]
    return {"min": round(ordered[0], 3), "p50": round(pick(0.50), 3),
            "p95": round(pick(0.95), 3), "max": round(ordered[-1], 3)}


async def probe(host: str, port: int, symbol: str, duration: float) -> dict:
    import websockets

    started = time.monotonic()
    summary_counts: Counter[str] = Counter()
    summary_bytes: Counter[str] = Counter()
    sync_summary_count = 0
    sync_summary_bytes = 0
    live_summary_count = 0
    live_summary_bytes = 0
    detail_counts: Counter[str] = Counter()
    detail_bytes: Counter[str] = Counter()
    ping_ms: list[float] = []
    detail_intervals_ms: list[float] = []
    sync_begin_at = sync_complete_at = None
    first_detail_at = None
    first_detail_cached = None
    last_detail_at = None
    status_samples: list[dict] = []

    summary_url = f"ws://{host}:{port}/ws/v2/summary"
    detail_url = f"ws://{host}:{port}/ws/v2/detail"
    async with websockets.connect(summary_url, proxy=None, ping_interval=20, ping_timeout=20,
                                  max_queue=4096) as summary, \
               websockets.connect(detail_url, proxy=None, ping_interval=20, ping_timeout=20,
                                  max_queue=4096) as detail:
        hello_raw = await asyncio.wait_for(detail.recv(), 3)
        hello = json.loads(hello_raw)
        if hello.get("type") != "hello" or hello.get("channel") != "detail":
            raise RuntimeError(f"unexpected detail hello: {hello}")
        subscribe_at = time.monotonic()
        await detail.send(json.dumps({"op": "subscribe", "symbol": symbol}))

        async def read_summary() -> None:
            nonlocal sync_begin_at, sync_complete_at
            nonlocal sync_summary_count, sync_summary_bytes, live_summary_count, live_summary_bytes
            while time.monotonic() - started < duration:
                remaining = duration - (time.monotonic() - started)
                try:
                    raw = await asyncio.wait_for(summary.recv(), min(5.0, max(0.05, remaining)))
                except asyncio.TimeoutError:
                    continue
                message = json.loads(raw)
                kind = message.get("type", "unknown")
                summary_counts[kind] += 1
                raw_bytes = len(raw.encode("utf-8"))
                summary_bytes[kind] += raw_bytes
                now = time.monotonic()
                if kind == "summary":
                    if sync_complete_at is None:
                        sync_summary_count += 1
                        sync_summary_bytes += raw_bytes
                    else:
                        live_summary_count += 1
                        live_summary_bytes += raw_bytes
                if kind == "sync_begin" and sync_begin_at is None:
                    sync_begin_at = now
                elif kind == "sync_complete" and sync_complete_at is None:
                    sync_complete_at = now
                elif kind == "status" and len(status_samples) < 120:
                    status_samples.append(message)

        async def read_detail() -> None:
            nonlocal first_detail_at, first_detail_cached, last_detail_at
            while time.monotonic() - started < duration:
                remaining = duration - (time.monotonic() - started)
                try:
                    raw = await asyncio.wait_for(detail.recv(), min(5.0, max(0.05, remaining)))
                except asyncio.TimeoutError:
                    continue
                message = json.loads(raw)
                kind = message.get("type", "unknown")
                detail_counts[kind] += 1
                detail_bytes[kind] += len(raw.encode("utf-8"))
                if kind != "detail":
                    continue
                now = time.monotonic()
                if first_detail_at is None:
                    first_detail_at = now
                    first_detail_cached = bool(message.get("cached"))
                if last_detail_at is not None:
                    detail_intervals_ms.append((now - last_detail_at) * 1000)
                last_detail_at = now

        async def ping_loop() -> None:
            while time.monotonic() - started < duration:
                before = time.monotonic()
                pong = await summary.ping()
                await asyncio.wait_for(pong, 5)
                ping_ms.append((time.monotonic() - before) * 1000)
                await asyncio.sleep(min(5.0, max(0.0, duration - (time.monotonic() - started))))

        await asyncio.gather(read_summary(), read_detail(), ping_loop())
        await detail.send(json.dumps({"op": "unsubscribe", "symbol": symbol}))

    elapsed = time.monotonic() - started
    summary_total = sum(summary_bytes.values())
    detail_total = sum(detail_bytes.values())
    latest_status = status_samples[-1] if status_samples else {}
    live_seconds = elapsed - (sync_complete_at - started) if sync_complete_at is not None else 0.0
    return {
        "ok": True,
        "host": host,
        "port": port,
        "symbol": symbol,
        "observed_seconds": round(elapsed, 3),
        "disconnects": 0,
        "summary": {
            "messages": dict(summary_counts), "bytes": dict(summary_bytes),
            "total_bytes": summary_total, "bytes_per_second": round(summary_total / elapsed, 2),
            "initial_summary_messages": sync_summary_count,
            "initial_summary_bytes": sync_summary_bytes,
            "live_summary_messages": live_summary_count,
            "live_summary_bytes": live_summary_bytes,
            "live_summary_messages_per_second": round(live_summary_count / live_seconds, 3)
                                                 if live_seconds > 0 else None,
            "live_summary_bytes_per_second": round(live_summary_bytes / live_seconds, 2)
                                              if live_seconds > 0 else None,
            "sync_ms": round((sync_complete_at - sync_begin_at) * 1000, 3)
                       if sync_begin_at is not None and sync_complete_at is not None else None,
        },
        "detail": {
            "messages": dict(detail_counts), "bytes": dict(detail_bytes),
            "total_bytes": detail_total, "bytes_per_second": round(detail_total / elapsed, 2),
            "first_snapshot_ms": round((first_detail_at - subscribe_at) * 1000, 3)
                                 if first_detail_at is not None else None,
            "first_snapshot_cached": first_detail_cached,
            "subsequent_snapshots": max(0, detail_counts.get("detail", 0) - 1),
            "interarrival_ms": percentiles(detail_intervals_ms),
        },
        "ping_rtt_ms": percentiles(ping_ms),
        "ping_count": len(ping_ms),
        "status_count": len(status_samples),
        "latest_status": {key: latest_status.get(key) for key in
                          ("phase", "ready_symbols", "quarantined", "summary_clients",
                           "detail_clients", "adapter_seq", "adapter_gaps", "sdk_queue_depth",
                           "core_latency_us", "core_latency_max_us")},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8421)
    parser.add_argument("--symbol", default="159866.SZ")
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = asyncio.run(probe(args.host, args.port, args.symbol, args.duration))
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

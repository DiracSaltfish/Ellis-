#!/usr/bin/env python3
"""Read-only A protocol smoke test for running simulation or live services."""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import time


def legacy(host: str, port: int, symbol: str) -> dict:
    client = socket.create_connection((host, port), timeout=3)
    stream = client.makefile("rwb")
    hello = json.loads(stream.readline())
    assert hello["v"] == 1 and hello["t"] == "hello"
    stream.write(json.dumps({"v": 1, "t": "subscribe", "id": "smoke", "symbols": [symbol], "interval_ms": 0}).encode() + b"\n")
    stream.flush()
    result: dict = {}
    for _ in range(20):
        message = json.loads(stream.readline())
        if message.get("t") == "l1" and message.get("books"):
            result = message["books"][0]
            break
    client.close()
    assert result.get("s") == symbol and len(result.get("bp", [])) == 5
    return result


async def websocket(host: str, port: int, symbol: str) -> tuple[dict, dict, dict]:
    import websockets

    summary_result: dict = {}
    async with websockets.connect(f"ws://{host}:{port}/ws/v2/summary", proxy=None) as ws:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            message = json.loads(await asyncio.wait_for(ws.recv(), 2))
            if message.get("type") == "summary" and message.get("s") == symbol:
                summary_result = message
            if message.get("type") == "sync_complete" and summary_result:
                break
        await ws.send(json.dumps({"op": "raw_snapshot"}))
        while True:
            raw = json.loads(await asyncio.wait_for(ws.recv(), 3))
            if raw.get("type") == "raw_snapshot":
                assert raw.get("available") and raw.get("adapter_seq", 0) > 0
                break
    async with websockets.connect(f"ws://{host}:{port}/ws/v2/detail", proxy=None) as ws:
        hello = json.loads(await ws.recv())
        assert hello["channel"] == "detail" and hello["max_symbols"] == 4
        await ws.send(json.dumps({"op": "subscribe", "symbol": symbol}))
        while True:
            message = json.loads(await asyncio.wait_for(ws.recv(), 3))
            if message.get("type") == "detail":
                assert len(message["bid_prices_e6"]) == 10
                return summary_result, message, raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--monitor-port", type=int, default=8421)
    parser.add_argument("--legacy-port", type=int, default=19195)
    # Use a member of the fixed 202 set so this smoke test cannot consume one
    # dynamic-capacity slot during the 60-second unsubscribe grace period.
    parser.add_argument("--symbol", default="159866.SZ")
    args = parser.parse_args()
    book = legacy(args.host, args.legacy_port, args.symbol)
    summary, detail, raw = asyncio.run(websocket(args.host, args.monitor_port, args.symbol))
    print(json.dumps({"ok": True, "legacy_lp": book["lp"], "summary_price_e6": summary["last_price_e6"],
                      "detail_cached": detail["cached"], "detail_levels": len(detail["bid_prices_e6"]),
                      "raw_adapter_seq": raw["adapter_seq"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

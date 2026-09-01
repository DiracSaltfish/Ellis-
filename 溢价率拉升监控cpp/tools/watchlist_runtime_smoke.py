#!/usr/bin/env python3
"""Loopback-only runtime watchlist add/remove smoke test.

The original watchlist is always restored before exit.  Run this on host A so
the admin operation passes A-core's loopback restriction.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any


async def receive_until(ws: Any, predicate: Any, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = json.loads(await asyncio.wait_for(ws.recv(), min(2.0, deadline - time.monotonic())))
        if predicate(message):
            return message
    raise TimeoutError("expected websocket event not received")


async def apply(ws: Any, symbols: list[str]) -> dict[str, Any]:
    await ws.send(json.dumps({"op": "set_watchlist", "symbols": symbols}, separators=(",", ":")))
    ack = await receive_until(ws, lambda item: item.get("type") == "watchlist_ack", 10.0)
    if not ack.get("accepted"):
        raise RuntimeError(f"watchlist rejected: {ack.get('error')}")
    return ack


async def run(args: argparse.Namespace) -> dict[str, Any]:
    import websockets

    payload = json.loads(args.watchlist.read_text(encoding="utf-8"))
    values = payload.get("symbols", payload) if isinstance(payload, dict) else payload
    original = [str(value).upper() for value in values]
    additions = [value.upper() for value in args.add]
    if any(value in original for value in additions):
        raise ValueError("test additions must not already be in the watchlist")

    snapshots: dict[str, dict[str, Any]] = {}
    removed: set[str] = set()
    post_remove_updates: list[str] = []
    restored = False
    async with websockets.connect(args.url, proxy=None, max_size=4 * 1024 * 1024) as ws:
        try:
            add_ack = await apply(ws, original + additions)
            deadline = time.monotonic() + args.snapshot_timeout
            while len(snapshots) < len(additions) and time.monotonic() < deadline:
                message = json.loads(await asyncio.wait_for(ws.recv(), 2.0))
                if message.get("type") == "summary" and message.get("s") in additions:
                    snapshots[message["s"]] = message
            if len(snapshots) != len(additions):
                missing = sorted(set(additions) - set(snapshots))
                raise TimeoutError(f"no live summary for {missing}")

            await ws.send(json.dumps({"op": "set_watchlist", "symbols": original}, separators=(",", ":")))
            deadline = time.monotonic() + 10.0
            remove_ack: dict[str, Any] | None = None
            while (remove_ack is None or len(removed) < len(additions)) and time.monotonic() < deadline:
                message = json.loads(await asyncio.wait_for(ws.recv(), 2.0))
                if message.get("type") == "symbol_removed" and message.get("symbol") in additions:
                    removed.add(message["symbol"])
                elif message.get("type") == "watchlist_ack":
                    if not message.get("accepted"):
                        raise RuntimeError(f"restore rejected: {message.get('error')}")
                    remove_ack = message
            if remove_ack is None or len(removed) != len(additions):
                raise TimeoutError("restore acknowledgement or symbol_removed event missing")
            restored = True

            quiet_deadline = time.monotonic() + args.quiet_seconds
            while time.monotonic() < quiet_deadline:
                try:
                    message = json.loads(await asyncio.wait_for(ws.recv(), quiet_deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                if message.get("type") == "summary" and message.get("s") in additions:
                    post_remove_updates.append(message["s"])
            if post_remove_updates:
                raise AssertionError(f"removed symbols were republished: {post_remove_updates}")
            return {
                "ok": True,
                "original_count": len(original),
                "temporary_count": add_ack.get("count"),
                "restored_count": remove_ack.get("count"),
                "removed_events": sorted(removed),
                "quiet_seconds": args.quiet_seconds,
                "post_remove_updates": post_remove_updates,
                "snapshots": snapshots,
            }
        finally:
            if not restored:
                try:
                    await apply(ws, original)
                except Exception:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8421/ws/v2/summary")
    parser.add_argument("--watchlist", type=Path, default=Path("config/watchlist.json"))
    parser.add_argument("--add", action="append", default=["164824.SZ", "513100.SH"])
    parser.add_argument("--snapshot-timeout", type=float, default=30.0)
    parser.add_argument("--quiet-seconds", type=float, default=5.0)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

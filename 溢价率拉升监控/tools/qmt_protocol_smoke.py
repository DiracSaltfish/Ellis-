#!/usr/bin/env python3
"""Exercise the fake QMT 10.1 JSONL contract; never defaults to a broker port."""
from __future__ import annotations

import argparse
import json
import socket
from typing import Any


def send(stream: Any, message: dict[str, Any]) -> None:
    stream.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
    stream.flush()


def receive(stream: Any, wanted: str) -> dict[str, Any]:
    while line := stream.readline():
        message = json.loads(line)
        if message.get("type") == wanted:
            return message
    raise RuntimeError(f"connection closed before {wanted}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19527)
    args = parser.parse_args()
    with socket.create_connection((args.host, args.port), timeout=5) as sock:
        stream = sock.makefile("rwb")
        receive(stream, "welcome")
        send(stream, {"type": "sync_request", "target": "all"})
        orders = receive(stream, "orders_data")
        positions = receive(stream, "positions_data")
        if orders.get("sync_mode") != "full" or positions.get("sync_mode") != "full":
            raise RuntimeError("full sync contract mismatch")

        requests = [
            {"type": "etf_order", "action": "PURCHASE", "code": "159518.SZ", "qty": 1,
             "client_order_id": "smoke-purchase"},
            {"type": "etf_order", "action": "REDEEM", "code": "159518.SZ", "qty": 1,
             "client_order_id": "smoke-redeem"},
            {"type": "order", "code": "159518.SZ", "side": "SELL", "price": 1.234,
             "qty": 100000, "client_order_id": "smoke-sell"},
        ]
        ids: list[str] = []
        for request in requests:
            send(stream, request)
            result = receive(stream, "etf_order_result" if request["type"] == "etf_order" else "order_result")
            if not result.get("success"):
                raise RuntimeError(f"mock rejected: {result}")
            snapshot = receive(stream, "orders_data")
            ids = [str(item.get("order_id")) for item in snapshot.get("data", [])]
        if len(ids) != 3:
            raise RuntimeError(f"expected three orders, got {len(ids)}")

        send(stream, {"type": "cancel_order", "order_id": ids[-1]})
        canceled = receive(stream, "cancel_result")
        refreshed = receive(stream, "orders_data")
        if not canceled.get("success") or not any(
            row.get("order_id") == ids[-1] and row.get("status") == "CANCELED"
            for row in refreshed.get("data", [])
        ):
            raise RuntimeError("cancel contract mismatch")

    print(json.dumps({"ok": True, "orders": 3, "purchase": 1, "redeem": 1,
                      "sell_qty": 100000, "sell_price": 1.234, "cancel": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Exercise 202/500/1000 maintained-symbol capacity through NDJSON v1."""
from __future__ import annotations

import argparse
import json
import socket
import time


def candidates(count: int) -> list[str]:
    result: list[str] = []
    index = 1
    while len(result) < count:
        symbol = f"{index:06d}.SZ"
        if not symbol.startswith("159"):
            result.append(symbol)
        index += 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19195)
    parser.add_argument("--target", choices=(202, 500, 1000), type=int, required=True)
    parser.add_argument("--duration", type=int, default=900, help="observation seconds; production acceptance uses >=900")
    args = parser.parse_args()
    dynamic = candidates(args.target - 202)
    clients: list[tuple[socket.socket, object]] = []
    accepted = rejected = 0
    rejection_details: list[dict] = []
    try:
        for offset in range(0, len(dynamic), 256):
            sock = socket.create_connection((args.host, args.port), timeout=5)
            file = sock.makefile("rwb")
            json.loads(file.readline())
            requested = dynamic[offset : offset + 256]
            file.write(json.dumps({"v": 1, "t": "subscribe", "id": offset, "symbols": requested, "interval_ms": 0}).encode() + b"\n")
            file.flush()
            while True:
                response = json.loads(file.readline())
                if response.get("t") == "ack":
                    accepted += len(response.get("symbols", []))
                    rejected += len(response.get("rejected", []))
                    rejection_details.extend(response.get("rejected", []))
                    break
            clients.append((sock, file))
        started = time.monotonic()
        while time.monotonic() - started < args.duration:
            time.sleep(min(1, args.duration - (time.monotonic() - started)))
        print(json.dumps({"target": args.target, "fixed": 202, "dynamic_requested": len(dynamic),
                          "dynamic_accepted": accepted, "rejected": rejected,
                          "rejection_details": rejection_details,
                          "observed_seconds": round(time.monotonic() - started, 3),
                          "production_duration_met": args.duration >= 900}))
        return 0 if accepted == len(dynamic) and rejected == 0 else 2
    finally:
        for sock, file in clients:
            file.close()
            sock.close()


if __name__ == "__main__":
    raise SystemExit(main())

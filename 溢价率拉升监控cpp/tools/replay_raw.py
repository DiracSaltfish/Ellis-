#!/usr/bin/env python3
"""Replay audited raw TGW records into a stopped adapter socket."""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapter"))
from proto_wire import BridgeFrame, encode_framed

from quality_report import lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--socket", type=Path, default=Path("runtime/tgw.sock"))
    parser.add_argument("--speed", type=float, default=1.0, help="0 sends without timing delay")
    args = parser.parse_args()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(args.socket.resolve()))
    session = "replay-" + uuid.uuid4().hex
    previous_ns = None
    sent = 0
    for raw_line in lines(args.input):
        record = json.loads(raw_line)
        receive_ns = int(record.get("receive_wall_ns", 0))
        if args.speed > 0 and previous_ns and receive_ns > previous_ns:
            time.sleep(min(1.0, (receive_ns - previous_ns) / 1_000_000_000 / args.speed))
        previous_ns = receive_ns
        event = record.get("event", {})
        frame = BridgeFrame(kind=1, sequence=sent + 1, session_id=session,
                            receive_wall_ns=time.time_ns(), receive_monotonic_ns=time.monotonic_ns(),
                            is_delta=bool(record.get("delta")), tag=str(record.get("tag", "14")),
                            payload_json=json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode())
        client.sendall(encode_framed(frame))
        sent += 1
    print(json.dumps({"sent": sent, "session": session}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

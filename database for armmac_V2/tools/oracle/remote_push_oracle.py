#!/usr/bin/env python3
"""Observe an authorized official-SDK L1 subscription and print counters only."""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from pathlib import Path

import yaml

from remote_sdk_oracle import load_env_file


class Counter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.timestamps: list[float] = []
        self.errors = 0
        self.callback_types: dict[str, int] = {}

    def _record(self, callback_type, data, error):
        with self._lock:
            if data is not None:
                self.timestamps.append(time.monotonic())
                self.callback_types[callback_type] = (
                    self.callback_types.get(callback_type, 0) + 1
                )
            if error:
                self.errors += 1

    def OnMDSnapshot(self, data, error):
        self._record("OnMDSnapshot", data, error)

    def OnMDHKTSnapshot(self, data, error):
        self._record("OnMDHKTSnapshot", data, error)

    def _IsDfFormat(self):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--kind", choices=("etf", "hkt"), default="etf")
    args = parser.parse_args()
    config = yaml.safe_load(Path("/opt/galaxy-relay/config/config.yaml").read_text())
    env = load_env_file(Path("/etc/galaxy-relay/relay.env"))
    upstream = config["amazingdata"]
    host_cfg = upstream["hosts"][0]

    import tgw

    cfg = tgw.Cfg()
    cfg.username = env[upstream["username_env"]]
    cfg.password = env[upstream["password_env"]]
    cfg.server_vip = host_cfg["host"]
    cfg.server_port = int(host_cfg["port"])
    cfg.force_logout = False
    if not tgw.Login(cfg, tgw.ApiMode.kInternetMode):
        print(json.dumps({"login": False}))
        return 2
    counter = Counter()
    item = tgw.SubscribeItem()
    if args.kind == "etf":
        item.market = tgw.MarketType.kSZSE
        item.flag = tgw.SubscribeDataType.kSnapshot
        item.security_code = "159518"
    else:
        item.market = tgw.MarketType.kSSE
        item.flag = tgw.SubscribeDataType.kHKTSnapshot
        item.security_code = "02800"
    item.category_type = 0
    try:
        status = int(tgw.Subscribe(item, counter))
        time.sleep(max(0.0, args.duration))
        with counter._lock:
            timestamps = list(counter.timestamps)
            errors = counter.errors
            callback_types = dict(counter.callback_types)
        intervals = [right - left for left, right in zip(timestamps, timestamps[1:])]
        print(json.dumps({
            "login": True,
            "subscribe_status": status,
            "duration_sec": args.duration,
            "message_count": len(timestamps),
            "callback_errors": errors,
            "callback_types": callback_types,
            "max_gap_sec": round(max(intervals), 3) if intervals else None,
            "median_gap_sec": round(statistics.median(intervals), 3) if intervals else None,
        }, sort_keys=True))
        tgw.UnSubscribe(item)
    finally:
        tgw.Close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

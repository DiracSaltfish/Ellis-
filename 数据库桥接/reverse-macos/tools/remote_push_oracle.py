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

    def OnMDSnapshot(self, data, error):
        with self._lock:
            if data is not None:
                self.timestamps.append(time.monotonic())
            if error:
                self.errors += 1

    def _IsDfFormat(self):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=30.0)
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
    item.market = tgw.MarketType.kSZSE
    item.flag = tgw.SubscribeDataType.kSnapshot
    item.security_code = "159518"
    item.category_type = 0
    try:
        status = int(tgw.Subscribe(item, counter))
        time.sleep(max(0.0, args.duration))
        with counter._lock:
            timestamps = list(counter.timestamps)
            errors = counter.errors
        intervals = [right - left for left, right in zip(timestamps, timestamps[1:])]
        print(json.dumps({
            "login": True,
            "subscribe_status": status,
            "duration_sec": args.duration,
            "message_count": len(timestamps),
            "callback_errors": errors,
            "max_gap_sec": round(max(intervals), 3) if intervals else None,
            "median_gap_sec": round(statistics.median(intervals), 3) if intervals else None,
        }, sort_keys=True))
        tgw.UnSubscribe(item)
    finally:
        tgw.Close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

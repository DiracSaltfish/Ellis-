#!/usr/bin/env python3
"""Observe official Login/Close re-entry without printing credentials."""
from __future__ import annotations

import getpass
import json
import time
from pathlib import Path

import yaml


def main() -> int:
    config = yaml.safe_load(
        Path("/opt/galaxy-relay/config/config.yaml").read_text(encoding="utf-8")
    )
    upstream = config["amazingdata"]
    host = upstream["hosts"][0]
    username = getpass.getpass("one-run username: ").strip()
    env: dict[str, str] = {}
    for line in Path("/etc/galaxy-relay/relay.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("'\"")
    password = env[upstream["password_env"]]
    if not username:
        raise ValueError("username must not be empty")

    import tgw

    outcomes: list[bool] = []
    for _ in range(2):
        cfg = tgw.Cfg()
        cfg.username = username
        cfg.password = password
        cfg.server_vip = host["host"]
        cfg.server_port = int(host["port"])
        cfg.force_logout = False
        logged_in = bool(tgw.Login(cfg, tgw.ApiMode.kInternetMode))
        outcomes.append(logged_in)
        tgw.Close()
        if not logged_in:
            break
        if len(outcomes) == 1:
            time.sleep(5.0)

    print(json.dumps({
        "attempt_count": len(outcomes),
        "login_results": outcomes,
        "all_succeeded": len(outcomes) == 2 and all(outcomes),
    }, sort_keys=True))
    return 0 if len(outcomes) == 2 and all(outcomes) else 2


if __name__ == "__main__":
    raise SystemExit(main())

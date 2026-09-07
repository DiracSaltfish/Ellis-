#!/usr/bin/env python3
"""Run two credential-safe macOS Login/Close cycles and print booleans only."""
from __future__ import annotations

import argparse
import configparser
import getpass
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "python"))

import tgw_macos as tgw  # noqa: E402


def _connection(path: Path) -> tuple[str, int, int, str]:
    parser = configparser.ConfigParser()
    with path.open(encoding="utf-8") as stream:
        parser.read_file(stream)
    section = parser["galaxy"]
    host = section["host"].split("#", 1)[0].strip().split()[0]
    port = section.getint("port")
    mode_name = section.get("api_mode", "kInternetMode").split("#", 1)[0].strip()
    return host, port, int(getattr(tgw.ApiMode, mode_name)), section["password"].strip()


def main() -> int:
    cli = argparse.ArgumentParser()
    cli.add_argument("--config", type=Path, required=True)
    args = cli.parse_args()
    host, port, mode, password = _connection(args.config)
    username = getpass.getpass("one-run username: ").strip()
    if not username:
        cli.error("username must not be empty")

    results: list[bool] = []
    backend_ids: list[int] = []
    for _ in range(2):
        cfg = tgw.Cfg().set(
            server_vip=host,
            server_port=port,
            username=username,
            password=password,
            force_logout=False,
        )
        logged_in = bool(tgw.Login(cfg, mode))
        results.append(logged_in)
        if tgw.interface._g_backend is not None:
            backend_ids.append(id(tgw.interface._g_backend))
        tgw.Close()
        if not logged_in:
            break
        if len(results) == 1:
            time.sleep(5.0)

    summary = {
        "attempt_count": len(results),
        "login_results": results,
        "all_succeeded": len(results) == 2 and all(results),
        "fresh_backend_each_cycle": len(backend_ids) == 2 and len(set(backend_ids)) == 2,
        "released_after_close": tgw.interface._g_backend is None,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["all_succeeded"] and summary["fresh_backend_each_cycle"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

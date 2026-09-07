#!/usr/bin/env python3
"""Run the reconstructed backend on bj while printing only result shape."""
from __future__ import annotations

import json
import platform
from pathlib import Path

import yaml

from remote_sdk_oracle import load_env_file, safe_shape


def main() -> int:
    config_path = Path("/opt/galaxy-relay/config/config.yaml")
    env_path = Path("/etc/galaxy-relay/relay.env")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    env = load_env_file(env_path)
    upstream = config["amazingdata"]
    host_cfg = upstream["hosts"][0]

    # This oracle deliberately exercises the platform-neutral wire backend on
    # Linux. Keep the public package's macOS-only import guard intact.
    platform.system = lambda: "Darwin"  # type: ignore[assignment]
    import tgw_macos as tgw

    cfg = tgw.Cfg().set(
        server_vip=host_cfg["host"],
        server_port=int(host_cfg["port"]),
        username=env[upstream["username_env"]],
        password=env[upstream["password_env"]],
        force_logout=False,
    )
    logged_in = bool(tgw.Login(cfg, tgw.ApiMode.kInternetMode))
    if not logged_in:
        print(json.dumps({"login": False, "query": "not_run"}, sort_keys=True))
        return 2
    try:
        request = tgw.ReqKline().set_code("510300")
        request.market_type = tgw.MarketType.kSSE
        request.cq_flag = 0
        request.cq_date = 0
        request.qj_flag = 0
        request.cyc_type = 10008
        request.cyc_def = 0
        request.auto_complete = 1
        request.begin_date = 20260825
        request.end_date = 20260825
        request.begin_time = 0
        request.end_time = 0
        result, error = tgw.QueryKline(request, return_df_format=False)
        print(json.dumps({
            "login": True,
            "query_error": int(error),
            "result_shape": safe_shape(result),
        }, sort_keys=True))
    finally:
        tgw.Close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

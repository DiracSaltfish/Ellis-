#!/usr/bin/env python3
"""Run one authorized official-Linux-SDK query, printing shape only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def safe_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): safe_shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return {"type": "list", "length": len(value), "item": safe_shape(value[0]) if value else None}
    # pandas is optional at this boundary, but the official wrapper can return it.
    if hasattr(value, "columns") and hasattr(value, "shape"):
        return {
            "type": type(value).__name__,
            "rows": int(value.shape[0]),
            "columns": [str(column) for column in value.columns],
        }
    return {"type": type(value).__name__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("/opt/galaxy-relay/config/config.yaml"))
    parser.add_argument("--env-file", type=Path, default=Path("/etc/galaxy-relay/relay.env"))
    parser.add_argument(
        "--kind", choices=("calendar", "kline", "snapshot"), default="calendar"
    )
    parser.add_argument("--security-code", default="159518")
    parser.add_argument("--market-type", type=int, default=102)
    parser.add_argument("--date", type=int, default=20260825)
    parser.add_argument("--begin-time", type=int, default=93000000)
    parser.add_argument("--end-time", type=int, default=93030000)
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    env = load_env_file(args.env_file)
    upstream = config["amazingdata"]
    host_cfg = upstream["hosts"][0]
    username = env[upstream["username_env"]]
    password = env[upstream["password_env"]]

    import tgw

    cfg = tgw.Cfg()
    cfg.username = username
    cfg.password = password
    cfg.server_vip = host_cfg["host"]
    cfg.server_port = int(host_cfg["port"])
    cfg.force_logout = False
    logged_in = bool(tgw.Login(cfg, tgw.ApiMode.kInternetMode))
    if not logged_in:
        print(json.dumps({"login": False, "query": "not_run"}, sort_keys=True))
        return 2

    try:
        set_codes: dict[str, int] = {}
        if args.kind == "calendar":
            task_id = tgw.GetTaskID()
            params = {
                "function_id": "A010061003",
                "start_date": "20260801",
                "end_date": "20260826",
                "market": "SSE",
            }
            set_codes = {
                key: int(tgw.SetThirdInfoParam(task_id, key, value))
                for key, value in params.items()
            }
            result, error = tgw.QueryThirdInfo(task_id, return_df_format=False)
            req_defaults = {}
        elif args.kind == "kline":
            request = tgw.ReqKline()
            request.security_code = "510300"
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
            req_defaults = {}
        else:
            request = tgw.ReqDefault()
            request.security_code = args.security_code
            request.market_type = args.market_type
            request.date = args.date
            request.begin_time = args.begin_time
            request.end_time = args.end_time
            # data_type/level_type intentionally keep the official constructor
            # defaults (0/0); only their presence is reported.
            result, error = tgw.QuerySnapshot(request, return_df_format=False)
            req_defaults = {
                "data_type": int(request.data_type),
                "level_type": int(request.level_type),
            }
        invariants: dict[str, bool] = {}
        if args.kind == "kline" and isinstance(result, list) and result:
            row = result[0]
            if isinstance(row, dict):
                invariants = {
                    "orig_time_equals_kline_time": row.get("orig_time") == row.get("kline_time"),
                    "orig_time_is_zero": row.get("orig_time") == 0,
                    "variety_category_is_zero": row.get("variety_category") == 0,
                }
        if args.kind == "snapshot" and isinstance(result, list) and result:
            row = result[0]
            if isinstance(row, dict):
                invariants = {
                    "first_row_keys_sorted": sorted(row.keys()) == list(row.keys()),
                    "all_values_are_scalars": all(
                        isinstance(value, (int, float, str)) for value in row.values()
                    ),
                    "has_code_field": "code" in row or "security_code" in row,
                    "row_key_count_identical": all(
                        len(item.keys()) == len(row.keys())
                        for item in result
                        if isinstance(item, dict)
                    ),
                    # Classification enums are protocol metadata, not business
                    # values; they pin down wrapper-side derivations.
                    "distinct_variety_category": sorted(
                        {item["variety_category"] for item in result if isinstance(item, dict)}
                    ),
                    "distinct_market_type": sorted(
                        {item["market_type"] for item in result if isinstance(item, dict)}
                    ),
                    "distinct_trading_phase_code": sorted(
                        {item.get("trading_phase_code") for item in result if isinstance(item, dict)}
                    ),
                    "orig_time_digit_lengths": sorted(
                        {len(str(item["orig_time"])) for item in result if isinstance(item, dict)}
                    ),
                }
        print(json.dumps({
            "login": True,
            "query_kind": args.kind,
            "set_param_statuses": sorted(set(set_codes.values())),
            "query_error_type": type(error).__name__,
            "query_error": int(error) if isinstance(error, int) else None,
            "result_shape": safe_shape(result),
            "result_invariants": invariants,
            "req_default_fields": req_defaults,
        }, sort_keys=True))
    finally:
        tgw.Close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

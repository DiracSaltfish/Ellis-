#!/usr/bin/env python3
"""Run one authorized official-Linux-SDK query, printing shape only."""
from __future__ import annotations

import argparse
import json
import threading
import time
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


def _etf_entry_parts(entry: Any) -> tuple[Any, Any]:
    """Official json-format rows are (basic_dict, constituent_list) tuples."""
    if isinstance(entry, dict):
        return entry.get("basic_info"), entry.get("constituent_stock_info")
    if isinstance(entry, (tuple, list)) and len(entry) == 2:
        return entry[0], entry[1]
    return None, None


class _EtfBatchCollector:
    """Async user SPI: records per-batch counters only, never row values."""

    def __init__(self, wait_seconds: float) -> None:
        self.wait_seconds = wait_seconds
        self.immediate_error: int | None = None
        self.data_batches: list[dict[str, Any]] = []
        self.status_errors: list[int] = []
        self._done = threading.Event()

    def __call__(self, result: Any, err_code: Any) -> None:  # OnResponse slot
        if err_code is not None and result is None:
            self.status_errors.append(
                err_code if isinstance(err_code, int) else str(type(err_code).__name__)
            )
            self._done.set()
            return
        if isinstance(result, list):
            batch: dict[str, Any] = {"records": len(result)}
            for entry in result:
                basic, cons = _etf_entry_parts(entry)
                if isinstance(basic, dict):
                    batch["basic_key_count"] = len(basic)
                    batch["basic_keys_sorted"] = sorted(basic)
                    batch["basic_value_types"] = sorted(
                        {type(value).__name__ for value in basic.values()}
                    )
                if isinstance(cons, list):
                    batch.setdefault("constituent_counts", []).append(len(cons))
                    if cons and isinstance(cons[0], dict):
                        batch["constituent_keys_sorted"] = sorted(cons[0])
            self.data_batches.append(batch)
        self._done.set()

    def summary(self) -> dict[str, Any]:
        self._done.wait(self.wait_seconds)
        return {
            "immediate_return": self.immediate_error,
            "data_batch_count": len(self.data_batches),
            "data_batches": self.data_batches,
            "status_errors": self.status_errors,
        }


def _etf_info_shapes(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Desensitized shape + invariant summary of one sync ETF result."""
    shape: dict[str, Any] = {}
    invariants: dict[str, Any] = {}
    if not isinstance(result, list):
        return {"type": type(result).__name__}, invariants
    shape["type"] = "list"
    shape["length"] = len(result)
    basics: list[Any] = []
    constituents: list[Any] = []
    for entry in result:
        basic, cons = _etf_entry_parts(entry)
        basics.append(basic)
        constituents.append(cons)
    first_basic = next((item for item in basics if isinstance(item, dict)), None)
    if not basics or first_basic is None:
        shape["entry_kind"] = type(result[0]).__name__ if result else None
        return shape, invariants
    shape["entry_kind"] = "pair"
    if isinstance(first_basic, dict):
        shape["basic_keys_sorted"] = sorted(first_basic)
        shape["basic_value_types"] = {
            key: type(value).__name__ for key, value in sorted(first_basic.items())
        }
        shape["all_basic_key_sets_identical"] = all(
            isinstance(item, dict) and sorted(item.keys()) == sorted(first_basic)
            for item in basics
        )
        flags = {str(first_basic.get(name, "")) for name in ("publish", "creation",
                                                             "redemption")}
        market_types = {
            item.get("market_type") for item in basics if isinstance(item, dict)
        }
        trading_days = {
            item.get("trading_day") for item in basics if isinstance(item, dict)
        }
        units = {
            item.get("creation_redemption_unit")
            for item in basics
            if isinstance(item, dict)
        }
        navs = {item.get("nav") for item in basics if isinstance(item, dict)}
        codes = [
            item.get("security_code") for item in basics if isinstance(item, dict)
        ]
        invariants = {
            "flag_values_present_only": sorted(value for value in flags if value),
            "distinct_market_type": sorted(market_types),
            "trading_day_digit_lengths": sorted({len(str(v)) for v in trading_days}),
            "creation_unit_positive_exists": any(
                isinstance(v, int) and v > 0 for v in units
            ),
            "nav_non_negative_exists": any(isinstance(v, int) and v >= 0 for v in navs),
            "duplicate_security_code_count": len(codes) - len(set(codes)),
        }
    first_cons = next((item for item in constituents if isinstance(item, list)), None)
    if first_cons is not None:
        shape["constituent_count_first"] = len(first_cons)
        shape["constituent_total"] = sum(
            len(item) for item in constituents if isinstance(item, list)
        )
        if first_cons and isinstance(first_cons[0], dict):
            shape["constituent_keys_sorted"] = sorted(first_cons[0])
            shape["constituent_value_types"] = {
                key: type(value).__name__
                for key, value in sorted(first_cons[0].items())
            }
    return shape, invariants


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
        "--kind",
        choices=("calendar", "kline", "month_kline", "snapshot", "etf-info"),
        default="calendar",
    )
    parser.add_argument("--security-code", default="159518")
    parser.add_argument("--market-type", type=int, default=102)
    parser.add_argument("--date", type=int, default=20260825)
    # K-line window controls; the defaults keep the historical daily sample.
    parser.add_argument("--cyc-type", type=int, default=10008)
    parser.add_argument("--begin-date", type=int, default=20260825)
    parser.add_argument("--end-date", type=int, default=20260825)
    parser.add_argument("--begin-time", type=int, default=93000000)
    parser.add_argument("--end-time", type=int, default=93030000)
    # ETF-info controls; the async collector re-issues the same minimal
    # request once (after a cooldown) to count per-batch deliveries.
    parser.add_argument("--etf-collector-wait", type=float, default=30.0)
    parser.add_argument("--etf-cooldown", type=float, default=5.0)
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
        elif args.kind in ("kline", "month_kline"):
            request = tgw.ReqKline()
            request.security_code = "510300"
            request.market_type = tgw.MarketType.kSSE
            request.cq_flag = 0
            request.cq_date = 0
            request.qj_flag = 0
            request.cyc_type = int(args.cyc_type)
            request.cyc_def = 0
            request.auto_complete = 1
            request.begin_date = int(args.begin_date)
            request.end_date = int(args.end_date)
            request.begin_time = 0
            request.end_time = 0
            result, error = tgw.QueryKline(request, return_df_format=False)
            req_defaults = {}
        elif args.kind == "snapshot":
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
        etf_summary: dict[str, Any] = {}
        if args.kind == "etf-info":
            item = tgw.SubCodeTableItem()
            item.market = int(args.market_type)
            item.security_code = str(args.security_code)
            result, error = tgw.QueryETFInfo(item, return_df_format=False)
            req_defaults = {}
            time.sleep(max(0.0, args.etf_cooldown))
            collector = _EtfBatchCollector(args.etf_collector_wait)
            issued, issue_error = tgw.QueryETFInfo(item, query_spi=collector)
            collector.immediate_error = (
                int(issue_error) if isinstance(issue_error, int) else None
            )
            if issued:
                # First delivery or timeout, plus a short grace window so any
                # additional batches are still counted.
                collector._done.wait(args.etf_collector_wait)
                time.sleep(2.0)
            etf_summary = {
                "async_immediate_return": collector.immediate_error,
                "async_data_batch_count": len(collector.data_batches),
                "async_batches": collector.data_batches,
                "async_status_errors": [
                    value if isinstance(value, int) else str(value)
                    for value in collector.status_errors
                ],
            }
            del collector
        invariants: dict[str, Any] = {}
        if args.kind in ("kline", "month_kline") and isinstance(result, list) and result:
            row = result[0]
            if isinstance(row, dict):
                invariants = {
                    "orig_time_equals_kline_time": row.get("orig_time") == row.get("kline_time"),
                    "orig_time_is_zero": row.get("orig_time") == 0,
                    "variety_category_is_zero": row.get("variety_category") == 0,
                    "kline_time_digit_lengths": sorted(
                        {len(str(item["kline_time"])) for item in result
                         if isinstance(item, dict)},
                    ),
                    "distinct_market_type": sorted(
                        {item["market_type"] for item in result if isinstance(item, dict)},
                    ),
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
        etf_shapes: dict[str, Any] = {}
        if args.kind == "etf-info":
            etf_shapes, etf_invariants = _etf_info_shapes(result)
            invariants = etf_invariants
        print(json.dumps({
            "login": True,
            "query_kind": args.kind,
            "set_param_statuses": sorted(set(set_codes.values())),
            "query_error_type": type(error).__name__,
            "query_error": int(error) if isinstance(error, int) else None,
            "result_shape": safe_shape(result) if args.kind != "etf-info" else etf_shapes,
            "result_invariants": invariants,
            "req_default_fields": req_defaults,
            "etf_async": etf_summary,
        }, sort_keys=True))
    finally:
        tgw.Close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

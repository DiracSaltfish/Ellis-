#!/usr/bin/env python3
"""Desensitized official-Linux oracle for two narrow securities-info cases.

This tool intentionally issues only these synchronous internet-mode probes:

* SZSE ``159919`` as a single ``SubCodeTableItem``;
* the ordered list SSE ``510300`` then SZSE ``159919``.

It reports container and protocol-neutral type/shape facts only.  Response
values, account material, endpoints, tokens, and raw frames stay in memory and
are never serialized.
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


_SSE = (101, "510300")
_SZSE = (102, "159919")


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _read_username() -> str:
    """Read a one-run account without echoing or retaining it."""
    username = (
        getpass.getpass("one-run username: ")
        if sys.stdin.isatty()
        else sys.stdin.readline()
    ).strip()
    if not username:
        raise ValueError("one-run username must not be empty")
    return username


def _make_item(tgw: Any, market: int, code: str) -> Any:
    item = tgw.SubCodeTableItem()
    item.market = market
    item.security_code = code
    return item


def _summary(rows: Any, expected_items: list[tuple[int, str]]) -> dict[str, Any]:
    """Describe one official return without retaining any business value."""
    result: dict[str, Any] = {"container_type": type(rows).__name__}
    if not isinstance(rows, list):
        return result
    result["row_count"] = len(rows)
    if not rows:
        return result
    first = rows[0]
    if not isinstance(first, dict):
        result["entry_type"] = type(first).__name__
        return result
    columns = list(first)
    row_dicts = [row for row in rows if isinstance(row, dict)]
    result.update({
        "entry_type": "dict",
        "column_count": len(columns),
        "columns_in_order": columns,
        "column_types": {key: type(first[key]).__name__ for key in columns},
        "all_rows_same_column_order": len(row_dicts) == len(rows) and all(
            list(row) == columns for row in row_dicts
        ),
        "only_int_and_str_values": all(
            isinstance(value, (int, str)) and not isinstance(value, bool)
            for row in row_dicts for value in row.values()
        ),
        # Market enums and the known request-order are protocol metadata, not
        # response business values.  This pins down return ordering for the
        # two-item call without serializing any security code.
        "market_sequence": [row.get("market_type") for row in row_dicts],
        "market_sequence_matches_request": [
            row.get("market_type") for row in row_dicts
        ] == [market for market, _code in expected_items],
        "distinct_variety_categories": sorted({
            row.get("variety_category") for row in row_dicts
            if isinstance(row.get("variety_category"), int)
        }),
        "date_digit_lengths": sorted({
            len(str(row[name]))
            for row in row_dicts
            for name in ("list_day", "expire_date")
            if isinstance(row.get(name), int)
        }),
        # Values are compared only in process memory, then reduced to this
        # boolean.  It prevents a server-side mix-up from looking like a
        # successful ordered multi-item result without exposing codes.
        "security_code_sequence_matches_request": [
            (row.get("market_type"), row.get("security_code")) for row in row_dicts
        ] == expected_items,
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=Path("/opt/galaxy-relay/config/config.yaml")
    )
    parser.add_argument(
        "--env-file", type=Path, default=Path("/etc/galaxy-relay/relay.env")
    )
    parser.add_argument("--username-stdin", action="store_true")
    parser.add_argument(
        "--cooldown-seconds", type=float, default=5.0,
        help="quiet delay between the single and ordered-pair probes",
    )
    args = parser.parse_args()
    if not args.username_stdin:
        raise ValueError("--username-stdin is required for this one-run oracle")

    with open(os.devnull, "w", encoding="utf-8") as devnull, \
            contextlib.redirect_stdout(devnull), \
            contextlib.redirect_stderr(devnull):
        import tgw
        import yaml

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    upstream = config["amazingdata"]
    host_cfg = upstream["hosts"][0]
    env = _load_env_file(args.env_file)
    username = _read_username()
    password = env[upstream["password_env"]]

    report: dict[str, Any] = {
        "scope": "szse-single-159919; ordered-sse-510300-szse-159919-pair; sync-only",
        "static": {
            "query_signature": str(inspect.signature(tgw.QuerySecuritiesInfo)),
            "item_type": tgw.SubCodeTableItem.__name__,
        },
    }
    cfg = tgw.Cfg()
    cfg.username = username
    cfg.password = password
    cfg.server_vip = host_cfg["host"]
    cfg.server_port = int(host_cfg["port"])
    cfg.force_logout = False
    logged_in = False
    try:
        with open(os.devnull, "w", encoding="utf-8") as devnull, \
                contextlib.redirect_stdout(devnull), \
                contextlib.redirect_stderr(devnull):
            logged_in = bool(tgw.Login(cfg, tgw.ApiMode.kInternetMode))
        report["login"] = logged_in
        if not logged_in:
            return_code = 2
        else:
            szse_rows, szse_error = tgw.QuerySecuritiesInfo(
                _make_item(tgw, *_SZSE), return_df_format=False
            )
            report["szse_single"] = {
                "error_type": type(szse_error).__name__,
                "error_code": szse_error if isinstance(szse_error, int) else None,
                "result": _summary(szse_rows, [_SZSE]),
            }
            time.sleep(max(0.0, args.cooldown_seconds))
            pair_rows, pair_error = tgw.QuerySecuritiesInfo(
                [_make_item(tgw, *_SSE), _make_item(tgw, *_SZSE)],
                return_df_format=False,
            )
            report["ordered_pair"] = {
                "request_market_order": [101, 102],
                "error_type": type(pair_error).__name__,
                "error_code": pair_error if isinstance(pair_error, int) else None,
                "result": _summary(pair_rows, [_SSE, _SZSE]),
            }
            return_code = 0
    except Exception as exc:  # pragma: no cover - official live boundary
        report["failure"] = {"exception_type": type(exc).__name__}
        return_code = 2
    finally:
        if logged_in:
            try:
                with open(os.devnull, "w", encoding="utf-8") as devnull, \
                        contextlib.redirect_stdout(devnull), \
                        contextlib.redirect_stderr(devnull):
                    tgw.Close()
                report["cleanup"] = {"closed": True}
            except Exception as exc:  # pragma: no cover - live-only cleanup
                report["cleanup"] = {"closed": False, "exception_type": type(exc).__name__}

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())

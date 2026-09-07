#!/usr/bin/env python3
"""Desensitized Mac oracle for the two verified securities-info cases only.

The command intentionally has no free code/market argument.  It runs exactly
one SZSE ``159919`` synchronous query, waits, then runs exactly the ordered
SSE ``510300`` + SZSE ``159919`` synchronous pair.  Only contract metadata is
printed; business values and all credential material remain in process memory.
"""
from __future__ import annotations

import argparse
import configparser
import contextlib
import getpass
import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "python"))

_SSE = (101, "510300")
_SZSE = (102, "159919")


def _strip_inline(value: str) -> str:
    return value.split("#", 1)[0].strip()


def _load_config(path: Path) -> tuple[str, int, str, str, int]:
    parser = configparser.ConfigParser()
    with path.open(encoding="utf-8") as stream:
        parser.read_file(stream)
    section = parser["galaxy"]
    hosts = [
        item.strip()
        for item in _strip_inline(section["host"]).replace("，", " ").split()
        if item.strip()
    ]
    if not hosts:
        raise ValueError("galaxy config contains no host")
    import tgw_macos as tgw
    mode_name = _strip_inline(section.get("api_mode", "kInternetMode"))
    return (
        hosts[0],
        section.getint("port"),
        _strip_inline(section["username"]),
        section["password"].strip(),
        int(getattr(tgw.ApiMode, mode_name)),
    )


def _read_username() -> str:
    username = (
        getpass.getpass("one-run username: ")
        if sys.stdin.isatty()
        else sys.stdin.readline()
    ).strip()
    if not username:
        raise ValueError("one-run username must not be empty")
    return username


def _item(tgw: Any, market: int, code: str) -> Any:
    item = tgw.SubCodeTableItem().set_code(code)
    item.market = market
    return item


def _summary(rows: Any, expected_items: list[tuple[int, str]]) -> dict[str, Any]:
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
    response_items = [
        (row.get("market_type"), row.get("security_code")) for row in row_dicts
    ]
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
        "market_sequence": [market for market, _code in response_items],
        "market_sequence_matches_request": [market for market, _code in response_items] == [
            market for market, _code in expected_items
        ],
        "security_code_sequence_matches_request": response_items == expected_items,
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
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--username-stdin", action="store_true")
    parser.add_argument("--cooldown-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if not args.username_stdin:
        raise ValueError("--username-stdin is required for this one-run oracle")

    import tgw_macos as tgw

    host, port, _configured_username, password, mode = _load_config(args.config)
    username = _read_username()
    report: dict[str, Any] = {
        "scope": "szse-single-159919; ordered-sse-510300-szse-159919-pair; sync-only",
        "static": {
            "query_signature": str(inspect.signature(tgw.QuerySecuritiesInfo)),
            "item_type": tgw.SubCodeTableItem.__name__,
        },
    }
    cfg = tgw.Cfg().set(
        server_vip=host,
        server_port=port,
        username=username,
        password=password,
        force_logout=False,
    )
    logged_in = False
    try:
        with open(os.devnull, "w", encoding="utf-8") as devnull, \
                contextlib.redirect_stdout(devnull), \
                contextlib.redirect_stderr(devnull):
            logged_in = bool(tgw.Login(cfg, mode))
        report["login"] = logged_in
        if not logged_in:
            return_code = 2
        else:
            with open(os.devnull, "w", encoding="utf-8") as devnull, \
                    contextlib.redirect_stdout(devnull), \
                    contextlib.redirect_stderr(devnull):
                szse_rows, szse_error = tgw.QuerySecuritiesInfo(
                    _item(tgw, *_SZSE), return_df_format=False
                )
            report["szse_single"] = {
                "error_type": type(szse_error).__name__,
                "error_code": szse_error if isinstance(szse_error, int) else None,
                "result": _summary(szse_rows, [_SZSE]),
            }
            time.sleep(max(0.0, args.cooldown_seconds))
            with open(os.devnull, "w", encoding="utf-8") as devnull, \
                    contextlib.redirect_stdout(devnull), \
                    contextlib.redirect_stderr(devnull):
                pair_rows, pair_error = tgw.QuerySecuritiesInfo(
                    [_item(tgw, *_SSE), _item(tgw, *_SZSE)],
                    return_df_format=False,
                )
            report["ordered_pair"] = {
                "request_market_order": [101, 102],
                "error_type": type(pair_error).__name__,
                "error_code": pair_error if isinstance(pair_error, int) else None,
                "result": _summary(pair_rows, [_SSE, _SZSE]),
            }
            return_code = 0
    except Exception as exc:  # pragma: no cover - authorized live boundary
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

#!/usr/bin/env python3
"""Observe official ``BaseData.get_code_info(EXTRA_ETF)`` without values.

The script is intentionally limited to one authorised high-level call.  It
keeps raw DataFrames only in process memory long enough to verify the wrapper's
normalisation, then emits a JSON summary of signatures, container shapes,
dtypes and boolean invariants.  It never prints credentials, identifiers,
names, prices, tokens, endpoints or response rows.
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import inspect
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any


def load_env_file(path: Path) -> dict[str, str]:
    """Read a protected env file without echoing any setting."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _read_username_from_stdin() -> str:
    """Read a one-run account without echoing or persisting it."""
    if sys.stdin.isatty():
        username = getpass.getpass("one-run username: ")
    else:
        username = sys.stdin.readline()
    username = username.strip()
    if not username:
        raise ValueError("one-run username must not be empty")
    return username


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.split(b"\0", 1)[0].decode("utf-8", errors="strict")
    return str(value)


def _market_suffix(market: int) -> str:
    return {101: ".SH", 102: ".SZ", 2: ".BJ"}[market]


def _is_etf_index(index: Any) -> bool:
    return isinstance(index, str) and bool(
        re.fullmatch(
            r"(?:"
            r"(?:510|511|512|513|515|516|517|518|520|530|551|560|561|562|563|588|589)\d{3}\.SH"
            r"|159\d{3}\.SZ"
            r")",
            index,
        )
    )


def _frame_summary(frame: Any) -> dict[str, Any]:
    """Return dtype/order/null/index facts only; never any row value."""
    summary: dict[str, Any] = {"container_type": type(frame).__name__}
    if not hasattr(frame, "columns") or not hasattr(frame, "index"):
        return summary
    columns = [str(column) for column in frame.columns]
    index = frame.index
    values = list(index)
    summary.update(
        {
            "rows": int(frame.shape[0]),
            "column_order": columns,
            "dtypes": {str(column): str(frame[column].dtype) for column in frame.columns},
            "null_counts": {str(column): int(frame[column].isna().sum()) for column in frame.columns},
            "index": {
                "class": type(index).__name__,
                "name": index.name,
                "dtype": str(getattr(index, "dtype", "unknown")),
                "unique": bool(index.is_unique),
                "all_strings": all(isinstance(value, str) for value in values),
                "suffix_counts": {
                    suffix: sum(
                        isinstance(value, str) and value.endswith(suffix)
                        for value in values
                    )
                    for suffix in (".SH", ".SZ", ".BJ")
                },
                "all_match_extra_etf_pattern": all(_is_etf_index(value) for value in values),
            },
        }
    )
    return summary


def _numeric_invariants(frame: Any) -> dict[str, Any]:
    names = ("pre_close", "high_limited", "low_limited", "price_tick")
    result: dict[str, Any] = {}
    for name in names:
        if name not in frame.columns:
            result[name] = {"present": False}
            continue
        values = frame[name]
        finite = values.dropna().map(lambda value: isinstance(value, (int, float)) and math.isfinite(value))
        result[name] = {
            "present": True,
            "all_non_null_numeric_finite": bool(finite.all()),
            "times_1e6_are_integral": bool(
                values.dropna().map(
                    lambda value: isinstance(value, (int, float))
                    and math.isfinite(value)
                    and abs(value * 1_000_000 - round(value * 1_000_000)) < 1e-6
                ).all()
            ),
        }
    return result


def _raw_call_summary(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "market": call["market"],
            "security_code_empty": call["security_code_empty"],
            "error_type": call["error_type"],
            "error_code": call["error_code"],
            "result": _frame_summary(call["frame"]),
        }
        for call in calls
    ]


def _scaling_invariants(result: Any, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare wrapper output to the intercepted raw frames in memory only."""
    try:
        import pandas as pd
    except ImportError:
        return {"available": False}
    rows: list[Any] = []
    for call in calls:
        frame = call["frame"]
        if not hasattr(frame, "columns") or "security_code" not in frame.columns:
            return {"available": False, "reason": "raw_frame_shape"}
        minimal = frame.loc[:, [
            "security_code", "symbol", "security_status", "pre_close_price",
            "high_limited", "low_limited", "price_tick", "list_day",
        ]].copy()
        minimal.index = [
            _as_text(code) + _market_suffix(call["market"])
            for code in minimal["security_code"]
        ]
        rows.append(minimal)
    raw = pd.concat(rows, axis=0)
    raw = raw.loc[raw.index.intersection(result.index)]
    observed = result.loc[raw.index]
    checks = {
        "row_set_equal": set(raw.index) == set(result.index),
        "symbol_exact": observed["symbol"].equals(raw["symbol"]),
        "security_status_exact": observed["security_status"].equals(raw["security_status"]),
        "list_day_exact": (
            "list_day" in observed.columns and observed["list_day"].equals(raw["list_day"])
        ),
    }
    for result_name, raw_name in (
        ("pre_close", "pre_close_price"),
        ("high_limited", "high_limited"),
        ("low_limited", "low_limited"),
        ("price_tick", "price_tick"),
    ):
        if result_name not in observed.columns:
            checks[f"{result_name}_scaled_exact"] = False
            continue
        scaled = observed[result_name] * 1_000_000
        # ``Series.equals`` treats an exactly equal float64/int64 pair as
        # unequal solely because their dtypes differ.  The high-level wrapper
        # intentionally introduces float64 through division, so compare the
        # aligned numeric values without serializing either side.
        checks[f"{result_name}_scaled_exact"] = bool(
            (scaled == raw[raw_name]).fillna(False).all()
        )
    return {"available": True, **checks}


def _logout(module: Any, username: str) -> dict[str, Any]:
    try:
        logout = getattr(module, "logout")
        signature = inspect.signature(logout)
        with open(os.devnull, "w", encoding="utf-8") as devnull, \
                contextlib.redirect_stdout(devnull), \
                contextlib.redirect_stderr(devnull):
            if len(signature.parameters) == 0:
                logout()
            else:
                logout(username)
        return {"attempted": True, "ok": True}
    except Exception as exc:  # pragma: no cover - live-only cleanup boundary
        try:
            import tgw

            with open(os.devnull, "w", encoding="utf-8") as devnull, \
                    contextlib.redirect_stdout(devnull), \
                    contextlib.redirect_stderr(devnull):
                tgw.Close()
            return {"attempted": True, "ok": True, "fallback": "tgw.Close"}
        except Exception as fallback_exc:
            return {
                "attempted": True,
                "ok": False,
                "exception_type": type(exc).__name__,
                "fallback_exception_type": type(fallback_exc).__name__,
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=Path("/opt/galaxy-relay/config/config.yaml")
    )
    parser.add_argument(
        "--env-file", type=Path, default=Path("/etc/galaxy-relay/relay.env")
    )
    parser.add_argument("--username-stdin", action="store_true")
    args = parser.parse_args()

    with open(os.devnull, "w", encoding="utf-8") as devnull, \
            contextlib.redirect_stdout(devnull), \
            contextlib.redirect_stderr(devnull):
        import AmazingData as ad
        import tgw
        import yaml

    base_class = ad.BaseData
    report: dict[str, Any] = {
        "static": {
            "base_data_type": type(base_class()).__name__,
            "get_code_info_signature": str(inspect.signature(base_class.get_code_info)),
            "get_code_info_defaults": [
                repr(value) for value in (base_class.get_code_info.__defaults__ or ())
            ],
        }
    }
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    upstream = config["amazingdata"]
    host_cfg = upstream["hosts"][0]
    env = load_env_file(args.env_file)
    username = env[upstream["username_env"]]
    password = env[upstream["password_env"]]
    if args.username_stdin:
        username = _read_username_from_stdin()

    calls: list[dict[str, Any]] = []
    original_query = tgw.QuerySecuritiesInfo

    def observed_query(item: Any, *call_args: Any, **call_kwargs: Any) -> Any:
        market = int(getattr(item, "market", -1))
        code = _as_text(getattr(item, "security_code", ""))
        with open(os.devnull, "w", encoding="utf-8") as devnull, \
                contextlib.redirect_stdout(devnull), \
                contextlib.redirect_stderr(devnull):
            result, error = original_query(item, *call_args, **call_kwargs)
        calls.append(
            {
                "market": market,
                "security_code_empty": code == "",
                "error_type": type(error).__name__,
                "error_code": error if isinstance(error, int) else None,
                "frame": result,
            }
        )
        return result, error

    logged_in = False
    try:
        with open(os.devnull, "w", encoding="utf-8") as devnull, \
                contextlib.redirect_stdout(devnull), \
                contextlib.redirect_stderr(devnull):
            login_result = ad.login(
                username=username,
                password=password,
                host=host_cfg["host"],
                port=int(host_cfg["port"]),
            )
        logged_in = bool(login_result)
        report["login"] = {
            "return_type": type(login_result).__name__,
            "truthy": logged_in,
        }
        if not logged_in:
            raise RuntimeError("official login was not truthy")
        tgw.QuerySecuritiesInfo = observed_query
        base = base_class()
        with open(os.devnull, "w", encoding="utf-8") as devnull, \
                contextlib.redirect_stdout(devnull), \
                contextlib.redirect_stderr(devnull):
            result = base.get_code_info(security_type="EXTRA_ETF")
        report["calls"] = _raw_call_summary(calls)
        report["result"] = _frame_summary(result)
        report["price_invariants"] = _numeric_invariants(result)
        report["raw_to_wrapper"] = _scaling_invariants(result, calls)
    except Exception as exc:  # pragma: no cover - live-only oracle boundary
        report["failure"] = {"exception_type": type(exc).__name__}
        return_code = 2
    else:
        return_code = 0
    finally:
        tgw.QuerySecuritiesInfo = original_query
        if logged_in:
            report["cleanup"] = _logout(ad, username)

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())

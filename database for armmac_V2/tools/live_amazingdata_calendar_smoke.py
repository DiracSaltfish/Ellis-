#!/usr/bin/env python3
"""Run the reconstructed high-level AmazingData calendar path, shape only."""
from __future__ import annotations

import argparse
import contextlib
import configparser
import datetime as dt
import getpass
import inspect
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))
sys.path.insert(0, str(ROOT / "experimental" / "amazingdata_compat"))

import tgw_macos as tgw  # noqa: E402
from amazingdata_re import BaseData  # noqa: E402


def _strip_inline(value: str) -> str:
    return value.split("#", 1)[0].strip()


def load_config(path: Path) -> tuple[list[str], int, str, str, int]:
    parser = configparser.ConfigParser()
    with path.open(encoding="utf-8") as stream:
        parser.read_file(stream)
    section = parser["galaxy"]
    raw_hosts = _strip_inline(section["host"])
    hosts = [item.strip() for item in raw_hosts.replace("，", " ").split() if item.strip()]
    mode_name = _strip_inline(section.get("api_mode", "kInternetMode"))
    return (
        hosts,
        section.getint("port"),
        _strip_inline(section["username"]),
        section["password"].strip(),
        int(getattr(tgw.ApiMode, mode_name)),
    )


def _date_invariants(values: list[Any]) -> dict[str, bool]:
    if not values:
        return {
            "all_are_eight_digit_strings": True,
            "all_are_eight_digit_ints": True,
            "all_are_date_or_datetime": True,
        }
    return {
        "all_are_eight_digit_strings": all(
            isinstance(value, str) and bool(re.fullmatch(r"[0-9]{8}", value))
            for value in values
        ),
        "all_are_eight_digit_ints": all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and bool(re.fullmatch(r"[0-9]{8}", str(value)))
            for value in values
        ),
        "all_are_date_or_datetime": all(
            isinstance(value, (dt.date, dt.datetime)) for value in values
        ),
    }


def summarize_calendar(value: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {"container_type": type(value).__name__}
    if not isinstance(value, (list, tuple)):
        return summary
    values = list(value)
    summary.update(
        {
            "length": len(values),
            "element_types": sorted({type(item).__name__ for item in values}),
            "sorted_ascending": (
                values == sorted(values)
                if all(isinstance(item, type(values[0])) for item in values) and values
                else True
            ),
            "unique": len(values) == len(set(values)),
            "date_invariants": _date_invariants(values),
        }
    )
    return summary


def invoke(base: BaseData, **kwargs: Any) -> tuple[Any | None, dict[str, Any]]:
    try:
        value = base.get_calendar(**kwargs)
    except Exception as exc:  # pragma: no cover - live-only smoke boundary
        return None, {"ok": False, "exception_type": type(exc).__name__}
    state = getattr(base, "calendar", None)
    return value, {
        "ok": True,
        "return": summarize_calendar(value),
        "calendar_attribute": summarize_calendar(state),
        "calendar_attribute_equals_return": state == value,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "galaxy_account.ini")
    parser.add_argument(
        "--username-stdin",
        action="store_true",
        help="read one authorized username from stdin without persisting or printing it",
    )
    parser.add_argument(
        "--branch",
        choices=("all", "default", "str", "datetime"),
        default="all",
        help="run one branch after a flow-control cooldown, or all branches when safe",
    )
    args = parser.parse_args()
    hosts, port, username, password, mode = load_config(args.config)
    if args.username_stdin:
        username = (
            getpass.getpass("one-run username: ")
            if sys.stdin.isatty()
            else sys.stdin.readline().strip()
        )
        if not username:
            parser.error("--username-stdin requires a non-empty line on stdin")

    report: dict[str, Any] = {}
    result_code = 2
    for host in hosts:
        cfg = tgw.Cfg().set(
            server_vip=host,
            server_port=port,
            username=username,
            password=password,
            force_logout=False,
        )
        try:
            with open(os.devnull, "w", encoding="utf-8") as devnull, \
                    contextlib.redirect_stdout(devnull), \
                    contextlib.redirect_stderr(devnull):
                logged_in = bool(tgw.Login(cfg, mode))
            report["login"] = {"ok": logged_in}
            if not logged_in:
                break
            base = BaseData()
            calls: dict[str, dict[str, Any]] = {}
            values: dict[str, Any | None] = {}
            requested = (
                ("default", {}),
                ("str", {"data_type": "str"}),
                ("datetime", {"data_type": "datetime"}),
            )
            for name, kwargs in requested:
                if args.branch not in ("all", name):
                    continue
                value, summary = invoke(base, **kwargs)
                values[name] = value
                calls[name] = summary
                if not summary["ok"]:
                    # Avoid turning one transport rejection into a burst of
                    # retry-like requests for the remaining branches.
                    break
            report["static"] = {
                "base_data_type": type(base).__name__,
                "get_calendar_signature": str(inspect.signature(BaseData.get_calendar)),
            }
            if args.branch == "all":
                calls["default_equals_explicit_str"] = (
                    values["default"] == values["str"]
                    if {"default", "str"}.issubset(values)
                    else None
                )
                calls["str_equals_datetime"] = (
                    values["str"] == values["datetime"]
                    if {"str", "datetime"}.issubset(values)
                    else None
                )
            report["calls"] = calls
            result_code = 0
            break
        except Exception as exc:  # pragma: no cover - live-only smoke boundary
            report["failure"] = {"exception_type": type(exc).__name__}
        finally:
            try:
                tgw.Close()
                report["cleanup"] = {"attempted": True, "ok": True}
            except Exception as exc:  # pragma: no cover - cleanup boundary
                report["cleanup"] = {
                    "attempted": True,
                    "ok": False,
                    "exception_type": type(exc).__name__,
                }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())

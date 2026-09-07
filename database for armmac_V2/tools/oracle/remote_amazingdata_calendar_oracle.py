#!/usr/bin/env python3
"""Run the official Linux AmazingData calendar oracle without exposing values.

This script is intentionally narrow: it inspects and exercises only
``AmazingData.BaseData.get_calendar`` for its documented default SH market and
the ``str``/``datetime`` data_type branches.  It emits signatures and
container/type/invariant summaries only; credentials, dates, rows, responses,
tokens and endpoints are never printed.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import getpass
import inspect
import json
import os
import re
from pathlib import Path
from typing import Any


def load_env_file(path: Path) -> dict[str, str]:
    """Read the protected relay environment without echoing any value."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _date_invariants(values: list[Any]) -> dict[str, bool]:
    """Report date-shape facts without returning any business date."""
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
    """Return a desensitized observable contract for one calendar call."""
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


def _invoke(base: Any, **kwargs: Any) -> tuple[Any | None, dict[str, Any]]:
    try:
        value = base.get_calendar(**kwargs)
    except Exception as exc:  # pragma: no cover - live-only oracle boundary
        return None, {"ok": False, "exception_type": type(exc).__name__}
    state = getattr(base, "calendar", None)
    return value, {
        "ok": True,
        "return": summarize_calendar(value),
        "calendar_attribute": summarize_calendar(state),
        "calendar_attribute_equals_return": state == value,
    }


def _logout(module: Any, username: str) -> dict[str, Any]:
    """Always attempt the official wrapper's normal close path, without detail."""
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
    parser.add_argument(
        "--username-stdin",
        action="store_true",
        help="read one authorized username from stdin without persisting or printing it",
    )
    args = parser.parse_args()

    import AmazingData as ad
    import yaml

    base_class = ad.BaseData
    report: dict[str, Any] = {
        "static": {
            "base_data_type": type(base_class()).__name__,
            "get_calendar_signature": str(inspect.signature(base_class.get_calendar)),
            "get_calendar_defaults": [
                repr(value)
                for value in (base_class.get_calendar.__defaults__ or ())
            ],
            "logout_signature": str(inspect.signature(ad.logout)),
        }
    }
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    upstream = config["amazingdata"]
    host_cfg = upstream["hosts"][0]
    env = load_env_file(args.env_file)
    username = env[upstream["username_env"]]
    password = env[upstream["password_env"]]
    if args.username_stdin:
        username = getpass.getpass("one-run username: ").strip()
        if not username:
            raise ValueError("one-run username must not be empty")

    logged_in = False
    try:
        # The upstream wrapper itself writes a login diagnostic containing
        # session material.  Discard it at the process boundary; this oracle
        # owns the only safe, shape-only output channel.
        with open(os.devnull, "w", encoding="utf-8") as devnull, \
                contextlib.redirect_stdout(devnull), \
                contextlib.redirect_stderr(devnull):
            login_result = ad.login(
                username=username,
                password=password,
                host=host_cfg["host"],
                port=int(host_cfg["port"]),
            )
        logged_in = True
        report["login"] = {
            "return_type": type(login_result).__name__,
            "truthy": bool(login_result),
        }
        base = base_class()
        default_value, default_summary = _invoke(base)
        str_value, str_summary = _invoke(base, data_type="str")
        datetime_value, datetime_summary = _invoke(base, data_type="datetime")
        report["calls"] = {
            "default": default_summary,
            "str": str_summary,
            "datetime": datetime_summary,
            "default_equals_explicit_str": default_value == str_value,
            "str_equals_datetime": str_value == datetime_value,
        }
    except Exception as exc:  # pragma: no cover - live-only oracle boundary
        report["failure"] = {"exception_type": type(exc).__name__}
        return_code = 2
    else:
        return_code = 0
    finally:
        if logged_in:
            report["cleanup"] = _logout(ad, username)

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())

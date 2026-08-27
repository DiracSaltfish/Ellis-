#!/usr/bin/env python3
"""Capture one sanitized TGW snapshot for a single security.

This diagnostic deliberately subscribes to exactly one symbol. Credentials,
connection identifiers, and opaque TGW headers are never written to output.
"""
from __future__ import annotations

import argparse
import configparser
import json
import time
from pathlib import Path
from typing import Any


def strip_inline_comment(value: str) -> str:
    return value.split("#", 1)[0].strip()


def sanitized_event(event: dict[str, Any], received_ms: int) -> dict[str, Any]:
    headers = event.get("headers")
    safe_headers: dict[str, Any] = {}
    if isinstance(headers, dict):
        for key in ("tag", "method"):
            if key in headers:
                safe_headers[key] = headers[key]
    return {
        "received_ms": received_ms,
        "headers": safe_headers,
        "status": event.get("status"),
        "is_delta": event.get("is_delta"),
        "data": event.get("data"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, type=Path)
    parser.add_argument("--username-file", type=Path)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    import tgw_macos as tgw

    account = configparser.ConfigParser()
    with args.account.open(encoding="utf-8") as stream:
        account.read_file(stream)
    section = account["galaxy"]
    username = strip_inline_comment(section["username"])
    if args.username_file:
        username = args.username_file.read_text(encoding="utf-8").strip()
    cfg = tgw.Cfg().set(
        server_vip=strip_inline_comment(section["host"]),
        server_port=section.getint("port"),
        username=username,
        password=section["password"].strip(),
        force_logout=section.getboolean("force_logout", fallback=False),
    )
    mode = getattr(tgw.ApiMode, strip_inline_comment(section.get("api_mode", "kInternetMode")))
    result: dict[str, Any] = {
        "symbol": args.symbol.upper(),
        "started_ms": int(time.time() * 1000),
        "login": False,
        "subscribe_result": None,
        "event": None,
        "error": None,
    }
    item = None
    try:
        result["login"] = bool(tgw.Login(cfg, mode))
        if not result["login"]:
            result["error"] = "login_failed"
            return 2
        symbol = args.symbol.upper()
        item = tgw.SubscribeItem().set_code(symbol[:6])
        item.market = tgw.MarketType.kSSE if symbol.endswith(".SH") else tgw.MarketType.kSZSE
        item.flag = tgw.SubscribeDataType.kSnapshot
        item.category_type = 0
        result["subscribe_result"] = int(tgw.Subscribe(item))
        if result["subscribe_result"] != 0:
            result["error"] = "subscribe_rejected"
            return 3
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            try:
                event = tgw.ReceiveRawEvent(timeout=min(1.0, max(0.05, deadline - time.monotonic())))
            except TimeoutError:
                continue
            if not isinstance(event, dict):
                continue
            data = event.get("data")
            if isinstance(data, dict):
                event_code = str(data.get("2", data.get("security_code", "")))
                if event_code and event_code != symbol[:6]:
                    continue
            result["event"] = sanitized_event(event, int(time.time() * 1000))
            return 0
        result["error"] = "event_timeout"
        return 4
    except Exception as exc:
        # Do not serialize exception text: an SDK error could echo credentials.
        result["error"] = f"{type(exc).__name__}"
        return 5
    finally:
        result["finished_ms"] = int(time.time() * 1000)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if item is not None:
            try:
                tgw.UnSubscribe(item)
            except Exception:
                pass
        try:
            tgw.Close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

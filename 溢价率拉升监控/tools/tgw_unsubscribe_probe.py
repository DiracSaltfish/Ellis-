#!/usr/bin/env python3
"""Verify that one TGW unsubscribe does not disturb a batch subscription."""
from __future__ import annotations

import argparse
import configparser
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from tgw_multi_probe import fingerprint_payload, load_symbols, make_item, strip_inline_comment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, type=Path)
    parser.add_argument("--username-file", type=Path)
    parser.add_argument("--watchlist", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--remove-symbol", required=True)
    parser.add_argument("--baseline-duration", type=float, default=10.0)
    parser.add_argument("--grace-duration", type=float, default=3.0)
    parser.add_argument("--observe-duration", type=float, default=20.0)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--events-output", required=True, type=Path)
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    if min(args.baseline_duration, args.grace_duration, args.observe_duration) < 0:
        parser.error("durations must be non-negative")

    import tgw_macos as tgw
    import tgw_macos._protocol as protocol

    symbols = load_symbols(args.watchlist)
    symbol_set = set(symbols)
    remove_symbol = args.remove_symbol.upper()
    if remove_symbol not in symbol_set:
        parser.error("--remove-symbol must be present in the initial watchlist")

    decoder_failures: list[dict[str, Any]] = []
    original_decoder = protocol.decode_server_payload

    def audited_decoder(payload: bytes) -> Any:
        try:
            return original_decoder(payload)
        except Exception:
            decoder_failures.append(fingerprint_payload(payload, protocol._decompress_zstd))
            raise

    protocol.decode_server_payload = audited_decoder

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

    def new_phase() -> dict[str, Any]:
        return {
            "events": 0,
            "full_events": 0,
            "delta_events": 0,
            "target_events": 0,
            "symbols": set(),
            "status_counts": Counter(),
        }

    phases = {name: new_phase() for name in ("baseline", "grace", "observe")}
    summary: dict[str, Any] = {
        "started_ms": int(time.time() * 1000),
        "sdk_version": tgw.__version__,
        "requested_symbols": len(symbols),
        "batch_size": args.batch_size,
        "remove_symbol": remove_symbol,
        "login": False,
        "batches": [],
        "unsubscribe": None,
        "phases": {},
        "decoder_failures": decoder_failures,
        "error": None,
    }
    items = [make_item(tgw, symbol) for symbol in symbols]
    item_by_symbol = dict(zip(symbols, items))
    active_symbols = set(symbols)

    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.events_output.parent.mkdir(parents=True, exist_ok=True)
    event_stream = None

    def receive_phase(name: str, duration: float) -> None:
        phase = phases[name]
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            try:
                event = tgw.ReceiveRawEvent(
                    timeout=min(1.0, max(0.05, deadline - time.monotonic()))
                )
            except TimeoutError:
                continue
            if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
                continue
            data = event["data"]
            code = str(data.get("2", data.get("security_code", "")))
            market = data.get("1", data.get("market_type"))
            suffix = ".SH" if market == 101 else ".SZ" if market == 102 else ""
            symbol = code + suffix if code else ""
            phase["events"] += 1
            phase["full_events" if not bool(event.get("is_delta")) else "delta_events"] += 1
            phase["target_events"] += int(symbol == remove_symbol)
            if symbol:
                phase["symbols"].add(symbol)
            phase["status_counts"][str(event.get("status"))] += 1
            safe_event = {
                "received_ms": int(time.time() * 1000),
                "phase": name,
                "headers": {
                    "tag": (event.get("headers") or {}).get("tag")
                    if isinstance(event.get("headers"), dict) else None
                },
                "status": event.get("status"),
                "is_delta": event.get("is_delta"),
                "data": data,
            }
            assert event_stream is not None
            event_stream.write(
                json.dumps(safe_event, ensure_ascii=False, separators=(",", ":")) + "\n"
            )

    result_code = 0
    try:
        summary["login"] = bool(tgw.Login(cfg, mode))
        if not summary["login"]:
            summary["error"] = "login_failed"
            result_code = 2
        else:
            for offset in range(0, len(items), args.batch_size):
                batch = items[offset:offset + args.batch_size]
                batch_started = time.monotonic()
                result = int(tgw.Subscribe(batch))
                summary["batches"].append({
                    "offset": offset,
                    "size": len(batch),
                    "result": result,
                    "latency_ms": round((time.monotonic() - batch_started) * 1000, 3),
                })
                if result != 0:
                    summary["error"] = "subscribe_rejected"
                    result_code = 3
                    break

        if result_code == 0:
            with args.events_output.open("w", encoding="utf-8") as opened_stream:
                event_stream = opened_stream
                receive_phase("baseline", args.baseline_duration)

                requested_ms = int(time.time() * 1000)
                unsubscribe_started = time.monotonic()
                unsubscribe_result = int(tgw.UnSubscribe(item_by_symbol[remove_symbol]))
                completed_ms = int(time.time() * 1000)
                summary["unsubscribe"] = {
                    "symbol": remove_symbol,
                    "requested_ms": requested_ms,
                    "completed_ms": completed_ms,
                    "result": unsubscribe_result,
                    "latency_ms": round((time.monotonic() - unsubscribe_started) * 1000, 3),
                }
                if unsubscribe_result != 0:
                    summary["error"] = "unsubscribe_rejected"
                    result_code = 4
                else:
                    active_symbols.remove(remove_symbol)
                    receive_phase("grace", args.grace_duration)
                    receive_phase("observe", args.observe_duration)
    except Exception as exc:
        summary["error"] = type(exc).__name__
        result_code = 5
    finally:
        for name, phase in phases.items():
            summary["phases"][name] = {
                "events": phase["events"],
                "full_events": phase["full_events"],
                "delta_events": phase["delta_events"],
                "target_events": phase["target_events"],
                "unique_symbols": len(phase["symbols"]),
                "status_counts": dict(phase["status_counts"]),
            }
        summary["finished_ms"] = int(time.time() * 1000)
        summary["decoder_failures"] = decoder_failures
        args.summary_output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        remaining_items = [item_by_symbol[symbol] for symbol in symbols if symbol in active_symbols]
        for offset in range(0, len(remaining_items), args.batch_size):
            try:
                tgw.UnSubscribe(remaining_items[offset:offset + args.batch_size])
            except Exception:
                break
        try:
            tgw.Close()
        except Exception:
            pass
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())

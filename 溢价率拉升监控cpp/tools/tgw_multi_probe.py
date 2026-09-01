#!/usr/bin/env python3
"""Controlled multi-symbol TGW subscription and coverage probe.

The tool keeps one login session, subscribes the requested watchlist in bounded
batches, and records only market events plus aggregate diagnostics. Unsupported
transport payloads are fingerprinted without persisting their raw contents.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any


def strip_inline_comment(value: str) -> str:
    return value.split("#", 1)[0].strip()


def load_symbols(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("symbols", payload) if isinstance(payload, dict) else payload
    symbols = [str(value).upper() for value in values]
    if len(symbols) != len(set(symbols)):
        raise ValueError("watchlist contains duplicate symbols")
    return symbols


def fingerprint_payload(payload: bytes, decompressor: Any = None) -> dict[str, Any]:
    zstd_magic = b"\x28\xb5\x2f\xfd"
    result: dict[str, Any] = {
        "length": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "first16_hex": payload[:16].hex(),
        "last8_hex": payload[-8:].hex(),
        "zstd_magic_offsets_first64": [i for i in range(min(64, len(payload) - 3))
                                          if payload[i:i + 4] == zstd_magic],
        "leading_byte": payload[0] if payload else None,
        "trailing_nul_count": len(payload) - len(payload.rstrip(b"\x00")),
    }
    offset = 0 if payload.startswith(zstd_magic) else 1 if payload[1:5] == zstd_magic else None
    if offset is None or decompressor is None:
        return result
    decoded = decompressor(payload[offset:])
    if decoded is None:
        result["decompressed"] = None
        return result
    structural: dict[str, Any] = {
        "length": len(decoded),
        "sha256": hashlib.sha256(decoded).hexdigest(),
        "first16_hex": decoded[:16].hex(),
        "last16_hex": decoded[-16:].hex(),
        "nul_count": decoded.count(b"\x00"),
        "newline_count": decoded.count(b"\n"),
        "leading_byte": decoded[0] if decoded else None,
    }
    try:
        text = decoded.decode("utf-8")
        structural["utf8"] = True
        stripped = text.rstrip("\x00\r\n\t ")
        try:
            parsed = json.loads(stripped)
            structural["json_type"] = type(parsed).__name__
            structural["json_items"] = len(parsed) if isinstance(parsed, (list, dict)) else None
        except json.JSONDecodeError as exc:
            structural["json_error_pos"] = exc.pos
            structural["json_error_type"] = exc.msg
            structural["json_error_window_hex"] = decoded[
                max(0, exc.pos - 8):min(len(decoded), exc.pos + 16)
            ].hex()
            segments = [segment for segment in stripped.split("\x00") if segment.strip()]
            parsed_types: Counter[str] = Counter()
            parsed_segments = 0
            for segment in segments:
                try:
                    parsed_types[type(json.loads(segment)).__name__] += 1
                    parsed_segments += 1
                except json.JSONDecodeError:
                    pass
            structural["nul_segments"] = len(segments)
            structural["nul_segments_json"] = parsed_segments
            structural["nul_segment_types"] = dict(parsed_types)
            stream_types: Counter[str] = Counter()
            stream_members = 0
            stream_offset = 0
            stream_error: dict[str, Any] | None = None
            decoder = json.JSONDecoder()
            while stream_offset < len(stripped):
                while stream_offset < len(stripped) and stripped[stream_offset].isspace():
                    stream_offset += 1
                if stream_offset >= len(stripped):
                    break
                try:
                    member, stream_offset = decoder.raw_decode(stripped, stream_offset)
                    stream_types[type(member).__name__] += 1
                    stream_members += 1
                except json.JSONDecodeError as stream_exc:
                    stream_error = {
                        "pos": stream_exc.pos,
                        "type": stream_exc.msg,
                        "window_hex": decoded[
                            max(0, stream_exc.pos - 8):min(len(decoded), stream_exc.pos + 16)
                        ].hex(),
                    }
                    break
            structural["stream_members"] = stream_members
            structural["stream_types"] = dict(stream_types)
            structural["stream_consumed"] = stream_offset
            structural["stream_error"] = stream_error
    except UnicodeDecodeError as exc:
        structural["utf8"] = False
        structural["utf8_error_pos"] = exc.start
    result["decompressed"] = structural
    return result


def make_item(tgw: Any, symbol: str) -> Any:
    item = tgw.SubscribeItem().set_code(symbol[:6])
    item.market = tgw.MarketType.kSSE if symbol.endswith(".SH") else tgw.MarketType.kSZSE
    item.flag = tgw.SubscribeDataType.kSnapshot
    item.category_type = 0
    return item


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, type=Path)
    parser.add_argument("--username-file", type=Path)
    parser.add_argument("--watchlist", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--append-symbol")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--events-output", required=True, type=Path)
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")

    import tgw_macos as tgw
    import tgw_macos._protocol as protocol

    symbols = load_symbols(args.watchlist)
    symbol_set = set(symbols)
    append_symbol = args.append_symbol.upper() if args.append_symbol else None
    if append_symbol and append_symbol in symbol_set:
        parser.error("--append-symbol must not already be in the watchlist")
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

    started_ms = int(time.time() * 1000)
    summary: dict[str, Any] = {
        "started_ms": started_ms,
        "requested_symbols": len(symbols),
        "batch_size": args.batch_size,
        "login": False,
        "batches": [],
        "append_subscription": None,
        "events": 0,
        "full_events": 0,
        "delta_events": 0,
        "unique_symbols": 0,
        "full_symbols": 0,
        "unknown_symbols": [],
        "missing_full_symbols": [],
        "status_counts": {},
        "numeric_key_sets": {},
        "decoder_failures": decoder_failures,
        "error": None,
    }
    items: list[Any] = []
    event_count = 0
    full_count = 0
    delta_count = 0
    seen_symbols: set[str] = set()
    full_symbols: set[str] = set()
    unknown_symbols: set[str] = set()
    status_counts: Counter[str] = Counter()
    key_sets: Counter[str] = Counter()

    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.events_output.parent.mkdir(parents=True, exist_ok=True)
    try:
        summary["login"] = bool(tgw.Login(cfg, mode))
        if not summary["login"]:
            summary["error"] = "login_failed"
            return 2
        items = [make_item(tgw, symbol) for symbol in symbols]
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
                return 3
        if append_symbol:
            append_item = make_item(tgw, append_symbol)
            append_requested_ms = int(time.time() * 1000)
            append_started = time.monotonic()
            append_result = int(tgw.Subscribe(append_item))
            summary["append_subscription"] = {
                "symbol": append_symbol,
                "requested_ms": append_requested_ms,
                "completed_ms": int(time.time() * 1000),
                "result": append_result,
                "latency_ms": round((time.monotonic() - append_started) * 1000, 3),
            }
            if append_result != 0:
                summary["error"] = "append_subscribe_rejected"
                return 5
            items.append(append_item)
            symbol_set.add(append_symbol)

        deadline = time.monotonic() + args.duration
        with args.events_output.open("w", encoding="utf-8") as event_stream:
            while time.monotonic() < deadline:
                try:
                    event = tgw.ReceiveRawEvent(timeout=min(1.0, max(0.05, deadline - time.monotonic())))
                except TimeoutError:
                    continue
                if not isinstance(event, dict):
                    continue
                data = event.get("data")
                if not isinstance(data, dict):
                    continue
                code = str(data.get("2", data.get("security_code", "")))
                market_value = data.get("1", data.get("market_type"))
                suffix = ".SH" if market_value == 101 else ".SZ" if market_value == 102 else ""
                symbol = code + suffix if code else ""
                if symbol:
                    seen_symbols.add(symbol)
                    if symbol not in symbol_set:
                        unknown_symbols.add(symbol)
                is_delta = bool(event.get("is_delta"))
                if is_delta:
                    delta_count += 1
                else:
                    full_count += 1
                    if symbol:
                        full_symbols.add(symbol)
                event_count += 1
                status_counts[str(event.get("status"))] += 1
                numeric_keys = sorted(key for key in data if str(key).isdigit())
                key_sets[",".join(numeric_keys)] += 1
                safe_event = {
                    "received_ms": int(time.time() * 1000),
                    "headers": {"tag": (event.get("headers") or {}).get("tag")
                                if isinstance(event.get("headers"), dict) else None},
                    "status": event.get("status"),
                    "is_delta": event.get("is_delta"),
                    "data": data,
                }
                event_stream.write(json.dumps(safe_event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:
        # Exception text can be unsafe if an SDK echoes its request, so retain
        # only the class name in the durable report.
        summary["error"] = type(exc).__name__
        return 4
    finally:
        summary.update({
            "finished_ms": int(time.time() * 1000),
            "events": event_count,
            "full_events": full_count,
            "delta_events": delta_count,
            "unique_symbols": len(seen_symbols),
            "full_symbols": len(full_symbols),
            "unknown_symbols": sorted(unknown_symbols),
            "missing_full_symbols": sorted(symbol_set - full_symbols),
            "status_counts": dict(status_counts),
            "numeric_key_sets": dict(key_sets),
            "decoder_failures": decoder_failures,
        })
        args.summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if items:
            for offset in range(0, len(items), args.batch_size):
                try:
                    tgw.UnSubscribe(items[offset:offset + args.batch_size])
                except Exception:
                    break
        try:
            tgw.Close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Summarize an SSL hook capture without printing credentials or tokens."""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from tgw_macos._protocol import CompressedMessage, decode_server_payload  # noqa: E402
from tgw_macos._websocket import apply_mask  # noqa: E402


def read_records(path: Path) -> list[tuple[str, bytes]]:
    data = path.read_bytes()
    records: list[tuple[str, bytes]] = []
    offset = 0
    while offset + 5 <= len(data):
        direction = chr(data[offset])
        length = struct.unpack_from("<I", data, offset + 1)[0]
        end = offset + 5 + length
        if direction not in {"W", "R"} or end > len(data):
            raise ValueError(f"invalid record at offset {offset}")
        records.append((direction, data[offset + 5:end]))
        offset = end
    if offset != len(data):
        raise ValueError(f"trailing {len(data) - offset} bytes")
    return records


def parse_header(data: bytes) -> tuple[int, int, bytes | None, int]:
    if len(data) < 2:
        raise ValueError("short WebSocket header")
    opcode = data[0] & 0x0F
    length = data[1] & 0x7F
    offset = 2
    if length == 126:
        length = int.from_bytes(data[offset:offset + 2], "big")
        offset += 2
    elif length == 127:
        length = int.from_bytes(data[offset:offset + 8], "big")
        offset += 8
    mask = data[offset:offset + 4] if data[1] & 0x80 else None
    offset += 4 if mask is not None else 0
    return opcode, length, mask, offset


def safe_json_summary(value: dict[str, Any]) -> str:
    method = value.get("method")
    headers = value.get("headers")
    tag = headers.get("tag") if isinstance(headers, dict) else None
    params = value.get("params")
    data = value.get("data")
    parts = []
    if method is not None:
        parts.append(f"method={method}")
    if tag is not None:
        parts.append(f"tag={tag}")
    if "status" in value:
        parts.append(f"status={value['status']!r}")
    if isinstance(params, dict):
        parts.append(f"param_keys={sorted(params)}")
    if isinstance(data, dict):
        parts.append(f"data_keys={sorted(data)}")
    return " ".join(parts) or f"keys={sorted(value)}"


def safe_schema(value: Any) -> Any:
    """Describe JSON shape without exposing any scalar value."""
    if isinstance(value, dict):
        return {key: safe_schema(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return {
            "type": "list",
            "length": len(value),
            "item": safe_schema(value[0]) if value else None,
        }
    return {"type": type(value).__name__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--logon-data-schema",
        action="store_true",
        help="print only the key/type shape of OnRspLogon.data; never scalar values",
    )
    args = parser.parse_args()
    records = read_records(args.capture)
    index = 0
    frames = 0
    while index < len(records):
        direction, chunk = records[index]
        if len(chunk) < 2 or not (chunk[0] & 0x80):
            index += 1
            continue
        try:
            opcode, length, mask, header_size = parse_header(chunk)
        except ValueError:
            index += 1
            continue
        payload = bytearray(chunk[header_size:])
        while len(payload) < length and index + 1 < len(records):
            next_direction, next_chunk = records[index + 1]
            if next_direction != direction:
                break
            index += 1
            payload.extend(next_chunk)
        payload = payload[:length]
        if len(payload) != length:
            print(f"{frames:03d} {direction} opcode={opcode} incomplete={len(payload)}/{length}")
        else:
            decoded_payload = apply_mask(bytes(payload), mask) if mask is not None else bytes(payload)
            detail = ""
            if opcode in {0x1, 0x2}:
                try:
                    value = decode_server_payload(decoded_payload)
                    detail = (
                        " compressed_without_decoder"
                        if isinstance(value, CompressedMessage)
                        else " " + safe_json_summary(value)
                    )
                    if (
                        args.logon_data_schema
                        and not isinstance(value, CompressedMessage)
                        and isinstance(value.get("headers"), dict)
                        and value["headers"].get("tag") == "OnRspLogon"
                    ):
                        print("logon_data_schema=" + json.dumps(
                            safe_schema(value.get("data")),
                            ensure_ascii=False,
                            sort_keys=True,
                        ))
                except Exception:
                    detail = " non_json_payload"
            elif opcode in {0x9, 0xA}:
                detail = f" control_payload_len={len(decoded_payload)}"
            print(f"{frames:03d} {direction} opcode={opcode} payload_len={length}{detail}")
        frames += 1
        index += 1
    print(f"records={len(records)} frames={frames} bytes={args.capture.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Safely summarize per-socket SSL_write plaintext captured by the oracle."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import struct
from collections import defaultdict
from pathlib import Path
from typing import Any


MAGIC = b"TGWSSL3\n"


def safe_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): safe_shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return {"type": "list", "length": len(value), "item": safe_shape(value[0]) if value else None}
    if isinstance(value, str) and value[:1] in {"[", "{"}:
        try:
            return {"type": "str", "decoded_json": safe_shape(json.loads(value))}
        except Exception:
            pass
    return {"type": type(value).__name__}


def value_kind(value: Any) -> str:
    """Type name only; never the business value itself."""
    if isinstance(value, bool):
        return "bool"
    return type(value).__name__


def csv_row_shape(encoded: str) -> dict[str, Any]:
    fields = encoded.split(",")
    kinds: list[str] = []
    for field in fields:
        try:
            int(field)
            kinds.append("int")
        except ValueError:
            kinds.append(value_kind(field))
    # Run-length encoding keeps summaries compact without exposing any value.
    rle: list[str] = []
    index = 0
    while index < len(kinds):
        run = 1
        while index + run < len(kinds) and kinds[index + run] == kinds[index]:
            run += 1
        rle.append(f"{kinds[index]}*{run}" if run > 1 else kinds[index])
        index += run
    return {
        "field_count": len(fields),
        "field_types_rle": rle,
    }


def data_container_shape(data: Any) -> Any:
    if isinstance(data, list):
        item = data[0] if data else None
        result: dict[str, Any] = {"type": "list", "length": len(data)}
        if isinstance(item, str):
            result["item_csv"] = csv_row_shape(item)
            result["all_rows_same_shape"] = all(
                isinstance(row, str) and row.count(",") == item.count(",")
                for row in data
            )
            result["uniform_type"] = (
                "str" if all(isinstance(row, str) for row in data) else "mixed"
            )
        elif item is not None:
            result["item"] = value_kind(item)
        return result
    if isinstance(data, str):
        try:
            decoded = json.loads(data)
        except Exception:
            return {"type": "str"}
        return {"type": "str", "decoded_json": safe_shape(decoded)}
    return {"type": type(data).__name__}


def read_streams(path: Path) -> dict[tuple[int, str], bytes]:
    raw = path.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError("not a TGWSSL3 capture")
    streams: dict[tuple[int, str], bytearray] = defaultdict(bytearray)
    offset = len(MAGIC)
    while offset + 13 <= len(raw):
        direction = raw[offset:offset + 1]
        stream_id, length = struct.unpack_from("<QI", raw, offset + 1)
        offset += 13
        end = offset + length
        if direction not in {b"W", b"R"} or end > len(raw):
            raise ValueError(f"invalid record at offset {offset - 13}")
        streams[(stream_id, direction.decode("ascii"))].extend(raw[offset:end])
        offset = end
    if offset != len(raw):
        raise ValueError("trailing capture bytes")
    return {key: bytes(data) for key, data in streams.items()}


def decode_json_payload(payload: bytes) -> Any:
    raw = payload
    if len(raw) > 5 and raw[1:5] == b"\x28\xb5\x2f\xfd":
        raw = raw[1:]
    if raw.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            import zstandard
            raw = zstandard.ZstdDecompressor().decompress(raw).rstrip(b"\x00\r\n\t ")
        except Exception:
            library_path = ctypes.util.find_library("zstd")
            if not library_path:
                return None
            library = ctypes.CDLL(library_path)
            library.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            library.ZSTD_getFrameContentSize.restype = ctypes.c_uint64
            library.ZSTD_decompress.argtypes = [
                ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t
            ]
            library.ZSTD_decompress.restype = ctypes.c_size_t
            source = ctypes.create_string_buffer(raw)
            size = int(library.ZSTD_getFrameContentSize(source, len(raw)))
            if size >= (1 << 63) or size > 64 * 1024 * 1024:
                return None
            target = ctypes.create_string_buffer(size)
            written = int(library.ZSTD_decompress(target, size, source, len(raw)))
            if written > size:
                return None
            raw = target.raw[:written].rstrip(b"\x00\r\n\t ")
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def websocket_frames(stream: bytes, start: int) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    offset = start
    while offset + 2 <= len(stream):
        first, second = stream[offset], stream[offset + 1]
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        cursor = offset + 2
        if length == 126:
            if cursor + 2 > len(stream): break
            length = int.from_bytes(stream[cursor:cursor + 2], "big")
            cursor += 2
        elif length == 127:
            if cursor + 8 > len(stream): break
            length = int.from_bytes(stream[cursor:cursor + 8], "big")
            cursor += 8
        mask = stream[cursor:cursor + 4] if masked else b""
        cursor += 4 if masked else 0
        if cursor + length > len(stream): break
        payload = stream[cursor:cursor + length]
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        item: dict[str, Any] = {"opcode": opcode, "payload_length": length}
        if opcode in {1, 2}:
            try:
                value = decode_json_payload(payload)
                if not isinstance(value, dict):
                    raise ValueError("not a JSON object")
                item["json_shape"] = safe_shape(value)
                headers = value.get("headers")
                if isinstance(headers, dict) and isinstance(headers.get("tag"), str):
                    item["tag"] = headers["tag"]
                if isinstance(headers, dict) and isinstance(headers.get("tag"), int):
                    item["query_response_controls"] = {
                        "tag": headers.get("tag"),
                        "pack_num": headers.get("pack_num"),
                        "all_pack_num": headers.get("all_pack_num"),
                    }
                if "status" in value:
                    item["status"] = value["status"]
                if isinstance(value, dict) and isinstance(value.get("method"), str):
                    item["method"] = value["method"]
                    params = value.get("params")
                    if isinstance(params, dict):
                        # Key order is protocol evidence; safe_shape sorts keys.
                        item["param_keys_in_order"] = list(params.keys())
                        item["param_value_types"] = {
                            str(key): value_kind(param)
                            for key, param in params.items()
                        }
                    if value["method"] == "ReqGetThirdInfo":
                        request_items = value.get("item")
                        item["protocol_controls"] = {
                            "offset": value.get("offset"),
                            "count": value.get("count"),
                            "query_bandwidth": (
                                params.get("QueryBandWidth") if isinstance(params, dict) else None
                            ),
                            "item_keys": [
                                entry.get("key")
                                for entry in request_items
                                if isinstance(entry, dict) and isinstance(entry.get("key"), str)
                            ] if isinstance(request_items, list) else [],
                        }
                elif "method" not in item and "status" in item:
                    data = value.get("data")
                    if data is not None:
                        item["data_shape"] = data_container_shape(data)
            except Exception:
                item["payload_kind"] = "non_json"
                item["payload_prefix8_hex"] = payload[:8].hex()
        frames.append(item)
        offset = cursor + length
    return frames


def query_packets(stream: bytes) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    offset = 0
    while offset + 15 <= len(stream):
        if stream[offset] != 2:
            offset += 1
            continue
        function_code = int.from_bytes(stream[offset + 1:offset + 3], "little")
        payload_length = int.from_bytes(stream[offset + 11:offset + 15], "little")
        end = offset + 15 + payload_length
        if payload_length > 64 * 1024 * 1024 or end > len(stream):
            offset += 1
            continue
        payload = stream[offset + 15:end]
        item: dict[str, Any] = {
            "function_code": function_code,
            "payload_length": payload_length,
        }
        try:
            item["json_shape"] = safe_shape(json.loads(payload.decode("utf-8")))
        except Exception:
            item["payload_kind"] = "non_json"
        packets.append(item)
        offset = end
    return packets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    raw_streams = read_streams(args.capture)
    ids = {stream_id for stream_id, _ in raw_streams}
    stream_numbers = {stream_id: index for index, stream_id in enumerate(sorted(ids), start=1)}
    summaries: list[dict[str, Any]] = []
    for (stream_id, direction), stream in sorted(raw_streams.items()):
        item: dict[str, Any] = {
            "stream": stream_numbers[stream_id],
            "direction": direction,
            "plaintext_bytes": len(stream),
        }
        if stream.startswith(b"GET ") and b"\r\n\r\n" in stream:
            header_end = stream.index(b"\r\n\r\n") + 4
            item["kind"] = "websocket"
            request_line = stream.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
            request_parts = request_line.split()
            item["request_path"] = request_parts[1] if len(request_parts) >= 2 else "unknown"
            item["frames"] = websocket_frames(stream, header_end)
        elif stream.startswith(b"HTTP/") and b"\r\n\r\n" in stream:
            header_end = stream.index(b"\r\n\r\n") + 4
            item["kind"] = "websocket_response"
            item["frames"] = websocket_frames(stream, header_end)
        else:
            packets = query_packets(stream)
            item["kind"] = "query" if packets else "unknown"
            item["packets"] = packets
            if not packets:
                item["prefix3_hex"] = stream[:3].hex()
        summaries.append(item)
    print(json.dumps({"streams": summaries}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

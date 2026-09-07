#!/usr/bin/env python3
"""Summarize network send buffers from a short strace without scalar values."""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any


BUFFER_RE = re.compile(
    r"(?:sendto|write)\((\d+), (\"(?:\\.|[^\"])*\"), (\d+)(?:,|\))"
)
IOV_FD_RE = re.compile(r"(?:sendmsg|writev)\((\d+),")
IOV_RE = re.compile(r"iov_base=(\"(?:\\.|[^\"])*\"), iov_len=(\d+)")


def json_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return {"type": "list", "length": len(value), "item": json_shape(value[0]) if value else None}
    return {"type": type(value).__name__}


def decode_literal(literal: str) -> bytes:
    # strace uses C-style byte escapes; the latin-1 round trip preserves bytes.
    value = ast.literal_eval(literal)
    return value.encode("latin1", errors="backslashreplace")


def summarize(data: bytes, requested_length: int) -> dict[str, Any]:
    result: dict[str, Any] = {"requested_length": requested_length, "captured_length": len(data)}
    if len(data) >= 5 and data[0] in {0x14, 0x15, 0x16, 0x17} and data[1] == 0x03:
        result["kind"] = "TLS_record"
        result["tls_content_type"] = data[0]
        result["tls_record_length"] = int.from_bytes(data[3:5], "big")
        return result
    if len(data) >= 15 and data[0] == 2:
        result.update({
            "kind": "TGW_query_packet",
            "function_code": int.from_bytes(data[1:3], "little"),
            "payload_length": int.from_bytes(data[11:15], "little"),
        })
        payload = data[15:15 + result["payload_length"]]
        try:
            result["json_shape"] = json_shape(json.loads(payload.decode("utf-8")))
        except Exception:
            result["payload_kind"] = "non_json_or_incomplete"
        return result
    if len(data) >= 19 and data[4] == 2:
        nested = summarize(data[4:], requested_length - 4)
        nested["kind"] = "length_prefixed_" + str(nested.get("kind", "other"))
        nested["outer_length"] = int.from_bytes(data[:4], "little")
        return nested
    json_offset = data.find(b"{")
    if json_offset >= 0:
        try:
            result["json_offset"] = json_offset
            result["json_shape"] = json_shape(json.loads(data[json_offset:].decode("utf-8")))
        except Exception:
            pass
    # Only the message marker/function-code bytes are shown. Session ids and
    # payload bytes begin later and are intentionally never emitted.
    result["prefix3_hex"] = data[:3].hex()
    result["kind"] = "other"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("traces", nargs="+", type=Path)
    args = parser.parse_args()
    summaries: list[dict[str, Any]] = []
    for path in args.traces:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = BUFFER_RE.search(line)
            if match:
                try:
                    data = decode_literal(match.group(2))
                except Exception:
                    continue
                requested_length = int(match.group(3))
                fd = int(match.group(1))
            else:
                fd_match = IOV_FD_RE.search(line)
                iov_matches = IOV_RE.findall(line)
                if not fd_match or not iov_matches:
                    continue
                try:
                    data = b"".join(decode_literal(literal) for literal, _ in iov_matches)
                except Exception:
                    continue
                requested_length = sum(int(length) for _, length in iov_matches)
                fd = int(fd_match.group(1))
            item = summarize(data, requested_length)
            item["fd"] = fd
            summaries.append(item)
    print(json.dumps({"send_count": len(summaries), "sends": summaries}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

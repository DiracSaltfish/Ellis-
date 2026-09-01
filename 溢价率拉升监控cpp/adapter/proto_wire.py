"""Dependency-free encoder/decoder for protocol/tgw_bridge.proto.

The bridge intentionally has a tiny protobuf surface so the adapter can start
before the project venv has grpc/protobuf installed. Unknown fields are skipped.
"""
from __future__ import annotations

import dataclasses
import struct
from typing import Iterator


@dataclasses.dataclass(slots=True)
class BridgeFrame:
    kind: int = 0
    sequence: int = 0
    session_id: str = ""
    receive_wall_ns: int = 0
    receive_monotonic_ns: int = 0
    is_delta: bool = False
    tag: str = ""
    payload_json: bytes = b""
    sdk_queue_depth: int = 0
    message: str = ""


def _varint(value: int) -> bytes:
    value &= (1 << 64) - 1
    output = bytearray()
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _field_varint(number: int, value: int) -> bytes:
    return _varint(number << 3) + _varint(value)


def _field_bytes(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def encode(frame: BridgeFrame) -> bytes:
    result = bytearray()
    if frame.kind:
        result += _field_varint(1, frame.kind)
    if frame.sequence:
        result += _field_varint(2, frame.sequence)
    if frame.session_id:
        result += _field_bytes(3, frame.session_id.encode())
    if frame.receive_wall_ns:
        result += _field_varint(4, frame.receive_wall_ns)
    if frame.receive_monotonic_ns:
        result += _field_varint(5, frame.receive_monotonic_ns)
    if frame.is_delta:
        result += _field_varint(6, 1)
    if frame.tag:
        result += _field_bytes(7, frame.tag.encode())
    if frame.payload_json:
        result += _field_bytes(8, frame.payload_json)
    if frame.sdk_queue_depth:
        result += _field_varint(9, frame.sdk_queue_depth)
    if frame.message:
        result += _field_bytes(10, frame.message.encode())
    return bytes(result)


def length_prefix(payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + payload


def encode_framed(frame: BridgeFrame) -> bytes:
    return length_prefix(encode(frame))


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift <= 63:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid protobuf varint")


def decode(payload: bytes) -> BridgeFrame:
    frame = BridgeFrame()
    offset = 0
    while offset < len(payload):
        key, offset = _read_varint(payload, offset)
        number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, offset = _read_varint(payload, offset)
            if number == 1:
                frame.kind = value
            elif number == 2:
                frame.sequence = value
            elif number == 4:
                frame.receive_wall_ns = value
            elif number == 5:
                frame.receive_monotonic_ns = value
            elif number == 6:
                frame.is_delta = bool(value)
            elif number == 9:
                frame.sdk_queue_depth = value
        elif wire_type == 2:
            size, offset = _read_varint(payload, offset)
            end = offset + size
            if end > len(payload):
                raise ValueError("truncated protobuf field")
            value = payload[offset:end]
            offset = end
            if number == 3:
                frame.session_id = value.decode()
            elif number == 7:
                frame.tag = value.decode()
            elif number == 8:
                frame.payload_json = value
            elif number == 10:
                frame.message = value.decode()
        elif wire_type == 1:
            offset += 8
        elif wire_type == 5:
            offset += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")
        if offset > len(payload):
            raise ValueError("truncated protobuf payload")
    return frame


def take_frames(buffer: bytearray, maximum: int = 64 * 1024 * 1024) -> Iterator[BridgeFrame]:
    while len(buffer) >= 4:
        size = struct.unpack(">I", buffer[:4])[0]
        if size > maximum:
            raise ValueError(f"frame size {size} exceeds limit")
        if len(buffer) < size + 4:
            return
        payload = bytes(buffer[4 : size + 4])
        del buffer[: size + 4]
        yield decode(payload)

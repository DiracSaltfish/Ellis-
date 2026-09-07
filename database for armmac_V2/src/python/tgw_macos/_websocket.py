"""Small RFC 6455 client-side framing helpers used by the TGW transport.

The vendor endpoint uses ordinary WebSocket v13 framing.  Client frames must be
masked; server frames must not be.  Keeping this layer independent from TGW's
JSON protocol makes it possible to test it with synthetic fixtures.
"""
from __future__ import annotations

import secrets
import socket
import struct
import threading
from dataclasses import dataclass


class WebSocketError(RuntimeError):
    pass


@dataclass(frozen=True)
class Frame:
    fin: bool
    opcode: int
    payload: bytes


def apply_mask(payload: bytes, mask_key: bytes) -> bytes:
    if len(mask_key) != 4:
        raise ValueError("WebSocket mask key must be four bytes")
    return bytes(value ^ mask_key[index & 3] for index, value in enumerate(payload))


def encode_frame(payload: bytes, opcode: int = 0x2, *, mask: bool = True,
                 fin: bool = True, mask_key: bytes | None = None) -> bytes:
    if not 0 <= opcode <= 0xF:
        raise ValueError("invalid WebSocket opcode")
    if opcode >= 0x8 and (not fin or len(payload) > 125):
        raise ValueError("invalid WebSocket control frame")

    first = (0x80 if fin else 0) | opcode
    length = len(payload)
    mask_bit = 0x80 if mask else 0
    if length < 126:
        header = bytes((first, mask_bit | length))
    elif length <= 0xFFFF:
        header = bytes((first, mask_bit | 126)) + struct.pack("!H", length)
    else:
        header = bytes((first, mask_bit | 127)) + struct.pack("!Q", length)

    if not mask:
        return header + payload
    key = mask_key if mask_key is not None else secrets.token_bytes(4)
    return header + key + apply_mask(payload, key)


class WebSocketStream:
    """Blocking frame reader/writer for an already-upgraded socket."""

    def __init__(self, sock: socket.socket, *, max_payload: int = 64 * 1024 * 1024):
        self.sock = sock
        self.max_payload = max_payload
        self._write_lock = threading.Lock()

    def _read_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            data = self.sock.recv(size - len(chunks))
            if not data:
                raise EOFError("WebSocket peer closed the connection")
            chunks.extend(data)
        return bytes(chunks)

    def read_frame(self) -> Frame:
        first, second = self._read_exact(2)
        fin = bool(first & 0x80)
        rsv = first & 0x70
        opcode = first & 0x0F
        if rsv:
            raise WebSocketError(f"unsupported RSV bits: 0x{rsv:02x}")

        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
            if length & (1 << 63):
                raise WebSocketError("invalid 64-bit WebSocket payload length")
        if length > self.max_payload:
            raise WebSocketError(f"WebSocket payload too large: {length}")
        if opcode >= 0x8 and (not fin or length > 125):
            raise WebSocketError("invalid WebSocket control frame")

        mask_key = self._read_exact(4) if masked else None
        payload = self._read_exact(length)
        if mask_key is not None:
            payload = apply_mask(payload, mask_key)
        return Frame(fin=fin, opcode=opcode, payload=payload)

    def send(self, payload: bytes, opcode: int = 0x2, *, fin: bool = True) -> None:
        frame = encode_frame(payload, opcode, mask=True, fin=fin)
        with self._write_lock:
            self.sock.sendall(frame)


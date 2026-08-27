"""Internet-mode TGW JSON protocol over TLS/WebSocket.

The request envelope and compressed push format are reconstructed from an
authorized official Linux client session.  No credential values or session
tokens are embedded here.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.util
import hashlib
import json
import os
import queue
import socket
import ssl
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ._websocket import WebSocketError, WebSocketStream


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
DEFAULT_TLS_SERVER_NAME = "www.dgw.com"
# Public ``SubscribeDataType`` values are not the internet wire tags. This
# mapping contains only pairs verified against the official 1.0.9.2 SDK.
VERIFIED_SUBSCRIBE_WIRE_TYPES = {
    10: 14,  # kSnapshot (mainland L1) -> wire/tag 14
    12: 16,  # kHKTSnapshot (Stock Connect L1) -> wire/tag 16
}


class TgwTransportError(RuntimeError):
    pass


class TgwProtocolError(RuntimeError):
    pass


class TgwTimeoutError(TimeoutError):
    pass


@dataclass(frozen=True)
class CompressedMessage:
    """Returned only when the optional zstandard decoder is unavailable."""

    payload: bytes


def _compact_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def default_mac_addresses() -> list[str]:
    configured = os.environ.get("TGW_MAC_ADDRESS", "").strip()
    if configured:
        values = [part.strip().lower() for part in configured.split(",") if part.strip()]
        if values:
            return values
    node = uuid.getnode()
    return [":".join(f"{(node >> shift) & 0xff:02x}" for shift in range(40, -1, -8))]


def build_logon_request(username: str, password: str, *, force_logout: bool = False,
                        client_version: str, process_id: int | None = None,
                        mac_addresses: list[str] | None = None) -> tuple[int, bytes]:
    request_id = 0
    request = {
        "headers": {"id": request_id, "userName": username},
        "method": "ReqLogon",
        "params": {
            "Username": username,
            "Password": password,
            "MacAddress": ",".join(mac_addresses or default_mac_addresses()),
            "Version": client_version,
            "ProcessId": os.getpid() if process_id is None else int(process_id),
            "ForceLogout": bool(force_logout),
            "PushBandWidth": 0.0,
            "QueryBandWidth": 0.0,
        },
    }
    return request_id, _compact_json(request)


def build_subscribe_request(username: str, token: str, request_id: int,
                            items: list[dict[str, Any]], *, unsubscribe: bool = False
                            ) -> bytes:
    if not items:
        raise ValueError("subscription list is empty")
    request = {
        "headers": {"userName": username, "token": token, "id": int(request_id)},
        "method": "ReqUnSubscribeBatch" if unsubscribe else "ReqSubscribeBatch",
        "params": {
            "marketType": [int(item["market"]) for item in items],
            "categoryType": [int(item.get("category_type", 0)) for item in items],
            "subscribeDataType": [
                VERIFIED_SUBSCRIBE_WIRE_TYPES.get(
                    int(item.get("flag", 0)), int(item.get("flag", 0))
                )
                for item in items
            ],
            "securityCode": [str(item.get("security_code", "")) for item in items],
        },
    }
    return _compact_json(request)


def build_third_info_request(username: str, token: str, request_id: int,
                             parameters: dict[str, Any], *, offset: int = 0,
                             count: int = 1000) -> bytes:
    """Build the official internet-mode ``ReqGetThirdInfo`` envelope."""
    function_id = str(parameters.get("function_id", "")).strip()
    if not function_id:
        raise ValueError("third-info request is missing function_id")
    if offset < 0 or count <= 0:
        raise ValueError("third-info offset/count must be non-negative/positive")
    request = {
        "headers": {"userName": username, "token": token, "id": int(request_id)},
        "method": "ReqGetThirdInfo",
        "params": {"QueryBandWidth": 0.0},
        "function_id": function_id,
        "offset": int(offset),
        "count": int(count),
        "item": [
            {"key": str(key), "value": str(value)}
            for key, value in parameters.items()
            if key != "function_id"
        ],
    }
    return _compact_json(request)


def build_query_complete_request(username: str, token: str, request_id: int) -> bytes:
    return _compact_json({
        "headers": {"userName": username, "token": token, "id": int(request_id)},
        "method": "ReqGetComplete",
    })


def build_kline_request(username: str, token: str, request_id: int, request: Any) -> bytes:
    """Build the official internet-mode ``ReqGetKline`` envelope."""
    security_code = getattr(request, "security_code", "")
    if isinstance(security_code, bytes):
        security_code = security_code.split(b"\0", 1)[0].decode("utf-8")
    security_code = str(security_code).strip()
    if not security_code:
        raise ValueError("kline request is missing security_code")
    cyc_type = int(getattr(request, "cyc_type"))
    if cyc_type != 10008:
        raise NotImplementedError(
            "only daily K-line cyc_type=10008 has been verified in internet mode"
        )
    # The public TGW enum and internet wire enum are different. The official
    # Linux SDK translates daily K-line 10008 to wire period_type/tag 10100.
    period_type = 10100
    return _compact_json({
        "headers": {"userName": username, "token": token, "id": int(request_id)},
        "method": "ReqGetKline",
        "params": {
            "security_code": security_code,
            "market_type": int(getattr(request, "market_type")),
            "cq_flag": int(getattr(request, "cq_flag")),
            "auto_complete": int(getattr(request, "auto_complete")),
            "period_type": period_type,
            "begin_date": int(getattr(request, "begin_date")),
            "end_date": int(getattr(request, "end_date")),
            "begin_time": int(getattr(request, "begin_time")),
            "end_time": int(getattr(request, "end_time")),
            "QueryBandWidth": 0.0,
        },
    })


# Verified with an authorized Linux SDK capture on 2026-08-26 (SZSE ETF
# 159518, data_type=0). The tag matches amd::mdga::MDDatatype.kSnapshot and
# the kDayKline=10100 precedent where the response tag equals the wire enum.
SNAPSHOT_WIRE_TAG = 11000
SNAPSHOT_ROW_FIELD_COUNT = 36


def build_snapshot_request(username: str, token: str, request_id: int, request: Any) -> bytes:
    """Build the official internet-mode ``ReqGetSnapshot`` envelope."""
    security_code = getattr(request, "security_code", "")
    if isinstance(security_code, bytes):
        security_code = security_code.split(b"\0", 1)[0].decode("utf-8")
    security_code = str(security_code).strip()
    if not security_code:
        raise ValueError("snapshot request is missing security_code")
    data_type = int(getattr(request, "data_type", 0))
    if data_type != 0:
        raise NotImplementedError(
            "only L1 snapshot data_type=0 has been verified in internet mode"
        )
    # level_type exists in the public ReqDefault ABI but the official client
    # does not transmit it for verified snapshot queries.
    return _compact_json({
        "headers": {"userName": username, "token": token, "id": int(request_id)},
        "method": "ReqGetSnapshot",
        "params": {
            "security_code": security_code,
            "market_type": int(getattr(request, "market_type")),
            "date": int(getattr(request, "date")),
            "begin_time": int(getattr(request, "begin_time")),
            "end_time": int(getattr(request, "end_time")),
            "data_type": data_type,
            "QueryBandWidth": 0.0,
        },
    })


def _split_packed_levels(field: str, name: str) -> list[int]:
    values = field.split("|") if field else []
    if len(values) != 10 or any(not value.lstrip("-").isdigit() for value in values):
        raise TgwProtocolError(f"snapshot {name} is not a pipe-packed array of 10 integers")
    return [int(value) for value in values]


def _snapshot_int(fields: list[str], index: int, name: str) -> int:
    value = fields[index]
    if not value.lstrip("-").isdigit():
        raise TgwProtocolError(f"snapshot field {name} is not an integer")
    return int(value)


def _decode_snapshot_row(fields: list[str]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "market_type": _snapshot_int(fields, 1, "market_type"),
        "security_code": fields[0],
        # The official wrapper emits a constant 0 here for verified queries,
        # mirroring its K-line behavior; no wire position carries this field.
        "variety_category": 0,
        "orig_time": _snapshot_int(fields, 2, "orig_time"),
        "trading_phase_code": fields[3],
        "pre_close_price": _snapshot_int(fields, 4, "pre_close_price"),
        "open_price": _snapshot_int(fields, 5, "open_price"),
        "high_price": _snapshot_int(fields, 6, "high_price"),
        "low_price": _snapshot_int(fields, 7, "low_price"),
        "last_price": _snapshot_int(fields, 8, "last_price"),
        "close_price": _snapshot_int(fields, 9, "close_price"),
    }
    bid_prices = _split_packed_levels(fields[10], "bid_price")
    bid_volumes = _split_packed_levels(fields[11], "bid_volume")
    offer_prices = _split_packed_levels(fields[12], "offer_price")
    offer_volumes = _split_packed_levels(fields[13], "offer_volume")
    for level in range(10):
        row[f"bid_price{level + 1}"] = bid_prices[level]
        row[f"bid_volume{level + 1}"] = bid_volumes[level]
        row[f"offer_price{level + 1}"] = offer_prices[level]
        row[f"offer_volume{level + 1}"] = offer_volumes[level]
    row["num_trades"] = _snapshot_int(fields, 14, "num_trades")
    row["total_volume_trade"] = _snapshot_int(fields, 15, "total_volume_trade")
    row["total_value_trade"] = _snapshot_int(fields, 16, "total_value_trade")
    row["IOPV"] = _snapshot_int(fields, 17, "IOPV")
    row["high_limited"] = _snapshot_int(fields, 18, "high_limited")
    row["low_limited"] = _snapshot_int(fields, 19, "low_limited")
    # Positions 20..35 exist on the wire (tail of the official CSV export
    # schema) but their semantics are unproven and the official Python
    # container does not expose them; they stay unparsed by design.
    return row


def parse_snapshot_packets(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decode the official 36-field CSV rows returned for ``ReqGetSnapshot``."""
    rows: list[dict[str, Any]] = []
    for packet in _ordered_query_packets(packets, SNAPSHOT_WIRE_TAG):
        data = packet.get("data")
        if not isinstance(data, list) or not all(isinstance(row, str) for row in data):
            raise TgwProtocolError("snapshot response data is not a string array")
        for encoded in data:
            fields = encoded.split(",")
            if len(fields) != SNAPSHOT_ROW_FIELD_COUNT:
                raise TgwProtocolError(
                    f"snapshot response row does not contain {SNAPSHOT_ROW_FIELD_COUNT} fields"
                )
            rows.append(_decode_snapshot_row(fields))
    return rows


def _ordered_query_packets(packets: list[dict[str, Any]], expected_tag: int
                           ) -> list[dict[str, Any]]:
    if not packets:
        raise TgwProtocolError("empty query response")
    ordered: list[tuple[int, dict[str, Any]]] = []
    expected_packets: int | None = None
    for packet in packets:
        if packet.get("status") != 0:
            raise TgwProtocolError(f"query rejected (status={packet.get('status')!r})")
        headers = packet.get("headers")
        if not isinstance(headers, dict) or headers.get("tag") != expected_tag:
            raise TgwProtocolError(f"unexpected query response tag (expected {expected_tag})")
        pack_num = headers.get("pack_num")
        all_pack_num = headers.get("all_pack_num")
        if not isinstance(pack_num, int) or not isinstance(all_pack_num, int):
            raise TgwProtocolError("query response is missing packet counters")
        if pack_num < 1 or all_pack_num < 1 or pack_num > all_pack_num:
            raise TgwProtocolError("invalid query packet counters")
        if expected_packets is None:
            expected_packets = all_pack_num
        elif expected_packets != all_pack_num:
            raise TgwProtocolError("inconsistent query packet count")
        ordered.append((pack_num, packet))
    packet_numbers = [number for number, _ in ordered]
    if len(packet_numbers) != len(set(packet_numbers)):
        raise TgwProtocolError("duplicate query packet number")
    if expected_packets is None or set(packet_numbers) != set(
        range(1, expected_packets + 1)
    ):
        raise TgwProtocolError("incomplete query packet sequence")
    return [packet for _, packet in sorted(ordered)]


def parse_third_info_packets(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate, order and flatten the paged ThirdInfo server response."""
    rows: list[dict[str, Any]] = []
    for packet in _ordered_query_packets(packets, 11101):
        encoded = packet.get("data")
        if not isinstance(encoded, str):
            raise TgwProtocolError("third-info data is not a JSON string")
        try:
            document = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise TgwProtocolError("invalid nested third-info JSON") from exc
        body = document.get("body") if isinstance(document, dict) else None
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise TgwProtocolError("third-info body.data is not an object array")
        rows.extend(data)
    return rows


def parse_kline_packets(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decode the official 9-field CSV rows returned for ``ReqGetKline``."""
    rows: list[dict[str, Any]] = []
    for packet in _ordered_query_packets(packets, 10100):
        data = packet.get("data")
        if not isinstance(data, list) or not all(isinstance(row, str) for row in data):
            raise TgwProtocolError("kline response data is not a string array")
        for encoded in data:
            fields = encoded.split(",")
            if len(fields) != 9:
                raise TgwProtocolError("kline response row does not contain 9 fields")
            try:
                numeric = [int(value) for value in fields[1:]]
            except ValueError as exc:
                raise TgwProtocolError("kline response contains a non-integer field") from exc
            rows.append({
                "market_type": numeric[0],
                "security_code": fields[0],
                "orig_time": 0,
                "kline_time": numeric[1],
                "open_price": numeric[2],
                "high_price": numeric[3],
                "low_price": numeric[4],
                "close_price": numeric[5],
                "volume_trade": numeric[6],
                "value_trade": numeric[7],
                "variety_category": 0,
            })
    return rows


def _decompress_zstd(payload: bytes) -> bytes | None:
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError:
        try:
            from compression import zstd  # type: ignore[attr-defined]
        except ImportError:
            candidates = [ctypes.util.find_library("zstd")]
            candidates.extend([
                "/opt/homebrew/lib/libzstd.dylib",
                "/usr/local/lib/libzstd.dylib",
                "libzstd.so.1",
            ])
            for library_path in dict.fromkeys(path for path in candidates if path):
                try:
                    library = ctypes.CDLL(library_path)
                except OSError:
                    continue
                library.ZSTD_getFrameContentSize.argtypes = [
                    ctypes.c_void_p, ctypes.c_size_t,
                ]
                library.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
                library.ZSTD_decompressBound.argtypes = [
                    ctypes.c_void_p, ctypes.c_size_t,
                ]
                library.ZSTD_decompressBound.restype = ctypes.c_ulonglong
                library.ZSTD_decompress.argtypes = [
                    ctypes.c_void_p, ctypes.c_size_t,
                    ctypes.c_void_p, ctypes.c_size_t,
                ]
                library.ZSTD_decompress.restype = ctypes.c_size_t
                library.ZSTD_isError.argtypes = [ctypes.c_size_t]
                library.ZSTD_isError.restype = ctypes.c_uint
                source = ctypes.create_string_buffer(payload)
                size = int(library.ZSTD_getFrameContentSize(source, len(payload)))
                if size == (1 << 64) - 1:  # frame does not declare content size
                    size = int(library.ZSTD_decompressBound(source, len(payload)))
                if size >= (1 << 63) or size > 64 * 1024 * 1024:
                    continue
                target = ctypes.create_string_buffer(size)
                written = int(library.ZSTD_decompress(
                    target, size, source, len(payload)
                ))
                if library.ZSTD_isError(written):
                    continue
                return target.raw[:written]
            return None
        return zstd.decompress(payload)
    return zstandard.ZstdDecompressor().decompress(payload)


def decode_server_payload(payload: bytes) -> dict[str, Any] | CompressedMessage:
    raw = payload
    if raw.startswith(ZSTD_MAGIC):
        compressed = raw
    elif len(raw) > 5 and raw[1:5] == ZSTD_MAGIC:
        # Official push frames observed in 1.0.9.2 use a one-byte marker (0x59)
        # before the standard ZSTD frame.
        compressed = raw[1:]
    else:
        compressed = None

    if compressed is not None:
        decoded = _decompress_zstd(compressed)
        if decoded is None:
            return CompressedMessage(payload=payload)
        # The official 1.0.9.2 encoder includes a C-string terminator in the
        # decompressed buffer. It is outside the JSON document.
        raw = decoded.rstrip(b"\x00\r\n\t ")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TgwProtocolError("server payload is neither TGW JSON nor supported ZSTD JSON") from exc
    if not isinstance(value, dict):
        raise TgwProtocolError("TGW server JSON must be an object")
    return value


def _find_header_end(buffer: bytearray) -> int:
    marker = buffer.find(b"\r\n\r\n")
    return marker + 4 if marker >= 0 else -1


class TgwWssClient:
    """Thread-safe request/response client for the TGW internet push endpoint."""

    def __init__(self, *, endpoint: str = "/amd/dgw/push", timeout: float = 15.0,
                 heartbeat_sec: float = 5.0, max_payload: int = 64 * 1024 * 1024):
        self.endpoint = endpoint
        self.timeout = timeout
        self.heartbeat_sec = heartbeat_sec
        self.max_payload = max_payload
        self.username = ""
        self.token = ""
        self.logon_response: dict[str, Any] | None = None
        self._sock: ssl.SSLSocket | None = None
        self._ws: WebSocketStream | None = None
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._waiters: dict[int, queue.Queue[Any]] = {}
        self._waiters_lock = threading.Lock()
        self._events: queue.Queue[Any] = queue.Queue(maxsize=10_000)
        # Push subscribe requests use a separate high-range sequence in the
        # official client. Query task ids are allocated by interface.GetTaskID.
        self._request_id = 1_000_000

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._stop.is_set()

    def next_request_id(self) -> int:
        with self._waiters_lock:
            value = self._request_id
            self._request_id += 1
            return value

    def connect(self, host: str, port: int, *, ca_file: str | None = None,
                server_name: str | None = None) -> None:
        if self.connected:
            raise TgwTransportError("TGW client is already connected")
        raw = socket.create_connection((host, int(port)), timeout=self.timeout)
        try:
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            context = ssl.create_default_context(cafile=ca_file)
            # The vendor endpoint currently negotiates TLS 1.2 with an older
            # cipher profile. Its bundled CA also predates OpenSSL 3's strict
            # requirement that Basic Constraints be marked critical. Keep the
            # relaxation scoped to this dedicated context; chain verification
            # and hostname verification remain enabled.
            context.minimum_version = ssl.TLSVersion.TLSv1
            context.maximum_version = ssl.TLSVersion.TLSv1_2
            context.set_ciphers("DEFAULT:@SECLEVEL=0")
            if hasattr(ssl, "VERIFY_X509_STRICT"):
                context.verify_flags &= ~ssl.VERIFY_X509_STRICT
            tls = context.wrap_socket(
                raw, server_hostname=server_name or DEFAULT_TLS_SERVER_NAME
            )
            self._perform_upgrade(tls, host, int(port))
        except Exception:
            raw.close()
            raise

        tls.settimeout(None)
        self._sock = tls
        self._ws = WebSocketStream(tls, max_payload=self.max_payload)
        self._stop.clear()
        self._reader = threading.Thread(target=self._reader_loop, name="tgw-wss-reader", daemon=True)
        self._reader.start()

    def _perform_upgrade(self, sock: ssl.SSLSocket, host: str, port: int) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.endpoint} HTTP/1.1\r\n"
            "Connection: Upgrade\r\n"
            f"Host: {host}:{port}\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Upgrade: websocket\r\n"
            "User-Agent: WebSocket++/0.8.2\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = bytearray()
        while _find_header_end(response) < 0:
            chunk = sock.recv(4096)
            if not chunk:
                raise TgwTransportError("connection closed during WebSocket upgrade")
            response.extend(chunk)
            if len(response) > 64 * 1024:
                raise TgwTransportError("oversized WebSocket upgrade response")

        header_end = _find_header_end(response)
        if header_end != len(response):
            # The official endpoint has not been observed coalescing a frame with
            # the HTTP response. Refuse it instead of silently dropping bytes.
            raise TgwTransportError("unexpected data after WebSocket upgrade headers")
        lines = response[:-4].decode("latin1").split("\r\n")
        if not lines or " 101 " not in f" {lines[0]} ":
            raise TgwTransportError(f"WebSocket upgrade rejected: {lines[0] if lines else 'empty'}")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise TgwTransportError("invalid Sec-WebSocket-Accept response")

    def _reader_loop(self) -> None:
        fragmented_opcode: int | None = None
        fragmented = bytearray()
        try:
            assert self._ws is not None
            while not self._stop.is_set():
                frame = self._ws.read_frame()
                if frame.opcode == 0x8:
                    if not self._stop.is_set():
                        code = int.from_bytes(frame.payload[:2], "big") if len(frame.payload) >= 2 else None
                        reason = frame.payload[2:].decode("utf-8", errors="replace")
                        detail = f"code={code}" if code is not None else "without status code"
                        if reason:
                            detail += f", reason={reason}"
                        self._fail_waiters(WebSocketError(f"server closed WebSocket ({detail})"))
                    break
                if frame.opcode == 0x9:
                    self._ws.send(frame.payload, opcode=0xA)
                    continue
                if frame.opcode == 0xA:
                    continue
                if frame.opcode in (0x1, 0x2):
                    if fragmented_opcode is not None:
                        raise WebSocketError("new data frame before fragmented message completed")
                    if frame.fin:
                        self._dispatch_payload(frame.payload)
                    else:
                        fragmented_opcode = frame.opcode
                        fragmented.extend(frame.payload)
                    continue
                if frame.opcode == 0x0:
                    if fragmented_opcode is None:
                        raise WebSocketError("unexpected continuation frame")
                    fragmented.extend(frame.payload)
                    if len(fragmented) > self.max_payload:
                        raise WebSocketError("fragmented message too large")
                    if frame.fin:
                        self._dispatch_payload(bytes(fragmented))
                        fragmented.clear()
                        fragmented_opcode = None
                    continue
                raise WebSocketError(f"unsupported opcode: {frame.opcode}")
        except Exception as exc:
            if not self._stop.is_set():
                self._fail_waiters(exc)
                self._offer_event(exc)
        finally:
            self._stop.set()

    def _dispatch_payload(self, payload: bytes) -> None:
        message = decode_server_payload(payload)
        if isinstance(message, CompressedMessage):
            self._offer_event(message)
            return
        headers = message.get("headers")
        request_id = headers.get("id") if isinstance(headers, dict) else None
        waiter = None
        if isinstance(request_id, int):
            with self._waiters_lock:
                waiter = self._waiters.get(request_id)
        if waiter is not None:
            waiter.put(message)
        else:
            self._offer_event(message)

    def _offer_event(self, value: Any) -> None:
        try:
            self._events.put_nowait(value)
        except queue.Full:
            try:
                self._events.get_nowait()
            except queue.Empty:
                pass
            self._events.put_nowait(value)

    def _fail_waiters(self, exc: Exception) -> None:
        with self._waiters_lock:
            waiters = list(self._waiters.values())
        for waiter in waiters:
            waiter.put(exc)

    def request(self, request_id: int, payload: bytes, *, timeout: float | None = None
                ) -> dict[str, Any]:
        return self.request_many(
            request_id, payload, done=lambda _message: True, timeout=timeout
        )[0]

    def request_many(self, request_id: int, payload: bytes, *,
                     done: Any, timeout: float | None = None) -> list[dict[str, Any]]:
        if self._ws is None or self._stop.is_set():
            raise TgwTransportError("TGW WebSocket is not connected")
        waiter: queue.Queue[Any] = queue.Queue()
        with self._waiters_lock:
            if request_id in self._waiters:
                raise TgwProtocolError(f"duplicate request id: {request_id}")
            self._waiters[request_id] = waiter
        try:
            self._ws.send(payload, opcode=0x2)
            deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
            values: list[dict[str, Any]] = []
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TgwTimeoutError(f"TGW request {request_id} timed out")
                try:
                    value = waiter.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TgwTimeoutError(f"TGW request {request_id} timed out") from exc
                if isinstance(value, Exception):
                    raise TgwTransportError(f"TGW reader failed: {value}") from value
                if not isinstance(value, dict):
                    raise TgwProtocolError("TGW response is not an object")
                values.append(value)
                if done(value):
                    return values
        finally:
            with self._waiters_lock:
                self._waiters.pop(request_id, None)

    def send(self, payload: bytes) -> None:
        if self._ws is None or self._stop.is_set():
            raise TgwTransportError("TGW WebSocket is not connected")
        self._ws.send(payload, opcode=0x2)

    def wait_closed(self, timeout: float) -> bool:
        """Wait for the peer's WebSocket Close without extending indefinitely."""
        return self._stop.wait(max(0.0, float(timeout)))

    def logon(self, username: str, password: str, *, force_logout: bool = False,
              client_version: str, mac_addresses: list[str] | None = None) -> dict[str, Any]:
        request_id, payload = build_logon_request(
            username,
            password,
            force_logout=force_logout,
            client_version=client_version,
            mac_addresses=mac_addresses,
        )
        response = self.request(request_id, payload)
        headers = response.get("headers")
        status = response.get("status")
        tag = headers.get("tag") if isinstance(headers, dict) else None
        token = headers.get("token") if isinstance(headers, dict) else None
        if status != 0 or tag != "OnRspLogon" or not isinstance(token, str) or not token:
            raise TgwProtocolError(f"TGW logon rejected (status={status!r}, tag={tag!r})")
        self.username = username
        self.token = token
        self.logon_response = response
        self._start_heartbeat()
        return response

    def subscribe(self, items: list[dict[str, Any]], *, unsubscribe: bool = False,
                  timeout: float | None = None) -> dict[str, Any]:
        if not self.token or not self.username:
            raise TgwProtocolError("TGW client is not logged on")
        request_id = self.next_request_id()
        payload = build_subscribe_request(
            self.username, self.token, request_id, items, unsubscribe=unsubscribe
        )
        response = self.request(request_id, payload, timeout=timeout)
        if response.get("status") != 0:
            raise TgwProtocolError(f"subscription rejected (status={response.get('status')!r})")
        return response

    def recv_event(self, timeout: float | None = None) -> Any:
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty as exc:
            raise TgwTimeoutError("timed out waiting for TGW push event") from exc

    def _start_heartbeat(self) -> None:
        if self.heartbeat_sec <= 0 or self._heartbeat is not None:
            return
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="tgw-wss-heartbeat", daemon=True
        )
        self._heartbeat.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_sec):
            try:
                if self._ws is not None:
                    self._ws.send(b"Heartbeat", opcode=0x9)
            except Exception as exc:
                self._fail_waiters(exc)
                self._stop.set()
                return

    def close(self) -> None:
        self._stop.set()
        ws, sock = self._ws, self._sock
        self._ws = None
        self._sock = None
        if ws is not None:
            try:
                ws.send(b"", opcode=0x8)
            except Exception:
                pass
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        current = threading.current_thread()
        for thread in (self._reader, self._heartbeat):
            if thread is not None and thread is not current:
                thread.join(timeout=2.0)
        self._reader = None
        self._heartbeat = None

    def __enter__(self) -> "TgwWssClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

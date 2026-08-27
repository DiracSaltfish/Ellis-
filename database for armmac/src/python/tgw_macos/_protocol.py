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


@dataclass(frozen=True)
class DecodedMessageBatch:
    """Multiple TGW JSON objects carried by one server WebSocket message."""

    messages: tuple[dict[str, Any], ...]


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
    wire_types: list[int] = []
    for item in items:
        public_type = int(item.get("flag", 0))
        try:
            wire_types.append(VERIFIED_SUBSCRIBE_WIRE_TYPES[public_type])
        except KeyError as exc:
            raise NotImplementedError(
                f"subscription flag {public_type} has not been wire-verified"
            ) from exc
    request = {
        "headers": {"userName": username, "token": token, "id": int(request_id)},
        "method": "ReqUnSubscribeBatch" if unsubscribe else "ReqSubscribeBatch",
        "params": {
            "marketType": [int(item["market"]) for item in items],
            "categoryType": [int(item.get("category_type", 0)) for item in items],
            "subscribeDataType": wire_types,
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


# Public TGW ``MDDatatype`` K-line cycles are not the internet wire values.
# Each entry below was captured from an authorized official Linux SDK session
# (2026-08-26, method ReqGetKline; response tag equals the wire period and the
# status/paging contract matches the daily one):
# one-minute 10000 -> 10000, daily 10008 -> 10100, weekly 10009 -> 10101,
# monthly 10010 -> 10102, seasonal 10011 -> 10103 and yearly 10012 ->
# 10104 (yearly captured independently on /amd/dgw/dgw2_query, not
# extrapolated from the others).
# Unlisted periods must keep failing explicitly until individually verified.
VERIFIED_KLINE_WIRE_TYPES = {
    10000: 10000,  # k1Kline      -> wire period_type/tag 10000
    10008: 10100,  # kDayKline    -> wire period_type/tag 10100
    10009: 10101,  # kWeekKline   -> wire period_type/tag 10101
    10010: 10102,  # kMonthKline  -> wire period_type/tag 10102
    10011: 10103,  # kSeasonKline -> wire period_type/tag 10103
    10012: 10104,  # kYearKline   -> wire period_type/tag 10104
}


def kline_wire_period(cyc_type: int) -> int:
    """Map a verified public K-line cycle to its internet wire enum value."""
    try:
        return VERIFIED_KLINE_WIRE_TYPES[int(cyc_type)]
    except KeyError as exc:
        raise NotImplementedError(
            f"K-line cyc_type={int(cyc_type)} has not been wire-verified in "
            "internet mode"
        ) from exc


def build_kline_request(username: str, token: str, request_id: int, request: Any) -> bytes:
    """Build the official internet-mode ``ReqGetKline`` envelope."""
    security_code = getattr(request, "security_code", "")
    if isinstance(security_code, bytes):
        security_code = security_code.split(b"\0", 1)[0].decode("utf-8")
    security_code = str(security_code).strip()
    if not security_code:
        raise ValueError("kline request is missing security_code")
    # The public TGW enum and internet wire enum are different. Verified
    # conversions live in VERIFIED_KLINE_WIRE_TYPES; unknown periods fail.
    period_type = kline_wire_period(int(getattr(request, "cyc_type")))
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

# Error responses on the dgw*_query channel do not reuse the numeric data tag.
# Captured empty-query frame (2026-08-26): headers {id, tag:"DataEmpty",
# pack_num:0, all_pack_num:0}, status=-100 (wire-generic failure) and an empty
# string data payload; the official SDK surfaces public ErrorCode.kDataEmpty.
# Only captured tags may map to public codes; unknown tags fail explicitly.
SNAPSHOT_ERROR_TAGS = {
    "DataEmpty": -76,  # ErrorCode.kDataEmpty ("数据为空")
}


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
    level_type = int(getattr(request, "level_type", 0))
    if level_type != 0:
        raise NotImplementedError(
            "only snapshot level_type=0 has been verified in internet mode"
        )
    market_type = int(getattr(request, "market_type"))
    if market_type != 102 or security_code != "159518":
        raise NotImplementedError(
            "only SZSE ETF 159518 snapshot query has completed Linux/macOS live alignment"
        )
    # level_type exists in the public ReqDefault ABI but the official client
    # does not transmit it for verified snapshot queries.
    return _compact_json({
        "headers": {"userName": username, "token": token, "id": int(request_id)},
        "method": "ReqGetSnapshot",
        "params": {
            "security_code": security_code,
            "market_type": market_type,
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


def _snapshot_error_code(packet: dict[str, Any]) -> int:
    """Map one captured wire error frame to its public error code."""
    headers = packet.get("headers")
    tag = headers.get("tag") if isinstance(headers, dict) else None
    if not isinstance(tag, str) or tag not in SNAPSHOT_ERROR_TAGS:
        raise TgwProtocolError(
            f"snapshot error tag {tag!r} has not been observed in internet mode"
        )
    return SNAPSHOT_ERROR_TAGS[tag]


def parse_snapshot_packets(packets: list[dict[str, Any]]
                           ) -> tuple[list[dict[str, Any]], int | None]:
    """Decode ``ReqGetSnapshot`` responses into official sync-mode semantics.

    Returns ``(rows, error_code)``: ``error_code is None`` means every frame
    had ``status=0`` and ``rows`` holds the decoded 57-key records; otherwise
    the captured error-frame shape maps to a public error code and rows stay
    empty. Data frames and error frames in one response were never observed
    and fail explicitly.
    """
    if not packets:
        raise TgwProtocolError("empty query response")
    ok_frames = [packet for packet in packets if packet.get("status") == 0]
    error_frames = [packet for packet in packets if packet.get("status") != 0]
    if ok_frames and error_frames:
        raise TgwProtocolError(
            "snapshot response mixes data and error frames (unobserved shape)"
        )
    if error_frames:
        codes = [_snapshot_error_code(packet) for packet in error_frames]
        if len(set(codes)) != 1:
            raise TgwProtocolError(
                "snapshot response carries multiple distinct error frames"
            )
        return [], codes[0]
    rows: list[dict[str, Any]] = []
    for packet in _ordered_query_packets(ok_frames, SNAPSHOT_WIRE_TAG):
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
    return rows, None


# ---------------------------------------------------------------------------
# QueryCodeTable (internet mode, full-market)
#
# Wire contract captured from an authorized official Linux SDK session on
# 2026-08-26: the code table runs on the *one-shot* dgw*_query endpoint (not
# the persistent push channel), request method ReqGetReduceCodeTable with only
# a QueryBandWidth param, headers id -> userName -> token. Responses carry the
# integer tag 11103 with pack_num/all_pack_num paging and ZSTD/0x59 frames.
# Each ``data`` row is backtick (U+0060) separated into exactly 6 fields whose
# order mirrors MDCodeTable. The official client retries a missing packet via
# ReqGetPackage {pack_num:"N,"}. A completion frame was not captured (the sync
# probe timed out on a missing packet), so the code table reuses the channel
# standard ReqGetComplete like the other dgw*_query queries; ReqGetCodelistComplete
# remains an unproven candidate and is not used.
# ---------------------------------------------------------------------------
CODE_TABLE_WIRE_TAG = 11103
CODE_TABLE_ROW_FIELD_COUNT = 6

# Column order follows MDCodeTable (tgw_struct.h:841-849).
CODE_TABLE_COLUMNS = [
    "security_code", "symbol", "english_name", "market_type",
    "security_type", "currency",
]


def build_code_table_request(username: str, token: str, request_id: int) -> bytes:
    """Build the official internet-mode ``ReqGetReduceCodeTable`` envelope.

    The query has no business parameters; only the bandwidth cap is sent,
    matching the captured params ``{"QueryBandWidth": 0.0}`` and the
    ``id -> userName -> token`` header order.
    """
    return _compact_json({
        "headers": {"id": int(request_id), "userName": username, "token": token},
        "method": "ReqGetReduceCodeTable",
        "params": {"QueryBandWidth": 0.0},
    })


def build_get_package_request(username: str, token: str, request_id: int,
                              pack_num: int) -> bytes:
    """Build the captured ``ReqGetPackage`` retry for a missing packet."""
    return _compact_json({
        "headers": {"id": int(request_id), "userName": username, "token": token},
        "method": "ReqGetPackage",
        "params": {"pack_num": f"{int(pack_num)},"},
    })


def parse_code_table_packets(packets: list[dict[str, Any]],
                             expected_tag: int = CODE_TABLE_WIRE_TAG
                             ) -> list[dict[str, Any]]:
    """Decode ``ReqGetReduceCodeTable`` responses into official 6-column rows.

    Reuses ``_ordered_query_packets`` to validate status/tag/packet integrity
    (tag 11103, status 0, a complete 1..all_pack_num set), then splits each
    row on the backtick separator. ``market_type`` is the only integer column;
    the rest are passed through as strings. Unknown tags/statuses fail.
    """
    rows: list[dict[str, Any]] = []
    for packet in _ordered_query_packets(packets, expected_tag):
        data = packet.get("data")
        if not isinstance(data, list) or not all(isinstance(row, str) for row in data):
            raise TgwProtocolError("code-table response data is not a string array")
        for encoded in data:
            fields = encoded.split("\x60")
            if len(fields) != CODE_TABLE_ROW_FIELD_COUNT:
                raise TgwProtocolError(
                    f"code-table response row does not contain "
                    f"{CODE_TABLE_ROW_FIELD_COUNT} fields"
                )
            market_text = fields[3].strip()
            if not market_text.lstrip("-").isdigit():
                raise TgwProtocolError("code-table market_type is not an integer")
            rows.append({
                "security_code": fields[0],
                "symbol": fields[1],
                "english_name": fields[2],
                "market_type": int(market_text),
                "security_type": fields[4],
                "currency": fields[5],
            })
    return rows


# ---------------------------------------------------------------------------
# QueryETFInfo (internet mode)
#
# Wire contract captured from an authorized official Linux SDK session on
# 2026-08-26 (SSE 510300): unlike K-line/snapshot/third-info queries this one
# runs on the *persistent push* connection (/amd/dgw/push), not on a one-shot
# dgw*_query endpoint.  The client sends ReqGetETFCodeTableList with a single
# ``Security`` param formatted "<code>|<market>", then ReqGetCodelistComplete.
# Responses carry the string tag "111" and no pack_num/all_pack_num paging
# controls; each frame holds numeric-key record objects ("1".."36") whose
# slot order mirrors MDETFCodeTableRecord (+ ConstituentStockInfo at slot 36).
# Single-char fields travel as ASCII integer codes (NUL -> empty string).
# Only the single-item, single-response-frame shape has been observed; all
# other branches fail explicitly below.
# ---------------------------------------------------------------------------
ETF_WIRE_TAG = "111"

# Live-verified market samples. The public header allows SSE/SZSE only, but
# only an SSE sample has completed the Linux/wire/Mac loop so far.
VERIFIED_ETF_INFO_MARKETS = {101}


def build_etf_info_request(username: str, token: str, request_id: int,
                           items: list[dict[str, Any]]) -> bytes:
    """Build the official internet-mode ``ReqGetETFCodeTableList`` envelope."""
    if len(items) != 1:
        raise NotImplementedError(
            "only the single-item ETF info query has been wire-verified"
        )
    market = int(items[0]["market"])
    if market not in VERIFIED_ETF_INFO_MARKETS:
        raise NotImplementedError(
            f"ETF info query for market {market} has not been wire-verified in "
            "internet mode"
        )
    security_code = str(items[0]["security_code"]).strip()
    if not security_code:
        raise ValueError("ETF info request is missing security_code")
    try:
        encoded_code = security_code.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("ETF info security_code is not encodable") from exc
    if len(encoded_code) > 32:
        raise ValueError("ETF info security_code exceeds 32 bytes")
    # Captured header key order for codelist-channel requests is
    # id -> userName -> token (the dgw*_query builders use a different order).
    return _compact_json({
        "headers": {"id": int(request_id), "userName": username, "token": token},
        "method": "ReqGetETFCodeTableList",
        "params": {"Security": f"{security_code}|{market}"},
    })


def build_etf_codelist_complete_request(username: str, token: str,
                                        request_id: int) -> bytes:
    """Build the captured codelist-channel completion message (no params)."""
    return _compact_json({
        "headers": {"id": int(request_id), "userName": username, "token": token},
        "method": "ReqGetCodelistComplete",
    })


# (wire position, official field name, kind).  kind "char" marks single C char
# fields that travel as ASCII integer codes; "str" marks character arrays.
ETF_RECORD_FIELDS: list[tuple[int, str, str]] = [
    (1, "security_code", "str"),
    (2, "creation_redemption_unit", "int"),
    (3, "max_cash_ratio", "int"),
    (4, "publish", "char"),
    (5, "creation", "char"),
    (6, "redemption", "char"),
    (7, "creation_redemption_switch", "char"),
    (8, "record_num", "int"),
    (9, "total_record_num", "int"),
    (10, "estimate_cash_component", "int"),
    (11, "trading_day", "int"),
    (12, "pre_trading_day", "int"),
    (13, "cash_component", "int"),
    (14, "nav_per_cu", "int"),
    (15, "nav", "int"),
    (16, "market_type", "int"),
    (17, "symbol", "str"),
    (18, "fund_management_company", "str"),
    (19, "underlying_security_id", "str"),
    (20, "underlying_security_id_source", "str"),
    (21, "dividend_per_cu", "int"),
    (22, "creation_limit", "int"),
    (23, "redemption_limit", "int"),
    (24, "creation_limit_per_user", "int"),
    (25, "redemption_limit_per_user", "int"),
    (26, "net_creation_limit", "int"),
    (27, "net_redemption_limit", "int"),
    (28, "net_creation_limit_per_user", "int"),
    (29, "net_redemption_limit_per_user", "int"),
    (30, "all_cash_flag", "char"),
    (31, "all_cash_amount", "str"),
    (32, "all_cash_premium_rate", "str"),
    (33, "all_cash_discount_rate", "str"),
    (34, "rtgs_flag", "char"),
    (35, "reserved", "str"),
]

ETF_CONSTITUENT_FIELDS: list[tuple[int, str, str]] = [
    (1, "security_code", "str"),
    (2, "market_type", "int"),
    (3, "underlying_symbol", "str"),
    (4, "component_share", "int"),
    (5, "substitute_flag", "char"),
    (6, "premium_ratio", "int"),
    (7, "discount_ratio", "int"),
    (8, "creation_cash_substitute", "int"),
    (9, "redemption_cash_substitute", "int"),
    (10, "substitution_cash_amount", "int"),
    (11, "underlying_security_id", "str"),
    (12, "buy_or_sell_to_open", "char"),
    (13, "reserved", "str"),
]


def _decode_etf_slots(record: dict[str, Any], fields: list[tuple[int, str, str]],
                      label: str, *, extra_slots: frozenset[str] = frozenset()
                      ) -> dict[str, Any]:
    expected_keys = {str(position) for position, _, _ in fields} | set(extra_slots)
    actual_keys = set(record)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise TgwProtocolError(
            f"ETF {label} slot mismatch (missing={missing}, extra={extra})"
        )
    decoded: dict[str, Any] = {}
    for position, name, kind in fields:
        value = record[str(position)]
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise TgwProtocolError(f"ETF {label}.{name} is not an integer")
            decoded[name] = value
        elif kind == "char":
            if isinstance(value, bool) or not isinstance(value, int):
                raise TgwProtocolError(f"ETF {label}.{name} is not an ASCII code")
            if not 0 <= value <= 0xFF:
                raise TgwProtocolError(f"ETF {label}.{name} ASCII code is out of range")
            decoded[name] = "" if value == 0 else chr(value)
        else:
            if not isinstance(value, str):
                raise TgwProtocolError(f"ETF {label}.{name} is not a string")
            decoded[name] = value
    return decoded


def _decode_etf_constituents(slot: Any) -> list[dict[str, Any]]:
    if not isinstance(slot, list):
        raise TgwProtocolError("ETF constituent slot is not a list")
    decoded: list[dict[str, Any]] = []
    for entry in slot:
        if not isinstance(entry, dict):
            raise TgwProtocolError("ETF constituent entry is not an object")
        decoded.append(
            _decode_etf_slots(entry, ETF_CONSTITUENT_FIELDS, "constituent")
        )
    return decoded


def decode_etf_record(record: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Convert one numeric-key wire record to the official named shape.

    Returns ``(basic_info, constituent_stock_info)`` mirroring the official
    json-format container rows. Values stay unscaled exactly as delivered by
    the official wrapper (scaling remains consumer-side per header comments).
    """
    if not isinstance(record, dict):
        raise TgwProtocolError("ETF record is not an object")
    basic = _decode_etf_slots(
        record, ETF_RECORD_FIELDS, "record", extra_slots=frozenset({"36"})
    )
    constituents = _decode_etf_constituents(record.get("36"))
    return basic, constituents


def parse_etf_info_packets(packets: list[dict[str, Any]], *,
                           expected_tag: str = ETF_WIRE_TAG,
                           expected_request_id: int | None = None
                           ) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Validate codelist-channel response frames and flatten their records.

    Frames on this channel carry no pack_num/all_pack_num paging controls, so
    validation covers status, tag, optional request-id echo and the nested
    record shape instead of packet counters. Multiple frames concatenate in
    arrival order (only the single-frame shape has been observed live).
    """
    if not packets:
        raise TgwProtocolError("empty query response")
    results: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for packet in packets:
        if packet.get("status") != 0:
            raise TgwProtocolError(
                f"query rejected (status={packet.get('status')!r})"
            )
        headers = packet.get("headers")
        if not isinstance(headers, dict) or headers.get("tag") != expected_tag:
            raise TgwProtocolError(
                f"unexpected query response tag (expected {expected_tag!r})"
            )
        if expected_request_id is not None and headers.get("id") != expected_request_id:
            raise TgwProtocolError("query response request id mismatch")
        data = packet.get("data")
        if not isinstance(data, list):
            raise TgwProtocolError("etf-info response data is not a list")
        for record in data:
            results.append(decode_etf_record(record))
    return results


# ---------------------------------------------------------------------------
# QuerySecuritiesInfo (internet mode)
#
# Wire contract captured from an authorized official Linux SDK session on
# 2026-08-26 (SSE 510300): this query shares the *persistent push* connection
# (/amd/dgw/push) with ETF info and subscriptions -- it does NOT open a
# one-shot dgw*_query endpoint. The request method is ReqGetCodeTableList
# (captured, not extrapolated) with a single "Security" param "<code>|<market>"
# and headers id -> userName -> token. Completion is ReqGetCodelistComplete
# (no params). Responses carry the string tag "109" (code_num in headers),
# status 0, 0x59+ZSTD frames, and a data array of numeric-key record objects
# ("1".."43") whose slot order mirrors MDCodeTableRecord; there are no
# pack_num/all_pack_num paging controls. Only the single-item, single-frame
# shape has been observed; all other branches fail explicitly below.
# ---------------------------------------------------------------------------
SECINFO_WIRE_TAG = "109"
SECINFO_RECORD_FIELD_COUNT = 43

# Live-verified market sample. The public header allows SSE/SZSE/NEEQ, but only
# an SSE sample has completed the Linux/wire/Mac loop so far.
VERIFIED_SECINFO_MARKETS = {101}


def build_secinfo_request(username: str, token: str, request_id: int,
                          items: list[dict[str, Any]]) -> bytes:
    """Build the captured internet-mode ``ReqGetCodeTableList`` envelope."""
    if len(items) != 1:
        raise NotImplementedError(
            "only the single-item securities-info query has been wire-verified"
        )
    market = int(items[0]["market"])
    if market not in VERIFIED_SECINFO_MARKETS:
        raise NotImplementedError(
            f"securities-info query for market {market} has not been "
            "wire-verified in internet mode"
        )
    security_code = str(items[0]["security_code"]).strip()
    if not security_code:
        raise ValueError("securities-info request is missing security_code")
    try:
        encoded_code = security_code.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("securities-info security_code is not encodable") from exc
    if len(encoded_code) > 32:
        raise ValueError("securities-info security_code exceeds 32 bytes")
    # Captured header key order for the push/codelist channel is
    # id -> userName -> token.
    return _compact_json({
        "headers": {"id": int(request_id), "userName": username, "token": token},
        "method": "ReqGetCodeTableList",
        "params": {"Security": f"{security_code}|{market}"},
    })


# (wire slot, official field name, kind). kind "str" marks fixed-width char
# arrays delivered as JSON strings; "int" marks every numeric field (int64,
# uint8, uint32) delivered as JSON integers. Slot order mirrors the
# MDCodeTableRecord ABI (tgw_struct.h:895-943).
SECINFO_RECORD_FIELDS: list[tuple[int, str, str]] = [
    (1, "security_code", "str"),
    (2, "market_type", "int"),
    (3, "symbol", "str"),
    (4, "english_name", "str"),
    (5, "security_type", "str"),
    (6, "currency", "str"),
    (7, "variety_category", "int"),
    (8, "pre_close_price", "int"),
    (9, "underlying_security_id", "str"),
    (10, "contract_type", "str"),
    (11, "exercise_price", "int"),
    (12, "expire_date", "int"),
    (13, "high_limited", "int"),
    (14, "low_limited", "int"),
    (15, "security_status", "str"),
    (16, "price_tick", "int"),
    (17, "buy_qty_unit", "int"),
    (18, "sell_qty_unit", "int"),
    (19, "market_buy_qty_unit", "int"),
    (20, "market_sell_qty_unit", "int"),
    (21, "buy_qty_lower_limit", "int"),
    (22, "buy_qty_upper_limit", "int"),
    (23, "sell_qty_lower_limit", "int"),
    (24, "sell_qty_upper_limit", "int"),
    (25, "market_buy_qty_lower_limit", "int"),
    (26, "market_buy_qty_upper_limit", "int"),
    (27, "market_sell_qty_lower_limit", "int"),
    (28, "market_sell_qty_upper_limit", "int"),
    (29, "list_day", "int"),
    (30, "par_value", "int"),
    (31, "outstanding_share", "int"),
    (32, "public_float_share_quantity", "int"),
    (33, "contract_multiplier", "int"),
    (34, "regular_share", "str"),
    (35, "interest", "int"),
    (36, "coupon_rate", "int"),
    (37, "product_code", "str"),
    (38, "delivery_year", "int"),
    (39, "delivery_month", "int"),
    (40, "create_date", "int"),
    (41, "start_deliv_date", "int"),
    (42, "end_deliv_date", "int"),
    (43, "position_type", "int"),
]


def decode_secinfo_record(record: dict[str, Any]) -> dict[str, Any]:
    """Convert one numeric-key wire record to the official named shape."""
    if not isinstance(record, dict):
        raise TgwProtocolError("securities-info record is not an object")
    expected_keys = {str(position) for position, _, _ in SECINFO_RECORD_FIELDS}
    actual_keys = set(record)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise TgwProtocolError(
            f"securities-info slot mismatch (missing={missing}, extra={extra})"
        )
    decoded: dict[str, Any] = {}
    for position, name, kind in SECINFO_RECORD_FIELDS:
        value = record[str(position)]
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise TgwProtocolError(
                    f"securities-info field {name} is not an integer"
                )
            decoded[name] = value
        else:
            if not isinstance(value, str):
                raise TgwProtocolError(
                    f"securities-info field {name} is not a string"
                )
            decoded[name] = value
    return decoded


def parse_secinfo_packets(packets: list[dict[str, Any]], *,
                          expected_tag: str = SECINFO_WIRE_TAG,
                          expected_request_id: int | None = None
                          ) -> list[dict[str, Any]]:
    """Validate codelist-channel response frames and flatten their records.

    Frames on this channel carry no pack_num/all_pack_num paging controls, so
    validation covers status, tag, optional request-id echo, the code_num count
    and the nested record shape instead of packet counters. Multiple frames
    concatenate in arrival order (only the single-frame shape observed live).
    """
    if not packets:
        raise TgwProtocolError("empty query response")
    results: list[dict[str, Any]] = []
    for packet in packets:
        if packet.get("status") != 0:
            raise TgwProtocolError(
                f"query rejected (status={packet.get('status')!r})"
            )
        headers = packet.get("headers")
        if not isinstance(headers, dict) or headers.get("tag") != expected_tag:
            raise TgwProtocolError(
                f"unexpected query response tag (expected {expected_tag!r})"
            )
        if expected_request_id is not None and headers.get("id") != expected_request_id:
            raise TgwProtocolError("query response request id mismatch")
        data = packet.get("data")
        if not isinstance(data, list):
            raise TgwProtocolError("securities-info response data is not a list")
        for record in data:
            results.append(decode_secinfo_record(record))
    return results


# ---------------------------------------------------------------------------
# QueryExFactorTable (internet mode, single code)
#
# Wire contract captured from an authorized official Linux SDK session on
# 2026-08-26 (SSE 000001): this query runs on the *one-shot* dgw*_query
# endpoint (not the persistent push channel), request method ReqGetExFactor
# with params security_code (str) then QueryBandWidth (float), headers
# id -> userName -> token. Completion is the channel-standard ReqGetComplete.
# Responses carry the integer tag 11102, status 0, pack_num/all_pack_num
# paging and 0x59+ZSTD frames; each ``data`` row is a 5-field CSV string whose
# order mirrors MDExFactorTable: inner_code, security_code, ex_date,
# ex_factor, cum_factor. The two double fields travel as fixed-point decimal
# strings with 18 fractional digits and decode to Python float via the
# official json path. Only the single-code, synchronous shape has been
# observed; all other branches fail explicitly below.
# ---------------------------------------------------------------------------
EX_FACTOR_WIRE_TAG = 11102
EX_FACTOR_ROW_FIELD_COUNT = 5


def build_ex_factor_request(username: str, token: str, request_id: int,
                            security_code: str) -> bytes:
    """Build the captured internet-mode ``ReqGetExFactor`` envelope.

    The public API takes only ``const char* code``; the wire carries it as the
    string ``security_code`` param. Header key order id -> userName -> token
    and the trailing QueryBandWidth float match the captured envelope.
    """
    security_code = str(security_code).strip()
    if not security_code:
        raise ValueError("ex-factor request is missing security_code")
    try:
        encoded_code = security_code.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("ex-factor security_code is not encodable") from exc
    if len(encoded_code) > 32:
        raise ValueError("ex-factor security_code exceeds 32 bytes")
    return _compact_json({
        "headers": {"id": int(request_id), "userName": username, "token": token},
        "method": "ReqGetExFactor",
        "params": {
            "security_code": security_code,
            "QueryBandWidth": 0.0,
        },
    })


def _decode_ex_factor_row(fields: list[str]) -> dict[str, Any]:
    """Decode one captured 5-field CSV row into the official named shape.

    The official wrapper parses the CSV into C++ doubles and re-serializes to
    JSON before exposing Python floats; parsing the fixed-point decimal string
    with ``float`` reproduces that exact double value.
    """
    if len(fields) != EX_FACTOR_ROW_FIELD_COUNT:
        raise TgwProtocolError(
            f"ex-factor response row does not contain "
            f"{EX_FACTOR_ROW_FIELD_COUNT} fields"
        )
    if not fields[2].isdigit():
        raise TgwProtocolError("ex-factor ex_date is not an integer")
    for name, value in (("ex_factor", fields[3]), ("cum_factor", fields[4])):
        try:
            float(value)
        except ValueError as exc:
            raise TgwProtocolError(
                f"ex-factor {name} is not a number"
            ) from exc
    return {
        "inner_code": fields[0],
        "security_code": fields[1],
        "ex_date": int(fields[2]),
        "ex_factor": float(fields[3]),
        "cum_factor": float(fields[4]),
    }


def parse_ex_factor_packets(packets: list[dict[str, Any]],
                            expected_tag: int = EX_FACTOR_WIRE_TAG
                            ) -> list[dict[str, Any]]:
    """Decode ``ReqGetExFactor`` responses into official 5-column rows.

    Reuses ``_ordered_query_packets`` to validate status/tag/packet integrity
    (tag 11102, status 0, a complete 1..all_pack_num set), then splits each
    row on commas and maps the CSV columns to the MDExFactorTable fields with
    doubles decoded as Python floats. Unknown tags/statuses fail.
    """
    rows: list[dict[str, Any]] = []
    for packet in _ordered_query_packets(packets, expected_tag):
        data = packet.get("data")
        if not isinstance(data, list) or not all(isinstance(row, str) for row in data):
            raise TgwProtocolError("ex-factor response data is not a string array")
        for encoded in data:
            rows.append(_decode_ex_factor_row(encoded.split(",")))
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


def parse_kline_packets(packets: list[dict[str, Any]], expected_tag: int) -> list[dict[str, Any]]:
    """Decode the official 9-field CSV rows returned for ``ReqGetKline``.

    The response tag equals the wire period enum (10100 daily / 10101 weekly /
    10102 monthly / 10103 seasonal / 10104 yearly), so callers must pass the
    same verified tag used to build the request.
    """
    rows: list[dict[str, Any]] = []
    for packet in _ordered_query_packets(packets, expected_tag):
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
    decompressor = zstandard.ZstdDecompressor()
    try:
        return decompressor.decompress(payload)
    except zstandard.ZstdError:
        # Official push frames may omit the frame content size; one-shot
        # decompress refuses those, the streaming object does not.
        try:
            return decompressor.decompressobj().decompress(payload)
        except zstandard.ZstdError:
            return None


def _decode_json_object_stream(raw: bytes) -> tuple[dict[str, Any], ...]:
    """Decode one or more TGW objects separated by whitespace or backticks.

    Bulk subscription pushes observed on 2026-08-27 are encoded as one ZSTD
    frame whose decompressed buffer contains adjacent JSON objects separated by
    ASCII 0x60 (``).  A final C NUL terminator belongs to the buffer, not to a
    JSON document.
    """

    try:
        text = raw.rstrip(b"\x00\r\n\t ").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TgwProtocolError("TGW server payload is not UTF-8 JSON") from exc
    if not text:
        raise TgwProtocolError("TGW server payload contains no JSON object")

    decoder = json.JSONDecoder()
    messages: list[dict[str, Any]] = []
    offset = 0
    length = len(text)
    while offset < length:
        if messages:
            separator_start = offset
            while offset < length and text[offset].isspace():
                offset += 1
            if offset < length and text[offset] == "`":
                offset += 1
                while offset < length and text[offset].isspace():
                    offset += 1
            elif offset == separator_start:
                raise TgwProtocolError(
                    "TGW JSON objects must be separated by whitespace or ASCII 0x60"
                )
            if offset >= length:
                raise TgwProtocolError("TGW server payload ends after an object separator")
        else:
            while offset < length and text[offset].isspace():
                offset += 1

        try:
            value, offset = decoder.raw_decode(text, offset)
        except json.JSONDecodeError as exc:
            raise TgwProtocolError("TGW server payload contains invalid JSON") from exc
        if not isinstance(value, dict):
            raise TgwProtocolError("TGW server JSON members must be objects")
        messages.append(value)

    return tuple(messages)


def decode_server_payload(
    payload: bytes,
) -> dict[str, Any] | DecodedMessageBatch | CompressedMessage:
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
        raw = decoded

    messages = _decode_json_object_stream(raw)
    if len(messages) == 1:
        return messages[0]
    return DecodedMessageBatch(messages=messages)


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
        decoded = decode_server_payload(payload)
        if isinstance(decoded, CompressedMessage):
            self._offer_event(decoded)
            return
        messages = decoded.messages if isinstance(decoded, DecodedMessageBatch) else (decoded,)
        for message in messages:
            self._dispatch_message(message)

    def _dispatch_message(self, message: dict[str, Any]) -> None:
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

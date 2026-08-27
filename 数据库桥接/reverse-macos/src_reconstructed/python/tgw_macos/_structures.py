"""ctypes mirrors of the public TGW 1.0.8 C++ header contract.

The official headers use ``#pragma pack(1)``. These structures are an API/ABI
compatibility boundary; application-only convenience fields must not be added.
"""
from __future__ import annotations

from ctypes import (
    Structure,
    c_bool,
    c_char,
    c_char_p,
    c_uint8,
    c_uint16,
    c_uint32,
    c_uint64,
)


def _encoded(value: str | bytes) -> bytes:
    return value if isinstance(value, bytes) else value.encode("utf-8")


class ColocaCfg(Structure):
    _pack_ = 1
    _fields_ = [
        ("channel_mode", c_uint64),
        ("qtcp_channel_thread", c_uint16),
        ("qtcp_req_time_out", c_uint16),
        ("qtcp_max_req_cnt", c_uint16),
        ("enable_order_book", c_uint8),
        ("entry_size", c_uint16),
        ("order_queue_size", c_uint8),
        ("order_book_deliver_interval_microsecond", c_uint32),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.qtcp_channel_thread = 3
        self.qtcp_req_time_out = 10
        self.qtcp_max_req_cnt = 1000
        self.entry_size = 10


class Cfg(Structure):
    _pack_ = 1
    _fields_ = [
        ("server_vip", c_char * 24),
        ("server_port", c_uint16),
        ("username", c_char * 32),
        ("password", c_char * 64),
        ("force_logout", c_bool),
        ("coloca_cfg", ColocaCfg),
    ]

    def set(self, **values: str | bytes | int | bool) -> "Cfg":
        for name, value in values.items():
            if name in {"server_vip", "username", "password"}:
                value = _encoded(value)  # type: ignore[arg-type]
            setattr(self, name, value)
        return self


class LogonResponse(Structure):
    _pack_ = 1
    _fields_ = [
        ("api_mode", c_uint16),
        ("logon_msg_len", c_uint32),
        ("logon_json", c_char_p),
    ]


class SubscribeItem(Structure):
    _pack_ = 1
    _fields_ = [
        ("market", c_uint8),
        ("flag", c_uint64),
        ("security_code", c_char * 32),
        ("category_type", c_uint8),
    ]

    def set_code(self, code: str | bytes) -> "SubscribeItem":
        self.security_code = _encoded(code)
        return self


class ReqKline(Structure):
    _pack_ = 1
    _fields_ = [
        ("security_code", c_char * 38),
        ("market_type", c_uint8),
        ("cq_flag", c_uint8),
        ("cq_date", c_uint32),
        ("qj_flag", c_uint32),
        ("cyc_type", c_uint16),
        ("cyc_def", c_uint32),
        ("auto_complete", c_uint8),
        ("begin_date", c_uint32),
        ("end_date", c_uint32),
        ("begin_time", c_uint32),
        ("end_time", c_uint32),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.auto_complete = 1

    def set_code(self, code: str | bytes) -> "ReqKline":
        self.security_code = _encoded(code)
        return self


class ReqDefault(Structure):
    _pack_ = 1
    _fields_ = [
        ("security_code", c_char * 38),
        ("market_type", c_uint8),
        ("date", c_uint32),
        ("begin_time", c_uint32),
        ("end_time", c_uint32),
        ("data_type", c_uint16),
        ("level_type", c_uint16),
    ]

    def set_code(self, code: str | bytes) -> "ReqDefault":
        self.security_code = _encoded(code)
        return self


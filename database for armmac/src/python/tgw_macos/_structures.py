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
    c_double,
    c_int32,
    c_int64,
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


class MDCodeTable(Structure):
    """Code-table output row; ``#pragma pack(1)`` per tgw_struct.h:841-849.

    ``market_type`` is the only numeric field (``uint8``); the rest are fixed
    width character arrays. Wire rows are decoded into the official 6-column
    JSON shape, so this structure is an ABI mirror for layout/offset tests.
    """

    _pack_ = 1
    _fields_ = [
        ("security_code", c_char * 16),
        ("symbol", c_char * 32),
        ("english_name", c_char * 128),
        ("market_type", c_uint8),
        ("security_type", c_char * 10),
        ("currency", c_char * 4),
    ]


class MDExFactorTable(Structure):
    """Ex-factor output row; ``#pragma pack(1)`` per tgw_struct.h:855-862.

    ``inner_code``/``security_code`` use the ``ConstField.kSecurityCodeLen``
    width (16); ``ex_date`` is ``uint32_t`` (yyyyMMdd); ``ex_factor`` and
    ``cum_factor`` are ``double`` (the header annotates them ``N38(15)``).
    Wire rows are decoded into the official 5-column JSON shape, so this
    structure is an ABI mirror for layout/offset tests.
    """

    _pack_ = 1
    _fields_ = [
        ("inner_code", c_char * 16),
        ("security_code", c_char * 16),
        ("ex_date", c_uint32),
        ("ex_factor", c_double),
        ("cum_factor", c_double),
    ]

    def set_code(self, code: str | bytes) -> "MDExFactorTable":
        self.security_code = _encoded(code)
        return self


class SubCodeTableItem(Structure):
    """Shared ``QuerySecuritiesInfo``/``QueryETFInfo`` request item.

    The public header declares a *signed* ``int32_t market`` here (unlike
    ``SubscribeItem.market`` which is ``uint8``).
    """

    _pack_ = 1
    _fields_ = [
        ("market", c_int32),
        ("security_code", c_char * 32),
    ]

    def set_code(self, code: str | bytes) -> "SubCodeTableItem":
        self.security_code = _encoded(code)
        return self


class MDCodeTableRecord(Structure):
    """Securities-static-info output row; ``#pragma pack(1)`` per
    tgw_struct.h:895-943.

    ``market_type``/``variety_category`` are ``uint8``; the fixed-width char
    arrays use the ``ConstField`` lengths. The wire delivers this as a
    numeric-key JSON object (slots 1..43 mirroring this field order), so this
    structure is an ABI mirror for layout/offset tests rather than the wire
    decode path.
    """

    _pack_ = 1
    _fields_ = [
        ("security_code", c_char * 32),
        ("market_type", c_uint8),
        ("symbol", c_char * 128),
        ("english_name", c_char * 64),
        ("security_type", c_char * 16),
        ("currency", c_char * 8),
        ("variety_category", c_uint8),
        ("pre_close_price", c_int64),
        ("underlying_security_id", c_char * 16),
        ("contract_type", c_char * 16),
        ("exercise_price", c_int64),
        ("expire_date", c_uint32),
        ("high_limited", c_int64),
        ("low_limited", c_int64),
        ("security_status", c_char * 16),
        ("price_tick", c_int64),
        ("buy_qty_unit", c_int64),
        ("sell_qty_unit", c_int64),
        ("market_buy_qty_unit", c_int64),
        ("market_sell_qty_unit", c_int64),
        ("buy_qty_lower_limit", c_int64),
        ("buy_qty_upper_limit", c_int64),
        ("sell_qty_lower_limit", c_int64),
        ("sell_qty_upper_limit", c_int64),
        ("market_buy_qty_lower_limit", c_int64),
        ("market_buy_qty_upper_limit", c_int64),
        ("market_sell_qty_lower_limit", c_int64),
        ("market_sell_qty_upper_limit", c_int64),
        ("list_day", c_uint32),
        ("par_value", c_int64),
        ("outstanding_share", c_int64),
        ("public_float_share_quantity", c_int64),
        ("contract_multiplier", c_int64),
        ("regular_share", c_char * 9),
        ("interest", c_int64),
        ("coupon_rate", c_int64),
        ("product_code", c_char * 32),
        ("delivery_year", c_uint32),
        ("delivery_month", c_uint32),
        ("create_date", c_uint32),
        ("start_deliv_date", c_uint32),
        ("end_deliv_date", c_uint32),
        ("position_type", c_uint32),
    ]


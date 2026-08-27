"""Explicit, evidence-scoped units for one verified K-line response.

The TGW wire response intentionally remains exposed as raw protocol integers in
``QueryKline``.  This module is an opt-in adapter for the one response whose
units have been reconciled against an independent client: SZSE 159691, one
minute, 2026-08-26 regular session.  It must not be widened by inference.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping


VERIFIED_159691_SZSE_ONE_MINUTE_DATE = 20260826

_RAW_KLINE_FIELDS = frozenset({
    "market_type",
    "security_code",
    "orig_time",
    "kline_time",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "volume_trade",
    "value_trade",
    "variety_category",
})
_PRICE_DIVISOR = Decimal("1000000")
_VOLUME_DIVISOR = 100
_VALUE_DIVISOR = Decimal("100000")


def _code_as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.split(b"\0", 1)[0].decode("utf-8")
    if isinstance(value, str):
        return value.split("\0", 1)[0]
    return str(value)


def require_verified_159691_szse_one_minute_request(request: object) -> None:
    """Reject normalized output outside the single independently checked scope."""
    expected = {
        "security_code": "159691",
        "market_type": 102,
        "cq_flag": 0,
        "cq_date": 0,
        "qj_flag": 0,
        "cyc_type": 10000,
        "cyc_def": 0,
        "auto_complete": 1,
        "begin_date": VERIFIED_159691_SZSE_ONE_MINUTE_DATE,
        "end_date": VERIFIED_159691_SZSE_ONE_MINUTE_DATE,
        "begin_time": 900,
        "end_time": 1500,
    }
    actual = {
        "security_code": _code_as_text(getattr(request, "security_code", "")),
        **{
            key: int(getattr(request, key, -1))
            for key in expected
            if key != "security_code"
        },
    }
    mismatches = [
        f"{key}={actual[key]!r}" for key, value in expected.items()
        if actual[key] != value
    ]
    if mismatches:
        raise NotImplementedError(
            "normalized K-line output is verified only for SZSE 159691 "
            "one-minute data on 2026-08-26 (09:00-15:00); got "
            + ", ".join(mismatches)
        )


def _require_int(row: Mapping[str, object], field: str, row_index: int) -> int:
    value = row[field]
    if type(value) is not int:
        raise ValueError(
            f"raw K-line row {row_index} field {field!r} must be an int, "
            f"got {type(value).__name__}"
        )
    return value


def _require_verified_row(row: Mapping[str, object], row_index: int) -> None:
    if set(row) != _RAW_KLINE_FIELDS:
        unexpected = sorted(
            set(row).symmetric_difference(_RAW_KLINE_FIELDS), key=repr
        )
        raise ValueError(
            f"raw K-line row {row_index} has an unexpected schema: {unexpected}"
        )
    if _code_as_text(row["security_code"]) != "159691":
        raise NotImplementedError(
            "normalized K-line output is verified only for security_code 159691"
        )
    for field in _RAW_KLINE_FIELDS - {"security_code"}:
        _require_int(row, field, row_index)
    if row["market_type"] != 102:
        raise NotImplementedError(
            "normalized K-line output is verified only for market_type 102"
        )
    kline_time = int(row["kline_time"])
    if kline_time // 10000 != VERIFIED_159691_SZSE_ONE_MINUTE_DATE:
        raise NotImplementedError(
            "normalized K-line output is verified only for 2026-08-26 rows"
        )
    time_of_day = kline_time % 10000
    if not (930 <= time_of_day <= 1130 or 1300 <= time_of_day <= 1500):
        raise ValueError(
            f"raw K-line row {row_index} has an invalid regular-session time "
            f"{time_of_day:04d}"
        )
    if row["orig_time"] != 0 or row["variety_category"] != 0:
        raise ValueError(
            f"raw K-line row {row_index} violates the verified row invariants"
        )
    open_price = int(row["open_price"])
    high_price = int(row["high_price"])
    low_price = int(row["low_price"])
    close_price = int(row["close_price"])
    if min(open_price, close_price) < low_price or max(open_price, close_price) > high_price:
        raise ValueError(f"raw K-line row {row_index} has invalid OHLC bounds")
    if min(low_price, int(row["volume_trade"]), int(row["value_trade"])) < 0:
        raise ValueError(f"raw K-line row {row_index} has a negative trade value")
    if int(row["volume_trade"]) % _VOLUME_DIVISOR != 0:
        raise ValueError(
            f"raw K-line row {row_index} volume_trade is not divisible by "
            f"{_VOLUME_DIVISOR}"
        )


def normalize_verified_159691_szse_one_minute_kline_rows(
    rows: Iterable[Mapping[str, object]],
) -> list[dict[str, Any]]:
    """Return an exact-unit view of the verified raw K-line rows.

    Prices and traded value are :class:`decimal.Decimal`, so neither a binary
    float nor a presentation rounding step can move a decimal point.  The
    original protocol integers remain available under ``raw_*`` names for
    traceability.  This function validates the fixed scope before converting.
    """
    normalized: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(
                f"raw K-line row {row_index} must be a mapping, "
                f"got {type(row).__name__}"
            )
        _require_verified_row(row, row_index)
        normalized.append({
            "market_type": 102,
            "security_code": "159691",
            "orig_time": 0,
            "kline_time": int(row["kline_time"]),
            "open_price_yuan": Decimal(int(row["open_price"])) / _PRICE_DIVISOR,
            "high_price_yuan": Decimal(int(row["high_price"])) / _PRICE_DIVISOR,
            "low_price_yuan": Decimal(int(row["low_price"])) / _PRICE_DIVISOR,
            "close_price_yuan": Decimal(int(row["close_price"])) / _PRICE_DIVISOR,
            "volume_shares": int(row["volume_trade"]) // _VOLUME_DIVISOR,
            "value_trade_yuan": Decimal(int(row["value_trade"])) / _VALUE_DIVISOR,
            "variety_category": 0,
            "raw_open_price": int(row["open_price"]),
            "raw_high_price": int(row["high_price"]),
            "raw_low_price": int(row["low_price"]),
            "raw_close_price": int(row["close_price"]),
            "raw_volume_trade": int(row["volume_trade"]),
            "raw_value_trade": int(row["value_trade"]),
        })
    return normalized

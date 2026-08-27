#!/usr/bin/env python3
"""Run one authorized official-Linux-SDK query, printing shape only."""
from __future__ import annotations

import argparse
import collections
import json
import threading
import time
from pathlib import Path
from typing import Any

import yaml


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _etf_entry_parts(entry: Any) -> tuple[Any, Any]:
    """Official json-format rows are (basic_dict, constituent_list) tuples."""
    if isinstance(entry, dict):
        return entry.get("basic_info"), entry.get("constituent_stock_info")
    if isinstance(entry, (tuple, list)) and len(entry) == 2:
        return entry[0], entry[1]
    return None, None


class _EtfBatchCollector:
    """Async user SPI: records per-batch counters only, never row values."""

    def __init__(self, wait_seconds: float) -> None:
        self.wait_seconds = wait_seconds
        self.immediate_error: int | None = None
        self.data_batches: list[dict[str, Any]] = []
        self.status_errors: list[int] = []
        self._done = threading.Event()

    def __call__(self, result: Any, err_code: Any) -> None:  # OnResponse slot
        if err_code is not None and result is None:
            self.status_errors.append(
                err_code if isinstance(err_code, int) else str(type(err_code).__name__)
            )
            self._done.set()
            return
        if isinstance(result, list):
            batch: dict[str, Any] = {"records": len(result)}
            for entry in result:
                basic, cons = _etf_entry_parts(entry)
                if isinstance(basic, dict):
                    batch["basic_key_count"] = len(basic)
                    batch["basic_keys_sorted"] = sorted(basic)
                    batch["basic_value_types"] = sorted(
                        {type(value).__name__ for value in basic.values()}
                    )
                if isinstance(cons, list):
                    batch.setdefault("constituent_counts", []).append(len(cons))
                    if cons and isinstance(cons[0], dict):
                        batch["constituent_keys_sorted"] = sorted(cons[0])
            self.data_batches.append(batch)
        self._done.set()

    def summary(self) -> dict[str, Any]:
        self._done.wait(self.wait_seconds)
        return {
            "immediate_return": self.immediate_error,
            "data_batch_count": len(self.data_batches),
            "data_batches": self.data_batches,
            "status_errors": self.status_errors,
        }


def _etf_info_shapes(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Desensitized shape + invariant summary of one sync ETF result."""
    shape: dict[str, Any] = {}
    invariants: dict[str, Any] = {}
    if not isinstance(result, list):
        return {"type": type(result).__name__}, invariants
    shape["type"] = "list"
    shape["length"] = len(result)
    basics: list[Any] = []
    constituents: list[Any] = []
    for entry in result:
        basic, cons = _etf_entry_parts(entry)
        basics.append(basic)
        constituents.append(cons)
    first_basic = next((item for item in basics if isinstance(item, dict)), None)
    if not basics or first_basic is None:
        shape["entry_kind"] = type(result[0]).__name__ if result else None
        return shape, invariants
    shape["entry_kind"] = "pair"
    if isinstance(first_basic, dict):
        shape["basic_keys_sorted"] = sorted(first_basic)
        shape["basic_value_types"] = {
            key: type(value).__name__ for key, value in sorted(first_basic.items())
        }
        shape["all_basic_key_sets_identical"] = all(
            isinstance(item, dict) and sorted(item.keys()) == sorted(first_basic)
            for item in basics
        )
        flags = {str(first_basic.get(name, "")) for name in ("publish", "creation",
                                                             "redemption")}
        market_types = {
            item.get("market_type") for item in basics if isinstance(item, dict)
        }
        trading_days = {
            item.get("trading_day") for item in basics if isinstance(item, dict)
        }
        units = {
            item.get("creation_redemption_unit")
            for item in basics
            if isinstance(item, dict)
        }
        navs = {item.get("nav") for item in basics if isinstance(item, dict)}
        codes = [
            item.get("security_code") for item in basics if isinstance(item, dict)
        ]
        invariants = {
            "flag_values_present_only": sorted(value for value in flags if value),
            "distinct_market_type": sorted(market_types),
            "trading_day_digit_lengths": sorted({len(str(v)) for v in trading_days}),
            "creation_unit_positive_exists": any(
                isinstance(v, int) and v > 0 for v in units
            ),
            "nav_non_negative_exists": any(isinstance(v, int) and v >= 0 for v in navs),
            "duplicate_security_code_count": len(codes) - len(set(codes)),
        }
    first_cons = next((item for item in constituents if isinstance(item, list)), None)
    if first_cons is not None:
        shape["constituent_count_first"] = len(first_cons)
        shape["constituent_total"] = sum(
            len(item) for item in constituents if isinstance(item, list)
        )
        if first_cons and isinstance(first_cons[0], dict):
            shape["constituent_keys_sorted"] = sorted(first_cons[0])
            shape["constituent_value_types"] = {
                key: type(value).__name__
                for key, value in sorted(first_cons[0].items())
            }
    return shape, invariants


class _SnapshotAsyncCollector:
    """Async user SPI for snapshot queries: counters and error codes only."""

    def __init__(self, wait_seconds: float) -> None:
        self.wait_seconds = wait_seconds
        self.submitted_at: float | None = None
        self.submit_return: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []
        self.first_call_delay: float | None = None
        self._done = threading.Event()

    def __call__(self, result: Any, err_code: Any) -> None:
        if self.submitted_at is not None and self.first_call_delay is None:
            self.first_call_delay = round(time.monotonic() - self.submitted_at, 3)
        self.calls.append({
            "result_kind": type(result).__name__ if result is not None else None,
            "records": len(result) if isinstance(result, list) else None,
            "err_code": (
                err_code
                if isinstance(err_code, int)
                else (str(err_code)[:80] if err_code is not None else None)
            ),
        })
        self._done.set()

    def summary(self) -> dict[str, Any]:
        self._done.wait(self.wait_seconds)
        time.sleep(2.0)  # grace window so trailing status callbacks still count
        return {
            "submit_return": self.submit_return,
            "call_count": len(self.calls),
            "calls": self.calls,
            "first_call_delay_sec": self.first_call_delay,
        }


class _SecuritiesInfoAsyncCollector:
    """Async user SPI for securities-info: per-batch counters and column types."""

    def __init__(self, wait_seconds: float, quiet_seconds: float = 5.0) -> None:
        self.wait_seconds = wait_seconds
        self.quiet_seconds = quiet_seconds
        self.submitted_at: float | None = None
        self.first_call_delay: float | None = None
        self.batch_sizes: list[int] = []
        self.status_errors: list[Any] = []
        self.columns: list[str] | None = None
        self.column_types: dict[str, str] | None = None
        self._rows: list[dict[str, Any]] = []
        self._last_call_at: float | None = None

    def __call__(self, result: Any, err_code: Any) -> None:
        self._last_call_at = time.monotonic()
        if self.submitted_at is not None and self.first_call_delay is None:
            self.first_call_delay = round(self._last_call_at - self.submitted_at, 3)
        if err_code is not None and result is None:
            self.status_errors.append(
                err_code if isinstance(err_code, int) else str(type(err_code).__name__)
            )
            return
        if isinstance(result, list):
            self.batch_sizes.append(len(result))
            for row in result:
                if isinstance(row, dict):
                    if self.columns is None:
                        self.columns = sorted(row)
                        self.column_types = {
                            key: type(row[key]).__name__ for key in sorted(row)
                        }
                    self._rows.append({
                        "market": row.get("market_type"),
                        "variety": row.get("variety_category"),
                        "price_fields_count": sum(
                            1 for key in _SECINFO_PRICE_FIELDS if key in row
                        ),
                        "qty_fields_count": sum(
                            1 for key in _SECINFO_QTY_FIELDS if key in row
                        ),
                    })

    def summary(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            if (
                self._last_call_at is not None
                and time.monotonic() - self._last_call_at > self.quiet_seconds
            ):
                break
            time.sleep(0.2)
        return {
            "batch_count": len(self.batch_sizes),
            "batch_sizes": self.batch_sizes,
            "total_rows": len(self._rows),
            "columns": self.columns,
            "column_types": self.column_types,
            "status_errors": self.status_errors,
            "first_call_delay_sec": self.first_call_delay,
            "invariants": {
                "distinct_market_type": sorted(
                    {row["market"] for row in self._rows if isinstance(row["market"], int)}
                ),
                "distinct_variety_category": sorted(
                    {row["variety"] for row in self._rows if isinstance(row["variety"], int)}
                ),
                "price_field_count": sorted(
                    {row["price_fields_count"] for row in self._rows}
                ),
                "qty_field_count": sorted(
                    {row["qty_fields_count"] for row in self._rows}
                ),
            },
        }


class _ExFactorAsyncCollector:
    """Async user SPI for ex-factor: per-batch counters and double invariants.

    Mirrors the sync probe's column/type and invariant summary but delivered
    through the official asynchronous query_spi path. Never records the factor
    business values themselves.
    """

    def __init__(self, wait_seconds: float, quiet_seconds: float = 5.0) -> None:
        self.wait_seconds = wait_seconds
        self.quiet_seconds = quiet_seconds
        self.submitted_at: float | None = None
        self.first_call_delay: float | None = None
        self.batch_sizes: list[int] = []
        self.status_errors: list[Any] = []
        self.columns: list[str] | None = None
        self.column_types: dict[str, str] | None = None
        self._rows: list[dict[str, Any]] = []
        self._cum_factors: list[float] = []
        self._last_call_at: float | None = None

    def __call__(self, result: Any, err_code: Any) -> None:
        self._last_call_at = time.monotonic()
        if self.submitted_at is not None and self.first_call_delay is None:
            self.first_call_delay = round(self._last_call_at - self.submitted_at, 3)
        if err_code is not None and result is None:
            self.status_errors.append(
                err_code if isinstance(err_code, int) else str(type(err_code).__name__)
            )
            return
        if isinstance(result, list):
            self.batch_sizes.append(len(result))
            for row in result:
                if isinstance(row, dict):
                    if self.columns is None:
                        self.columns = sorted(row)
                        self.column_types = {
                            key: type(row[key]).__name__ for key in sorted(row)
                        }
                    self._rows.append(_ex_factor_row_invariants(row))
                    if isinstance(row.get("cum_factor"), float):
                        self._cum_factors.append(row["cum_factor"])

    def summary(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            if (
                self._last_call_at is not None
                and time.monotonic() - self._last_call_at > self.quiet_seconds
            ):
                break
            time.sleep(0.2)
        return {
            "batch_count": len(self.batch_sizes),
            "batch_sizes": self.batch_sizes,
            "total_rows": len(self._rows),
            "columns": self.columns,
            "column_types": self.column_types,
            "status_errors": self.status_errors,
            "first_call_delay_sec": self.first_call_delay,
            "invariants": _merge_ex_factor_invariants(
                self._rows, self._cum_factors
            ),
        }


def _ex_factor_row_invariants(row: dict[str, Any]) -> dict[str, Any]:
    """Desensitized per-row invariant summary (no business factor values)."""
    def digit_length(value: Any) -> int | None:
        return len(str(value)) if isinstance(value, (int, float)) else None

    def decimal_places(value: Any) -> int | None:
        if isinstance(value, float):
            text = format(value, ".15g")
            return len(text.split(".")[1]) if "." in text else 0
        return None

    inv: dict[str, Any] = {
        "ex_date_digit_length": digit_length(row.get("ex_date")),
        "ex_date_is_int": isinstance(row.get("ex_date"), int),
        "ex_factor_kind": type(row.get("ex_factor")).__name__,
        "ex_factor_is_float": isinstance(row.get("ex_factor"), float),
        "ex_factor_is_non_negative": isinstance(row.get("ex_factor"), (int, float))
        and row.get("ex_factor") >= 0,
        "ex_factor_decimal_places": decimal_places(row.get("ex_factor")),
        "cum_factor_kind": type(row.get("cum_factor")).__name__,
        "cum_factor_is_float": isinstance(row.get("cum_factor"), float),
        "cum_factor_is_non_negative": isinstance(row.get("cum_factor"), (int, float))
        and row.get("cum_factor") >= 0,
        "cum_factor_decimal_places": decimal_places(row.get("cum_factor")),
        "inner_code_len": len(str(row.get("inner_code", ""))),
        "security_code_len": len(str(row.get("security_code", ""))),
    }
    return inv


def _merge_ex_factor_invariants(rows: list[dict[str, Any]],
                                cum_factors: list[float] | None = None
                                ) -> dict[str, Any]:
    """Collapse per-row invariants into compact set summaries.

    ``cum_factors`` carries the actual cumulative values so the monotonic
    invariant is computed on the real doubles (the per-row dict only holds
    desensitized metadata and never the factor values).
    """
    keys = [
        "ex_date_digit_length", "ex_date_is_int", "ex_factor_kind",
        "ex_factor_is_float", "ex_factor_is_non_negative",
        "ex_factor_decimal_places", "cum_factor_kind", "cum_factor_is_float",
        "cum_factor_is_non_negative", "cum_factor_decimal_places",
        "inner_code_len", "security_code_len",
    ]
    merged: dict[str, Any] = {}
    for key in keys:
        values = [row[key] for row in rows if row.get(key) is not None]
        merged[key] = sorted(set(values))
    if cum_factors:
        merged["cum_factor_monotonic_nondecreasing"] = all(
            left <= right
            for left, right in zip(cum_factors, cum_factors[1:])
        )
        merged["cum_factor_monotonic_violations"] = sum(
            1 for left, right in zip(cum_factors, cum_factors[1:]) if left > right
        )
        merged["cum_factor_first_is_one_or_more"] = any(
            value >= 1.0 for value in cum_factors
        )
        merged["cum_factor_positive_count"] = sum(
            1 for value in cum_factors if value > 0.0
        )
        merged["cum_factor_row_count"] = len(cum_factors)
    if rows:
        merged["inner_code_distinct_count"] = len(
            {row.get("inner_code") for row in rows}
        )
        merged["security_code_distinct_count"] = len(
            {row.get("security_code") for row in rows}
        )
    return merged


_SECINFO_PRICE_FIELDS = {
    "pre_close_price", "exercise_price", "high_limited", "low_limited",
    "price_tick", "par_value", "coupon_rate",
}
_SECINFO_QTY_FIELDS = {
    "buy_qty_unit", "sell_qty_unit", "market_buy_qty_unit", "market_sell_qty_unit",
    "buy_qty_lower_limit", "buy_qty_upper_limit", "sell_qty_lower_limit",
    "sell_qty_upper_limit", "market_buy_qty_lower_limit", "market_buy_qty_upper_limit",
    "market_sell_qty_lower_limit", "market_sell_qty_upper_limit",
    "outstanding_share", "public_float_share_quantity",
}


def _securities_info_shapes(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Desensitized shape + invariant summary of one securities-info result."""
    shape: dict[str, Any] = {}
    invariants: dict[str, Any] = {}
    if not isinstance(result, list):
        return {"type": type(result).__name__}, invariants
    shape["type"] = "list"
    shape["length"] = len(result)
    if not result:
        return shape, invariants
    first = result[0]
    if not isinstance(first, dict):
        shape["entry_kind"] = type(first).__name__
        return shape, invariants
    shape["entry_kind"] = "object"
    keys = sorted(first)
    shape["column_count"] = len(keys)
    shape["columns_sorted"] = keys
    shape["value_types"] = {key: type(first[key]).__name__ for key in keys}
    shape["all_key_sets_identical"] = all(
        isinstance(row, dict) and sorted(row) == keys for row in result
    )
    markets = {row.get("market_type") for row in result if isinstance(row, dict)}
    varieties = {
        row.get("variety_category") for row in result if isinstance(row, dict)
    }
    date_lengths = sorted({
        len(str(row[key]))
        for row in result if isinstance(row, dict)
        for key in ("list_day", "expire_date")
        if key in row and isinstance(row[key], (int,))
    })
    non_empty_fraction = {
        key: round(
            sum(
                1 for row in result
                if isinstance(row, dict) and row.get(key) not in (None, "", 0)
            ) / len(result),
            2,
        )
        for key in ("underlying_security_id", "contract_type", "product_code",
                    "regular_share", "english_name")
    }
    invariants = {
        "distinct_market_type": sorted(markets),
        "distinct_variety_category": sorted(varieties),
        "date_digit_lengths": date_lengths,
        "non_empty_fraction": non_empty_fraction,
        "string_len_histogram_currency": dict(sorted(collections.Counter(
            len(row.get("currency", ""))
            for row in result if isinstance(row, dict) and isinstance(row.get("currency"), str)
        ).items())),
    }
    return shape, invariants


class _CodeTableBatchCollector:
    """Async user SPI for the code table: per-batch counters and column metadata."""

    def __init__(self, wait_seconds: float, quiet_seconds: float = 5.0) -> None:
        self.wait_seconds = wait_seconds
        self.quiet_seconds = quiet_seconds
        self.submitted_at: float | None = None
        self.first_call_delay: float | None = None
        self.batch_sizes: list[int] = []
        self.status_errors: list[Any] = []
        self.columns: list[str] | None = None
        self.column_types: dict[str, str] | None = None
        self._rows: list[dict[str, Any]] = []
        self._last_call_at: float | None = None

    def __call__(self, result: Any, err_code: Any) -> None:
        self._last_call_at = time.monotonic()
        if self.submitted_at is not None and self.first_call_delay is None:
            self.first_call_delay = round(self._last_call_at - self.submitted_at, 3)
        if err_code is not None and result is None:
            self.status_errors.append(
                err_code if isinstance(err_code, int) else str(type(err_code).__name__)
            )
            return
        if isinstance(result, list):
            self.batch_sizes.append(len(result))
            for row in result:
                if isinstance(row, dict):
                    if self.columns is None:
                        self.columns = sorted(row)
                        self.column_types = {
                            key: type(row[key]).__name__ for key in sorted(row)
                        }
                    self._rows.append({
                        "market": row.get("market_type"),
                        "stype": row.get("security_type"),
                        "currency": row.get("currency"),
                        "code": row.get("security_code"),
                        "symbol_empty": row.get("symbol") == "",
                        "en_empty": row.get("english_name") == "",
                    })

    def summary(self) -> dict[str, Any]:
        deadline = time.monotonic() + self.wait_seconds
        while time.monotonic() < deadline:
            if (
                self._last_call_at is not None
                and time.monotonic() - self._last_call_at > self.quiet_seconds
            ):
                break
            time.sleep(0.2)
        codes = [row["code"] for row in self._rows]
        return {
            "batch_count": len(self.batch_sizes),
            "batch_sizes_head": self.batch_sizes[:20],
            "batch_sizes_tail": self.batch_sizes[-5:],
            "total_rows": len(self._rows),
            "columns": self.columns,
            "column_types": self.column_types,
            "status_errors": self.status_errors,
            "first_call_delay_sec": self.first_call_delay,
            "invariants": {
                "distinct_market_types": sorted(
                    {row["market"] for row in self._rows if isinstance(row["market"], int)}
                ),
                "distinct_security_types": sorted(
                    {row["stype"] for row in self._rows if isinstance(row["stype"], str)}
                ),
                "distinct_currencies": sorted(
                    {row["currency"] for row in self._rows if isinstance(row["currency"], str)}
                ),
                "code_length_histogram": dict(
                    sorted(collections.Counter(
                        len(code) for code in codes if isinstance(code, str)
                    ).items())
                ),
                "duplicate_code_rows": len(codes) - len(set(codes)),
                "empty_symbol_rows": sum(1 for row in self._rows if row["symbol_empty"]),
                "empty_english_name_rows": sum(1 for row in self._rows if row["en_empty"]),
            },
        }


def _code_table_sync_summary(result: Any) -> dict[str, Any]:
    """Desynchronized sync-wrapper summary: row count and column names only."""
    if not isinstance(result, list):
        return {"type": type(result).__name__}
    first = result[0] if result else None
    return {
        "type": "list",
        "rows": len(result),
        "columns": sorted(first) if isinstance(first, dict) else None,
    }


def _ex_factor_sync_summary(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Desensitized shape + invariant summary of one sync ex-factor result."""
    shape: dict[str, Any] = {}
    invariants: dict[str, Any] = {}
    if not isinstance(result, list):
        return {"type": type(result).__name__}, invariants
    shape["type"] = "list"
    shape["length"] = len(result)
    if not result:
        return shape, invariants
    first = result[0]
    if not isinstance(first, dict):
        shape["entry_kind"] = type(first).__name__
        return shape, invariants
    shape["entry_kind"] = "object"
    keys = sorted(first)
    shape["column_count"] = len(keys)
    shape["columns_sorted"] = keys
    shape["value_types"] = {key: type(first[key]).__name__ for key in keys}
    shape["all_key_sets_identical"] = all(
        isinstance(row, dict) and sorted(row) == keys for row in result
    )
    row_invariants = [_ex_factor_row_invariants(row) for row in result]
    cum_factors = [
        row.get("cum_factor")
        for row in result
        if isinstance(row.get("cum_factor"), float)
    ]
    invariants = _merge_ex_factor_invariants(row_invariants, cum_factors)
    return shape, invariants


def safe_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): safe_shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return {"type": "list", "length": len(value), "item": safe_shape(value[0]) if value else None}
    # pandas is optional at this boundary, but the official wrapper can return it.
    if hasattr(value, "columns") and hasattr(value, "shape"):
        return {
            "type": type(value).__name__,
            "rows": int(value.shape[0]),
            "columns": [str(column) for column in value.columns],
        }
    return {"type": type(value).__name__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("/opt/galaxy-relay/config/config.yaml"))
    parser.add_argument("--env-file", type=Path, default=Path("/etc/galaxy-relay/relay.env"))
    parser.add_argument(
        "--kind",
        choices=("calendar", "kline", "month_kline", "snapshot", "etf-info",
                 "code-table", "securities-info", "ex-factor"),
        default="calendar",
    )
    parser.add_argument("--security-code", default="159518")
    parser.add_argument("--market-type", type=int, default=102)
    parser.add_argument("--date", type=int, default=20260825)
    # K-line window controls; the defaults keep the historical daily sample.
    parser.add_argument("--cyc-type", type=int, default=10008)
    parser.add_argument("--begin-date", type=int, default=20260825)
    parser.add_argument("--end-date", type=int, default=20260825)
    parser.add_argument("--begin-time", type=int, default=93000000)
    parser.add_argument("--end-time", type=int, default=93030000)
    # ETF-info controls; the async collector re-issues the same minimal
    # request once (after a cooldown) to count per-batch deliveries.
    parser.add_argument("--etf-collector-wait", type=float, default=30.0)
    parser.add_argument("--etf-cooldown", type=float, default=5.0)
    # Snapshot async-SPI controls: one collector run instead of the sync call.
    parser.add_argument("--snapshot-async", action="store_true")
    parser.add_argument("--collector-wait", type=float, default=20.0)
    # Code-table controls: sync probe first, then one async full-batch run.
    parser.add_argument("--code-table-cooldown", type=float, default=5.0)
    parser.add_argument("--code-table-collector-wait", type=float, default=60.0)
    # Securities-info controls: sync probe first, then one async full-batch run.
    parser.add_argument("--securities-info-cooldown", type=float, default=5.0)
    parser.add_argument("--securities-info-collector-wait", type=float, default=30.0)
    # Ex-factor controls: sync probe first, then one async full-batch run.
    parser.add_argument("--ex-factor-cooldown", type=float, default=5.0)
    parser.add_argument("--ex-factor-collector-wait", type=float, default=30.0)
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    env = load_env_file(args.env_file)
    upstream = config["amazingdata"]
    host_cfg = upstream["hosts"][0]
    username = env[upstream["username_env"]]
    password = env[upstream["password_env"]]

    import tgw

    cfg = tgw.Cfg()
    cfg.username = username
    cfg.password = password
    cfg.server_vip = host_cfg["host"]
    cfg.server_port = int(host_cfg["port"])
    cfg.force_logout = False
    logged_in = bool(tgw.Login(cfg, tgw.ApiMode.kInternetMode))
    if not logged_in:
        print(json.dumps({"login": False, "query": "not_run"}, sort_keys=True))
        return 2

    try:
        set_codes: dict[str, int] = {}
        snapshot_async_summary: dict[str, Any] = {}
        if args.kind == "calendar":
            task_id = tgw.GetTaskID()
            params = {
                "function_id": "A010061003",
                "start_date": "20260801",
                "end_date": "20260826",
                "market": "SSE",
            }
            set_codes = {
                key: int(tgw.SetThirdInfoParam(task_id, key, value))
                for key, value in params.items()
            }
            result, error = tgw.QueryThirdInfo(task_id, return_df_format=False)
            req_defaults = {}
        elif args.kind in ("kline", "month_kline"):
            request = tgw.ReqKline()
            request.security_code = str(args.security_code)
            request.market_type = int(args.market_type)
            request.cq_flag = 0
            request.cq_date = 0
            request.qj_flag = 0
            request.cyc_type = int(args.cyc_type)
            request.cyc_def = 0
            request.auto_complete = 1
            request.begin_date = int(args.begin_date)
            request.end_date = int(args.end_date)
            request.begin_time = int(args.begin_time)
            request.end_time = int(args.end_time)
            result, error = tgw.QueryKline(request, return_df_format=False)
            req_defaults = {}
        elif args.kind == "snapshot":
            request = tgw.ReqDefault()
            request.security_code = args.security_code
            request.market_type = args.market_type
            request.date = args.date
            request.begin_time = args.begin_time
            request.end_time = args.end_time
            # data_type/level_type intentionally keep the official constructor
            # defaults (0/0); only their presence is reported.
            req_defaults = {
                "data_type": int(request.data_type),
                "level_type": int(request.level_type),
            }
            if args.snapshot_async:
                collector = _SnapshotAsyncCollector(args.collector_wait)
                collector.submitted_at = time.monotonic()
                submit_result, submit_err = tgw.QuerySnapshot(
                    request, query_spi=collector, return_df_format=False
                )
                collector.submit_return = {
                    "result": (
                        submit_result if isinstance(submit_result, bool)
                        else type(submit_result).__name__
                    ),
                    "err": int(submit_err) if isinstance(submit_err, int) else None,
                }
                snapshot_async_summary = collector.summary()
                del collector
                result = None
                error = 0
            else:
                result, error = tgw.QuerySnapshot(request, return_df_format=False)
        code_table_sync: dict[str, Any] = {}
        code_table_async: dict[str, Any] = {}
        if args.kind == "code-table":
            # Minimal sample discipline: one synchronous probe records the
            # wrapper's own container (known first-batch race), then one
            # cooled-down asynchronous collector accumulates every batch for
            # authoritative counts. Non-zero sync error stops immediately.
            result, error = tgw.QueryCodeTable(return_df_format=False)
            req_defaults = {}
            code_table_sync = _code_table_sync_summary(result)
            if isinstance(error, int) and error != 0:
                code_table_async["skipped"] = "sync probe returned non-zero"
            else:
                time.sleep(max(0.0, args.code_table_cooldown))
                collector = _CodeTableBatchCollector(args.code_table_collector_wait)
                collector.submitted_at = time.monotonic()
                issued, issue_error = tgw.QueryCodeTable(
                    query_spi=collector, return_df_format=False
                )
                code_table_async["submit_return"] = {
                    "result": (
                        issued if isinstance(issued, bool) else type(issued).__name__
                    ),
                    "err": int(issue_error) if isinstance(issue_error, int) else None,
                }
                code_table_async.update(collector.summary())
                del collector
        etf_summary: dict[str, Any] = {}
        secinfo_async: dict[str, Any] = {}
        exfactor_async: dict[str, Any] = {}
        if args.kind == "securities-info":
            item = tgw.SubCodeTableItem()
            item.market = int(args.market_type)
            item.security_code = str(args.security_code)
            result, error = tgw.QuerySecuritiesInfo(item, return_df_format=False)
            req_defaults = {}
            time.sleep(max(0.0, args.securities_info_cooldown))
            collector = _SecuritiesInfoAsyncCollector(args.securities_info_collector_wait)
            collector.submitted_at = time.monotonic()
            issued, issue_error = tgw.QuerySecuritiesInfo(
                item, query_spi=collector, return_df_format=False
            )
            secinfo_async["submit_return"] = {
                "result": (
                    issued if isinstance(issued, bool) else type(issued).__name__
                ),
                "err": int(issue_error) if isinstance(issue_error, int) else None,
            }
            secinfo_async.update(collector.summary())
            del collector
        if args.kind == "ex-factor":
            result, error = tgw.QueryExFactorTable(
                str(args.security_code), return_df_format=False
            )
            req_defaults = {}
            time.sleep(max(0.0, args.ex_factor_cooldown))
            collector = _ExFactorAsyncCollector(args.ex_factor_collector_wait)
            collector.submitted_at = time.monotonic()
            issued, issue_error = tgw.QueryExFactorTable(
                str(args.security_code), query_spi=collector, return_df_format=False
            )
            exfactor_async["submit_return"] = {
                "result": (
                    issued if isinstance(issued, bool) else type(issued).__name__
                ),
                "err": int(issue_error) if isinstance(issue_error, int) else None,
            }
            exfactor_async.update(collector.summary())
            del collector
        if args.kind == "etf-info":
            item = tgw.SubCodeTableItem()
            item.market = int(args.market_type)
            item.security_code = str(args.security_code)
            result, error = tgw.QueryETFInfo(item, return_df_format=False)
            req_defaults = {}
            time.sleep(max(0.0, args.etf_cooldown))
            collector = _EtfBatchCollector(args.etf_collector_wait)
            issued, issue_error = tgw.QueryETFInfo(item, query_spi=collector)
            collector.immediate_error = (
                int(issue_error) if isinstance(issue_error, int) else None
            )
            if issued:
                # First delivery or timeout, plus a short grace window so any
                # additional batches are still counted.
                collector._done.wait(args.etf_collector_wait)
                time.sleep(2.0)
            etf_summary = {
                "async_immediate_return": collector.immediate_error,
                "async_data_batch_count": len(collector.data_batches),
                "async_batches": collector.data_batches,
                "async_status_errors": [
                    value if isinstance(value, int) else str(value)
                    for value in collector.status_errors
                ],
            }
            del collector
        invariants: dict[str, Any] = {}
        if args.kind in ("kline", "month_kline") and isinstance(result, list) and result:
            row = result[0]
            if isinstance(row, dict):
                invariants = {
                    "orig_time_equals_kline_time": row.get("orig_time") == row.get("kline_time"),
                    "orig_time_is_zero": row.get("orig_time") == 0,
                    "variety_category_is_zero": row.get("variety_category") == 0,
                    "kline_time_digit_lengths": sorted(
                        {len(str(item["kline_time"])) for item in result
                         if isinstance(item, dict)},
                    ),
                    "distinct_market_type": sorted(
                        {item["market_type"] for item in result if isinstance(item, dict)},
                    ),
                }
        if args.kind == "snapshot" and isinstance(result, list) and result:
            row = result[0]
            if isinstance(row, dict):
                invariants = {
                    "first_row_keys_sorted": sorted(row.keys()) == list(row.keys()),
                    "all_values_are_scalars": all(
                        isinstance(value, (int, float, str)) for value in row.values()
                    ),
                    "has_code_field": "code" in row or "security_code" in row,
                    "row_key_count_identical": all(
                        len(item.keys()) == len(row.keys())
                        for item in result
                        if isinstance(item, dict)
                    ),
                    # Classification enums are protocol metadata, not business
                    # values; they pin down wrapper-side derivations.
                    "distinct_variety_category": sorted(
                        {item["variety_category"] for item in result if isinstance(item, dict)}
                    ),
                    "distinct_market_type": sorted(
                        {item["market_type"] for item in result if isinstance(item, dict)}
                    ),
                    "distinct_trading_phase_code": sorted(
                        {item.get("trading_phase_code") for item in result if isinstance(item, dict)}
                    ),
                    "orig_time_digit_lengths": sorted(
                        {len(str(item["orig_time"])) for item in result if isinstance(item, dict)}
                    ),
                }
        etf_shapes: dict[str, Any] = {}
        exfactor_shapes: dict[str, Any] = {}
        if args.kind == "etf-info":
            etf_shapes, etf_invariants = _etf_info_shapes(result)
            invariants = etf_invariants
        if args.kind == "securities-info":
            etf_shapes, secinfo_invariants = _securities_info_shapes(result)
            invariants = secinfo_invariants
        if args.kind == "ex-factor":
            exfactor_shapes, exfactor_invariants = _ex_factor_sync_summary(result)
            invariants = exfactor_invariants
        if args.kind == "code-table":
            # Never route code-table rows through safe_shape(): the generic
            # recursion would print first-row business values. The dedicated
            # sync summary carries row/column metadata only.
            result_shape = code_table_sync
        elif args.kind == "securities-info":
            # Dedicated shape already strips business values; do not recurse.
            result_shape = etf_shapes
        elif args.kind == "ex-factor":
            # Dedicated shape already strips business values; do not recurse.
            result_shape = exfactor_shapes
        else:
            result_shape = safe_shape(result) if args.kind != "etf-info" else etf_shapes
        print(json.dumps({
            "login": True,
            "query_kind": args.kind,
            "set_param_statuses": sorted(set(set_codes.values())),
            "query_error_type": type(error).__name__,
            "query_error": int(error) if isinstance(error, int) else None,
            "result_shape": result_shape,
            "result_invariants": invariants,
            "req_default_fields": req_defaults,
            "etf_async": etf_summary,
            "snapshot_async": snapshot_async_summary,
            "code_table_sync": code_table_sync,
            "code_table_async": code_table_async,
            "securities_info_async": secinfo_async,
            "ex_factor_async": exfactor_async,
        }, sort_keys=True))
    finally:
        tgw.Close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

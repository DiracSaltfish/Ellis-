#!/usr/bin/env python3
"""Migrate Private chart history from API snapshots into the compact minute table.

The live API is intentionally used as the migration source so model-specific
historical corrections are preserved. Without --apply the script is read-only
and prints a deterministic digest that can be compared before and after a
deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen


SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,32}$")
DAY_PATTERN = re.compile(r"^\d{8}$")
ISO_DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS private_valuation_minute_history (
  symbol VARCHAR(32) NOT NULL,
  snapshot_minute DATETIME NOT NULL,
  trading_day DATE NOT NULL,
  market_price DECIMAL(18, 6) NOT NULL,
  basket_bid_nav DECIMAL(18, 10) NOT NULL,
  basket_ask_nav DECIMAL(18, 10) NOT NULL,
  buy_direction_premium_rate DECIMAL(16, 10) NOT NULL,
  sell_direction_premium_rate DECIMAL(16, 10) NOT NULL,
  pcf_trading_day DATE NULL,
  xop_equivalent_shares DECIMAL(24, 6) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (symbol, snapshot_minute),
  KEY idx_private_minute_history_day (trading_day, symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def finite_number(value: Any, field: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    return finite_number(value, field)


def sql_number(value: float | None) -> str:
    if value is None:
        return "NULL"
    return format(value, ".12g")


def sql_day(value: str | None) -> str:
    if not value:
        return "NULL"
    if not ISO_DAY_PATTERN.fullmatch(value):
        raise ValueError(f"invalid ISO day: {value!r}")
    return f"'{value}'"


def normalize_row(symbol: str, row: dict[str, Any]) -> tuple[Any, ...]:
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError(f"invalid symbol: {symbol!r}")
    observed = datetime.fromisoformat(str(row["minute"]))
    if observed.tzinfo is None:
        raise ValueError(f"minute lacks timezone: {row['minute']!r}")
    local_minute = observed.replace(second=0, microsecond=0)
    utc_minute = local_minute.astimezone(timezone.utc).replace(tzinfo=None)
    pcf_day = str(row.get("pcf_trading_day") or "") or None
    if pcf_day is not None and not ISO_DAY_PATTERN.fullmatch(pcf_day):
        raise ValueError(f"invalid PCF day: {pcf_day!r}")
    return (
        symbol,
        utc_minute.strftime("%Y-%m-%d %H:%M:%S"),
        local_minute.date().isoformat(),
        finite_number(row["market_price"], "market_price"),
        finite_number(row["basket_bid_nav"], "basket_bid_nav"),
        finite_number(row["basket_ask_nav"], "basket_ask_nav"),
        finite_number(row["buy_direction_premium_rate"], "buy_direction_premium_rate"),
        finite_number(row["sell_direction_premium_rate"], "sell_direction_premium_rate"),
        pcf_day,
        optional_number(row.get("xop_equivalent_shares"), "xop_equivalent_shares"),
    )


def row_sql(row: tuple[Any, ...]) -> str:
    symbol, minute, trading_day, market, nav_bid, nav_ask, buy, sell, pcf_day, shares = row
    return (
        f"('{symbol}','{minute}','{trading_day}',"
        f"{sql_number(market)},{sql_number(nav_bid)},{sql_number(nav_ask)},"
        f"{sql_number(buy)},{sql_number(sell)},{sql_day(pcf_day)},{sql_number(shares)})"
    )


def mysql(mysql_bin: str, database: str, sql: str) -> None:
    subprocess.run(
        [mysql_bin, "--database", database, "--batch", "--skip-column-names"],
        input=sql,
        text=True,
        check=True,
    )


def upsert_batch(mysql_bin: str, database: str, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    values = ",\n".join(row_sql(row) for row in rows)
    mysql(
        mysql_bin,
        database,
        """
INSERT INTO private_valuation_minute_history (
  symbol, snapshot_minute, trading_day, market_price, basket_bid_nav,
  basket_ask_nav, buy_direction_premium_rate, sell_direction_premium_rate,
  pcf_trading_day, xop_equivalent_shares
) VALUES
"""
        + values
        + """
ON DUPLICATE KEY UPDATE
  trading_day = VALUES(trading_day),
  market_price = VALUES(market_price),
  basket_bid_nav = VALUES(basket_bid_nav),
  basket_ask_nav = VALUES(basket_ask_nav),
  buy_direction_premium_rate = VALUES(buy_direction_premium_rate),
  sell_direction_premium_rate = VALUES(sell_direction_premium_rate),
  pcf_trading_day = VALUES(pcf_trading_day),
  xop_equivalent_shares = VALUES(xop_equivalent_shares);
""",
    )


def update_digest(digest: Any, row: tuple[Any, ...]) -> None:
    canonical = list(row)
    for index in range(3, 8):
        canonical[index] = format(canonical[index], ".10f")
    if canonical[9] is not None:
        canonical[9] = format(canonical[9], ".6f")
    digest.update(json.dumps(canonical, ensure_ascii=True, separators=(",", ":")).encode())
    digest.update(b"\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/api/v1/private")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--mysql-bin", default="mysql")
    parser.add_argument("--database", default="newnavnav")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch_size <= 0:
        raise ValueError("batch size must be positive")
    base_url = args.base_url.rstrip("/")
    if args.apply:
        mysql(args.mysql_bin, args.database, CREATE_TABLE_SQL)

    list_payload = fetch_json(f"{base_url}/funds", args.timeout)
    symbols = [str(item["symbol"]).upper() for item in list_payload.get("funds", [])]
    digest = hashlib.sha256()
    total_rows = 0
    batch: list[tuple[Any, ...]] = []

    for symbol in symbols:
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError(f"invalid symbol: {symbol!r}")
        dates_payload = fetch_json(f"{base_url}/funds/{symbol}/minute-history/dates", args.timeout)
        dates = [str(day) for day in dates_payload.get("dates", [])]
        symbol_rows = 0
        for day in sorted(dates):
            if not DAY_PATTERN.fullmatch(day):
                raise ValueError(f"invalid history day: {day!r}")
            payload = fetch_json(
                f"{base_url}/funds/{symbol}/minute-history?date={day}",
                args.timeout,
            )
            normalized = [normalize_row(symbol, item) for item in payload.get("rows", [])]
            normalized.sort(key=lambda item: item[1])
            for row in normalized:
                update_digest(digest, row)
                total_rows += 1
                symbol_rows += 1
                if args.apply:
                    batch.append(row)
                    if len(batch) >= args.batch_size:
                        upsert_batch(args.mysql_bin, args.database, batch)
                        batch.clear()
        print(f"symbol={symbol} rows={symbol_rows}", flush=True)

    if args.apply and batch:
        upsert_batch(args.mysql_bin, args.database, batch)
    print(
        f"mode={'apply' if args.apply else 'verify'} symbols={len(symbols)} "
        f"rows={total_rows} sha256={digest.hexdigest()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"migration failed: {exc}", file=sys.stderr, flush=True)
        raise

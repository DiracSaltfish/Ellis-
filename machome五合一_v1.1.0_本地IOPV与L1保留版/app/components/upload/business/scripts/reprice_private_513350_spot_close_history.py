#!/usr/bin/env python3
"""Replay existing SH513350 private minutes with dated CFETS 16:30 FX closes.

This reads only the private snapshot JSON, changes the FX input, and writes it
back through the protected private-history import API. Public minute history
and the current private input are never changed.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_513350_spot_close as spot_close  # noqa: E402


SYMBOL = "SH513350"
HISTORY_PATH = f"/api/v1/private/funds/{SYMBOL}/minute-history/import"
MYSQL_DSN_PATTERN = re.compile(r"^(?P<user>[^:]+):(?P<password>[^@]+)@tcp\((?P<host>[^:)]+):(?P<port>\d+)\)/(?P<database>[^?]+)")


class RepriceError(RuntimeError):
    """A source snapshot cannot be replayed safely."""


def parse_env_file(path: str | Path) -> dict[str, str]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RepriceError(f"cannot read database environment file: {exc}") from exc
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip() and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def mysql_config(env_file: str | Path) -> tuple[list[str], dict[str, str]]:
    dsn = parse_env_file(env_file).get("MYSQL_DSN", "")
    matched = MYSQL_DSN_PATTERN.match(dsn)
    if not matched:
        raise RepriceError("MYSQL_DSN is missing or unsupported")
    fields = matched.groupdict()
    environment = os.environ.copy()
    environment["MYSQL_PWD"] = fields["password"]
    command = [
        "mysql", "--batch", "--raw", "--skip-column-names", "--default-character-set=utf8mb4",
        "--host", fields["host"], "--port", fields["port"], "--user", fields["user"], fields["database"],
    ]
    return command, environment


def load_snapshots(env_file: str | Path) -> Iterable[dict[str, Any]]:
    command, environment = mysql_config(env_file)
    query = """
SELECT snapshot_json
FROM private_valuation_snapshots
WHERE symbol = 'SH513350'
  AND (
    TIME(snapshot_minute + INTERVAL 8 HOUR) BETWEEN '09:30:00' AND '11:30:00'
    OR TIME(snapshot_minute + INTERVAL 8 HOUR) BETWEEN '13:00:00' AND '15:00:00'
  )
ORDER BY snapshot_minute
"""
    completed = subprocess.run(command + ["--execute", query], env=environment, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RepriceError("cannot read SH513350 private history from MySQL")
    for line in completed.stdout.splitlines():
        try:
            snapshot = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RepriceError("stored private snapshot JSON is malformed") from exc
        if isinstance(snapshot, dict):
            yield snapshot


def parse_minute(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RepriceError("snapshot as_of is invalid") from exc
    if parsed.tzinfo is None:
        raise RepriceError("snapshot as_of must include a timezone")
    return parsed.astimezone(spot_close.SHANGHAI)


def market_price(snapshot: dict[str, Any]) -> float:
    quote = snapshot.get("domestic_quote")
    if not isinstance(quote, dict):
        raise RepriceError("snapshot domestic_quote is missing")
    try:
        value = float(quote["price"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RepriceError("snapshot domestic quote price is invalid") from exc
    if not math.isfinite(value) or value <= 0:
        raise RepriceError("snapshot domestic quote price must be positive")
    return value


def reprice_row(snapshot: dict[str, Any], rates: dict[date, float]) -> dict[str, Any]:
    if snapshot.get("symbol") != SYMBOL:
        raise RepriceError("unexpected symbol in snapshot export")
    minute = parse_minute(snapshot.get("as_of"))
    input_payload = snapshot.get("input")
    if not isinstance(input_payload, dict):
        raise RepriceError("snapshot input is missing")
    input_copy = copy.deepcopy(input_payload)
    pcf = input_copy.get("pcf")
    fx = input_copy.get("fx")
    if not isinstance(pcf, dict) or not isinstance(fx, dict):
        raise RepriceError("snapshot PCF or FX input is missing")
    trading_day = minute.date()
    if pcf.get("trading_day") != trading_day.isoformat() or fx.get("trading_day") != trading_day.isoformat():
        raise RepriceError(f"snapshot dates do not match minute {minute.isoformat()}")
    try:
        rate = rates[trading_day]
    except KeyError as exc:
        raise RepriceError(f"missing 16:30 spot close for {trading_day}") from exc
    fx.update({
        "rate": rate,
        "quote_time": spot_close.SPOT_CLOSE_TIME,
        "source": spot_close.CFETS_USD_CNY_SPOT_CLOSE_SOURCE,
        "fetched_at": spot_close.SpotCloseQuote(rate=rate, trading_day=trading_day).observed_at.isoformat(),
    })
    input_copy["source"] = "server-private-history-fx-reprice"
    return {"minute": minute.isoformat(), "market_price": market_price(snapshot), "input": input_copy}


def batches(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def upload(server: str, token: str, batch: list[dict[str, Any]], timeout: float) -> int:
    request = urllib.request.Request(
        server.rstrip("/") + HISTORY_PATH,
        data=json.dumps({"rows": batch}, separators=(",", ":"), allow_nan=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Upload-Token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise RepriceError("private history import failed") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RepriceError("private history import was not acknowledged")
    return int(payload.get("imported") or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mysql-env-file", required=True)
    parser.add_argument("--server", default="http://127.0.0.1:8080")
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--rates-file", default=str(spot_close.DEFAULT_RATES_PATH))
    parser.add_argument("--batch-size", type=int, default=240)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and not args.token:
        raise SystemExit("NNN_UPLOAD_TOKEN or --token is required unless --dry-run is used")
    if args.batch_size < 1 or args.batch_size > 500:
        raise SystemExit("--batch-size must be between 1 and 500")
    rates = spot_close.load_rates(args.rates_file)
    rows = [reprice_row(snapshot, rates) for snapshot in load_snapshots(args.mysql_env_file)]
    if not rows:
        raise RepriceError("no SH513350 private snapshots were found")
    if args.dry_run:
        print(f"dry-run rows={len(rows)} days={len({row['minute'][:10] for row in rows})}")
        return 0
    imported = sum(upload(args.server, args.token, batch, args.timeout) for batch in batches(rows, args.batch_size))
    print(f"repriced {SYMBOL} rows={len(rows)} imported={imported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

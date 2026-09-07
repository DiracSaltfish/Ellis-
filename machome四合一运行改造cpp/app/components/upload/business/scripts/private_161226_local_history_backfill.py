#!/usr/bin/env python3
"""Backfill SZ161226 private minute history from local QMT/ETF files.

The AG minute file is the limiting futures source.  When the futures market
has no bar during a scheduled break, the last available futures price and
cumulative VWAP are frozen until the next AG bar.  This keeps the fund's
minute grid complete without inventing intraday futures movement.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_161226_silver_uploader as silver  # noqa: E402


SYMBOL = silver.SYMBOL
BACKFILL_SOURCE = "local-qmt-ag-shfe-official-161226-backfill"
AG_SOURCE = "QMT_AG_1M"
SHFE_DAILY_URL = "https://www.shfe.cn/data/tradedata/future/dailydata/kx{day}.dat"
EASTMONEY_NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz?fundCode=161226&pageIndex={page}&pageSize=40"
AG_CONTRACT_MULTIPLIER = 15.0
SHANGHAI = silver.SHANGHAI


class BackfillError(RuntimeError):
    """A local source or cross-source validation check failed."""


def positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}; use YYYY-MM-DD") from exc


def parse_fund_minute(value: str) -> dt.datetime:
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise BackfillError(f"unsupported fund minute timestamp {value!r}")


def read_ag_bars(root: Path) -> dict[str, dict[dt.datetime, dict[str, float]]]:
    result: dict[str, dict[dt.datetime, dict[str, float]]] = defaultdict(dict)
    files = sorted(root.glob("ag*_1m.csv"))
    if not files:
        raise BackfillError(f"no ag*_1m.csv files found in {root}")
    for path in files:
        contract = path.stem.split("_")[0].upper()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                raw_minute = str(row.get("datetime") or "")
                try:
                    minute = dt.datetime.strptime(raw_minute, "%Y%m%d%H%M%S")
                except ValueError:
                    continue
                close = positive(row.get("close"))
                if close is None:
                    continue
                result[contract][minute] = {
                    "close": close,
                    "volume": positive(row.get("volume")) or 0.0,
                    "amount": positive(row.get("amount")) or 0.0,
                }
    return result


def read_fund_days(root: Path) -> dict[dt.date, dict[dt.datetime, float]]:
    result: dict[dt.date, dict[dt.datetime, float]] = {}
    files = sorted(root.glob("2026-*/*_1min.zip"))
    if not files:
        raise BackfillError(f"no *_1min.zip files found in {root}")
    for path in files:
        try:
            day = dt.datetime.strptime(path.stem[:8], "%Y%m%d").date()
        except ValueError:
            continue
        with zipfile.ZipFile(path) as archive:
            name = next((item for item in archive.namelist() if item.endswith("161226.SZ.csv")), None)
            if not name:
                continue
            text = archive.read(name).decode("utf-8-sig")
        rows: dict[dt.datetime, float] = {}
        for row in csv.DictReader(text.splitlines()):
            minute = parse_fund_minute(str(row.get("时间") or ""))
            if minute.date() != day:
                raise BackfillError(f"fund file {path} contains {minute.date()} row under {day}")
            market_price = positive(row.get("收盘价"))
            if market_price is None:
                raise BackfillError(f"fund file {path} has invalid close at {minute}")
            rows[minute] = market_price
        if rows:
            result[day] = rows
    return result


def session_bars(
    bars: dict[dt.datetime, dict[str, float]], trading_day: dt.date
) -> list[tuple[dt.datetime, dict[str, float]]]:
    previous_day = trading_day - dt.timedelta(days=1)
    selected = []
    for minute, row in bars.items():
        if minute.date() == previous_day and minute.time() >= dt.time(21, 0):
            selected.append((minute, row))
        elif minute.date() == trading_day and (
            minute.time() <= dt.time(2, 30)
            or dt.time(9, 0) <= minute.time() <= dt.time(11, 30)
            or dt.time(13, 30) <= minute.time() <= dt.time(15, 0)
        ):
            selected.append((minute, row))
    selected.sort(key=lambda item: item[0])
    if not selected:
        raise BackfillError(f"no AG session bars for {trading_day}")
    return selected


def ag_values_by_minute(
    bars: dict[dt.datetime, dict[str, float]], trading_day: dt.date
) -> dict[dt.datetime, tuple[float, float]]:
    cumulative_volume = 0.0
    cumulative_amount = 0.0
    result: dict[dt.datetime, tuple[float, float]] = {}
    for minute, row in session_bars(bars, trading_day):
        volume = row["volume"]
        amount = row["amount"]
        if volume > 0 and amount > 0:
            cumulative_volume += volume
            cumulative_amount += amount
        if cumulative_volume <= 0 or cumulative_amount <= 0:
            continue
        average = cumulative_amount / (cumulative_volume * AG_CONTRACT_MULTIPLIER)
        result[minute] = (row["close"], average)
    return result


def fetch_shfe_settlement(day: dt.date, contract: str, timeout: float) -> float:
    payload = silver.request_json(
        SHFE_DAILY_URL.format(day=day.strftime("%Y%m%d")),
        timeout,
        {"User-Agent": "Mozilla/5.0"},
    )
    for row in payload.get("o_curinstrument", []) if isinstance(payload, dict) else []:
        if row.get("PRODUCTID") != "ag_f" or str(row.get("DELIVERYMONTH")) != contract[2:]:
            continue
        value = positive(row.get("SETTLEMENTPRICE"))
        if value is not None:
            return value
    raise BackfillError(f"SHFE official settlement missing for {contract} on {day}")


def fetch_navs_for_range(timeout: float, earliest_trading_day: dt.date) -> list[tuple[dt.date, float]]:
    """Fetch enough Eastmoney pages to find the prior published NAV."""
    result: list[tuple[dt.date, float]] = []
    for page in range(1, 8):
        payload = silver.request_json(
            EASTMONEY_NAV_URL.format(page=page),
            timeout,
            {"Referer": "https://fundf10.eastmoney.com/jjjz_161226.html", "User-Agent": "Mozilla/5.0"},
        )
        rows = payload.get("Data", {}).get("LSJZList", []) if isinstance(payload, dict) else []
        if not rows:
            break
        oldest_on_page: dt.date | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                nav_day = dt.datetime.strptime(str(row.get("FSRQ") or ""), "%Y-%m-%d").date()
            except ValueError:
                continue
            nav = positive(row.get("DWJZ"))
            if nav is None:
                continue
            result.append((nav_day, nav))
            oldest_on_page = nav_day if oldest_on_page is None else min(oldest_on_page, nav_day)
        if oldest_on_page is not None and oldest_on_page < earliest_trading_day:
            break
    result.sort(reverse=True)
    if not result:
        raise BackfillError("Eastmoney returned no positive SZ161226 NAV")
    return result


def history_rows(
    *,
    trading_day: dt.date,
    fund_minutes: dict[dt.datetime, float],
    ag_minutes: dict[dt.datetime, tuple[float, float]],
    base_nav_day: dt.date,
    base_nav: float,
    previous_settlement: float,
) -> list[dict[str, Any]]:
    last_futures: tuple[float, float] | None = None
    rows: list[dict[str, Any]] = []
    for minute in sorted(fund_minutes):
        if minute not in ag_minutes:
            if last_futures is None:
                raise BackfillError(f"no prior AG value to freeze at {minute}")
        else:
            last_futures = ag_minutes[minute]
        futures_price, intraday_average = last_futures
        observed_at = minute.replace(tzinfo=SHANGHAI)
        value = silver.build_input(
            trading_day=trading_day,
            observed_at=observed_at,
            contract=silver.ag_contract(trading_day),
            base_nav_day=base_nav_day,
            base_nav=base_nav,
            previous_settlement=previous_settlement,
            futures_price=futures_price,
            intraday_average=intraday_average,
            average_basis=silver.MINLINE_AVERAGE_BASIS,
            source=BACKFILL_SOURCE,
            generated_at=observed_at,
        )
        value["silver"]["source"] = AG_SOURCE
        value["source"] = BACKFILL_SOURCE
        rows.append({
            "minute": silver.common.iso_timestamp(observed_at),
            "market_price": fund_minutes[minute],
            "input": value,
        })
    return rows


def post_day(args: argparse.Namespace, rows: list[dict[str, Any]]) -> int:
    return silver.post_history(args, rows)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--ag-dir", type=Path, default=Path("/Users/ellis/.anydesk/incoming/2026-08-20 20:52:57.764/QMT_AG_1M"))
    result.add_argument("--fund-dir", type=Path, default=Path("/Volumes/Upan/基金2026.7-8"))
    result.add_argument("--start-date", type=parse_date, default=dt.date(2026, 7, 1))
    result.add_argument("--end-date", type=parse_date)
    result.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument("--origin-tls-insecure", action="store_true", default=silver.common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    result.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", "40")))
    result.add_argument("--dry-run", action="store_true")
    return result


def main() -> int:
    silver.common.load_env_file(".sina-uploader.env")
    args = parser().parse_args()
    if not args.token:
        args.token = os.getenv("NNN_UPLOAD_TOKEN", "")
    if not args.token and not args.dry_run:
        raise BackfillError("NNN_UPLOAD_TOKEN or --token is required")

    ag = read_ag_bars(args.ag_dir)
    fund = read_fund_days(args.fund_dir)
    available_days = sorted(day for day in fund if day >= args.start_date and (args.end_date is None or day <= args.end_date))
    if not available_days:
        raise BackfillError("no fund minute days match the requested date range")
    if args.end_date is None:
        args.end_date = available_days[-1]
    navs = fetch_navs_for_range(args.timeout, available_days[0])
    settlements: dict[tuple[dt.date, str], float] = {}
    total = 0
    for trading_day in available_days:
        contract = silver.ag_contract(trading_day)
        contract_bars = ag.get(contract)
        if not contract_bars:
            raise BackfillError(f"local AG minute file missing for {contract}")
        base_nav_day, base_nav = silver.base_nav_for_day(navs, trading_day)
        settlement_key = (base_nav_day, contract)
        if settlement_key not in settlements:
            settlements[settlement_key] = fetch_shfe_settlement(base_nav_day, contract, args.timeout)
        rows = history_rows(
            trading_day=trading_day,
            fund_minutes=fund[trading_day],
            ag_minutes=ag_values_by_minute(contract_bars, trading_day),
            base_nav_day=base_nav_day,
            base_nav=base_nav,
            previous_settlement=settlements[settlement_key],
        )
        if args.dry_run:
            print(f"dry-run day={trading_day} contract={contract} rows={len(rows)} base={base_nav_day} settlement={settlements[settlement_key]}", flush=True)
        else:
            imported = post_day(args, rows)
            total += imported
            print(f"backfilled day={trading_day} contract={contract} rows={imported} base={base_nav_day} settlement={settlements[settlement_key]}", flush=True)
    print(f"complete days={len(available_days)} rows={total if not args.dry_run else sum(len(fund[day]) for day in available_days)} range={available_days[0]}..{available_days[-1]}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BackfillError, silver.SourceUnavailableError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        raise SystemExit(1)

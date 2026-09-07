#!/usr/bin/env python3
import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sina_quote_uploader import load_env_file, neutralize_source_name, neutralize_upload_source

try:
    from ib_insync import ContFuture, Future, IB, Stock
except ImportError as exc:
    raise SystemExit("ib_insync is required: python3 -m pip install --user ib_insync ibapi") from exc


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_STORE_ROOT = SCRIPT_DIR / "quote_store"
SESSION_TZ = ZoneInfo("Asia/Shanghai")
SESSION_START = dtime(9, 30)
SESSION_END = dtime(15, 0)
FIELDS = [
    "trade_date",
    "symbol",
    "minute",
    "timestamp",
    "last_price",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "source",
]


def main() -> int:
    load_env_file(".sina-uploader.env")
    parser = argparse.ArgumentParser(description="Backfill intraday external 1m bars from IBKR/TWS into web minute valuation history.")
    parser.add_argument("--host", default=os.getenv("IBKR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IBKR_PORT", "7496")))
    parser.add_argument("--client-id", type=int, default=int(os.getenv("IBKR_CLIENT_ID", "22652")))
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--source", default=os.getenv("NNN_UPLOAD_SOURCE", "home-mac-1m"))
    parser.add_argument("--store-root", default=os.getenv("NNN_QUOTE_STORE_DIR", str(DEFAULT_STORE_ROOT)))
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--start-date", default="", help="Inclusive start date for multi-day backfill.")
    parser.add_argument("--end-date", default="", help="Inclusive end date for multi-day backfill.")
    parser.add_argument("--include-weekends", action="store_true")
    parser.add_argument("--markets", default="futures", help="Comma-separated: futures,hk,us")
    parser.add_argument("--symbols", default="", help="Comma-separated server symbols. Empty means required symbols filtered by --markets.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--request-delay", type=float, default=0.35)
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--upload-chunk-size", type=int, default=20000)
    parser.add_argument("--flush-symbols", type=int, default=25, help="Flush multi-day range rows after this many symbols.")
    parser.add_argument("--per-day-requests", action="store_true", help="Use one IBKR request per day instead of one range request per symbol.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    if not args.token.strip() and not args.no_upload:
        print("NNN_UPLOAD_TOKEN or --token is required unless --no-upload is set", file=sys.stderr)
        return 2

    trade_days = parse_trade_days(args)
    markets = {item.strip().lower() for item in args.markets.split(",") if item.strip()}
    symbols = split_symbols(args.symbols)
    if not symbols:
        symbols = fetch_required_symbols(args.server, args.token, args.timeout)
        symbols = [symbol for symbol in symbols if symbol_market(symbol) in markets]
    symbols = dedupe(symbols)
    if args.limit > 0:
        symbols = symbols[: args.limit]
    if not symbols:
        print("no symbols to backfill")
        return 0

    print(
        f"{now_iso()} ibkr minute backfill dates={trade_days[0].isoformat()}..{trade_days[-1].isoformat()} "
        f"days={len(trade_days)} markets={','.join(sorted(markets))} symbols={len(symbols)}",
        flush=True,
    )
    if args.dry_run:
        print(json.dumps(symbols, ensure_ascii=False, indent=2))
        return 0

    ib = IB()
    ib.connect(args.host, args.port, clientId=args.client_id, timeout=args.timeout)
    total_rows = 0
    total_accepted = 0
    failures: list[str] = []
    try:
        ib.reqMarketDataType(1)
        if len(trade_days) > 1 and not args.per_day_requests:
            total_rows, total_accepted = backfill_range_requests(ib, args, trade_days, symbols, failures)
        else:
            total_rows, total_accepted = backfill_day_requests(ib, args, trade_days, symbols, failures)
    finally:
        ib.disconnect()

    print(
        f"{now_iso()} ibkr minute backfill finished symbols={len(symbols)} "
        f"rows={total_rows} accepted={total_accepted} failures={len(failures)}",
        flush=True,
    )
    if failures:
        print("first failures:", json.dumps(failures[:20], ensure_ascii=False), file=sys.stderr, flush=True)
    return 0


def backfill_day_requests(ib: IB, args, trade_days: list[date], symbols: list[str], failures: list[str]) -> tuple[int, int]:
    total_rows = 0
    total_accepted = 0
    for day_index, trade_day in enumerate(trade_days, 1):
        day_rows: list[dict] = []
        print(f"{now_iso()} backfill day [{day_index}/{len(trade_days)}] {trade_day.isoformat()}", flush=True)
        for index, symbol in enumerate(symbols, 1):
            try:
                rows = fetch_symbol_rows(ib, symbol, trade_day)
                if rows:
                    day_rows.extend(rows)
                    print(
                        f"{now_iso()} [{day_index}/{len(trade_days)} {index}/{len(symbols)}] "
                        f"{trade_day.isoformat()} {symbol} rows={len(rows)}",
                        flush=True,
                    )
                else:
                    failures.append(f"{trade_day.isoformat()} {symbol}: no rows")
                    print(
                        f"{now_iso()} [{day_index}/{len(trade_days)} {index}/{len(symbols)}] "
                        f"{trade_day.isoformat()} {symbol} no rows",
                        file=sys.stderr,
                        flush=True,
                    )
            except Exception as exc:
                failures.append(f"{trade_day.isoformat()} {symbol}: {exc}")
                print(
                    f"{now_iso()} [{day_index}/{len(trade_days)} {index}/{len(symbols)}] "
                    f"{trade_day.isoformat()} {symbol} failed: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
            ib.sleep(args.request_delay)
        write_rows(args.store_root, trade_day, day_rows)
        total_rows += len(day_rows)
        if day_rows and not args.no_upload:
            total_accepted += upload_reference_rows(
                args.server,
                args.token,
                neutralize_upload_source(args.source),
                day_rows,
                args.timeout,
                args.upload_chunk_size,
            )
    return total_rows, total_accepted


def backfill_range_requests(ib: IB, args, trade_days: list[date], symbols: list[str], failures: list[str]) -> tuple[int, int]:
    total_rows = 0
    total_accepted = 0
    requested_days = {day.isoformat(): day for day in trade_days}
    pending: dict[date, list[dict]] = {day: [] for day in trade_days}
    pending_symbols = 0
    for index, symbol in enumerate(symbols, 1):
        try:
            rows = fetch_symbol_rows_range(ib, symbol, trade_days[0], trade_days[-1])
            rows = [row for row in rows if row.get("trade_date") in requested_days]
            if rows:
                for row in rows:
                    pending[requested_days[row["trade_date"]]].append(row)
                total_rows += len(rows)
                by_day = {}
                for row in rows:
                    by_day[row["trade_date"]] = by_day.get(row["trade_date"], 0) + 1
                print(f"{now_iso()} [{index}/{len(symbols)}] {symbol} rows={len(rows)} days={len(by_day)}", flush=True)
            else:
                failures.append(f"{symbol}: no rows")
                print(f"{now_iso()} [{index}/{len(symbols)}] {symbol} no rows", file=sys.stderr, flush=True)
        except Exception as exc:
            failures.append(f"{symbol}: {exc}")
            print(f"{now_iso()} [{index}/{len(symbols)}] {symbol} failed: {exc}", file=sys.stderr, flush=True)
        pending_symbols += 1
        if pending_symbols >= max(1, args.flush_symbols):
            total_accepted += flush_pending_rows(args, pending)
            pending = {day: [] for day in trade_days}
            pending_symbols = 0
        ib.sleep(args.request_delay)
    total_accepted += flush_pending_rows(args, pending)
    return total_rows, total_accepted


def flush_pending_rows(args, pending: dict[date, list[dict]]) -> int:
    accepted = 0
    for trade_day, rows in pending.items():
        if not rows:
            continue
        write_rows(args.store_root, trade_day, rows)
        if not args.no_upload:
            accepted += upload_reference_rows(
                args.server,
                args.token,
                neutralize_upload_source(args.source),
                rows,
                args.timeout,
                args.upload_chunk_size,
            )
    return accepted


def fetch_symbol_rows(ib: IB, symbol: str, trade_day: date) -> list[dict]:
    market = symbol_market(symbol)
    start, end = session_bounds(trade_day)
    if market == "us":
        return fetch_us_rows(ib, symbol, start, end)
    contract = ib_contract(symbol, market)
    if contract is None:
        raise RuntimeError("unsupported symbol")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise RuntimeError("contract qualification failed")
    if market == "futures":
        contract = concrete_future_contract(symbol, qualified[0])
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError("front future contract qualification failed")
    duration = int((end - start).total_seconds()) + 60
    bars = ib.reqHistoricalData(
        qualified[0],
        endDateTime=end,
        durationStr=f"{duration} S",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=(market == "hk"),
        formatDate=2,
        keepUpToDate=False,
    )
    source = "daily"
    return rows_from_bars(symbol, market, bars, source, start, end)


def fetch_symbol_rows_range(ib: IB, symbol: str, start_day: date, end_day: date) -> list[dict]:
    market = symbol_market(symbol)
    start, _ = session_bounds(start_day)
    _, end = session_bounds(end_day)
    duration_days = max(1, (end.date() - start.date()).days + 2)
    if market == "us":
        return fetch_us_rows_range(ib, symbol, start, end, duration_days)
    contract = ib_contract(symbol, market)
    if contract is None:
        raise RuntimeError("unsupported symbol")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise RuntimeError("contract qualification failed")
    if market == "futures":
        contract = concrete_future_contract(symbol, qualified[0])
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError("front future contract qualification failed")
    bars = ib.reqHistoricalData(
        qualified[0],
        endDateTime=end,
        durationStr=f"{duration_days} D",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=(market == "hk"),
        formatDate=2,
        keepUpToDate=False,
    )
    return rows_from_bars(symbol, market, bars, "range", start, end)


def fetch_us_rows(ib: IB, symbol: str, start: datetime, end: datetime) -> list[dict]:
    live_contract = Stock(symbol, "SMART", "USD")
    qualified_live = ib.qualifyContracts(live_contract)
    if not qualified_live:
        raise RuntimeError("us live contract qualification failed")
    primary_exchange = str(getattr(qualified_live[0], "primaryExchange", "") or "")
    if not primary_exchange:
        raise RuntimeError("missing primary exchange")
    overnight_contract = Stock(symbol, "OVERNIGHT", "USD", primaryExchange=primary_exchange)
    qualified = ib.qualifyContracts(overnight_contract)
    if not qualified:
        raise RuntimeError("us overnight contract qualification failed")
    duration = int((end - start).total_seconds()) + 120
    for what in us_probe_order(symbol):
        try:
            bars = ib.reqHistoricalData(
                qualified[0],
                endDateTime=end,
                durationStr=f"{duration} S",
                barSizeSetting="1 min",
                whatToShow=what,
                useRTH=False,
                formatDate=2,
                keepUpToDate=False,
            )
        except Exception:
            continue
        rows = rows_from_bars(symbol, "us", bars, f"us_overnight_1m:{what.lower()}", start, end)
        if rows:
            return rows
    return []


def fetch_us_rows_range(ib: IB, symbol: str, start: datetime, end: datetime, duration_days: int) -> list[dict]:
    live_contract = Stock(symbol, "SMART", "USD")
    qualified_live = ib.qualifyContracts(live_contract)
    if not qualified_live:
        raise RuntimeError("us live contract qualification failed")
    primary_exchange = str(getattr(qualified_live[0], "primaryExchange", "") or "")
    if not primary_exchange:
        raise RuntimeError("missing primary exchange")
    overnight_contract = Stock(symbol, "OVERNIGHT", "USD", primaryExchange=primary_exchange)
    qualified = ib.qualifyContracts(overnight_contract)
    if not qualified:
        raise RuntimeError("us overnight contract qualification failed")
    for what in us_probe_order(symbol):
        try:
            bars = ib.reqHistoricalData(
                qualified[0],
                endDateTime=end,
                durationStr=f"{duration_days} D",
                barSizeSetting="1 min",
                whatToShow=what,
                useRTH=False,
                formatDate=2,
                keepUpToDate=False,
            )
        except Exception:
            continue
        rows = rows_from_bars(symbol, "us", bars, f"us_overnight_1m:{what.lower()}", start, end)
        if rows:
            return rows
    return []


def rows_from_bars(symbol: str, market: str, bars: Iterable, source: str, start: datetime, end: datetime) -> list[dict]:
    rows = []
    for bar in bars:
        bar_dt = normalize_bar_datetime(bar.date, market)
        if bar_dt is None:
            continue
        sh_dt = bar_dt.astimezone(SESSION_TZ).replace(second=0, microsecond=0)
        if sh_dt < start or sh_dt > end:
            continue
        if not is_session_minute(sh_dt):
            continue
        close = float(getattr(bar, "close", 0) or 0)
        if close <= 0:
            continue
        rows.append(
            {
                "trade_date": sh_dt.date().isoformat(),
                "symbol": symbol,
                "minute": sh_dt.strftime("%Y-%m-%d %H:%M"),
                "timestamp": sh_dt.isoformat(),
                "last_price": close,
                "open": float(getattr(bar, "open", 0) or 0),
                "high": float(getattr(bar, "high", 0) or 0),
                "low": float(getattr(bar, "low", 0) or 0),
                "close": close,
                "volume": float(getattr(bar, "volume", 0) or 0),
                "amount": 0,
                "source": neutralize_source_name(source),
            }
        )
    rows.sort(key=lambda row: (row["symbol"], row["minute"]))
    return rows


def is_session_minute(value: datetime) -> bool:
    minute = value.time()
    return SESSION_START <= minute <= SESSION_END


def ib_contract(symbol: str, market: str):
    if market == "hk":
        return Stock(str(int(symbol)), "SEHK", "HKD")
    if market == "futures":
        future_symbol, exchange = {
            "HF_CL": ("CL", "NYMEX"),
            "HF_GC": ("MGC", "COMEX"),
            "HF_SI": ("SI", "COMEX"),
            "HF_HG": ("HG", "COMEX"),
            "HF_ZN": ("ZN", "CBOT"),
            "HF_NQ": ("NQ", "CME"),
            "HF_ES": ("ES", "CME"),
            "HF_NK": ("N225M", "OSE.JPN"),
        }.get(symbol.upper(), ("", ""))
        if not future_symbol:
            return None
        return ContFuture(future_symbol, exchange)
    return None


def concrete_future_contract(symbol: str, front):
    future_symbol, exchange, currency, multiplier = {
        "HF_CL": ("CL", "NYMEX", "USD", "1000"),
        "HF_GC": ("MGC", "COMEX", "USD", "10"),
        "HF_SI": ("SI", "COMEX", "USD", "5000"),
        "HF_HG": ("HG", "COMEX", "USD", "25000"),
        "HF_ZN": ("ZN", "CBOT", "USD", ""),
        "HF_NQ": ("NQ", "CME", "USD", "20"),
        "HF_ES": ("ES", "CME", "USD", "50"),
        "HF_NK": ("N225M", "OSE.JPN", "JPY", "100"),
    }.get(symbol.upper(), ("", "", "", ""))
    if not future_symbol:
        raise RuntimeError("unsupported future symbol")
    contract_month = getattr(front, "lastTradeDateOrContractMonth", "") or getattr(front, "localSymbol", "")
    if not contract_month:
        raise RuntimeError("front future has no contract month")
    return Future(
        future_symbol,
        contract_month,
        exchange,
        currency=currency,
        multiplier=getattr(front, "multiplier", "") or multiplier,
    )


def normalize_bar_datetime(value, market: str) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    else:
        return None
    if parsed.tzinfo is not None:
        return parsed
    if market == "hk":
        return parsed.replace(tzinfo=ZoneInfo("Asia/Hong_Kong"))
    if market == "futures":
        return parsed.replace(tzinfo=ZoneInfo("America/New_York"))
    if market == "us":
        return parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.replace(tzinfo=SESSION_TZ)


def upload_reference_rows(server: str, token: str, source: str, rows: list[dict], timeout: float, chunk_size: int) -> int:
    accepted = 0
    for chunk in chunks(rows, max(1, chunk_size)):
        payload = json.dumps({"source": neutralize_upload_source(source), "rows": chunk}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            server.rstrip("/") + "/api/v1/minute-history/reference-backfill",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "X-Upload-Token": token},
        )
        try:
            with urllib.request.urlopen(request, timeout=max(timeout, 30)) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"upload status {exc.code}: {body}") from exc
        if parsed.get("error"):
            raise RuntimeError(parsed["error"])
        accepted += int(parsed.get("accepted") or 0)
        print(
            f"{now_iso()} uploaded reference rows={len(chunk)} accepted={parsed.get('accepted')} "
            f"skipped={parsed.get('skipped')}",
            flush=True,
        )
    return accepted


def write_rows(root: str, trade_day: date, rows: list[dict]) -> None:
    if not root or not rows:
        return
    folder = Path(root) / trade_day.strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "ibkr_reference_1m.csv"
    ordered: list[dict] = []
    offsets: dict[tuple[str, str], int] = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                key = (str(row.get("symbol") or "").strip(), str(row.get("minute") or "").strip())
                if key[0] and key[1]:
                    offsets[key] = len(ordered)
                    ordered.append(row)
    for row in rows:
        key = (str(row.get("symbol") or "").strip(), str(row.get("minute") or "").strip())
        if key[0] and key[1]:
            out = {field: row.get(field, "") for field in FIELDS}
            if key in offsets:
                ordered[offsets[key]] = out
            else:
                offsets[key] = len(ordered)
                ordered.append(out)
    tmp_path = path.with_suffix(".csv.tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in ordered)
    tmp_path.replace(path)
    print(f"{now_iso()} stored rows={len(rows)} merged_rows={len(ordered)} path={path}", flush=True)


def fetch_required_symbols(server: str, token: str, timeout: float) -> list[str]:
    request = urllib.request.Request(
        server.rstrip("/") + "/api/v1/uploads/quotes/required-symbols",
        headers={"X-Upload-Token": token, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    return parsed.get("symbols") or []


def symbol_market(symbol: str) -> str:
    value = symbol.strip()
    lower = value.lower()
    if len(value) == 5 and value.isdigit():
        return "hk"
    if lower.startswith("hf_"):
        return "futures"
    if value and value[0].isalpha():
        return "us"
    return ""


def us_probe_order(symbol: str) -> list[str]:
    upper = symbol.strip().upper()
    if upper in {"ARKQ", "FINX"}:
        return ["MIDPOINT", "TRADES", "BID_ASK"]
    return ["TRADES", "MIDPOINT", "BID_ASK"]


def session_bounds(trade_day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(trade_day, SESSION_START, SESSION_TZ),
        datetime.combine(trade_day, SESSION_END, SESSION_TZ),
    )


def parse_trade_date(value: str) -> date:
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    return date.fromisoformat(value[:10])


def parse_trade_days(args) -> list[date]:
    if args.start_date or args.end_date:
        start = parse_trade_date(args.start_date or args.trade_date)
        end = parse_trade_date(args.end_date or args.trade_date)
        if end < start:
            raise ValueError("--end-date must be >= --start-date")
        days = []
        cursor = start
        while cursor <= end:
            if args.include_weekends or cursor.weekday() < 5:
                days.append(cursor)
            cursor += timedelta(days=1)
        if not days:
            raise ValueError("date range contains no trade days")
        return days
    return [parse_trade_date(args.trade_date)]


def split_symbols(value: str) -> list[str]:
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def dedupe(values: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def chunks(values: list, size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"{now_iso()} ibkr minute backfill failed: {exc}", file=sys.stderr, flush=True)
        raise

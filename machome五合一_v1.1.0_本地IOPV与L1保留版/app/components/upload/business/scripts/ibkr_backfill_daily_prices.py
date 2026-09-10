#!/usr/bin/env python3
import argparse
import csv
import json
import os
import sys
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from sina_quote_uploader import load_env_file, neutralize_source_name, neutralize_upload_source
from sina_ws_uploader import close_ws, connect_ws, now_iso, recv_json, send_json

try:
    from ib_insync import ContFuture, Future, IB, Index, Stock, util
except ImportError as exc:
    raise SystemExit("ib_insync is required: python3 -m pip install --user ib_insync ibapi") from exc


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_STORE_ROOT = SCRIPT_DIR / "quote_store"
FIELDS = [
    "stored_at",
    "reason",
    "symbol",
    "name",
    "price",
    "prev_close",
    "open",
    "high",
    "low",
    "volume",
    "amount",
    "change_pct",
    "quote_date",
    "quote_time",
    "source",
    "source_symbol",
    "quote_session",
]


def main() -> int:
    load_env_file(".sina-uploader.env")
    parser = argparse.ArgumentParser(description="Backfill daily base prices from IBKR/TWS into local quote_store CSV.")
    parser.add_argument("--host", default=os.getenv("IBKR_HOST", os.getenv("NNN_IB_HOST", "127.0.0.1")))
    parser.add_argument("--port", type=int, default=int(os.getenv("IBKR_PORT", os.getenv("NNN_IB_PORT", "7496"))))
    parser.add_argument("--client-id", type=int, default=int(os.getenv("IBKR_CLIENT_ID", "22631")))
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--source", default=os.getenv("NNN_UPLOAD_SOURCE", "home-mac-daily"))
    parser.add_argument("--store-root", default=os.getenv("NNN_QUOTE_STORE_DIR", str(DEFAULT_STORE_ROOT)))
    parser.add_argument("--dates", default=",".join(recent_weekdays(date.today(), 3)))
    parser.add_argument("--from-server-requests", action="store_true", default=True)
    parser.add_argument("--no-server-requests", dest="from_server_requests", action="store_false")
    parser.add_argument("--include-futures", action="store_true", default=True)
    parser.add_argument("--no-futures", dest="include_futures", action="store_false")
    parser.add_argument("--symbols", default="", help="Comma-separated explicit symbols to backfill, such as HF_CL,HF_GC,HF_SI,HF_HG,HF_ZN,HF_NQ,HF_ES")
    parser.add_argument("--upload", action="store_true", help="Upload fetched rows to the web server over websocket as daily_prices.")
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dates = [normalize_date(item) for item in args.dates.split(",") if normalize_date(item)]
    requests = []
    if args.from_server_requests:
        requests.extend(fetch_daily_price_requests(args.server, args.token, args.source))
    if args.include_futures:
        for day in dates:
            for symbol in ("HF_CL", "HF_GC", "HF_SI", "HF_HG", "HF_ZN", "HF_NQ", "HF_ES"):
                requests.append({"symbol": symbol, "date": day, "market": "us_commodity_futures", "reason": "futures_backfill"})
    explicit_symbols = [normalize_symbol(item) for item in args.symbols.split(",") if normalize_symbol(item)]
    for day in dates:
        for symbol in explicit_symbols:
            market = infer_explicit_market(symbol)
            if not market:
                print(f"{now_iso()} skip unsupported explicit symbol={symbol}", file=sys.stderr, flush=True)
                continue
            requests.append({"symbol": symbol, "date": day, "market": market, "reason": "daily_backfill"})
    requests = dedupe_requests(requests)
    if not requests:
        print("no requests to backfill")
        return 0

    print(f"{now_iso()} ibkr backfill requests={len(requests)} dates={','.join(dates)} store={args.store_root}", flush=True)
    if args.dry_run:
        print(json.dumps(requests, ensure_ascii=False, indent=2))
        return 0

    ib = IB()
    ib.connect(args.host, args.port, clientId=args.client_id, timeout=10)
    written = 0
    uploaded_rows = []
    try:
        for req in requests:
            symbol = req["symbol"]
            target_date = req["date"]
            market = req["market"]
            try:
                row = fetch_ibkr_row(ib, symbol, target_date, market, req.get("reason", "daily_backfill"))
            except Exception as exc:
                print(f"{now_iso()} ibkr backfill failed symbol={symbol} date={target_date} market={market}: {exc}", file=sys.stderr, flush=True)
                continue
            if row is None:
                print(f"{now_iso()} ibkr backfill missing symbol={symbol} date={target_date} market={market}", file=sys.stderr, flush=True)
                continue
            append_quote_row(args.store_root, row)
            uploaded_rows.append(
                {
                    "symbol": symbol,
                    "date": target_date,
                    "close": row["price"],
                    "adj_close": row["price"],
                    "source": row["source"],
                }
            )
            written += 1
            print(f"{now_iso()} ibkr backfilled symbol={symbol} date={target_date} market={market} close={row['price']}", flush=True)
            ib.sleep(0.25)
    finally:
        ib.disconnect()
    if args.upload and uploaded_rows:
        if not args.token.strip():
            raise SystemExit("--upload requires --token or NNN_UPLOAD_TOKEN")
        accepted = upload_daily_price_rows(args.server, args.token, neutralize_upload_source(args.source), uploaded_rows, args.timeout)
        print(f"{now_iso()} uploaded daily prices accepted={accepted}/{len(uploaded_rows)}", flush=True)
    print(f"{now_iso()} ibkr backfill finished written={written}/{len(requests)}", flush=True)
    return 0


def fetch_daily_price_requests(server: str, token: str, source: str) -> list[dict]:
    if not token.strip():
        return []
    conn = connect_ws(server, token, source + "-inspect", 8)
    try:
        send_json(conn, {"type": "hello", "source": source + "-inspect", "sent_at": now_iso()})
        deadline = time.time() + 8
        while time.time() < deadline:
            msg = recv_json(conn, timeout=1)
            if not msg:
                continue
            if msg.get("type") == "daily_price_request":
                return msg.get("daily_price_requests") or []
    finally:
        close_ws(conn)
    return []


def fetch_ibkr_row(ib: IB, symbol: str, target_date: str, market: str, reason: str) -> Optional[dict]:
    if market == "us_commodity_futures":
        return fetch_future_anchor_row(ib, symbol, target_date, reason)
    if market == "us":
        contract = Stock(ib_symbol(symbol), "SMART", "USD")
        return fetch_daily_close_row(ib, contract, symbol, target_date, market, reason, "America/New_York")
    if market == "hk":
        contract = Stock(str(int(symbol)), "SEHK", "HKD")
        return fetch_daily_close_row(ib, contract, symbol, target_date, market, reason, "Asia/Hong_Kong")
    if market == "eu":
        contract = eu_contract(symbol)
        return fetch_daily_close_row(ib, contract, symbol, target_date, market, reason, "Europe/Berlin")
    if market == "jp":
        contract = jp_contract(symbol)
        return fetch_daily_close_row(ib, contract, symbol, target_date, market, reason, "Asia/Tokyo")
    return None


def fetch_daily_close_row(ib: IB, contract, symbol: str, target_date: str, market: str, reason: str, timezone: str) -> Optional[dict]:
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise RuntimeError("contract qualification failed")
    target = date.fromisoformat(target_date)
    end = datetime.combine(target + timedelta(days=1), dtime(0, 0), ZoneInfo(timezone))
    bars = ib.reqHistoricalData(
        qualified[0],
        endDateTime=end,
        durationStr="4 D",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=2,
    )
    for bar in reversed(bars):
        bar_date = bar.date.date() if isinstance(bar.date, datetime) else bar.date
        if bar_date == target:
            return quote_row(symbol, target_date, market, reason, float(bar.close), close_time_for_market(market, target_date), "daily")
    return None


def fetch_future_anchor_row(ib: IB, symbol: str, target_date: str, reason: str) -> Optional[dict]:
    future_symbol, exchange, multiplier = {
        "HF_CL": ("CL", "NYMEX", "1000"),
        "HF_GC": ("MGC", "COMEX", "10"),
        "HF_SI": ("SI", "COMEX", "5000"),
        "HF_HG": ("HG", "COMEX", "25000"),
        "HF_ZN": ("ZN", "CBOT", ""),
        "HF_NQ": ("NQ", "CME", "20"),
        "HF_ES": ("ES", "CME", "50"),
    }.get(symbol, ("", "", ""))
    if not future_symbol:
        return None
    contract = ContFuture(future_symbol, exchange)
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise RuntimeError("contract qualification failed")
    front = qualified[0]
    contract = Future(
        future_symbol,
        front.lastTradeDateOrContractMonth,
        exchange,
        currency="USD",
        multiplier=getattr(front, "multiplier", "") or multiplier,
    )
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise RuntimeError("front future contract qualification failed")
    target = date.fromisoformat(target_date)
    anchor = datetime.combine(target, dtime(16, 0), ZoneInfo("America/New_York"))
    bars = ib.reqHistoricalData(
        qualified[0],
        endDateTime=anchor + timedelta(minutes=3),
        durationStr="900 S",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=False,
        formatDate=2,
    )
    best = None
    best_delta = None
    for bar in bars:
        bar_dt = bar.date
        if not isinstance(bar_dt, datetime):
            continue
        if bar_dt.tzinfo is None:
            bar_dt = bar_dt.replace(tzinfo=ZoneInfo("America/New_York"))
        delta = abs((bar_dt - anchor).total_seconds())
        if best is None or delta < best_delta:
            best = bar
            best_delta = delta
    if best is None:
        return None
    return quote_row(symbol, target_date, "us_commodity_futures", reason, float(best.close), anchor, "daily")


def quote_row(symbol: str, target_date: str, market: str, reason: str, close: float, anchor: datetime, source: str) -> dict:
    local_anchor = anchor.astimezone()
    return {
        "stored_at": local_anchor.isoformat(timespec="seconds"),
        "reason": reason,
        "symbol": symbol,
        "name": symbol,
        "price": close,
        "prev_close": close,
        "open": "",
        "high": "",
        "low": "",
        "volume": "",
        "amount": "",
        "change_pct": "",
        "quote_date": target_date,
        "quote_time": local_anchor.strftime("%H:%M:%S"),
        "source": neutralize_source_name(source),
        "source_symbol": symbol,
        "quote_session": market,
    }


def append_quote_row(root: str, row: dict) -> None:
    folder = Path(root) / row["quote_date"].replace("-", "")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "quotes.csv"
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in FIELDS})


def close_time_for_market(market: str, target_date: str) -> datetime:
    day = date.fromisoformat(target_date)
    specs = {
        "us": ("America/New_York", dtime(16, 20)),
        "hk": ("Asia/Hong_Kong", dtime(16, 15)),
        "jp": ("Asia/Tokyo", dtime(15, 45)),
        "eu": ("Europe/Berlin", dtime(17, 45)),
    }
    timezone, tm = specs[market]
    return datetime.combine(day, tm, ZoneInfo(timezone))


def eu_contract(symbol: str):
    lower = symbol.lower()
    if lower == "znb_dax":
        return Index("DAX", "EUREX", "EUR")
    if lower == "znb_cac":
        return Index("CAC40", "MONEP", "EUR")
    raise RuntimeError(f"unsupported eu symbol {symbol}")


def jp_contract(symbol: str):
    lower = symbol.lower()
    if lower == "znb_nky":
        return Index("N225", "OSE.JPN", "JPY")
    if lower == "znb_tpx":
        return Index("TOPIX", "TSEJ", "JPY")
    raise RuntimeError(f"unsupported jp symbol {symbol}")


def ib_symbol(symbol: str) -> str:
    if symbol.upper() == "BRK.B":
        return "BRK B"
    return symbol.upper()


def infer_explicit_market(symbol: str) -> str:
    upper = symbol.upper()
    if upper in {"HF_CL", "HF_GC", "HF_SI", "HF_HG", "HF_ZN", "HF_NQ", "HF_ES"}:
        return "us_commodity_futures"
    if upper in {"COMT", "BCD"}:
        return "us"
    if upper.startswith("FX_"):
        return ""
    if upper.isdigit() and len(upper) == 5:
        return "hk"
    return "us"


def dedupe_requests(requests: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for req in requests:
        symbol = str(req.get("symbol") or "").strip()
        target_date = normalize_date(str(req.get("date") or ""))
        market = str(req.get("market") or "").strip().lower()
        if not symbol or not target_date or not market:
            continue
        key = (symbol, target_date, market)
        if key in seen:
            continue
        seen.add(key)
        out.append({"symbol": symbol, "date": target_date, "market": market, "reason": req.get("reason") or "daily_backfill"})
    return out


def recent_weekdays(today: date, count: int) -> list[str]:
    days = []
    cursor = today - timedelta(days=1)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    days.reverse()
    return days


def normalize_date(value: str) -> str:
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return ""


def normalize_symbol(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    lower = value.lower()
    if lower.startswith("hf_"):
        return "HF_" + value[3:].upper()
    if len(value) >= 3 and value[:2].upper() in {"SH", "SZ", "BJ"}:
        return value[:2].upper() + value[2:]
    return value.upper()


def upload_daily_price_rows(server: str, token: str, source: str, rows: list[dict], timeout: float) -> int:
    origin_ip = os.getenv("NNN_ORIGIN_IP", "")
    origin_tls_insecure = str(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")).strip().lower() in {"1", "true", "yes", "on"}
    origin_ca_file = os.getenv("NNN_ORIGIN_CA_FILE", "")
    conn = connect_ws(server, token, source + "-daily-backfill", timeout, origin_ip, origin_tls_insecure, origin_ca_file)
    try:
        send_json(conn, {"type": "hello", "source": source + "-daily-backfill", "sent_at": now_iso()})
        while recv_json(conn, timeout=0.25) is not None:
            pass
        request_id = f"{source}-daily-{int(time.time() * 1000)}"
        send_json(
            conn,
            {
                "type": "daily_prices",
                "source": source,
                "request_id": request_id,
                "sent_at": now_iso(),
                "daily_prices": rows,
            },
        )
        deadline = time.time() + max(timeout, 30)
        while time.time() < deadline:
            msg = recv_json(conn, timeout=min(0.5, max(0.05, deadline - time.time())))
            if not msg:
                continue
            if msg.get("type") == "ack" and (msg.get("request_id") in {"", request_id}):
                return int(msg.get("accepted") or 0)
            if msg.get("type") == "error":
                raise RuntimeError(msg.get("error") or "daily price upload failed")
        raise RuntimeError("daily price upload timed out waiting for ack")
    finally:
        close_ws(conn)


if __name__ == "__main__":
    raise SystemExit(main())

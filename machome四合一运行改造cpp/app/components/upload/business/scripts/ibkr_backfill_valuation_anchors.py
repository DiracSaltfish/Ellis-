#!/usr/bin/env python3
import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from sina_quote_uploader import (
    is_true,
    load_env_file,
    neutralize_capture_status,
    neutralize_source_name,
    neutralize_upload_source,
    open_server_request,
)

try:
    from ib_insync import ContFuture, Future, IB, Stock
except ImportError as exc:
    raise SystemExit("ib_insync is required: python3 -m pip install --user ib_insync ibapi") from exc


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_STORE_ROOT = SCRIPT_DIR / "quote_store"
ANCHOR_PRICE_FIELDS = [
    "stored_at",
    "fund_symbol",
    "anchor_date",
    "anchor_key",
    "reference_symbol",
    "target_at",
    "target_timezone",
    "target_beijing_time",
    "weight",
    "price",
    "observed_at",
    "source",
    "capture_status",
]

ANCHOR_RULES = {
    "SH501300": {
        "reference_symbol": "HF_ZN",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SH501018": {
        "reference_symbol": "HF_CL",
        "anchors": [
            {"anchor_key": "jp_close", "timezone": "Asia/Tokyo", "local_time": "15:30", "weight": 0.15},
            {"anchor_key": "eu_close", "timezone": "Europe/London", "local_time": "16:30", "weight": 0.48},
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 0.37},
        ],
    },
    "SZ160723": {
        "reference_symbol": "HF_CL",
        "anchors": [
            {"anchor_key": "hk_close", "timezone": "Asia/Hong_Kong", "local_time": "16:00", "weight": 0.0222},
            {"anchor_key": "jp_close", "timezone": "Asia/Tokyo", "local_time": "15:30", "weight": 0.0575},
            {"anchor_key": "eu_close", "timezone": "Europe/London", "local_time": "16:30", "weight": 0.4956},
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 0.4246},
        ],
    },
    "SZ161129": {
        "reference_symbol": "HF_CL",
        "anchors": [
            {"anchor_key": "hk_close", "timezone": "Asia/Hong_Kong", "local_time": "16:00", "weight": 0.1351},
            {"anchor_key": "eu_close", "timezone": "Europe/London", "local_time": "16:30", "weight": 0.4135},
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 0.4514},
        ],
    },
    "SZ160719": {
        "reference_symbol": "HF_GC",
        "anchors": [
            {"anchor_key": "ch_close", "timezone": "Europe/Zurich", "local_time": "17:30", "weight": 0.4498},
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 0.5502},
        ],
    },
    "SZ161116": {
        "reference_symbol": "HF_GC",
        "anchors": [
            {"anchor_key": "ch_close", "timezone": "Europe/Zurich", "local_time": "17:30", "weight": 0.1901386308140265},
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 0.8098613691859736},
        ],
    },
    "SZ165513": {
        "reference_symbol": "HF_GC",
        "anchors": [
            {"anchor_key": "jp_close", "timezone": "Asia/Tokyo", "local_time": "15:30", "weight": 0.026280559453056896},
            {"anchor_key": "eu_close", "timezone": "Europe/London", "local_time": "16:30", "weight": 0.08587014298432005},
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 0.887849297562623},
        ],
    },
    "SZ164701": {
        "reference_symbol": "HF_GC",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SH513000": {
        "reference_symbol": "HF_NK",
        "anchors": [
            {"anchor_key": "jp_close", "timezone": "Asia/Tokyo", "local_time": "15:30", "weight": 1.0},
        ],
    },
    "SH513520": {
        "reference_symbol": "HF_NK",
        "anchors": [
            {"anchor_key": "jp_close", "timezone": "Asia/Tokyo", "local_time": "15:30", "weight": 1.0},
        ],
    },
    "SH513880": {
        "reference_symbol": "HF_NK",
        "anchors": [
            {"anchor_key": "jp_close", "timezone": "Asia/Tokyo", "local_time": "15:30", "weight": 1.0},
        ],
    },
    "SZ159866": {
        "reference_symbol": "HF_NK",
        "anchors": [
            {"anchor_key": "jp_close", "timezone": "Asia/Tokyo", "local_time": "15:30", "weight": 1.0},
        ],
    },
    "SH513500": {
        "reference_symbol": "HF_ES",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SH513650": {
        "reference_symbol": "HF_ES",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SH513290": {
        "reference_symbol": "IBB",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ159612": {
        "reference_symbol": "HF_ES",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ161125": {
        "reference_symbol": "HF_ES",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ159655": {
        "reference_symbol": "HF_ES",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SH513350": {
        "reference_symbol": "XOP",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ159518": {
        "reference_symbol": "XOP",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ162411": {
        "reference_symbol": "XOP",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SH513400": {
        "reference_symbol": "DIA",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ160140": {
        "reference_symbol": "RWR",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ161126": {
        "reference_symbol": "RSPH",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ161127": {
        "reference_symbol": "XBI",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ161128": {
        "reference_symbol": "VGT",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ162415": {
        "reference_symbol": "XLY",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ159502": {
        "reference_symbol": "XBI",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ160416": {
        "reference_symbol": "IXC",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ162719": {
        "reference_symbol": "IEO",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ163208": {
        "reference_symbol": "XLE",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SH513850": {
        "reference_symbol": "OEF",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
    "SZ159577": {
        "reference_symbol": "MGC",
        "anchors": [
            {"anchor_key": "us_close", "timezone": "America/New_York", "local_time": "16:00", "weight": 1.0},
        ],
    },
}

REFERENCE_CONTRACTS = {
    "HF_CL": {"symbol": "CL", "exchange": "NYMEX", "currency": "USD", "multiplier": "1000"},
    "HF_GC": {"symbol": "MGC", "exchange": "COMEX", "currency": "USD", "multiplier": "10"},
    "HF_ZN": {"symbol": "ZN", "exchange": "CBOT", "currency": "USD", "multiplier": ""},
    "HF_NK": {"symbol": "N225M", "exchange": "OSE.JPN", "currency": "JPY", "multiplier": "100"},
    "HF_ES": {"symbol": "ES", "exchange": "CME", "currency": "USD", "multiplier": "50"},
}


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def env_int(*names: str, default: int) -> int:
    return int(env_first(*names, default=str(default)))


def main() -> int:
    load_env_file(".sina-uploader.env")
    parser = argparse.ArgumentParser(description="Backfill valuation anchor prices from IBKR/TWS into local uploader store.")
    parser.add_argument("--host", default=env_first("IBKR_HOST", "NNN_IB_HOST", default="127.0.0.1"))
    parser.add_argument("--port", type=int, default=env_int("IBKR_PORT", "NNN_IB_PORT", default=7496))
    parser.add_argument(
        "--client-id",
        type=int,
        default=env_int("IBKR_ANCHOR_BACKFILL_CLIENT_ID", "NNN_IB_ANCHOR_BACKFILL_CLIENT_ID", default=22962),
    )
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument(
        "--origin-tls-insecure",
        action="store_true",
        default=is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")),
    )
    parser.add_argument("--source", default=os.getenv("NNN_UPLOAD_SOURCE", "home-mac-anchor"))
    parser.add_argument("--store-root", default=os.getenv("NNN_QUOTE_STORE_DIR", str(DEFAULT_STORE_ROOT)))
    parser.add_argument("--fund", default=os.getenv("NNN_ANCHOR_BACKFILL_FUNDS", "SH501018"))
    parser.add_argument("--dates", default=",".join(recent_weekdays(date.today(), 5)))
    parser.add_argument(
        "--skip-future-targets",
        action="store_true",
        default=True,
        help="Skip anchors whose target_at is still in the future.",
    )
    parser.add_argument(
        "--include-future-targets",
        dest="skip_future_targets",
        action="store_false",
        help="Include anchors whose target_at is still in the future.",
    )
    parser.add_argument("--upload", action="store_true", help="POST rows to /api/v1/valuation-anchors/prices after writing local store.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=12)
    args = parser.parse_args()

    funds = parse_funds(args.fund)
    dates = [normalize_date(item) for item in args.dates.split(",") if normalize_date(item)]
    if not dates:
        raise SystemExit("no valid dates")
    requests: list[dict] = []
    for fund in funds:
        requests.extend(build_anchor_requests(fund, dates))
    requests.sort(key=lambda item: (item["target_at"], item["fund_symbol"], item["anchor_key"]))
    if args.skip_future_targets:
        before = len(requests)
        now_utc = datetime.now(timezone.utc)
        requests = [item for item in requests if parse_dt(item["target_at"]) <= now_utc]
        skipped = before - len(requests)
        if skipped > 0:
            print(f"{now_iso()} skip future valuation anchor requests funds={','.join(funds)} skipped={skipped}", flush=True)
    print(
        f"{now_iso()} ibkr valuation anchor backfill funds={','.join(funds)} "
        f"dates={','.join(dates)} requests={len(requests)} client_id={args.client_id}",
        flush=True,
    )
    if args.dry_run:
        print(json.dumps(requests, ensure_ascii=False, indent=2))
        return 0

    ib = IB()
    ib.connect(args.host, args.port, clientId=args.client_id, timeout=args.timeout)
    rows: list[dict] = []
    failures: list[str] = []
    contract_cache: dict[str, tuple[object, bool]] = {}
    price_cache: dict[tuple[str, str, bool], tuple[float, str]] = {}
    try:
        ib.reqMarketDataType(1)
        for index, request in enumerate(requests, 1):
            fund = request["fund_symbol"]
            reference_symbol = request["reference_symbol"]
            if reference_symbol not in contract_cache:
                contract_cache[reference_symbol] = qualify_reference_contract(ib, reference_symbol)
            contract, use_rth = contract_cache[reference_symbol]
            cached = False
            try:
                cache_key = (reference_symbol, request["target_at"], use_rth)
                cached = cache_key in price_cache
                if not cached:
                    price_cache[cache_key] = fetch_anchor_price(
                        ib, contract, parse_dt(request["target_at"]), use_rth
                    )
                price, source = price_cache[cache_key]
                row = {
                    **request,
                    "price": price,
                    "observed_at": request["target_at"],
                    "source": neutralize_source_name(source),
                    "capture_status": neutralize_capture_status("anchor_backfill"),
                }
                rows.append(row)
                print(
                    f"{now_iso()} [{index}/{len(requests)}] {fund} {request['anchor_date']} "
                    f"{request['anchor_key']} price={price:.4f} source={source} cached={str(cached).lower()}",
                    flush=True,
                )
            except Exception as exc:
                failures.append(f"{fund} {request['anchor_date']} {request['anchor_key']}: {exc}")
                print(
                    f"{now_iso()} [{index}/{len(requests)}] {fund} {request['anchor_date']} "
                    f"{request['anchor_key']} failed: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
            if not cached:
                ib.sleep(0.25)
    finally:
        ib.disconnect()

    write_anchor_rows(args.store_root, rows)
    accepted = 0
    if args.upload and rows:
        if not args.token.strip():
            raise SystemExit("--upload requires --token or NNN_UPLOAD_TOKEN")
        accepted = upload_anchor_rows(
            args.server,
            args.token,
            neutralize_upload_source(args.source),
            rows,
            args.timeout,
            args.origin_ip,
            args.origin_tls_insecure,
            args.origin_ca_file,
        )
    print(
        f"{now_iso()} ibkr valuation anchor backfill finished rows={len(rows)} accepted={accepted} failures={len(failures)}",
        flush=True,
    )
    if failures:
        print("first failures:", json.dumps(failures[:20], ensure_ascii=False), file=sys.stderr, flush=True)
    return 0


def build_anchor_requests(fund: str, dates: list[str]) -> list[dict]:
    rule = ANCHOR_RULES[fund]
    requests: list[dict] = []
    for anchor_date in dates:
        for anchor in rule["anchors"]:
            target_at = anchor_target_at(anchor_date, anchor)
            requests.append(
                {
                    "fund_symbol": fund,
                    "anchor_date": anchor_date,
                    "anchor_key": anchor["anchor_key"],
                    "reference_symbol": rule["reference_symbol"],
                    "target_at": target_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                    "target_timezone": anchor["timezone"],
                    "target_beijing_time": target_at.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "weight": anchor["weight"],
                }
            )
    requests.sort(key=lambda item: item["target_at"])
    return requests


def parse_funds(value: str) -> list[str]:
    raw = str(value or "").strip()
    if raw.lower() in {"all", "*"}:
        return sorted(ANCHOR_RULES)
    funds: list[str] = []
    seen: set[str] = set()
    for item in raw.replace("，", ",").split(","):
        fund = normalize_symbol(item)
        if not fund:
            continue
        if fund not in ANCHOR_RULES:
            raise SystemExit(f"unsupported fund: {item}")
        if fund not in seen:
            seen.add(fund)
            funds.append(fund)
    if not funds:
        raise SystemExit("no valid funds")
    return funds


def anchor_target_at(anchor_date: str, anchor: dict) -> datetime:
    day = date.fromisoformat(anchor_date)
    hour, minute = parse_hhmm(anchor["local_time"])
    return datetime.combine(day, dtime(hour, minute), ZoneInfo(anchor["timezone"]))


def qualify_reference_contract(ib: IB, reference_symbol: str):
    config = REFERENCE_CONTRACTS.get(reference_symbol)
    if config:
        cont = ContFuture(config["symbol"], config["exchange"])
        qualified = ib.qualifyContracts(cont)
        if not qualified:
            raise RuntimeError(f"{reference_symbol} continuous future qualification failed")
        front = qualified[0]
        month = getattr(front, "lastTradeDateOrContractMonth", "") or getattr(front, "localSymbol", "")
        if not month:
            raise RuntimeError(f"{reference_symbol} front future has no contract month")
        multiplier = getattr(front, "multiplier", "") or config["multiplier"]
        contract = Future(
            config["symbol"],
            month,
            config["exchange"],
            currency=config["currency"],
            multiplier=multiplier,
        )
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"{reference_symbol} front future qualification failed")
        return qualified[0], False
    contract = Stock(reference_symbol, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise RuntimeError(f"{reference_symbol} stock qualification failed")
    return qualified[0], True


def fetch_anchor_price(ib: IB, contract, target_at: datetime, use_rth: bool) -> tuple[float, str]:
    target_ny = target_at.astimezone(ZoneInfo("America/New_York"))
    query_end = target_ny + timedelta(minutes=3)
    for what in ("TRADES", "MIDPOINT", "BID_ASK"):
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=query_end,
            durationStr="1800 S",
            barSizeSetting="1 min",
            whatToShow=what,
            useRTH=use_rth,
            formatDate=2,
            keepUpToDate=False,
        )
        candidates: list[tuple[float, datetime, float]] = []
        for bar in bars:
            bar_dt = normalize_bar_datetime(bar.date)
            close = float(getattr(bar, "close", 0) or 0)
            if bar_dt is None or close <= 0:
                continue
            delta = abs((bar_dt - target_ny).total_seconds())
            if delta <= 180:
                candidates.append((delta, bar_dt, close))
        if candidates:
            _, bar_dt, close = min(candidates, key=lambda item: item[0])
            return close, neutralize_source_name(f"anchor_1m:{what}@{bar_dt.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M')}")
    raise RuntimeError(f"missing 1m bar near {target_at.isoformat()}")


def normalize_bar_datetime(value) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=ZoneInfo("America/New_York"))


def write_anchor_rows(root: str, rows: list[dict]) -> None:
    if not root or not rows:
        return
    by_day: dict[str, list[dict]] = {}
    for row in rows:
        target = parse_dt(row["target_at"])
        day = target.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        by_day.setdefault(day, []).append(row)
    for day, day_rows in by_day.items():
        folder = Path(root) / day
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "valuation_anchor_prices.csv"
        exists = path.exists()
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ANCHOR_PRICE_FIELDS)
            if not exists:
                writer.writeheader()
            stored_at = now_iso()
            for row in day_rows:
                out = {field: row.get(field, "") for field in ANCHOR_PRICE_FIELDS}
                out["stored_at"] = stored_at
                writer.writerow(out)
        print(f"{now_iso()} stored valuation anchors rows={len(day_rows)} path={path}", flush=True)


def upload_anchor_rows(
    server: str,
    token: str,
    source: str,
    rows: list[dict],
    timeout: float,
    origin_ip: str = "",
    origin_tls_insecure: bool = False,
    origin_ca_file: str = "",
) -> int:
    payload = json.dumps({"source": source, "prices": rows}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        server.rstrip("/") + "/api/v1/valuation-anchors/prices",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "X-Upload-Token": token},
    )
    try:
        with open_server_request(
            request,
            max(timeout, 30),
            origin_ip,
            origin_tls_insecure,
            origin_ca_file,
        ) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"upload status {exc.code}: {body}") from exc
    if parsed.get("error"):
        raise RuntimeError(parsed["error"])
    print(
        f"{now_iso()} uploaded valuation anchors accepted={parsed.get('accepted')} skipped={parsed.get('skipped')}",
        flush=True,
    )
    return int(parsed.get("accepted") or 0)


def parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"invalid time: {value}")
    return int(parts[0]), int(parts[1])


def parse_dt(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_symbol(value: str) -> str:
    value = str(value or "").strip()
    lower = value.lower()
    if lower.startswith("hf_"):
        return "HF_" + value[3:].upper()
    if len(value) >= 3 and value[:2].upper() in {"SH", "SZ", "BJ"}:
        return value[:2].upper() + value[2:]
    return value.upper()


def normalize_date(value: str) -> str:
    value = str(value or "").strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return ""


def recent_weekdays(today: date, count: int) -> list[str]:
    out = []
    cursor = today
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    out.reverse()
    return out


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"{now_iso()} ibkr valuation anchor backfill failed: {exc}", file=sys.stderr, flush=True)
        raise

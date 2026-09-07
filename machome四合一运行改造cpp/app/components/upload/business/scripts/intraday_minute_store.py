#!/usr/bin/env python3
import csv
import os
from datetime import datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
MINUTE_QUOTE_FIELDS = [
    "stored_at",
    "trading_day",
    "minute_bucket",
    "minute_label",
    "symbol",
    "market",
    "name",
    "price",
    "prev_close",
    "open",
    "high",
    "low",
    "volume",
    "amount",
    "change_pct",
    "observed_at",
    "last_seen_at",
    "capture_count",
    "quote_date",
    "quote_time",
    "quote_timezone",
    "quote_session",
    "source",
    "source_symbol",
]


def now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def minute_bucket(dt: datetime) -> datetime:
    return dt.astimezone(BEIJING_TZ).replace(second=0, microsecond=0)


def is_cn_intraday_minute_session(dt: datetime) -> bool:
    local = dt.astimezone(BEIJING_TZ)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    return 9 * 60 + 30 <= minute <= 15 * 60


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def symbol_market_code(symbol: str) -> str:
    value = str(symbol or "").strip()
    lower = value.lower()
    upper = value.upper()
    if upper.startswith("NF_") or (len(upper) == 8 and upper[:2] in {"SH", "SZ", "BJ"} and upper[2:].isdigit()):
        return "cn"
    if upper.endswith("-HK"):
        return "hk"
    if upper.endswith("-JP"):
        return "jp"
    if upper.endswith("-EU"):
        return "eu"
    if len(value) == 5 and value.isdigit():
        return "hk"
    if lower.startswith(("znb_nky", "znb_tpx")):
        return "jp"
    if lower.startswith(("znb_dax", "znb_cac")):
        return "eu"
    if lower.startswith("hf_"):
        return "us_commodity_futures"
    if lower.startswith("gb_") or is_us_like_symbol(upper):
        return "us"
    return ""


def is_us_like_symbol(symbol: str) -> bool:
    if not symbol or len(symbol) > 10:
        return False
    if not ("A" <= symbol[0] <= "Z"):
        return False
    return all(("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch == "." for ch in symbol)


def parse_quote_observed_at(quote: dict, fallback: datetime) -> datetime:
    quote_date = str(quote.get("quote_date") or "").strip()
    quote_time = str(quote.get("quote_time") or "").strip()
    quote_timezone = str(quote.get("quote_timezone") or "").strip()
    if quote_date and quote_time and quote_timezone:
        try:
            parsed = datetime.fromisoformat(f"{quote_date}T{quote_time}")
            return parsed.replace(tzinfo=ZoneInfo(quote_timezone))
        except Exception:
            pass
    return fallback.astimezone(BEIJING_TZ)


def float_or_zero(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


class IntradayMinuteArchive:
    def __init__(self, root: str):
        self.root = str(root or "").strip()
        self.pending_bucket: Optional[datetime] = None
        self.pending_rows: dict[str, dict] = {}

    def record(self, quotes: Iterable[dict], observed_at: Optional[datetime] = None) -> int:
        if not self.root:
            return 0
        now = (observed_at or now_beijing()).astimezone(BEIJING_TZ)
        self.flush_due(now)
        if not is_cn_intraday_minute_session(now):
            return 0
        bucket = minute_bucket(now)
        if self.pending_bucket is None or bucket != self.pending_bucket:
            self.pending_bucket = bucket
            self.pending_rows = {}
        seen = 0
        for quote in quotes:
            symbol = normalize_symbol(quote.get("symbol", ""))
            if not symbol:
                continue
            row = build_minute_row(quote, bucket, now)
            existing = self.pending_rows.get(symbol)
            if existing:
                row["capture_count"] = int(existing.get("capture_count") or 0) + 1
            self.pending_rows[symbol] = row
            seen += 1
        return seen

    def flush_due(self, observed_at: Optional[datetime] = None, force: bool = False) -> int:
        if not self.root or self.pending_bucket is None or not self.pending_rows:
            return 0
        now = (observed_at or now_beijing()).astimezone(BEIJING_TZ)
        current_bucket = minute_bucket(now)
        if not force and current_bucket <= self.pending_bucket:
            return 0
        rows = list(self.pending_rows.values())
        self._append_rows(self.pending_bucket, rows, now)
        self.pending_bucket = None
        self.pending_rows = {}
        return len(rows)

    def close(self) -> int:
        return self.flush_due(now_beijing(), force=True)

    def _append_rows(self, bucket: datetime, rows: list[dict], stored_at: datetime) -> None:
        day = bucket.strftime("%Y%m%d")
        folder = os.path.join(self.root, day)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "minute_quotes.csv")
        exists = os.path.exists(path)
        stored_value = stored_at.astimezone(BEIJING_TZ).isoformat(timespec="seconds")
        with open(path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=MINUTE_QUOTE_FIELDS)
            if not exists:
                writer.writeheader()
            for row in rows:
                out = dict(row)
                out["stored_at"] = stored_value
                writer.writerow(out)


def build_minute_row(quote: dict, bucket: datetime, captured_at: datetime) -> dict:
    observed_at = parse_quote_observed_at(quote, captured_at).astimezone(BEIJING_TZ)
    return {
        "stored_at": "",
        "trading_day": bucket.date().isoformat(),
        "minute_bucket": bucket.isoformat(timespec="seconds"),
        "minute_label": bucket.strftime("%H:%M"),
        "symbol": normalize_symbol(quote.get("symbol", "")),
        "market": symbol_market_code(str(quote.get("symbol", ""))),
        "name": str(quote.get("name") or "").strip(),
        "price": float_or_zero(quote.get("price")),
        "prev_close": float_or_zero(quote.get("prev_close")),
        "open": float_or_zero(quote.get("open")),
        "high": float_or_zero(quote.get("high")),
        "low": float_or_zero(quote.get("low")),
        "volume": float_or_zero(quote.get("volume")),
        "amount": float_or_zero(quote.get("amount")),
        "change_pct": float_or_zero(quote.get("change_pct")),
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "last_seen_at": captured_at.astimezone(BEIJING_TZ).isoformat(timespec="seconds"),
        "capture_count": 1,
        "quote_date": str(quote.get("quote_date") or "").strip(),
        "quote_time": str(quote.get("quote_time") or "").strip(),
        "quote_timezone": str(quote.get("quote_timezone") or "").strip(),
        "quote_session": str(quote.get("quote_session") or "").strip(),
        "source": str(quote.get("source") or "").strip(),
        "source_symbol": str(quote.get("source_symbol") or "").strip(),
    }

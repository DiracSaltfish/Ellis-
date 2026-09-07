#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sina_quote_uploader import (
    neutralize_source_name,
    neutralize_upload_source,
    open_server_request,
    server_request_headers,
)

try:
    from ib_insync import IB, Stock
except ImportError:  # pragma: no cover - runtime dependency
    IB = None
    Stock = None


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
NEW_YORK_TZ = ZoneInfo("America/New_York")
REFERENCE_FIELDS = [
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
DEFAULT_HOLDING_FUNDS = ("SH501312",)
DEFAULT_HOLDING_SYMBOL_OVERRIDES = {
    "SH501312": (
        "ARKK",
        "ARKG",
        "ARKQ",
        "SOXX",
        "AIQ",
        "QQQ",
        "BOTZ",
        "XLK",
        "SMH",
        "FINX",
    ),
}
OVERNIGHT_STREAM_DEFAULT_ORDER = ("TRADES", "MIDPOINT")
OVERNIGHT_STREAM_OVERRIDES = {
    "ARKG": ("MIDPOINT",),
    "ARKQ": ("MIDPOINT",),
    "FINX": ("MIDPOINT",),
}
REFERENCE_UPLOAD_RETRIES = 3
DEFAULT_IB_ACTIVE_START = "09:00"
DEFAULT_IB_ACTIVE_END = "15:05"
DEFAULT_IB_CONNECT_RETRY_SECONDS = 30.0
DEFAULT_IB_CONNECT_TIMEOUT = 20.0
DEFAULT_IB_WARMUP_SECONDS = 12.0
DEFAULT_IB_STALE_AFTER_SECONDS = 90.0
DEFAULT_IB_STALE_RECONNECT_STREAK = 3


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def default_holding_symbols(funds: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for fund in funds:
        for symbol in DEFAULT_HOLDING_SYMBOL_OVERRIDES.get(normalize_symbol(fund), ()): 
            normalized = normalize_symbol(symbol)
            if normalized and normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
    return out


def normalize_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except Exception:
        return ""


def is_finite_positive(value) -> bool:
    try:
        number = float(value)
    except Exception:
        return False
    return math.isfinite(number) and number > 0


def midpoint(bid, ask) -> float:
    if is_finite_positive(bid) and is_finite_positive(ask):
        return (float(bid) + float(ask)) / 2
    return 0.0


def now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def minute_bucket(dt: datetime) -> datetime:
    return dt.astimezone(BEIJING_TZ).replace(second=0, microsecond=0)


def is_cn_session(dt: datetime) -> bool:
    local = dt.astimezone(BEIJING_TZ)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    return 9 * 60 + 30 <= minute <= 15 * 60


def last_complete_session_minute(dt: datetime) -> Optional[datetime]:
    local = dt.astimezone(BEIJING_TZ).replace(second=0, microsecond=0)
    if not is_cn_session(local):
        return None
    if local.minute == 0 and local.second == 0:
        return local - timedelta(minutes=1)
    return local - timedelta(minutes=1)


def parse_ib_host(default_host: str) -> str:
    value = str(default_host or "").strip()
    return value or "127.0.0.1"


def quote_datetime(value, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        return fallback.astimezone(BEIJING_TZ)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BEIJING_TZ)


def build_minute_row(symbol: str, price: float, observed_at: datetime, source: str) -> dict:
    minute = minute_bucket(observed_at)
    return {
        "trade_date": minute.date().isoformat(),
        "symbol": normalize_symbol(symbol),
        "minute": minute.strftime("%Y-%m-%d %H:%M"),
        "timestamp": minute.isoformat(),
        "last_price": price,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 0,
        "amount": 0,
        "source": source,
    }


class IBReferenceMinuteArchive:
    def __init__(self, root: str):
        self.root = str(root or "").strip()
        self.pending_bucket: Optional[datetime] = None
        self.pending_rows: dict[str, dict] = {}

    def record(self, quotes: Iterable[dict], observed_at: datetime) -> None:
        if not self.root or not is_cn_session(observed_at):
            return
        bucket = minute_bucket(observed_at)
        if self.pending_bucket is None or bucket != self.pending_bucket:
            self.pending_bucket = bucket
            self.pending_rows = {}
        for quote in quotes:
            symbol = normalize_symbol(quote.get("symbol", ""))
            price = float(quote.get("price") or 0)
            if not symbol or price <= 0:
                continue
            current = self.pending_rows.get(symbol)
            if current is None:
                self.pending_rows[symbol] = build_minute_row(symbol, price, observed_at, neutralize_source_name(str(quote.get("source") or "us_live")))
                continue
            current["last_price"] = price
            current["close"] = price
            current["high"] = max(float(current.get("high") or price), price)
            current["low"] = min(float(current.get("low") or price), price)
            current["source"] = neutralize_source_name(str(quote.get("source") or current.get("source") or "us_live"))

    def flush_due(self, observed_at: datetime, force: bool = False) -> list[dict]:
        if not self.root or self.pending_bucket is None or not self.pending_rows:
            return []
        current_bucket = minute_bucket(observed_at)
        if not force and current_bucket <= self.pending_bucket:
            return []
        rows = list(self.pending_rows.values())
        self.store_rows(rows)
        self.pending_bucket = None
        self.pending_rows = {}
        return rows

    def close(self) -> list[dict]:
        return self.flush_due(now_beijing(), force=True)

    def store_rows(self, rows: list[dict]) -> None:
        if not self.root or not rows:
            return
        trade_day = rows[0]["trade_date"].replace("-", "")
        folder = Path(self.root) / trade_day
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "ibkr_reference_1m.csv"
        exists = path.exists()
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=REFERENCE_FIELDS)
            if not exists:
                writer.writeheader()
            writer.writerows({field: row.get(field, "") for field in REFERENCE_FIELDS} for row in rows)


@dataclass
class IBBridgeArgs:
    enabled: bool
    host: str
    port: int
    client_id: int
    timeout: float
    server: str
    token: str
    source: str
    store_root: str
    origin_ip: str
    origin_tls_insecure: bool
    origin_ca_file: str
    holding_funds: tuple[str, ...]
    holding_refresh_interval: float
    extra_symbols: tuple[str, ...]
    live_mode: str
    active_start_minute: int
    active_end_minute: int
    connect_retry_seconds: float
    connect_timeout: float
    warmup_seconds: float
    stale_after_seconds: float
    stale_reconnect_streak: int


class IBUSHoldingsBridge:
    def __init__(self, args: IBBridgeArgs):
        self.args = args
        self.ib = None
        self.live_contracts: dict[str, object] = {}
        self.overnight_contracts: dict[str, object] = {}
        self.tickers: dict[str, object] = {}
        self.overnight_streams: dict[str, tuple[object, str]] = {}
        self.overnight_stream_choice: dict[str, str] = {}
        self.overnight_stream_retry_at: dict[str, float] = {}
        self.prev_close_cache: dict[tuple[str, str], float] = {}
        self.holding_symbols: set[str] = set(default_holding_symbols(args.holding_funds)) | set(args.extra_symbols)
        self.last_holding_refresh = 0.0
        self.reference_archive = IBReferenceMinuteArchive(args.store_root)
        self.catchup_day = ""
        self.next_connect_retry_at = 0.0
        self.last_connect_at = 0.0
        self.last_subscription_change_at = 0.0
        self.overnight_stream_stale_counts: dict[str, int] = {}

    def close(self) -> None:
        flushed = self.reference_archive.close()
        if flushed:
            self.upload_reference_rows(flushed)
        self.disconnect_runtime()

    def disconnect_runtime(self) -> None:
        if self.ib is not None:
            for symbol, contract in self.live_contracts.items():
                if symbol not in self.tickers:
                    continue
                try:
                    self.ib.cancelMktData(contract)
                except Exception:
                    pass
            for bars, _what in self.overnight_streams.values():
                try:
                    self.ib.cancelHistoricalData(bars)
                except Exception:
                    pass
            try:
                if self.ib.isConnected():
                    self.ib.disconnect()
            except Exception:
                pass
        self.ib = None
        self.tickers.clear()
        self.overnight_streams.clear()

    def within_active_window(self, observed_at: Optional[datetime] = None) -> bool:
        return is_ib_active_window(
            observed_at or now_beijing(),
            self.args.active_start_minute,
            self.args.active_end_minute,
        )

    def maintain_connection_window(self, observed_at: Optional[datetime] = None) -> None:
        if self.within_active_window(observed_at):
            return
        self.disconnect_runtime()

    def refresh_target_symbols(self, required_symbols: list[str], force: bool = False, observed_at: Optional[datetime] = None) -> set[str]:
        if not self.args.enabled:
            return set()
        if not self.within_active_window(observed_at):
            return set()
        now = time.time()
        if force or not self.last_holding_refresh or now - self.last_holding_refresh >= self.args.holding_refresh_interval:
            try:
                refreshed = fetch_holding_symbols_for_funds(
                    self.args.server,
                    self.args.token,
                    self.args.holding_funds,
                    self.args.timeout,
                    self.args.origin_ip,
                    self.args.origin_tls_insecure,
                    self.args.origin_ca_file,
                )
                self.holding_symbols = set(refreshed) | set(self.args.extra_symbols)
                self.last_holding_refresh = now
            except Exception as exc:
                print(f"{datetime.now().isoformat(timespec='seconds')} ib holding symbol refresh failed: {exc}", flush=True)
        required = {normalize_symbol(item) for item in required_symbols if normalize_symbol(item)}
        return required & self.holding_symbols

    def ensure_connected(self, observed_at: Optional[datetime] = None) -> bool:
        if not self.args.enabled or IB is None or Stock is None:
            return False
        if not self.within_active_window(observed_at):
            self.disconnect_runtime()
            return False
        if self.ib is not None:
            try:
                if self.ib.isConnected():
                    self.next_connect_retry_at = 0.0
                    return True
            except Exception:
                pass
        now_ts = time.time()
        if self.next_connect_retry_at > now_ts:
            return False
        self.ib = IB()
        try:
            self.ib.connect(
                parse_ib_host(self.args.host),
                self.args.port,
                clientId=self.args.client_id,
                timeout=max(1.0, float(self.args.connect_timeout)),
            )
            self.ib.reqMarketDataType(1)
            self.next_connect_retry_at = 0.0
            self.last_connect_at = time.time()
            return True
        except Exception:
            self.next_connect_retry_at = now_ts + max(1.0, float(self.args.connect_retry_seconds))
            self.disconnect_runtime()
            raise

    def ensure_live_subscriptions(self, symbols: Iterable[str], observed_at: Optional[datetime] = None) -> None:
        target = [normalize_symbol(symbol) for symbol in symbols if normalize_symbol(symbol)]
        if not target or not self.ensure_connected(observed_at):
            return
        changed = False
        if self.args.live_mode in {"overnight", "hybrid"}:
            for symbol in target:
                before = symbol in self.overnight_streams
                self.ensure_overnight_stream(symbol, observed_at)
                if not before and symbol in self.overnight_streams:
                    changed = True
        if self.args.live_mode not in {"smart", "hybrid"}:
            if changed:
                self.last_subscription_change_at = time.time()
            return
        for symbol in target:
            if symbol in self.tickers:
                continue
            live_contract = self.qualify_live_contract(symbol)
            if live_contract is None:
                continue
            overnight_contract = self.qualify_overnight_contract(symbol, getattr(live_contract, "primaryExchange", "") or "")
            self.live_contracts[symbol] = live_contract
            if overnight_contract is not None:
                self.overnight_contracts[symbol] = overnight_contract
            self.tickers[symbol] = self.ib.reqMktData(live_contract, "236", False, False)
            changed = True
        self.ib.sleep(0.2)
        if changed:
            self.last_subscription_change_at = time.time()

    def ensure_overnight_stream(self, symbol: str, observed_at: Optional[datetime] = None) -> None:
        if not self.ensure_connected():
            return
        if symbol in self.overnight_streams:
            age_seconds = self.overnight_stream_age_seconds(symbol, observed_at)
            if age_seconds is None or age_seconds <= self.stale_after_seconds():
                return
            self.handle_stale_overnight_stream(symbol, age_seconds, "existing stream stale before reuse")
        retry_at = self.overnight_stream_retry_at.get(symbol, 0.0)
        if retry_at > time.time():
            return
        contract = self.qualify_overnight_contract(symbol, self.primary_exchange(symbol))
        if contract is None:
            self.overnight_stream_retry_at[symbol] = time.time() + 60
            return
        for what in overnight_probe_order(symbol, self.overnight_stream_choice.get(symbol)):
            try:
                bars = self.ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr="1200 S",
                    barSizeSetting="1 min",
                    whatToShow=what,
                    useRTH=False,
                    formatDate=2,
                    keepUpToDate=True,
                )
            except Exception:
                continue
            self.ib.sleep(0.8)
            age_seconds = self.stream_age_seconds(bars, observed_at)
            if bars and stream_last_price(bars) > 0 and (age_seconds is None or age_seconds <= self.stale_after_seconds()):
                self.overnight_streams[symbol] = (bars, what)
                self.overnight_stream_choice[symbol] = what
                self.clear_overnight_stream_stale(symbol)
                return
            try:
                self.ib.cancelHistoricalData(bars)
            except Exception:
                pass
            self.ib.sleep(0.1)
        self.overnight_stream_retry_at[symbol] = time.time() + 60

    def qualify_live_contract(self, symbol: str):
        if not self.ensure_connected():
            return None
        if symbol in self.live_contracts:
            return self.live_contracts[symbol]
        contract = Stock(symbol, "SMART", "USD")
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            return None
        self.live_contracts[symbol] = qualified[0]
        return qualified[0]

    def qualify_overnight_contract(self, symbol: str, primary_exchange: str):
        if not self.ensure_connected():
            return None
        if symbol in self.overnight_contracts:
            return self.overnight_contracts[symbol]
        contract = Stock(symbol, "OVERNIGHT", "USD", primaryExchange=primary_exchange)
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            return None
        self.overnight_contracts[symbol] = qualified[0]
        return qualified[0]

    def live_quotes(self, required_symbols: list[str], observed_at: Optional[datetime] = None) -> dict[str, dict]:
        captured_at = (observed_at or now_beijing()).astimezone(BEIJING_TZ)
        target = self.refresh_target_symbols(required_symbols, observed_at=captured_at)
        if not target:
            self.maintain_connection_window(captured_at)
            return {}
        self.ensure_live_subscriptions(target, captured_at)
        quotes = self.collect_live_quotes(target, captured_at)
        if self.should_warmup_live_quotes(target, quotes):
            deadline = time.time() + max(0.0, float(self.args.warmup_seconds))
            while time.time() < deadline and len(quotes) < len(target):
                if self.ib is None:
                    break
                self.ib.sleep(min(0.5, max(0.05, deadline-time.time())))
                quotes = self.collect_live_quotes(target, now_beijing())
        return quotes

    def should_warmup_live_quotes(self, target: set[str], quotes: dict[str, dict]) -> bool:
        if not target or len(quotes) >= len(target):
            return False
        warmup = max(0.0, float(self.args.warmup_seconds))
        if warmup <= 0:
            return False
        now_ts = time.time()
        return (
            now_ts - self.last_connect_at <= warmup
            or now_ts - self.last_subscription_change_at <= warmup
        )

    def collect_live_quotes(self, target: set[str], observed_at: datetime) -> dict[str, dict]:
        if self.ib is not None:
            self.ib.sleep(0.1)
        quotes: dict[str, dict] = {}
        for symbol in sorted(target):
            quote = None
            if self.args.live_mode in {"overnight", "hybrid"} and is_cn_session(observed_at):
                quote = self.overnight_stream_quote(symbol, observed_at)
            if (quote is None or quote.get("price", 0) <= 0) and self.args.live_mode in {"smart", "hybrid"}:
                ticker = self.tickers.get(symbol)
                quote = ticker_to_live_quote(symbol, ticker, observed_at)
            if quote and quote.get("price", 0) > 0:
                quotes[symbol] = quote
        return quotes

    def overnight_stream_quote(self, symbol: str, observed_at: datetime) -> Optional[dict]:
        stream = self.overnight_streams.get(symbol)
        if not stream:
            return None
        bars, what = stream
        bar = last_usable_bar(bars)
        if bar is None:
            return None
        age_seconds = self.stream_age_seconds(bars, observed_at)
        if age_seconds is not None and age_seconds > self.stale_after_seconds():
            self.handle_stale_overnight_stream(symbol, age_seconds, "overnight stream quote stale")
            return None
        price = float(getattr(bar, "close", 0) or 0)
        if price <= 0:
            return None
        self.clear_overnight_stream_stale(symbol)
        bar_dt = getattr(bar, "date", None)
        quote_at = quote_datetime(bar_dt, observed_at)
        prev_close = self.previous_regular_close(symbol, observed_at)
        change_pct = 0.0
        if prev_close > 0:
            change_pct = (price / prev_close - 1) * 100
        return {
            "symbol": symbol,
            "name": symbol,
            "price": price,
            "prev_close": prev_close,
            "open": float(getattr(bar, "open", 0) or prev_close or price),
            "high": float(getattr(bar, "high", 0) or price),
            "low": float(getattr(bar, "low", 0) or price),
            "volume": float(getattr(bar, "volume", 0) or 0),
            "amount": 0,
            "change_pct": change_pct,
            "quote_date": quote_at.strftime("%Y-%m-%d"),
            "quote_time": quote_at.strftime("%H:%M:%S"),
            "quote_timezone": "Asia/Shanghai",
            "source": neutralize_source_name(f"us_overnight_live:{what.lower()}"),
            "source_symbol": symbol,
            "quote_session": "us_overnight_live",
        }

    def stale_after_seconds(self) -> float:
        return max(1.0, float(self.args.stale_after_seconds))

    def stale_reconnect_streak(self) -> int:
        return max(1, int(self.args.stale_reconnect_streak))

    def in_live_warmup_window(self) -> bool:
        warmup = max(0.0, float(self.args.warmup_seconds))
        if warmup <= 0:
            return False
        now_ts = time.time()
        return (
            now_ts - self.last_connect_at <= warmup
            or now_ts - self.last_subscription_change_at <= warmup
        )

    def clear_overnight_stream_stale(self, symbol: str) -> None:
        self.overnight_stream_stale_counts.pop(symbol, None)

    def reset_overnight_stream(self, symbol: str, reason: str) -> None:
        stream = self.overnight_streams.pop(symbol, None)
        if stream is None:
            return
        bars, _what = stream
        try:
            if self.ib is not None:
                self.ib.cancelHistoricalData(bars)
        except Exception:
            pass
        print(
            f"{datetime.now().isoformat(timespec='seconds')} ib overnight stream reset symbol={symbol} reason={reason}",
            flush=True,
        )

    def handle_stale_overnight_stream(self, symbol: str, age_seconds: float, reason: str) -> None:
        self.reset_overnight_stream(symbol, f"{reason} age={age_seconds:.1f}s")
        stale_count = self.overnight_stream_stale_counts.get(symbol, 0) + 1
        self.overnight_stream_stale_counts[symbol] = stale_count
        print(
            f"{datetime.now().isoformat(timespec='seconds')} ib overnight stream stale symbol={symbol} "
            f"age={age_seconds:.1f}s stale_count={stale_count}",
            flush=True,
        )
        if stale_count < self.stale_reconnect_streak() or self.in_live_warmup_window():
            return
        print(
            f"{datetime.now().isoformat(timespec='seconds')} ib runtime reconnect requested "
            f"symbol={symbol} stale_count={stale_count}",
            flush=True,
        )
        self.disconnect_runtime()
        self.overnight_stream_stale_counts.clear()

    def overnight_stream_age_seconds(self, symbol: str, observed_at: Optional[datetime]) -> Optional[float]:
        stream = self.overnight_streams.get(symbol)
        if stream is None:
            return None
        bars, _what = stream
        return self.stream_age_seconds(bars, observed_at)

    def stream_age_seconds(self, bars, observed_at: Optional[datetime]) -> Optional[float]:
        if observed_at is None:
            return None
        bar = last_usable_bar(bars)
        if bar is None:
            return None
        bar_dt = getattr(bar, "date", None)
        quote_at = quote_datetime(bar_dt, observed_at)
        observed_utc = observed_at.astimezone(timezone.utc)
        quote_utc = quote_at.astimezone(timezone.utc)
        return max(0.0, (observed_utc - quote_utc).total_seconds())

    def catchup_reference_minutes(self, required_symbols: list[str], observed_at: Optional[datetime] = None) -> int:
        now = (observed_at or now_beijing()).astimezone(BEIJING_TZ)
        if not is_cn_session(now):
            self.maintain_connection_window(now)
            return 0
        day_key = now.strftime("%Y%m%d")
        if self.catchup_day == day_key:
            return 0
        target = sorted(self.refresh_target_symbols(required_symbols, force=True, observed_at=now))
        if not target or not self.ensure_connected(now):
            return 0
        end_minute = last_complete_session_minute(now)
        if end_minute is None:
            return 0
        rows: list[dict] = []
        for symbol in target:
            rows.extend(self.fetch_overnight_history_rows(symbol, now, end_minute))
            if self.ib is not None:
                self.ib.sleep(0.1)
        if rows:
            self.reference_archive.store_rows(rows)
            self.upload_reference_rows(rows)
        self.catchup_day = day_key
        return len(rows)

    def fetch_overnight_history_rows(self, symbol: str, now: datetime, end_minute: datetime) -> list[dict]:
        contract = self.qualify_overnight_contract(symbol, self.primary_exchange(symbol))
        if contract is None:
            return []
        session_start = datetime.combine(now.date(), dtime(9, 30), BEIJING_TZ)
        duration_seconds = max(1800, int((end_minute - session_start).total_seconds()) + 120)
        for what in overnight_probe_order(symbol, self.overnight_stream_choice.get(symbol)):
            try:
                bars = self.ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr=f"{duration_seconds} S",
                    barSizeSetting="1 min",
                    whatToShow=what,
                    useRTH=False,
                    formatDate=2,
                    keepUpToDate=False,
                )
            except Exception:
                continue
            rows = []
            for bar in bars:
                bar_dt = getattr(bar, "date", None)
                if not isinstance(bar_dt, datetime):
                    continue
                if bar_dt.tzinfo is None:
                    bar_dt = bar_dt.replace(tzinfo=timezone.utc)
                sh_dt = bar_dt.astimezone(BEIJING_TZ).replace(second=0, microsecond=0)
                if sh_dt < session_start or sh_dt > end_minute:
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
                        "source": neutralize_source_name(f"us_overnight_1m:{what.lower()}"),
                    }
                )
            if rows:
                rows.sort(key=lambda item: (item["symbol"], item["minute"]))
                self.overnight_stream_choice[symbol] = what
                return rows
        return []

    def record_reference_quotes(self, quotes: Iterable[dict], observed_at: Optional[datetime] = None) -> None:
        now = (observed_at or now_beijing()).astimezone(BEIJING_TZ)
        eligible = [quote for quote in quotes if normalize_symbol(quote.get("symbol", "")) in self.holding_symbols and ("overnight" in str(quote.get("quote_session") or "").lower() or str(quote.get("source") or "").startswith(("us_", "ibkr_us_")))]
        if eligible:
            self.reference_archive.record(eligible, now)

    def flush_reference_minutes(self, observed_at: Optional[datetime] = None, force: bool = False) -> int:
        now = (observed_at or now_beijing()).astimezone(BEIJING_TZ)
        rows = self.reference_archive.flush_due(now, force=force)
        if rows:
            self.upload_reference_rows(rows)
        return len(rows)

    def upload_reference_rows(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        payload = json.dumps({"source": neutralize_upload_source(self.args.source + "-reference"), "rows": rows}, ensure_ascii=False).encode("utf-8")
        last_exc = None
        for attempt in range(1, REFERENCE_UPLOAD_RETRIES + 1):
            headers = server_request_headers(self.args.server, self.args.token, "application/json")
            headers["Connection"] = "close"
            req = urllib.request.Request(
                self.args.server.rstrip("/") + "/api/v1/minute-history/reference-backfill",
                data=payload,
                method="POST",
                headers=headers,
            )
            try:
                with open_server_request(
                    req,
                    max(self.args.timeout, 20),
                    self.args.origin_ip,
                    self.args.origin_tls_insecure,
                    self.args.origin_ca_file,
                ) as resp:
                    parsed = json.loads(resp.read().decode("utf-8"))
                return int(parsed.get("accepted") or 0)
            except Exception as exc:
                last_exc = exc
                if attempt < REFERENCE_UPLOAD_RETRIES:
                    time.sleep(0.8 * attempt)
                    continue
        print(f"{datetime.now().isoformat(timespec='seconds')} ib reference upload failed: {last_exc}", flush=True)
        return 0

    def resolve_missing_daily_prices(self, requests: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
        prices: list[dict] = []
        quote_rows: list[dict] = []
        warnings: list[str] = []
        now = now_beijing()
        if not requests or not self.ensure_connected(now):
            return prices, quote_rows, warnings
        self.refresh_target_symbols(
            [str(item.get("symbol") or "") for item in requests],
            force=not self.holding_symbols,
            observed_at=now,
        )
        eligible = self.holding_symbols | set(self.args.extra_symbols)
        for item in requests:
            symbol = normalize_symbol(item.get("symbol", ""))
            market = str(item.get("market") or "").strip().lower()
            target_date = str(item.get("date") or "").strip()
            if market != "us" or symbol not in eligible or not target_date:
                continue
            price_row, quote_row = self.fetch_us_close(symbol, target_date)
            if price_row is None or quote_row is None:
                warnings.append(f"{symbol} {target_date} ib close not found")
                continue
            prices.append(price_row)
            quote_rows.append(quote_row)
            if self.ib is not None:
                self.ib.sleep(0.05)
        return prices, quote_rows, warnings

    def resolve_missing_valuation_anchor_prices(self, requests: list[dict]) -> tuple[list[dict], list[str]]:
        prices: list[dict] = []
        warnings: list[str] = []
        now = now_beijing()
        now_utc = now.astimezone(timezone.utc)
        due_requests: list[tuple[dict, datetime]] = []
        for item in requests:
            target_at = parse_ib_anchor_datetime(str(item.get("target_at") or ""))
            if target_at is None or target_at.astimezone(timezone.utc) > now_utc:
                continue
            due_requests.append((item, target_at))
        if not due_requests or not self.ensure_connected(now):
            return prices, warnings
        self.refresh_target_symbols(
            [str(item.get("reference_symbol") or "") for item, _target_at in due_requests],
            force=not self.holding_symbols,
            observed_at=now,
        )
        eligible = self.holding_symbols | set(self.args.extra_symbols)
        anchor_cache: dict[tuple[str, str], Optional[dict]] = {}
        for item, target_at in due_requests:
            symbol = normalize_symbol(item.get("reference_symbol", ""))
            if symbol not in eligible:
                continue
            cache_key = (symbol, target_at.astimezone(timezone.utc).isoformat())
            cached = cache_key in anchor_cache
            if not cached:
                anchor_cache[cache_key] = self.fetch_us_anchor(symbol, item, target_at)
            template = anchor_cache[cache_key]
            if template is None:
                warnings.append(f"{symbol} {item.get('anchor_date')} {item.get('anchor_key')} ib anchor not found")
                continue
            row = valuation_anchor_row_for_request(template, item, symbol)
            prices.append(row)
            if self.ib is not None and not cached:
                self.ib.sleep(0.05)
        return prices, warnings

    def fetch_us_close(self, symbol: str, target_date: str) -> tuple[Optional[dict], Optional[dict]]:
        contract = self.qualify_live_contract(symbol)
        if contract is None:
            return None, None
        target_day = date.fromisoformat(target_date)
        anchor = datetime.combine(target_day, dtime(16, 0), NEW_YORK_TZ)
        best = None
        best_delta = None
        try:
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime=anchor + timedelta(minutes=3),
                durationStr="900 S",
                barSizeSetting="1 min",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=2,
                keepUpToDate=False,
            )
        except Exception:
            bars = []
        for bar in bars:
            bar_dt = getattr(bar, "date", None)
            if not isinstance(bar_dt, datetime):
                continue
            if bar_dt.tzinfo is None:
                bar_dt = bar_dt.replace(tzinfo=timezone.utc)
            close = float(getattr(bar, "close", 0) or 0)
            if close <= 0:
                continue
            delta = abs((bar_dt.astimezone(NEW_YORK_TZ) - anchor).total_seconds())
            if best is None or delta < best_delta:
                best = (bar_dt, close)
                best_delta = delta
        if best is None:
            try:
                daily = self.ib.reqHistoricalData(
                    contract,
                    endDateTime=datetime.combine(target_day + timedelta(days=1), dtime(0, 0), NEW_YORK_TZ),
                    durationStr="4 D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                    formatDate=2,
                    keepUpToDate=False,
                )
            except Exception:
                daily = []
            for bar in reversed(daily):
                bar_dt = getattr(bar, "date", None)
                if isinstance(bar_dt, datetime):
                    bar_day = bar_dt.astimezone(NEW_YORK_TZ).date()
                else:
                    bar_day = bar_dt
                close = float(getattr(bar, "close", 0) or 0)
                if bar_day == target_day and close > 0:
                    best = (anchor.astimezone(timezone.utc), close)
                    break
        if best is None:
            return None, None
        observed_at, close = best
        sh_time = observed_at.astimezone(BEIJING_TZ)
        quote_row = {
            "symbol": symbol,
            "name": symbol,
            "price": close,
            "prev_close": close,
            "open": close,
            "high": close,
            "low": close,
            "volume": 0,
            "amount": 0,
            "change_pct": 0,
            "quote_date": target_date,
            "quote_time": "16:00:00",
            "quote_timezone": "America/New_York",
            "source": "daily",
            "source_symbol": symbol,
            "quote_session": "us_regular_close",
            "observed_at": sh_time.isoformat(timespec="seconds"),
        }
        price_row = {
            "symbol": symbol,
            "date": target_date,
            "close": close,
            "adj_close": close,
            "source": "daily",
        }
        return price_row, quote_row

    def fetch_us_anchor(self, symbol: str, request: dict, target_at: datetime) -> Optional[dict]:
        contract = self.qualify_live_contract(symbol)
        if contract is None:
            return None
        best = None
        best_delta = None
        for what in ("TRADES", "MIDPOINT", "BID_ASK"):
            try:
                bars = self.ib.reqHistoricalData(
                    contract,
                    endDateTime=target_at + timedelta(minutes=3),
                    durationStr="1800 S",
                    barSizeSetting="1 min",
                    whatToShow=what,
                    useRTH=True,
                    formatDate=2,
                    keepUpToDate=False,
                )
            except Exception:
                bars = []
            for bar in bars:
                bar_dt = getattr(bar, "date", None)
                if not isinstance(bar_dt, datetime):
                    continue
                if bar_dt.tzinfo is None:
                    bar_dt = bar_dt.replace(tzinfo=timezone.utc)
                close = float(getattr(bar, "close", 0) or 0)
                if close <= 0:
                    continue
                delta = abs((bar_dt.astimezone(target_at.tzinfo) - target_at).total_seconds())
                if delta > 180:
                    continue
                if best is None or delta < best_delta:
                    best = (bar_dt, close, what)
                    best_delta = delta
            if best is not None:
                break
        if best is None:
            _price_row, quote_row = self.fetch_us_close(symbol, target_at.date().isoformat())
            if quote_row is None:
                return None
            observed_at = target_at.astimezone(BEIJING_TZ)
            return {
                "fund_symbol": normalize_symbol(str(request.get("fund_symbol") or "")),
                "anchor_date": normalize_date(str(request.get("anchor_date") or "")),
                "anchor_key": str(request.get("anchor_key") or "").strip(),
                "reference_symbol": symbol,
                "target_at": str(request.get("target_at") or "").strip(),
                "target_timezone": str(request.get("target_timezone") or "").strip(),
                "target_beijing_time": str(request.get("target_beijing_time") or "").strip(),
                "weight": float(request.get("weight") or 0),
                "price": float(quote_row.get("price") or 0),
                "observed_at": observed_at.isoformat(timespec="seconds"),
                "source": "daily",
                "capture_status": "daily_close_fallback",
            }
        observed_at, close, _what = best
        return {
            "fund_symbol": normalize_symbol(str(request.get("fund_symbol") or "")),
            "anchor_date": normalize_date(str(request.get("anchor_date") or "")),
            "anchor_key": str(request.get("anchor_key") or "").strip(),
            "reference_symbol": symbol,
            "target_at": str(request.get("target_at") or "").strip(),
            "target_timezone": str(request.get("target_timezone") or "").strip(),
            "target_beijing_time": str(request.get("target_beijing_time") or "").strip(),
            "weight": float(request.get("weight") or 0),
            "price": close,
            "observed_at": observed_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "source": "daily",
            "capture_status": "captured_exact",
        }

    def primary_exchange(self, symbol: str) -> str:
        contract = self.qualify_live_contract(symbol)
        if contract is None:
            return ""
        return str(getattr(contract, "primaryExchange", "") or "")

    def previous_regular_close(self, symbol: str, observed_at: datetime) -> float:
        target_day = most_recent_us_regular_close_day(observed_at)
        for _ in range(7):
            key = (symbol, target_day.isoformat())
            if key in self.prev_close_cache:
                return self.prev_close_cache[key]
            _price_row, quote_row = self.fetch_us_close(symbol, target_day.isoformat())
            if quote_row is not None:
                price = float(quote_row.get("price") or 0)
                if price > 0:
                    self.prev_close_cache[key] = price
                    return price
            target_day -= timedelta(days=1)
        return 0.0


def valuation_anchor_row_for_request(template: dict, request: dict, symbol: str) -> dict:
    """Attach fund-specific audit metadata to one shared reference observation."""
    row = dict(template)
    row.update({
        "fund_symbol": normalize_symbol(str(request.get("fund_symbol") or "")),
        "anchor_date": normalize_date(str(request.get("anchor_date") or "")),
        "anchor_key": str(request.get("anchor_key") or "").strip(),
        "reference_symbol": symbol,
        "target_at": str(request.get("target_at") or "").strip(),
        "target_timezone": str(request.get("target_timezone") or "").strip(),
        "target_beijing_time": str(request.get("target_beijing_time") or "").strip(),
        "weight": float(request.get("weight") or 0),
    })
    return row


def ticker_to_live_quote(symbol: str, ticker, observed_at: datetime) -> Optional[dict]:
    if ticker is None:
        return None
    price = 0.0
    last = getattr(ticker, "last", 0)
    bid = getattr(ticker, "bid", 0)
    ask = getattr(ticker, "ask", 0)
    close = getattr(ticker, "close", 0)
    if is_finite_positive(last):
        price = float(last)
    else:
        price = midpoint(bid, ask)
    if price <= 0:
        try:
            market_price = float(ticker.marketPrice())
        except Exception:
            market_price = 0.0
        if is_finite_positive(market_price):
            price = market_price
    if price <= 0:
        return None
    prev_close = float(close) if is_finite_positive(close) else 0.0
    quote_at = quote_datetime(getattr(ticker, "time", None), observed_at)
    change_pct = 0.0
    if prev_close > 0:
        change_pct = (price / prev_close - 1) * 100
    return {
        "symbol": symbol,
        "name": symbol,
        "price": price,
        "prev_close": prev_close,
        "open": prev_close or price,
        "high": max(price, prev_close or price),
        "low": min(price, prev_close or price),
        "volume": 0,
        "amount": 0,
        "change_pct": change_pct,
        "quote_date": quote_at.strftime("%Y-%m-%d"),
        "quote_time": quote_at.strftime("%H:%M:%S"),
        "quote_timezone": "Asia/Shanghai",
        "source": "us_live",
        "source_symbol": symbol,
        "quote_session": "us_smart_live",
    }


def stream_last_price(bars) -> float:
    bar = last_usable_bar(bars)
    if bar is None:
        return 0.0
    return float(getattr(bar, "close", 0) or 0)


def last_usable_bar(bars):
    if not bars:
        return None
    for bar in reversed(bars):
        price = float(getattr(bar, "close", 0) or 0)
        if price > 0:
            return bar
    return None


def most_recent_us_regular_close_day(observed_at: datetime) -> date:
    ny = observed_at.astimezone(NEW_YORK_TZ)
    close_day = ny.date()
    if ny.time() < dtime(16, 0):
        close_day -= timedelta(days=1)
    return close_day


def parse_ib_anchor_datetime(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_hhmm_to_minute(value: str, default: str) -> int:
    text = str(value or "").strip() or default
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except Exception:
        hour_text, minute_text = default.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return hour * 60 + minute


def is_ib_active_window(observed_at: datetime, start_minute: int, end_minute: int) -> bool:
    local = observed_at.astimezone(BEIJING_TZ)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    if start_minute <= end_minute:
        return start_minute <= minute <= end_minute
    return minute >= start_minute or minute <= end_minute


def overnight_probe_order(symbol: str, preferred: Optional[str] = None) -> tuple[str, ...]:
    out: list[str] = []
    symbol = normalize_symbol(symbol)
    if preferred:
        out.append(str(preferred).strip().upper())
    out.extend(OVERNIGHT_STREAM_OVERRIDES.get(symbol, ()))
    out.extend(OVERNIGHT_STREAM_DEFAULT_ORDER)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in out:
        normalized = str(item or "").strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return tuple(deduped)


def fetch_holding_symbols_for_funds(
    server: str,
    token: str,
    funds: Iterable[str],
    timeout: float,
    origin_ip: str = "",
    origin_tls_insecure: bool = False,
    origin_ca_file: str = "",
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for fund in funds:
        fund_symbol = normalize_symbol(fund)
        if not fund_symbol:
            continue
        url = server.rstrip("/") + "/api/v1/funds/" + urllib.parse.quote(fund_symbol)
        req = urllib.request.Request(url, headers=server_request_headers(server, token))
        with open_server_request(req, timeout, origin_ip, origin_tls_insecure, origin_ca_file) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        for item in payload.get("valuation_inputs") or []:
            if str(item.get("role") or "").strip() != "持仓标的":
                continue
            if str(item.get("market") or "").strip().lower() != "us":
                continue
            symbol = normalize_symbol(item.get("symbol", ""))
            if symbol and symbol not in seen:
                seen.add(symbol)
                out.append(symbol)
    return out


def bridge_from_args(args) -> Optional[IBUSHoldingsBridge]:
    enabled = str(os.getenv("NNN_ENABLE_IB_US_HOLDINGS", "1")).strip().lower() not in {"0", "false", "no", "off"}
    holding_funds = tuple(
        normalize_symbol(item)
        for item in str(os.getenv("NNN_IB_HOLDING_FUNDS", ",".join(DEFAULT_HOLDING_FUNDS))).replace("，", ",").split(",")
        if normalize_symbol(item)
    )
    extra_symbols = tuple(
        normalize_symbol(item)
        for item in str(os.getenv("NNN_IB_EXTRA_SYMBOLS", "")).replace("，", ",").split(",")
        if normalize_symbol(item)
    )
    bridge_args = IBBridgeArgs(
        enabled=enabled and bool(holding_funds or extra_symbols),
        host=str(os.getenv("NNN_IB_HOST", os.getenv("IBKR_HOST", "127.0.0.1"))).strip() or "127.0.0.1",
        port=int(os.getenv("NNN_IB_PORT", os.getenv("IBKR_PORT", "7496"))),
        client_id=int(os.getenv("NNN_IB_WS_CLIENT_ID", "22961")),
        timeout=float(os.getenv("NNN_IB_TIMEOUT", getattr(args, "timeout", 8))),
        server=str(getattr(args, "server", "") or "").strip(),
        token=str(getattr(args, "token", "") or "").strip(),
        source=str(getattr(args, "source", "home-mac") or "").strip(),
        store_root=str(getattr(args, "store_root", "") or "").strip(),
        origin_ip=str(getattr(args, "origin_ip", "") or "").strip(),
        origin_tls_insecure=bool(getattr(args, "origin_tls_insecure", False)),
        origin_ca_file=str(getattr(args, "origin_ca_file", "") or "").strip(),
        holding_funds=holding_funds,
        holding_refresh_interval=float(os.getenv("NNN_IB_HOLDING_REFRESH_INTERVAL", "600")),
        extra_symbols=extra_symbols,
        live_mode=str(os.getenv("NNN_IB_US_LIVE_MODE", "smart")).strip().lower() or "smart",
        active_start_minute=parse_hhmm_to_minute(os.getenv("NNN_IB_ACTIVE_START", DEFAULT_IB_ACTIVE_START), DEFAULT_IB_ACTIVE_START),
        active_end_minute=parse_hhmm_to_minute(os.getenv("NNN_IB_ACTIVE_END", DEFAULT_IB_ACTIVE_END), DEFAULT_IB_ACTIVE_END),
        connect_retry_seconds=float(os.getenv("NNN_IB_CONNECT_RETRY_SECONDS", str(DEFAULT_IB_CONNECT_RETRY_SECONDS))),
        connect_timeout=float(os.getenv("NNN_IB_CONNECT_TIMEOUT", str(DEFAULT_IB_CONNECT_TIMEOUT))),
        warmup_seconds=float(os.getenv("NNN_IB_WARMUP_SECONDS", str(DEFAULT_IB_WARMUP_SECONDS))),
        stale_after_seconds=float(os.getenv("NNN_IB_STALE_AFTER_SECONDS", str(DEFAULT_IB_STALE_AFTER_SECONDS))),
        stale_reconnect_streak=int(os.getenv("NNN_IB_STALE_RECONNECT_STREAK", str(DEFAULT_IB_STALE_RECONNECT_STREAK))),
    )
    if not bridge_args.enabled:
        return None
    return IBUSHoldingsBridge(bridge_args)

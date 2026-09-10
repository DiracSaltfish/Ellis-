#!/usr/bin/env python3
import atexit
import argparse
import base64
import csv
import fcntl
import hashlib
import io
import json
import os
import random
import re
import socket
import ssl
import struct
import sys
import time
import urllib.parse
from datetime import date, datetime, time as dtime, timedelta, timezone
from html.parser import HTMLParser
from typing import Optional
from zoneinfo import ZoneInfo

from intraday_minute_store import IntradayMinuteArchive, MINUTE_QUOTE_FIELDS
from ib_us_uploader_support import bridge_from_args
import sina_quote_uploader as base
import upload_monitor_status as upload_health
from sina_quote_uploader import (
    chunks,
    fetch_required_symbols,
    fetch_sina_quotes,
    is_true,
    load_env_file,
    neutralize_capture_status,
    neutralize_source_name,
    neutralize_upload_source,
    parse_duration,
    request_stop,
    sleep_until_stop,
    split_symbols,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE_ROOT = os.path.join(SCRIPT_DIR, "quote_store")
DEFAULT_INTRADAY_MINUTE_STORE_ROOT = os.path.join(SCRIPT_DIR, "intraday_minute_store")
DEFAULT_LOCK_FILE = os.path.join(SCRIPT_DIR, ".runtime", "sina_ws_uploader.lock")
DEFAULT_VALUATION_POSITION_STATE_FILE = os.path.join(SCRIPT_DIR, ".runtime", "valuation_position_overrides.json")
DEFAULT_MINUTE_BACKFILL_SYNC_INTERVAL = "60s"
DEFAULT_WS_RECONNECT_DELAY = "8s"
DEFAULT_WS_MAX_RECONNECT_DELAY = "30s"
DEFAULT_HOLDINGS_UPLOAD_INTERVAL = "30m"
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


def main() -> int:
    load_env_file(".sina-uploader.env")
    parser = argparse.ArgumentParser(description="Push Sina quotes to newnavnav over WebSocket.")
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument(
        "--origin-tls-insecure",
        action="store_true",
        default=is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")),
    )
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--source", default=os.getenv("NNN_UPLOAD_SOURCE", "home-mac-py-ws"))
    parser.add_argument("--interval", default=os.getenv("NNN_UPLOAD_INTERVAL", "3s"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", "8")))
    parser.add_argument("--symbols", default=os.getenv("NNN_UPLOAD_SYMBOLS", ""))
    parser.add_argument("--store-root", default=os.getenv("NNN_QUOTE_STORE_DIR", DEFAULT_STORE_ROOT))
    parser.add_argument("--store-sent", action="store_true", default=is_true(os.getenv("NNN_STORE_SENT_QUOTES", "0")))
    parser.add_argument(
        "--intraday-minute-store-root",
        default=os.getenv("NNN_INTRADAY_MINUTE_STORE_DIR", DEFAULT_INTRADAY_MINUTE_STORE_ROOT),
    )
    parser.add_argument(
        "--disable-intraday-minute-store",
        action="store_false",
        dest="store_intraday_minutes",
        default=not is_true(os.getenv("NNN_DISABLE_INTRADAY_MINUTE_STORE", "")),
    )
    parser.add_argument(
        "--minute-backfill-sync-interval",
        default=os.getenv("NNN_MINUTE_BACKFILL_SYNC_INTERVAL", DEFAULT_MINUTE_BACKFILL_SYNC_INTERVAL),
    )
    parser.add_argument("--lock-file", default=os.getenv("NNN_UPLOADER_LOCK_FILE", DEFAULT_LOCK_FILE))
    parser.add_argument(
        "--valuation-position-state-file",
        default=os.getenv("NNN_VALUATION_POSITION_STATE_FILE", DEFAULT_VALUATION_POSITION_STATE_FILE),
    )
    parser.add_argument(
        "--disable-instance-lock",
        action="store_true",
        default=is_true(os.getenv("NNN_DISABLE_INSTANCE_LOCK", "")),
    )
    parser.add_argument("--full-interval", default=os.getenv("NNN_FULL_PUSH_INTERVAL", "60s"))
    parser.add_argument("--required-refresh-interval", default=os.getenv("NNN_REQUIRED_REFRESH_INTERVAL", "10m"))
    parser.add_argument("--heartbeat-interval", default=os.getenv("NNN_HEARTBEAT_INTERVAL", "30s"))
    parser.add_argument("--reconnect-delay", default=os.getenv("NNN_WS_RECONNECT_DELAY", DEFAULT_WS_RECONNECT_DELAY))
    parser.add_argument(
        "--max-reconnect-delay",
        default=os.getenv("NNN_WS_MAX_RECONNECT_DELAY", DEFAULT_WS_MAX_RECONNECT_DELAY),
    )
    parser.add_argument(
        "--enable-holdings-upload",
        action="store_true",
        default=is_true(os.getenv("NNN_ENABLE_HOLDINGS_UPLOAD", "")),
    )
    parser.add_argument(
        "--holdings-upload-funds",
        default=os.getenv("NNN_HOLDINGS_UPLOAD_FUNDS", os.getenv("NNN_IB_HOLDING_FUNDS", "")),
    )
    parser.add_argument(
        "--holdings-upload-interval",
        default=os.getenv("NNN_HOLDINGS_UPLOAD_INTERVAL", DEFAULT_HOLDINGS_UPLOAD_INTERVAL),
    )
    parser.add_argument("--palmmicro-base-url", default=os.getenv("NNN_PALMMICRO_BASE_URL", "https://www.palmmicro.com"))
    parser.add_argument(
        "--close-capture-delay-seconds",
        type=int,
        default=int(os.getenv("NNN_CLOSE_CAPTURE_DELAY_SECONDS", "90")),
    )
    parser.add_argument(
        "--anchor-capture-delay-seconds",
        type=int,
        default=int(os.getenv("NNN_ANCHOR_CAPTURE_DELAY_SECONDS", "90")),
    )
    parser.add_argument(
        "--futures-probe-pre-seconds",
        type=int,
        default=int(os.getenv("NNN_FUTURES_PROBE_PRE_SECONDS", "120")),
    )
    parser.add_argument(
        "--futures-probe-post-seconds",
        type=int,
        default=int(os.getenv("NNN_FUTURES_PROBE_POST_SECONDS", "180")),
    )
    parser.add_argument(
        "--anchor-probe-pre-seconds",
        type=int,
        default=int(os.getenv("NNN_ANCHOR_PROBE_PRE_SECONDS", "120")),
    )
    parser.add_argument(
        "--anchor-probe-post-seconds",
        type=int,
        default=int(os.getenv("NNN_ANCHOR_PROBE_POST_SECONDS", "180")),
    )
    parser.add_argument("--mode", choices=["smart", "all"], default=os.getenv("NNN_PUSH_MODE", "smart"))
    parser.add_argument("--once", action="store_true", default=is_true(os.getenv("NNN_UPLOAD_ONCE", "")))
    args = parser.parse_args()
    args.source = neutralize_upload_source(args.source)

    if not args.token.strip():
        print("NNN_UPLOAD_TOKEN or --token is required", file=sys.stderr)
        return 2

    import signal

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    lock_handle = None
    try:
        if not args.disable_instance_lock:
            lock_handle = acquire_instance_lock(args.lock_file)
        if args.once:
            return run_ws_once(args)
        run_ws_forever(args)
        return 0
    finally:
        release_instance_lock(lock_handle)


def acquire_instance_lock(path: str):
    lock_path = str(path or "").strip()
    if not lock_path:
        return None
    folder = os.path.dirname(lock_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        owner = handle.read().strip()
        handle.close()
        owner_text = owner or "unknown"
        raise RuntimeError(f"another uploader instance is already running: {owner_text}")
    payload = {
        "pid": os.getpid(),
        "started_at": now_iso(),
        "script": os.path.abspath(sys.argv[0]),
    }
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(payload, ensure_ascii=False))
    handle.flush()
    atexit.register(release_instance_lock, handle)
    return handle


def release_instance_lock(handle) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        handle.close()
    except Exception:
        pass


class WSRuntimeState:
    def __init__(self, minute_backfill_sync=None):
        self.minute_backfill_sync = minute_backfill_sync
        self.next_holdings_upload = 0.0

    def holdings_upload_due(self, now_at: float, interval: float) -> bool:
        return interval > 0 and now_at >= self.next_holdings_upload

    def note_holdings_upload(self, now_at: float, interval: float) -> None:
        self.next_holdings_upload = now_at + interval if interval > 0 else 0.0

    def reset_holdings_upload(self) -> None:
        self.next_holdings_upload = 0.0


def run_ws_once(args) -> int:
	symbols = split_symbols(args.symbols)
	if not symbols:
		symbols = fetch_required_symbols(
			args.server,
			args.token,
			args.timeout,
			args.origin_ip,
			args.origin_tls_insecure,
			args.origin_ca_file,
		)
	active = active_symbols(symbols, args.mode)
	if not active:
		print(f"{now_iso()} ws once skipped symbols={len(symbols)} outside_active_session")
		return 0
	ib_bridge = bridge_from_args(args)
	quotes = list(fetch_current_quotes(active, args, ib_bridge).values())
	minute_archive = create_intraday_minute_archive(args)
	conn = connect_ws(
		args.server,
		args.token,
		args.source,
		args.timeout,
		args.origin_ip,
		args.origin_tls_insecure,
		args.origin_ca_file,
	)
	try:
		send_json(conn, {"type": "hello", "source": args.source, "sent_at": now_iso()})
		if args.enable_holdings_upload:
			upload_due_holding_snapshots(args)
		if ib_bridge is not None:
			ib_bridge.catchup_reference_minutes(active, datetime.now(ZoneInfo("Asia/Shanghai")))
		batch_count = send_quotes(conn, "quotes", args.source, 1, "", quotes)
		accepted = wait_for_acks(conn, batch_count, args.timeout)
		upload_health.record_success(args.source, stage="ws_quote_ack", accepted=accepted, symbols=active)
		if minute_archive is not None:
			minute_archive.record(quotes)
			minute_archive.close()
		if ib_bridge is not None:
			ib_bridge.record_reference_quotes(quotes, datetime.now(ZoneInfo("Asia/Shanghai")))
			ib_bridge.flush_reference_minutes(datetime.now(ZoneInfo("Asia/Shanghai")), force=True)
		store_quotes(args.store_root, quotes, "once")
		print(f"{now_iso()} ws quotes sent symbols={len(active)} quotes={len(quotes)} accepted={accepted}")
		return 0
	finally:
		if ib_bridge is not None:
			ib_bridge.close()
		close_ws(conn)


def jittered_reconnect_delay(delay: float, max_delay: float) -> float:
    capped = max(0.0, min(delay, max_delay))
    if capped <= 0:
        return 0.0
    jitter_cap = min(max_delay - capped, max(1.0, capped * 0.2))
    if jitter_cap <= 0:
        return capped
    return capped + random.uniform(0.0, jitter_cap)


def run_ws_forever(args) -> None:
    reconnect_delay = parse_duration(getattr(args, "reconnect_delay", DEFAULT_WS_RECONNECT_DELAY), 8.0)
    max_reconnect_delay = parse_duration(getattr(args, "max_reconnect_delay", DEFAULT_WS_MAX_RECONNECT_DELAY), 30.0)
    runtime_state = WSRuntimeState(create_intraday_minute_backfill_sync(args))
    reconnect_attempt = 0
    while not base.STOP:
        try:
            run_ws_session(args, runtime_state)
            reconnect_delay = parse_duration(getattr(args, "reconnect_delay", DEFAULT_WS_RECONNECT_DELAY), 8.0)
            reconnect_attempt = 0
        except Exception as exc:
            reconnect_attempt += 1
            upload_health.record_failure(args.source, stage="ws_session", error=exc)
            sleep_for = jittered_reconnect_delay(reconnect_delay, max_reconnect_delay)
            print(
                f"{now_iso()} ws session failed: {exc}; reconnecting attempt={reconnect_attempt} "
                f"sleep={sleep_for:.1f}s",
                file=sys.stderr,
                flush=True,
            )
            sleep_until_stop(sleep_for)
            reconnect_delay = min(max_reconnect_delay, reconnect_delay * 1.5)


def safe_sync_minute_backfill(minute_backfill_sync, args, observed_at: datetime, force: bool = False) -> int:
    if minute_backfill_sync is None:
        return 0
    try:
        return minute_backfill_sync.sync(args, observed_at, force=force)
    except Exception as exc:
        print(f"{now_iso()} minute backfill sync failed: {exc}", file=sys.stderr, flush=True)
        return 0


def run_ws_session(args, runtime_state: Optional[WSRuntimeState] = None) -> None:
    interval = parse_duration(args.interval, 3.0)
    full_interval = parse_duration(args.full_interval, 60.0)
    minute_backfill_sync_interval = parse_duration(args.minute_backfill_sync_interval, 60.0)
    holdings_upload_interval = parse_duration(args.holdings_upload_interval, 1800.0)
    required_refresh_interval = parse_duration(args.required_refresh_interval, 600.0)
    heartbeat_interval = parse_duration(args.heartbeat_interval, 30.0)
    fixed_symbols = split_symbols(args.symbols)
    required_symbols = fixed_symbols[:]
    last_sent: dict[str, tuple] = {}
    pending_anchor_requests: dict[tuple[str, str, str], dict] = {}
    last_full_push = 0.0
    last_day = current_day()
    captured_closes: set[tuple[str, str]] = set()
    captured_anchors: set[tuple[str, str, str]] = set()
    next_required_refresh = time.time() + required_refresh_interval
    next_heartbeat = time.time() + heartbeat_interval
    next_close_capture_check = 0.0
    next_anchor_capture_check = 0.0
    seq = 0
    minute_archive = create_intraday_minute_archive(args)
    runtime_state = runtime_state or WSRuntimeState(create_intraday_minute_backfill_sync(args))
    minute_backfill_sync = runtime_state.minute_backfill_sync
    ib_bridge = bridge_from_args(args)

    conn = connect_ws(
        args.server,
        args.token,
        args.source,
        args.timeout,
        args.origin_ip,
        args.origin_tls_insecure,
        args.origin_ca_file,
    )
    try:
        send_json(conn, {"type": "hello", "source": args.source, "sent_at": now_iso()})
        if args.enable_holdings_upload and runtime_state.holdings_upload_due(time.time(), holdings_upload_interval):
            upload_due_holding_snapshots(args)
            runtime_state.note_holdings_upload(time.time(), holdings_upload_interval)
        deadline = time.time() + min(args.timeout, 3.0)
        while time.time() < deadline:
            msg = recv_json(conn, timeout=0.25)
            if not msg:
                continue
            if msg.get("required_symbols") and not fixed_symbols:
                required_symbols = msg["required_symbols"]
            if msg.get("type") == "quote_request":
                handle_quote_request(conn, args, msg, ib_bridge)
                if required_symbols:
                    break
            if msg.get("type") == "daily_price_request":
                handle_daily_price_request(conn, args, msg, ib_bridge)
            if msg.get("type") == "valuation_anchor_price_request":
                handle_valuation_anchor_price_request(conn, args, msg, pending_anchor_requests, ib_bridge)
            if msg.get("type") == "valuation_position_state_request":
                handle_valuation_position_state_request(conn, args, msg)
            if msg.get("type") == "valuation_position_set_request":
                handle_valuation_position_set_request(conn, args, msg)
        if not required_symbols:
            required_symbols = fetch_required_symbols(
                args.server,
                args.token,
                args.timeout,
                args.origin_ip,
                args.origin_tls_insecure,
                args.origin_ca_file,
            )
        send_valuation_position_state(conn, args, "initial-state")

        print(
            f"{now_iso()} ws connected required={len(required_symbols)} interval={interval}s "
            f"full_interval={full_interval}s mode={args.mode}",
            flush=True,
        )
        upload_health.record_heartbeat(args.source, stage="ws_connected", detail=f"required={len(required_symbols)}")
        next_push = 0.0
        next_minute_backfill_sync = time.time() + minute_backfill_sync_interval
        if ib_bridge is not None:
            ib_bridge.catchup_reference_minutes(required_symbols, datetime.now(ZoneInfo("Asia/Shanghai")))
        if minute_backfill_sync is not None:
            safe_sync_minute_backfill(minute_backfill_sync, args, datetime.now(ZoneInfo("Asia/Shanghai")))
        while not base.STOP:
            now = time.time()
            now_beijing = datetime.now(ZoneInfo("Asia/Shanghai"))
            day = current_day()
            if day != last_day:
                last_day = day
                last_sent.clear()
                captured_closes.clear()
                captured_anchors.clear()
                last_full_push = 0.0
                next_push = 0.0
                if not fixed_symbols:
                    required_symbols = refresh_required_symbols(args, required_symbols)
                    next_required_refresh = now + required_refresh_interval
                if ib_bridge is not None:
                    ib_bridge.catchup_reference_minutes(required_symbols, now_beijing)
                if minute_backfill_sync is not None:
                    minute_backfill_sync.reset_for_day(last_day)
                runtime_state.reset_holdings_upload()
                print(f"{now_iso()} day rollover detected day={day} required={len(required_symbols)}", flush=True)

            if not fixed_symbols and required_refresh_interval > 0 and now >= next_required_refresh:
                required_symbols = refresh_required_symbols(args, required_symbols)
                next_required_refresh = now + required_refresh_interval
                if ib_bridge is not None:
                    ib_bridge.catchup_reference_minutes(required_symbols, now_beijing)

            if heartbeat_interval > 0 and now >= next_heartbeat:
                send_json(conn, {"type": "ping", "source": args.source, "sent_at": now_iso()})
                next_heartbeat = now + heartbeat_interval

            if minute_archive is not None:
                minute_archive.flush_due(now_beijing)
            if ib_bridge is not None:
                ib_bridge.maintain_connection_window(now_beijing)
                ib_bridge.flush_reference_minutes(now_beijing)
            if minute_backfill_sync is not None and now >= next_minute_backfill_sync:
                safe_sync_minute_backfill(minute_backfill_sync, args, now_beijing)
                next_minute_backfill_sync = now + minute_backfill_sync_interval

            if args.enable_holdings_upload and runtime_state.holdings_upload_due(now, holdings_upload_interval):
                upload_due_holding_snapshots(args)
                runtime_state.note_holdings_upload(time.time(), holdings_upload_interval)

            if now >= next_close_capture_check:
                capture_due_market_closes(conn, args, required_symbols, captured_closes)
                next_close_capture_check = now + 5.0

            if now >= next_anchor_capture_check:
                capture_due_valuation_anchors(conn, args, pending_anchor_requests, captured_anchors)
                next_anchor_capture_check = now + 5.0

            for msg in drain_messages(conn):
                msg_type = msg.get("type")
                if msg.get("required_symbols") and not fixed_symbols:
                    required_symbols = msg["required_symbols"]
                if msg_type == "quote_request":
                    handle_quote_request(conn, args, msg, ib_bridge)
                elif msg_type == "daily_price_request":
                    handle_daily_price_request(conn, args, msg, ib_bridge)
                elif msg_type == "valuation_anchor_price_request":
                    handle_valuation_anchor_price_request(conn, args, msg, pending_anchor_requests, ib_bridge)
                elif msg_type == "valuation_position_state_request":
                    handle_valuation_position_state_request(conn, args, msg)
                elif msg_type == "valuation_position_set_request":
                    handle_valuation_position_set_request(conn, args, msg)
                elif msg_type == "ping":
                    send_json(conn, {"type": "pong", "source": args.source, "sent_at": now_iso()})
                elif msg_type == "ack":
                    upload_health.record_success(
                        args.source,
                        stage="ws_quote_ack",
                        accepted=int(msg.get("accepted") or 0),
                    )
                elif msg_type == "pong":
                    pass
                elif msg_type == "error":
                    upload_health.record_failure(args.source, stage="ws_server", error=msg.get("error"))
                    print(f"{now_iso()} server error: {msg.get('error')}", file=sys.stderr, flush=True)

            if now >= next_push:
                active = active_symbols(required_symbols, args.mode)
                full = now - last_full_push >= full_interval
                if active:
                    quotes = fetch_current_quotes(active, args, ib_bridge)
                    if minute_archive is not None:
                        minute_archive.record(quotes.values(), datetime.now(ZoneInfo("Asia/Shanghai")))
                    if ib_bridge is not None:
                        ib_bridge.record_reference_quotes(quotes.values(), datetime.now(ZoneInfo("Asia/Shanghai")))
                    selected = select_changed_quotes(quotes, last_sent, full)
                    if selected:
                        seq += 1
                        send_quotes(conn, "quotes", args.source, seq, "", selected)
                        if args.store_sent:
                            store_quotes(args.store_root, selected, "push")
                        print(
                            f"{now_iso()} ws push symbols={len(active)} quotes={len(selected)} full={str(full).lower()}",
                            flush=True,
                        )
                    if full:
                        last_full_push = now
                next_push = now + interval
            sleep_until_stop(0.25)
    finally:
        if minute_archive is not None:
            minute_archive.close()
        if minute_backfill_sync is not None:
            safe_sync_minute_backfill(minute_backfill_sync, args, datetime.now(ZoneInfo("Asia/Shanghai")))
        if ib_bridge is not None:
            ib_bridge.close()
            sleep_until_stop(1.0)
        close_ws(conn)


def create_intraday_minute_archive(args) -> Optional[IntradayMinuteArchive]:
    if not getattr(args, "store_intraday_minutes", False):
        return None
    root = str(getattr(args, "intraday_minute_store_root", "") or "").strip()
    if not root:
        return None
    return IntradayMinuteArchive(root)


def create_intraday_minute_backfill_sync(args):
    if not getattr(args, "store_intraday_minutes", False):
        return None
    root = str(getattr(args, "intraday_minute_store_root", "") or "").strip()
    if not root:
        return None
    return IntradayMinuteBackfillSync(root)


class IntradayMinuteBackfillSync:
    def __init__(self, root: str):
        self.root = str(root or "").strip()
        self.day_key = ""
        self.offset = 0

    def reset_for_day(self, day_key: str) -> None:
        self.day_key = str(day_key or "")
        self.offset = 0

    def sync(self, args, observed_at: datetime, force: bool = False) -> int:
        if not self.root:
            return 0
        local = observed_at.astimezone(ZoneInfo("Asia/Shanghai"))
        day_key = local.strftime("%Y%m%d")
        if day_key != self.day_key:
            self.reset_for_day(day_key)
        path = os.path.join(self.root, day_key, "minute_quotes.csv")
        if not os.path.exists(path):
            return 0
        start_offset = self.offset
        with open(path, "r", encoding="utf-8", newline="") as handle:
            handle.seek(start_offset)
            payload = handle.read()
            end_offset = handle.tell()
        if not payload:
            return 0
        rows = parse_intraday_minute_rows(payload, start_offset == 0)
        upload_rows = build_intraday_minute_backfill_rows(rows)
        if not upload_rows:
            self.offset = end_offset
            return 0
        accepted = upload_intraday_minute_backfill(args, upload_rows, force=force)
        self.offset = end_offset
        return accepted


def parse_intraday_minute_rows(payload: str, include_header: bool) -> list[dict]:
    payload = str(payload or "")
    if not payload.strip():
        return []
    reader = csv.DictReader(io.StringIO(payload), fieldnames=None if include_header else MINUTE_QUOTE_FIELDS)
    return [row for row in reader if row]


def build_intraday_minute_backfill_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        market = str(row.get("market") or "").strip().lower()
        if market != "cn":
            continue
        if len(symbol) < 8 or symbol[:2] not in {"SH", "SZ", "BJ"} or not symbol[2:].isdigit():
            continue
        minute_bucket = str(row.get("minute_bucket") or "").strip()
        minute_label = str(row.get("minute_label") or "").strip()
        trade_date = str(row.get("trading_day") or "").strip()
        minute = f"{trade_date} {minute_label}" if trade_date and minute_label else minute_bucket
        price = float_or_zero(row.get("price"))
        if price <= 0:
            continue
        out.append(
            {
                "symbol": symbol,
                "minute": minute,
                "timestamp": minute_bucket,
                "last_price": price,
                "open": float_or_zero(row.get("open")),
                "high": float_or_zero(row.get("high")),
                "low": float_or_zero(row.get("low")),
                "close": price,
                "volume": float_or_zero(row.get("volume")),
                "amount": float_or_zero(row.get("amount")),
                "source": "intraday_minute_store",
            }
        )
    return out


def upload_intraday_minute_backfill(args, rows: list[dict], force: bool = False) -> int:
    if not rows:
        return 0
    accepted = 0
    for chunk in chunks(rows, 2000):
        payload = json.dumps({"source": neutralize_upload_source(args.source + "-minute-store"), "rows": chunk}, ensure_ascii=False).encode("utf-8")
        req = base.urllib.request.Request(
            args.server.rstrip("/") + "/api/v1/minute-history/backfill",
            data=payload,
            method="POST",
            headers=base.server_request_headers(args.server, args.token, "application/json"),
        )
        with base.open_server_request(
            req,
            max(args.timeout, 20),
            args.origin_ip,
            args.origin_tls_insecure,
            args.origin_ca_file,
        ) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        accepted += int(parsed.get("accepted") or 0)
    if force or accepted:
        print(f"{now_iso()} minute backfill synced rows={len(rows)} accepted={accepted}", flush=True)
    return accepted


def float_or_zero(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def load_valuation_position_states(path: str) -> dict[str, dict]:
    file_path = str(path or "").strip()
    if not file_path or not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    rows = payload.get("states") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {}
    states: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = normalize_symbol(str(row.get("symbol") or ""))
        ratio = float_or_zero(row.get("ratio"))
        if not symbol or ratio <= 0:
            continue
        states[symbol] = {
            "symbol": symbol,
            "ratio": ratio,
            "source": str(row.get("source") or "").strip(),
            "updated_at": str(row.get("updated_at") or "").strip(),
        }
    return states


def save_valuation_position_states(path: str, states: dict[str, dict]) -> None:
    file_path = str(path or "").strip()
    if not file_path:
        return
    folder = os.path.dirname(file_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    rows = []
    for symbol in sorted(states):
        item = states[symbol]
        rows.append(
            {
                "symbol": symbol,
                "ratio": float_or_zero(item.get("ratio")),
                "source": str(item.get("source") or "").strip(),
                "updated_at": str(item.get("updated_at") or "").strip(),
            }
        )
    tmp_path = file_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump({"stored_at": now_iso(), "states": rows}, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, file_path)


def valuation_position_state_rows(path: str) -> list[dict]:
    return [load_valuation_position_states(path)[symbol] for symbol in sorted(load_valuation_position_states(path))]


def apply_valuation_position_updates(path: str, updates: list[dict], source: str) -> list[dict]:
    states = load_valuation_position_states(path)
    applied: list[dict] = []
    updated_at = now_iso()
    row_source = neutralize_source_name(source or "debug_ws")
    for item in updates:
        symbol = normalize_symbol(str(item.get("symbol") or ""))
        ratio = float_or_zero(item.get("ratio"))
        if not symbol or ratio <= 0:
            continue
        row = {
            "symbol": symbol,
            "ratio": ratio,
            "source": row_source,
            "updated_at": updated_at,
        }
        states[symbol] = row
        applied.append(row)
    save_valuation_position_states(path, states)
    return applied


def send_valuation_position_state(conn, args, request_id: str = "") -> None:
    rows = valuation_position_state_rows(args.valuation_position_state_file)
    send_json(
        conn,
        {
            "type": "valuation_position_state",
            "source": args.source,
            "request_id": request_id,
            "sent_at": now_iso(),
            "valuation_position_states": rows,
        },
    )
    print(f"{now_iso()} ws valuation position state answered request_id={request_id} states={len(rows)}", flush=True)


def handle_valuation_position_state_request(conn, args, msg: dict) -> None:
    request_id = str(msg.get("request_id") or "").strip()
    send_valuation_position_state(conn, args, request_id)


def handle_valuation_position_set_request(conn, args, msg: dict) -> None:
    request_id = str(msg.get("request_id") or "").strip()
    updates = msg.get("valuation_position_updates")
    if not isinstance(updates, list) or not updates:
        print(f"{now_iso()} ws valuation position update rejected request_id={request_id} reason=missing_updates", flush=True)
        send_json(
            conn,
            {
                "type": "valuation_position_set_result",
                "source": args.source,
                "request_id": request_id,
                "sent_at": now_iso(),
                "error": "valuation_position_updates is required",
            },
        )
        return
    applied = apply_valuation_position_updates(args.valuation_position_state_file, updates, "debug_ws")
    if not applied:
        print(f"{now_iso()} ws valuation position update rejected request_id={request_id} reason=no_valid_updates", flush=True)
        send_json(
            conn,
            {
                "type": "valuation_position_set_result",
                "source": args.source,
                "request_id": request_id,
                "sent_at": now_iso(),
                "error": "no valid valuation position updates",
            },
        )
        return
    send_json(
        conn,
        {
            "type": "valuation_position_set_result",
            "source": args.source,
            "request_id": request_id,
            "sent_at": now_iso(),
            "valuation_position_states": applied,
        },
    )
    print(
        f"{now_iso()} ws valuation position updated request_id={request_id} "
        f"updates={len(updates)} applied={len(applied)}",
        flush=True,
    )


class PalmmicroHoldingsHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: dict[str, list[list[dict]]] = {}
        self.page_text_parts: list[str] = []
        self.current_table = ""
        self.current_row: Optional[list[dict]] = None
        self.current_cell: Optional[dict] = None
        self.current_link_text: Optional[list[str]] = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "table":
            table_id = attrs_dict.get("id", "")
            if table_id in {"holdingstable", "netvaluehistorytable"}:
                self.current_table = table_id
                self.tables.setdefault(table_id, [])
        elif tag == "tr" and self.current_table:
            self.current_row = []
        elif tag in {"td", "th"} and self.current_row is not None:
            self.current_cell = {"text": [], "title": attrs_dict.get("title", ""), "links": [], "link_texts": []}
        elif tag == "a" and self.current_cell is not None:
            href = attrs_dict.get("href", "")
            if href:
                self.current_cell["links"].append(href)
            self.current_link_text = []

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.current_cell is not None and self.current_row is not None:
            cell = self.current_cell
            cell["text"] = clean_palmmicro_text(" ".join(cell["text"]))
            cell["link_texts"] = [clean_palmmicro_text(item) for item in cell["link_texts"] if clean_palmmicro_text(item)]
            self.current_row.append(cell)
            self.current_cell = None
            self.current_link_text = None
        elif tag == "a" and self.current_cell is not None and self.current_link_text is not None:
            text = clean_palmmicro_text(" ".join(self.current_link_text))
            if text:
                self.current_cell["link_texts"].append(text)
            self.current_link_text = None
        elif tag == "tr" and self.current_table and self.current_row is not None:
            if self.current_row:
                self.tables.setdefault(self.current_table, []).append(self.current_row)
            self.current_row = None
        elif tag == "table" and self.current_table:
            self.current_table = ""

    def handle_data(self, data):
        if data:
            self.page_text_parts.append(data)
        if self.current_cell is not None:
            self.current_cell["text"].append(data)
        if self.current_link_text is not None:
            self.current_link_text.append(data)


def upload_due_holding_snapshots(args) -> int:
    funds = [
        normalize_symbol(item)
        for item in str(args.holdings_upload_funds or "").replace("，", ",").split(",")
        if normalize_symbol(item)
    ]
    if not funds:
        return 0
    accepted = 0
    for fund in funds:
        try:
            snapshot = fetch_palmmicro_holding_snapshot(args, fund)
            if not snapshot:
                continue
            accepted += upload_holding_snapshot(args, snapshot)
        except Exception as exc:
            print(f"{now_iso()} holdings upload failed fund={fund}: {exc}", file=sys.stderr, flush=True)
    return accepted


def fetch_palmmicro_holding_snapshot(args, fund: str) -> Optional[dict]:
    base_url = str(args.palmmicro_base_url or "https://www.palmmicro.com").rstrip("/")
    url = base_url + "/woody/res/holdingscn.php?symbol=" + urllib.parse.quote(fund)
    request = base.urllib.request.Request(
        url,
        headers={
            "User-Agent": "newnavnav-uploader-holdings/0.1",
            "Referer": base_url + "/woody/res/qdiimixcn.php",
            "Cookie": "screenwidth=1600; screenheight=1000",
        },
    )
    with base.urllib.request.urlopen(request, timeout=max(args.timeout, 15)) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parser = PalmmicroHoldingsHTMLParser()
    parser.feed(raw)
    page_text = clean_palmmicro_text(" ".join(parser.page_text_parts))
    if "登录帐号" in page_text:
        print(f"{now_iso()} holdings fetch skipped fund={fund} login_required", flush=True)
        return None
    date_match = re.search(r"基金持仓\s*更新于([0-9]{4}-[0-9]{2}-[0-9]{2})", page_text)
    if not date_match:
        print(f"{now_iso()} holdings fetch skipped fund={fund} missing_date", flush=True)
        return None
    position = 1.0
    position_match = re.search(r"值使用([0-9.]+)", page_text)
    if position_match:
        position = float_or_zero(position_match.group(1)) or 1.0
    holdings = []
    for row in parser.tables.get("holdingstable", []):
        if len(row) < 3:
            continue
        symbol = symbol_from_palmmicro_cell(row[0])
        if not symbol or symbol in {"代码", "全部"}:
            continue
        ratio = parse_palmmicro_float(row[1].get("text", ""))
        if ratio <= 0:
            continue
        holdings.append(
            {
                "symbol": symbol,
                "name": clean_palmmicro_text(row[0].get("title", "")),
                "ratio": ratio,
                "fx_adjust": parse_palmmicro_float(row[6].get("text", "")) if len(row) > 6 else 0,
                "currency": infer_holding_currency(symbol),
            }
        )
    if not holdings:
        print(f"{now_iso()} holdings fetch skipped fund={fund} empty_holdings", flush=True)
        return None
    return {
        "fund_symbol": fund,
        "holding_date": date_match.group(1),
        "position": position,
        "holdings": holdings,
    }


def upload_holding_snapshot(args, snapshot: dict) -> int:
    payload = dict(snapshot)
    payload["source"] = neutralize_upload_source(args.source + "-holdings")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = base.urllib.request.Request(
        args.server.rstrip("/") + "/api/v1/uploads/holdings",
        data=data,
        method="POST",
        headers=base.server_request_headers(args.server, args.token, "application/json"),
    )
    with base.open_server_request(
        request,
        max(args.timeout, 20),
        args.origin_ip,
        args.origin_tls_insecure,
        args.origin_ca_file,
    ) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    if parsed.get("error"):
        raise RuntimeError(parsed["error"])
    accepted = int(parsed.get("accepted") or 0)
    print(
        f"{now_iso()} holdings uploaded fund={payload['fund_symbol']} date={payload['holding_date']} accepted={accepted}",
        flush=True,
    )
    return accepted


def symbol_from_palmmicro_cell(cell: dict) -> str:
    for href in cell.get("links") or []:
        parsed = urllib.parse.urlparse(href)
        query = urllib.parse.parse_qs(parsed.query)
        symbol = normalize_symbol((query.get("symbol") or [""])[0])
        if symbol:
            return symbol
    for text in cell.get("link_texts") or []:
        symbol = normalize_symbol(text)
        if symbol:
            return symbol
    return normalize_symbol(cell.get("text", ""))


def clean_palmmicro_text(value: str) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def parse_palmmicro_float(value: str) -> float:
    return float_or_zero(str(value or "").replace("%", "").strip())


def infer_holding_currency(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    if symbol.isdigit() and len(symbol) == 5:
        return "HKD"
    if len(symbol) >= 3 and symbol[:2] in {"SH", "SZ", "BJ"}:
        return "CNY"
    return "USD"


def refresh_required_symbols(args, current: list[str]) -> list[str]:
    try:
        refreshed = fetch_required_symbols(
            args.server,
            args.token,
            args.timeout,
            args.origin_ip,
            args.origin_tls_insecure,
            args.origin_ca_file,
        )
    except Exception as exc:
        print(f"{now_iso()} required symbols refresh failed: {exc}", file=sys.stderr, flush=True)
        return current
    if refreshed != current:
        print(f"{now_iso()} required symbols refreshed old={len(current)} new={len(refreshed)}", flush=True)
    return refreshed


def fetch_current_quotes(symbols: list[str], args, ib_bridge=None) -> dict[str, dict]:
    quotes: dict[str, dict]
    observed_at = datetime.now(ZoneInfo("Asia/Shanghai"))
    ib_target_symbols: set[str] = set()
    if ib_bridge is not None:
        try:
            ib_target_symbols = ib_bridge.refresh_target_symbols(symbols, observed_at=observed_at)
        except Exception as exc:
            print(f"{now_iso()} ib target symbol refresh failed: {exc}", file=sys.stderr, flush=True)
            ib_target_symbols = set()
    try:
        quotes = fetch_sina_quotes(symbols, args.timeout)
    except Exception as exc:
        print(f"{now_iso()} sina quote fetch failed: {exc}", file=sys.stderr, flush=True)
        quotes = {}
    if ib_bridge is not None:
        try:
            for symbol in ib_target_symbols:
                quotes.pop(symbol, None)
            quotes.update(ib_bridge.live_quotes(symbols, observed_at))
        except Exception as exc:
            print(f"{now_iso()} ib live quote merge failed: {exc}", file=sys.stderr, flush=True)
    return quotes


def handle_quote_request(conn, args, msg: dict, ib_bridge=None) -> None:
    symbols = msg.get("symbols") or msg.get("required_symbols") or []
    request_id = msg.get("request_id") or ""
    reason = msg.get("reason") or "server_request"
    symbols = active_symbols(symbols, args.mode)
    if not symbols:
        print(f"{now_iso()} ws request skipped request_id={request_id} reason={reason} outside_active_session")
        return
    quotes = list(fetch_current_quotes(symbols, args, ib_bridge).values())
    store_quotes(args.store_root, quotes, f"request:{reason}")
    send_quotes(conn, "quote_response", args.source, 0, request_id, quotes)
    print(f"{now_iso()} ws request answered request_id={request_id} symbols={len(symbols)} quotes={len(quotes)}")


def handle_daily_price_request(conn, args, msg: dict, ib_bridge=None) -> None:
    requests = msg.get("daily_price_requests") or []
    request_id = msg.get("request_id") or ""
    prices, warnings = resolve_daily_price_requests(args.store_root, requests)
    if ib_bridge is not None:
        missing_requests = missing_daily_price_requests(prices, requests)
        ib_prices, ib_quote_rows, ib_warnings = ib_bridge.resolve_missing_daily_prices(missing_requests)
        for row in ib_quote_rows:
            target_date = normalize_date(str(row.get("quote_date") or ""))
            if not target_date:
                continue
            store_quotes(
                args.store_root,
                [row],
                f"close:us:{target_date}",
                target_date.replace("-", ""),
            )
        if ib_prices:
            prices.extend(ib_prices)
        warnings.extend(ib_warnings)
    send_json(
        conn,
        {
            "type": "daily_prices",
            "source": args.source,
            "request_id": request_id,
            "sent_at": now_iso(),
            "daily_prices": prices,
            "warnings": warnings,
        },
    )
    print(
        f"{now_iso()} ws daily prices answered request_id={request_id} "
        f"requests={len(requests)} prices={len(prices)} warnings={len(warnings)}",
        flush=True,
    )


def missing_daily_price_requests(prices: list[dict], requests: list[dict]) -> list[dict]:
    found = {
        (
            normalize_symbol(str(item.get("symbol") or "")),
            normalize_date(str(item.get("date") or "")),
        )
        for item in prices
    }
    missing: list[dict] = []
    for item in requests:
        key = (
            normalize_symbol(str(item.get("symbol") or "")),
            normalize_date(str(item.get("date") or "")),
        )
        if key in found:
            continue
        missing.append(item)
    return missing


def handle_valuation_anchor_price_request(conn, args, msg: dict, pending: dict[tuple[str, str, str], dict], ib_bridge=None) -> None:
    requests = msg.get("valuation_anchor_price_requests") or []
    request_id = msg.get("request_id") or ""
    prices, warnings, missing = resolve_valuation_anchor_price_requests(args.store_root, requests)
    if ib_bridge is not None and missing:
        ib_prices, ib_warnings = ib_bridge.resolve_missing_valuation_anchor_prices(missing)
        if ib_prices:
            store_anchor_prices(args.store_root, ib_prices)
            prices.extend(ib_prices)
        warnings.extend(ib_warnings)
        missing = missing_valuation_anchor_requests(prices, missing)
    for item in missing:
        key = anchor_request_key(item)
        if key:
            pending[key] = item
    send_json(
        conn,
        {
            "type": "valuation_anchor_prices",
            "source": args.source,
            "request_id": request_id,
            "sent_at": now_iso(),
            "valuation_anchor_prices": prices,
            "warnings": warnings,
        },
    )
    print(
        f"{now_iso()} ws valuation anchors answered request_id={request_id} "
        f"requests={len(requests)} prices={len(prices)} pending={len(pending)} warnings={len(warnings)}",
        flush=True,
    )


def missing_valuation_anchor_requests(prices: list[dict], requests: list[dict]) -> list[dict]:
    found = {
        anchor_request_key(item)
        for item in prices
        if anchor_request_key(item)
    }
    missing: list[dict] = []
    for item in requests:
        key = anchor_request_key(item)
        if not key or key in found:
            continue
        missing.append(item)
    return missing


def active_symbols(symbols: list[str], mode: str) -> list[str]:
    if mode == "all" or is_cn_session():
        return symbols
    return []


def capture_due_market_closes(conn, args, symbols: list[str], captured: set[tuple[str, str]]) -> None:
    if args.mode == "all" or not symbols:
        return
    now_local = datetime.now().astimezone()
    for market in ("cn", "hk", "jp", "eu", "us", "us_commodity_futures"):
        target_date = due_close_target_date(market)
        if not target_date:
            continue
        target_dt = market_anchor_datetime(target_date, market)
        if market != "us_commodity_futures" and target_dt is not None and not market_close_capture_ready(
            now_local,
            target_dt,
            int(getattr(args, "close_capture_delay_seconds", 0) or 0),
        ):
            continue
        key = (market, target_date)
        if key in captured:
            continue
        market_symbols = symbols_for_market(symbols, market)
        if not market_symbols:
            captured.add(key)
            continue
        stored_symbols = stored_close_symbols(args.store_root, market, target_date)
        pending_symbols = [symbol for symbol in market_symbols if normalize_symbol(symbol) not in stored_symbols]
        if not pending_symbols:
            captured.add(key)
            continue
        if market == "us_commodity_futures" and target_dt is not None:
            probe_pre = max(0, int(getattr(args, "futures_probe_pre_seconds", 0) or 0))
            probe_post = max(
                int(getattr(args, "close_capture_delay_seconds", 0) or 0),
                int(getattr(args, "futures_probe_post_seconds", 0) or 0),
            )
            if probe_window_contains(now_local, target_dt, probe_pre, probe_post):
                try:
                    quotes = list(fetch_sina_quotes(pending_symbols, args.timeout).values())
                except Exception as exc:
                    print(f"{now_iso()} futures close probe failed date={target_date}: {exc}", file=sys.stderr, flush=True)
                    continue
                if quotes:
                    store_quotes(args.store_root, quotes, f"close_probe:{market}:{target_date}", target_date.replace("-", ""))
            if now_local < target_dt + timedelta(seconds=probe_post):
                continue
            requests = [
                {"symbol": symbol, "date": target_date, "market": market, "reason": f"close_probe:{market}:{target_date}"}
                for symbol in pending_symbols
            ]
            uploaded_rows, warnings = resolve_daily_price_requests(args.store_root, requests)
            if warnings:
                print(
                    f"{now_iso()} futures close probe warnings date={target_date} warnings={len(warnings)}",
                    file=sys.stderr,
                    flush=True,
                )
            if not uploaded_rows:
                captured.add(key)
                continue
            send_json(
                conn,
                {
                    "type": "daily_prices",
                    "source": args.source,
                    "request_id": "close-capture-" + str(int(time.time() * 1000)),
                    "sent_at": now_iso(),
                    "daily_prices": uploaded_rows,
                    "warnings": warnings,
                },
            )
            uploaded_symbols = {normalize_symbol(row.get("symbol", "")) for row in uploaded_rows}
            complete = all(normalize_symbol(symbol) in uploaded_symbols or normalize_symbol(symbol) in stored_symbols for symbol in market_symbols)
            if complete or warnings:
                captured.add(key)
            print(
                f"{now_iso()} futures close resolved date={target_date} "
                f"symbols={len(pending_symbols)} daily_prices={len(uploaded_rows)} complete={str(complete).lower()}",
                flush=True,
            )
            continue
        try:
            quotes = list(fetch_sina_quotes(pending_symbols, args.timeout).values())
        except Exception as exc:
            print(f"{now_iso()} close capture failed market={market} date={target_date}: {exc}", file=sys.stderr, flush=True)
            continue
        if not quotes:
            continue
        reason = f"close:{market}:{target_date}"
        store_quotes(args.store_root, quotes, reason, target_date.replace("-", ""))
        uploaded_rows = build_daily_price_rows(quotes, target_date)
        if uploaded_rows:
            send_json(
                conn,
                {
                    "type": "daily_prices",
                    "source": args.source,
                    "request_id": "close-capture-" + str(int(time.time() * 1000)),
                    "sent_at": now_iso(),
                    "daily_prices": uploaded_rows,
                },
            )
        stored_symbols.update(normalize_symbol(quote.get("symbol", "")) for quote in quotes if quote.get("symbol"))
        if all(normalize_symbol(symbol) in stored_symbols for symbol in market_symbols):
            captured.add(key)
        print(
            f"{now_iso()} close captured market={market} date={target_date} "
            f"symbols={len(pending_symbols)} quotes={len(quotes)} daily_prices={len(uploaded_rows)} complete={str(key in captured).lower()}",
            flush=True,
        )


def capture_due_valuation_anchors(
    conn,
    args,
    pending: dict[tuple[str, str, str], dict],
    captured: set[tuple[str, str, str]],
) -> None:
    if not pending:
        return
    now = datetime.now(timezone.utc)
    probe_due: list[dict] = []
    finalize_due: list[dict] = []
    probe_pre = max(0, int(getattr(args, "anchor_probe_pre_seconds", 0) or 0))
    probe_post = max(
        int(getattr(args, "anchor_capture_delay_seconds", 0) or 0),
        int(getattr(args, "anchor_probe_post_seconds", 0) or 0),
    )
    for key, item in list(pending.items()):
        if key in captured:
            pending.pop(key, None)
            continue
        target_at = parse_anchor_datetime(str(item.get("target_at") or ""))
        if target_at is None:
            pending.pop(key, None)
            continue
        target_utc = target_at.astimezone(timezone.utc)
        if anchor_capture_expired(now, target_utc, probe_post):
            if key:
                captured.add(key)
            pending.pop(key, None)
            continue
        if probe_window_contains(now, target_utc, probe_pre, probe_post):
            probe_due.append(item)
        if now >= target_utc + timedelta(seconds=probe_post):
            finalize_due.append(item)
    if probe_due:
        symbols = sorted({normalize_symbol(str(item.get("reference_symbol") or "")) for item in probe_due if item.get("reference_symbol")})
        if symbols:
            try:
                quotes = fetch_sina_quotes(symbols, args.timeout)
            except Exception as exc:
                print(f"{now_iso()} valuation anchor probe failed symbols={','.join(symbols)}: {exc}", file=sys.stderr, flush=True)
                quotes = {}
            for item in probe_due:
                symbol = normalize_symbol(str(item.get("reference_symbol") or ""))
                quote = quotes.get(symbol)
                if not quote or close_price_from_quote(quote) <= 0:
                    continue
                target_at = parse_anchor_datetime(str(item.get("target_at") or ""))
                store_day = anchor_probe_store_day(target_at, item)
                store_quotes(
                    args.store_root,
                    [quote],
                    anchor_probe_reason(item),
                    store_day,
                )

    if not finalize_due:
        return
    rows: list[dict] = []
    warnings: list[str] = []
    probes = resolve_anchor_probe_quotes(args.store_root, finalize_due)
    for item in finalize_due:
        key = anchor_request_key(item)
        best_probe = probes.get(key)
        if best_probe is None:
            warnings.append(
                f"{item.get('reference_symbol')} {item.get('anchor_date')} {item.get('anchor_key')} probe not found"
            )
            if key:
                captured.add(key)
                pending.pop(key, None)
            continue
        row = anchor_price_row_from_request(
            item,
            price=float_or_zero(best_probe.get("price")),
            observed_at=str(best_probe.get("observed_at") or "").strip(),
            source=neutralize_source_name(str(best_probe.get("source") or args.source).strip()),
            capture_status=neutralize_capture_status("captured_probe_window"),
        )
        rows.append(row)
        if key:
            captured.add(key)
            pending.pop(key, None)
    if not rows and not warnings:
        return
    if rows:
        store_anchor_prices(args.store_root, rows)
    send_json(
        conn,
        {
            "type": "valuation_anchor_prices",
            "source": args.source,
            "request_id": "anchor-capture-" + str(int(time.time() * 1000)),
            "sent_at": now_iso(),
            "valuation_anchor_prices": rows,
            "warnings": warnings,
        },
    )
    print(
        f"{now_iso()} valuation anchors finalized rows={len(rows)} warnings={len(warnings)}",
        flush=True,
    )


def stored_close_symbols(root: str, market: str, target_date: str) -> set[str]:
    reason = f"close:{market}:{target_date}"
    symbols: set[str] = set()
    for row in iter_stored_quote_rows(root, target_date):
        if row.get("reason") != reason:
            continue
        symbol = normalize_symbol(row.get("symbol", ""))
        if symbol:
            symbols.add(symbol)
    return symbols


def due_close_target_date(market: str) -> str:
    specs = {
        "cn": ("Asia/Shanghai", dtime(15, 10)),
        "hk": ("Asia/Hong_Kong", dtime(16, 15)),
        "jp": ("Asia/Tokyo", dtime(15, 45)),
        "eu": ("Europe/Berlin", dtime(17, 45)),
        "us": ("America/New_York", dtime(16, 20)),
        "us_commodity_futures": ("America/New_York", dtime(16, 0)),
    }
    timezone, anchor = specs[market]
    now = datetime.now(ZoneInfo(timezone))
    if now.weekday() >= 5:
        return ""
    if now.time() < anchor:
        return ""
    return now.date().isoformat()


def symbols_for_market(symbols: list[str], market: str) -> list[str]:
    out: list[str] = []
    for symbol in symbols:
        symbol_market = symbol_market_code(symbol)
        if symbol_market == market:
            out.append(symbol)
    return out


def symbol_market_code(symbol: str) -> str:
    value = symbol.strip()
    lower = value.lower()
    upper = value.upper()
    if is_cn_symbol(value):
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


def is_weekend() -> bool:
    return datetime.now().weekday() >= 5


def is_cn_session() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= minute <= 15 * 60


def is_cn_symbol(symbol: str) -> bool:
    upper = symbol.upper()
    return upper.startswith("NF_") or (len(upper) == 8 and upper[:2] in {"SH", "SZ", "BJ"} and upper[2:].isdigit())


def is_us_like_symbol(symbol: str) -> bool:
    if not symbol or len(symbol) > 10:
        return False
    if not ("A" <= symbol[0] <= "Z"):
        return False
    return all(("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch == "." for ch in symbol)


def current_day() -> str:
    return datetime.now().strftime("%Y%m%d")


def resolve_daily_price_requests(root: str, requests: list[dict]) -> tuple[list[dict], list[str]]:
    prices: list[dict] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    entries: list[dict] = []
    for item in requests:
        symbol = normalize_symbol(str(item.get("symbol") or ""))
        target_date = normalize_date(str(item.get("date") or ""))
        market = str(item.get("market") or "").strip().lower()
        if not symbol or not target_date:
            warnings.append(f"invalid daily price request: {item}")
            continue
        key = (symbol, target_date)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"symbol": symbol, "target_date": target_date, "market": market})
    found = resolve_daily_prices_from_quote_store(root, entries)
    for entry in entries:
        symbol = entry["symbol"]
        target_date = entry["target_date"]
        key = (symbol, target_date)
        row = found.get(key)
        if row is None:
            warnings.append(f"{symbol} {target_date} stored close not found")
            continue
        prices.append(row)
    return prices, warnings


def resolve_daily_prices_from_quote_store(root: str, entries: list[dict]) -> dict[tuple[str, str], dict]:
    found: dict[tuple[str, str], dict] = {}
    by_date: dict[str, list[dict]] = {}
    for entry in entries:
        by_date.setdefault(entry["target_date"], []).append(entry)
    for target_date, date_entries in by_date.items():
        by_symbol = {entry["symbol"]: entry for entry in date_entries}
        candidates: dict[tuple[str, str], list[tuple[tuple[int, float], dict]]] = {
            (entry["symbol"], entry["target_date"]): [] for entry in date_entries
        }
        targets = {
            (entry["symbol"], entry["target_date"]): market_anchor_datetime(entry["target_date"], entry["market"])
            for entry in date_entries
        }
        for row in iter_stored_quote_rows(root, target_date):
            symbol = normalize_symbol(row.get("symbol", ""))
            entry = by_symbol.get(symbol)
            if not entry:
                continue
            key = (entry["symbol"], entry["target_date"])
            candidate = daily_price_candidate_from_quote_row(row, entry["target_date"], targets.get(key))
            if candidate is not None:
                candidates[key].append(candidate)
        for entry in date_entries:
            key = (entry["symbol"], entry["target_date"])
            if candidates[key]:
                _, best = min(candidates[key], key=lambda item: item[0])
                found[key] = daily_price_row_from_quote_row(entry["symbol"], entry["target_date"], best)
                continue
            target_dt = targets.get(key)
            fallback = find_stored_reference_minute_price(root, entry["symbol"], entry["target_date"], entry["market"], target_dt)
            if fallback is not None:
                found[key] = fallback
    return found


def daily_price_candidate_from_quote_row(row: dict, target_date: str, target_dt: Optional[datetime]) -> Optional[tuple[tuple[int, float], dict]]:
    price = close_price_from_row(row)
    if price <= 0:
        return None
    stored_at = parse_stored_at(row.get("stored_at", ""))
    source_at = parse_quote_row_datetime(row)
    quote_date = normalize_date(row.get("quote_date", ""))
    rank = stored_daily_price_rank(row)
    if source_at and target_dt:
        return ((rank, abs((source_at - target_dt).total_seconds())), row)
    if stored_at and target_dt:
        return ((rank, abs((stored_at - target_dt).total_seconds())), row)
    if quote_date == target_date:
        return ((rank, 0), row)
    return None


def daily_price_row_from_quote_row(symbol: str, target_date: str, row: dict) -> dict:
    close = close_price_from_row(row)
    return {
        "symbol": symbol,
        "date": target_date,
        "close": close,
        "adj_close": close,
        "source": neutralize_source_name(f"mac_store:{row.get('source') or row.get('reason') or 'quote_store'}"),
    }


def resolve_valuation_anchor_price_requests(root: str, requests: list[dict]) -> tuple[list[dict], list[str], list[dict]]:
    prices: list[dict] = []
    warnings: list[str] = []
    missing: list[dict] = []
    probe_candidates: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in requests:
        key = anchor_request_key(item)
        if not key:
            warnings.append(f"invalid valuation anchor request: {item}")
            continue
        if key in seen:
            continue
        seen.add(key)
        row = find_stored_anchor_store_price(root, item)
        if row is None:
            probe_candidates.append(item)
            continue
        prices.append(row)
    probes = resolve_anchor_probe_quotes(root, probe_candidates)
    for item in probe_candidates:
        key = anchor_request_key(item)
        probe = probes.get(key)
        if probe is None:
            missing.append(item)
            continue
        prices.append(anchor_price_from_probe(item, probe))
    return prices, warnings, missing


def find_stored_anchor_price(root: str, request: dict) -> Optional[dict]:
    row = find_stored_anchor_store_price(root, request)
    if row is not None:
        return row
    probe = find_stored_anchor_probe_quote(root, request)
    if probe is None:
        return None
    return anchor_price_from_probe(request, probe)


def find_stored_anchor_store_price(root: str, request: dict) -> Optional[dict]:
    key = anchor_request_key(request)
    if not key:
        return None
    candidates: list[tuple[tuple[int, float], dict]] = []
    target_at = parse_anchor_datetime(str(request.get("target_at") or ""))
    for row in iter_stored_anchor_rows(root, request):
        row_key = anchor_request_key(row)
        if row_key != key:
            continue
        price = float_or_zero(row.get("price"))
        if price <= 0:
            continue
        observed_at = parse_anchor_datetime(str(row.get("observed_at") or ""))
        row_target = parse_anchor_datetime(str(row.get("target_at") or ""))
        if target_at and observed_at:
            delta = abs((observed_at - target_at).total_seconds())
        elif target_at and row_target:
            delta = abs((row_target - target_at).total_seconds())
        else:
            delta = 0
        # Prefer auditable market-event times. stored_at is only the local file
        # append time and must never be promoted into observed_at.
        candidates.append(((0 if observed_at else 1, delta), row))
    if not candidates:
        return None
    _, best = min(candidates, key=lambda item: item[0])
    return {
        "fund_symbol": normalize_symbol(best.get("fund_symbol", "")),
        "anchor_date": normalize_date(best.get("anchor_date", "")),
        "anchor_key": str(best.get("anchor_key") or "").strip(),
        "reference_symbol": normalize_symbol(best.get("reference_symbol", "")),
        "target_at": str(best.get("target_at") or request.get("target_at") or "").strip(),
        "target_timezone": str(best.get("target_timezone") or request.get("target_timezone") or "").strip(),
        "observed_at": str(best.get("observed_at") or "").strip(),
        "price": float_or_zero(best.get("price")),
        "weight": float_or_zero(best.get("weight") or request.get("weight")),
        "source": neutralize_source_name(str(best.get("source") or "mac_anchor_store").strip()),
        "capture_status": neutralize_capture_status(str(best.get("capture_status") or "stored").strip()),
    }


def anchor_price_from_probe(request: dict, probe: dict) -> dict:
    return {
        "fund_symbol": normalize_symbol(str(request.get("fund_symbol") or "")),
        "anchor_date": normalize_date(str(request.get("anchor_date") or "")),
        "anchor_key": str(request.get("anchor_key") or "").strip(),
        "reference_symbol": normalize_symbol(str(request.get("reference_symbol") or "")),
        "target_at": str(request.get("target_at") or "").strip(),
        "target_timezone": str(request.get("target_timezone") or "").strip(),
        "observed_at": str(probe.get("observed_at") or "").strip(),
        "price": float_or_zero(probe.get("price")),
        "weight": float_or_zero(request.get("weight")),
        "source": neutralize_source_name(str(probe.get("source") or "mac_anchor_probe").strip()),
        "capture_status": neutralize_capture_status("stored_probe_window"),
    }


def resolve_anchor_probe_quotes(root: str, requests: list[dict]) -> dict[tuple[str, str, str], dict]:
    found: dict[tuple[str, str, str], dict] = {}
    by_date: dict[str, list[dict]] = {}
    for item in requests:
        key = anchor_request_key(item)
        target_at = parse_anchor_datetime(str(item.get("target_at") or ""))
        anchor_date = normalize_date(str(item.get("anchor_date") or ""))
        if not key or target_at is None or not anchor_date:
            continue
        by_date.setdefault(anchor_date, []).append(item)
    for anchor_date, date_requests in by_date.items():
        by_reason: dict[str, list[dict]] = {}
        targets: dict[tuple[str, str, str], datetime] = {}
        candidates: dict[tuple[str, str, str], list[tuple[float, dict]]] = {}
        for item in date_requests:
            key = anchor_request_key(item)
            by_reason.setdefault(anchor_probe_reason(item), []).append(item)
            targets[key] = parse_anchor_datetime(str(item.get("target_at") or ""))
            candidates[key] = []
        for row in iter_stored_quote_rows(root, anchor_date):
            matches = by_reason.get(str(row.get("reason") or "").strip())
            if not matches:
                continue
            price = close_price_from_row(row)
            if price <= 0:
                continue
            for item in matches:
                key = anchor_request_key(item)
                target_at = targets.get(key)
                if target_at is None:
                    continue
                observed_at = parse_quote_row_datetime(row, target_at)
                if observed_at is None:
                    continue
                delta = abs((observed_at.astimezone(timezone.utc) - target_at.astimezone(timezone.utc)).total_seconds())
                candidates[key].append(
                    (
                        delta,
                        {
                            "price": price,
                            "observed_at": observed_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                            "stored_at": str(row.get("stored_at") or "").strip(),
                            "source": neutralize_source_name(str(row.get("source") or "quote_store").strip()),
                        },
                    )
                )
        for key, key_candidates in candidates.items():
            if key_candidates:
                _, found[key] = min(key_candidates, key=lambda item: item[0])
    return found


def find_stored_anchor_probe_quote(root: str, request: dict) -> Optional[dict]:
    target_at = parse_anchor_datetime(str(request.get("target_at") or ""))
    if target_at is None:
        return None
    reason = anchor_probe_reason(request)
    candidates: list[tuple[float, dict]] = []
    for row in iter_stored_quote_rows(root, normalize_date(str(request.get("anchor_date") or ""))):
        if str(row.get("reason") or "").strip() != reason:
            continue
        price = close_price_from_row(row)
        if price <= 0:
            continue
        observed_at = parse_quote_row_datetime(row, target_at)
        if observed_at is None:
            continue
        delta = abs((observed_at.astimezone(timezone.utc) - target_at.astimezone(timezone.utc)).total_seconds())
        candidates.append(
            (
                delta,
                {
                    "price": price,
                    "observed_at": observed_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                    "stored_at": str(row.get("stored_at") or "").strip(),
                    "source": neutralize_source_name(str(row.get("source") or "quote_store").strip()),
                },
            )
        )
    if not candidates:
        return None
    _, best = min(candidates, key=lambda item: item[0])
    return best


def iter_stored_anchor_rows(root: str, request: dict):
    anchor_date = normalize_date(str(request.get("anchor_date") or ""))
    folders = set(candidate_store_days(anchor_date)) if anchor_date else set()
    target_at = parse_anchor_datetime(str(request.get("target_at") or ""))
    if target_at:
        sh_target = target_at.astimezone(ZoneInfo("Asia/Shanghai"))
        for offset in (-1, 0, 1):
            folders.add((sh_target.date() + timedelta(days=offset)).strftime("%Y%m%d"))
    for folder in sorted(folders):
        path = os.path.join(root, folder, "valuation_anchor_prices.csv")
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as handle:
            yield from csv.DictReader(handle)


def anchor_request_key(item: dict) -> tuple[str, str, str]:
    fund_symbol = normalize_symbol(str(item.get("fund_symbol") or ""))
    anchor_date = normalize_date(str(item.get("anchor_date") or ""))
    anchor_key = str(item.get("anchor_key") or "").strip()
    if not fund_symbol or not anchor_date or not anchor_key:
        return tuple()
    return (fund_symbol, anchor_date, anchor_key)


def anchor_price_row_from_request(
    item: dict,
    price: float,
    observed_at: str,
    source: str,
    capture_status: str,
) -> dict:
    return {
        "fund_symbol": normalize_symbol(str(item.get("fund_symbol") or "")),
        "anchor_date": normalize_date(str(item.get("anchor_date") or "")),
        "anchor_key": str(item.get("anchor_key") or "").strip(),
        "reference_symbol": normalize_symbol(str(item.get("reference_symbol") or "")),
        "target_at": str(item.get("target_at") or "").strip(),
        "target_timezone": str(item.get("target_timezone") or "").strip(),
        "target_beijing_time": str(item.get("target_beijing_time") or "").strip(),
        "weight": float_or_zero(item.get("weight")),
        "price": price,
        "observed_at": observed_at,
        "source": neutralize_source_name(source),
        "capture_status": neutralize_capture_status(capture_status),
    }


def store_anchor_prices(root: str, rows: list[dict]) -> None:
    if not root or not rows:
        return
    by_day: dict[str, list[dict]] = {}
    for row in rows:
        folder = anchor_store_day(row)
        by_day.setdefault(folder, []).append(row)
    for day, day_rows in by_day.items():
        folder = os.path.join(root, day)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "valuation_anchor_prices.csv")
        exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ANCHOR_PRICE_FIELDS)
            if not exists:
                writer.writeheader()
            stored_at = now_iso()
            for row in day_rows:
                out = {field: row.get(field, "") for field in ANCHOR_PRICE_FIELDS}
                out["stored_at"] = stored_at
                writer.writerow(out)


def anchor_store_day(row: dict) -> str:
    target_at = parse_anchor_datetime(str(row.get("target_at") or ""))
    if target_at:
        return target_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    anchor_date = normalize_date(str(row.get("anchor_date") or ""))
    if anchor_date:
        return anchor_date.replace("-", "")
    return current_day()


def anchor_probe_store_day(target_at: Optional[datetime], item: dict) -> str:
    if target_at is not None:
        return target_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    anchor_date = normalize_date(str(item.get("anchor_date") or ""))
    if anchor_date:
        return anchor_date.replace("-", "")
    return current_day()


def anchor_probe_reason(item: dict) -> str:
    fund_symbol = normalize_symbol(str(item.get("fund_symbol") or ""))
    anchor_date = normalize_date(str(item.get("anchor_date") or ""))
    anchor_key = str(item.get("anchor_key") or "").strip()
    return f"anchor_probe:{fund_symbol}:{anchor_date}:{anchor_key}"


def find_stored_daily_price(root: str, symbol: str, target_date: str, market: str) -> Optional[dict]:
    target_dt = market_anchor_datetime(target_date, market)
    candidates: list[tuple[tuple[int, float], dict]] = []
    for row in iter_stored_quote_rows(root, target_date):
        if normalize_symbol(row.get("symbol", "")) != symbol:
            continue
        price = close_price_from_row(row)
        if price <= 0:
            continue
        stored_at = parse_stored_at(row.get("stored_at", ""))
        source_at = parse_quote_row_datetime(row, target_dt)
        quote_date = normalize_date(row.get("quote_date", ""))
        if source_at and target_dt:
            delta = abs((source_at - target_dt).total_seconds())
            source_rank = stored_daily_price_rank(row)
            candidates.append(((source_rank, delta), row))
            continue
        if stored_at and target_dt:
            delta = abs((stored_at - target_dt).total_seconds())
            source_rank = stored_daily_price_rank(row)
            candidates.append(((source_rank, delta), row))
            continue
        if quote_date == target_date:
            candidates.append(((stored_daily_price_rank(row), 0), row))
    if not candidates:
        return find_stored_reference_minute_price(root, symbol, target_date, market, target_dt)
    _, best = min(candidates, key=lambda item: item[0])
    close = close_price_from_row(best)
    return {
        "symbol": symbol,
        "date": target_date,
        "close": close,
        "adj_close": close,
        "source": neutralize_source_name(f"mac_store:{best.get('source') or best.get('reason') or 'quote_store'}"),
    }


def find_stored_reference_minute_price(root: str, symbol: str, target_date: str, market: str, target_dt: Optional[datetime]) -> Optional[dict]:
    if target_dt is None:
        return None
    candidates: list[tuple[float, dict]] = []
    for row in iter_stored_reference_minute_rows(root, target_date):
        if normalize_symbol(row.get("symbol", "")) != symbol:
            continue
        price = float_or_zero(row.get("close") or row.get("last_price"))
        if price <= 0:
            continue
        timestamp = parse_anchor_datetime(str(row.get("timestamp") or ""))
        if timestamp is None:
            continue
        delta = abs((timestamp - target_dt).total_seconds())
        if delta > 1800:
            continue
        candidates.append((delta, row))
    if not candidates:
        return None
    _, best = min(candidates, key=lambda item: item[0])
    close = float_or_zero(best.get("close") or best.get("last_price"))
    source = neutralize_source_name(str(best.get("source") or "reference_1m").strip())
    return {
        "symbol": symbol,
        "date": target_date,
        "close": close,
        "adj_close": close,
        "source": f"mac_store:{source}",
    }


def iter_stored_quote_rows(root: str, target_date: str):
    for folder in candidate_store_days(target_date):
        path = os.path.join(root, folder, "quotes.csv")
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as handle:
            yield from csv.DictReader(handle)


def iter_stored_reference_minute_rows(root: str, target_date: str):
    for folder in candidate_store_days(target_date):
        path = os.path.join(root, folder, "ibkr_reference_1m.csv")
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as handle:
            yield from csv.DictReader(handle)


def candidate_store_days(target_date: str) -> list[str]:
    base = date.fromisoformat(target_date)
    days = [base + timedelta(days=offset) for offset in (-1, 0, 1, 2)]
    return [day.strftime("%Y%m%d") for day in days]


def market_anchor_datetime(target_date: str, market: str) -> Optional[datetime]:
    try:
        day = date.fromisoformat(target_date)
    except ValueError:
        return None
    local_tz = datetime.now().astimezone().tzinfo
    specs = {
        "cn": ("Asia/Shanghai", dtime(15, 10)),
        "hk": ("Asia/Hong_Kong", dtime(16, 15)),
        "jp": ("Asia/Tokyo", dtime(15, 45)),
        "eu": ("Europe/Berlin", dtime(17, 45)),
        "us": ("America/New_York", dtime(16, 20)),
        "us_commodity_futures": ("America/New_York", dtime(16, 0)),
    }
    timezone, anchor = specs.get(market, ("Asia/Shanghai", dtime(15, 10)))
    aware = datetime.combine(day, anchor, ZoneInfo(timezone))
    return aware.astimezone(local_tz)


def market_close_capture_ready(now_local: datetime, target_dt: datetime, delay_seconds: int) -> bool:
    return now_local >= target_dt + timedelta(seconds=max(0, delay_seconds))


def probe_window_contains(now_at: datetime, target_at: datetime, pre_seconds: int, post_seconds: int) -> bool:
    pre_seconds = max(0, pre_seconds)
    post_seconds = max(0, post_seconds)
    return target_at - timedelta(seconds=pre_seconds) <= now_at <= target_at + timedelta(seconds=post_seconds)


def anchor_capture_expired(now_utc: datetime, target_at_utc: datetime, post_seconds: int) -> bool:
    post_seconds = max(0, post_seconds)
    delta = (now_utc - target_at_utc).total_seconds()
    return delta > max(post_seconds, 180) + 3600


def close_price_from_row(row: dict) -> float:
    source = str(row.get("source") or "").lower()
    prev_close = float_or_zero(row.get("prev_close"))
    price = float_or_zero(row.get("price"))
    if source == "sina_us_after_hours" and prev_close > 0:
        return prev_close
    return price if price > 0 else prev_close


def close_price_from_quote(quote: dict) -> float:
    if not quote:
        return 0.0
    source = str(quote.get("source") or "").lower()
    prev_close = float_or_zero(quote.get("prev_close"))
    price = float_or_zero(quote.get("price"))
    if source == "sina_us_after_hours" and prev_close > 0:
        return prev_close
    return price if price > 0 else prev_close


def build_daily_price_rows(quotes: list[dict], target_date: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for quote in quotes:
        symbol = normalize_symbol(str(quote.get("symbol") or ""))
        if not symbol:
            continue
        price = close_price_from_quote(quote)
        if price <= 0:
            continue
        key = (symbol, target_date)
        if key in seen:
            continue
        seen.add(key)
        source = str(quote.get("source") or "quote_store").strip() or "quote_store"
        rows.append(
            {
                "symbol": symbol,
                "date": target_date,
                "close": price,
                "adj_close": price,
                "source": neutralize_source_name(f"ws_close_capture:{source}"),
            }
        )
    return rows


def parse_stored_at(value: str) -> Optional[datetime]:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def parse_quote_row_datetime(row: dict, target_at: Optional[datetime] = None) -> Optional[datetime]:
    quote_date = normalize_date(str(row.get("quote_date") or ""))
    quote_time = str(row.get("quote_time") or "").strip()
    quote_timezone = str(row.get("quote_timezone") or "").strip()
    if not quote_date or not quote_time or not quote_timezone:
        return None
    value = f"{quote_date}T{quote_time}"
    try:
        parsed = datetime.fromisoformat(value)
        declared = parsed.replace(tzinfo=ZoneInfo(quote_timezone))
    except Exception:
        return None

    source = str(row.get("source") or "").strip().lower()
    if target_at is None or source not in {"sina_us", "sina_us_after_hours"}:
        return declared

    # Sina's gb_* payload carries a Shanghai calendar date/time in the current
    # ingestion path, while legacy quote_store rows label that wall clock as
    # America/New_York. Compare both interpretations to the requested market
    # event and retain the actual quote timestamp nearest the target. Never use
    # stored_at here: it records fetch/storage time, not market event time.
    shanghai = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    target_utc = target_at.astimezone(timezone.utc)
    return min(
        (declared, shanghai),
        key=lambda item: abs((item.astimezone(timezone.utc) - target_utc).total_seconds()),
    )


def parse_anchor_datetime(value: str) -> Optional[datetime]:
    value = value.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def stored_daily_price_rank(row: dict) -> int:
    reason = str(row.get("reason") or "").strip().lower()
    source = str(row.get("source") or "").strip().lower()
    source = neutralize_source_name(source)
    if reason.startswith("close:") or reason.startswith("close_probe:") or source in {"daily", "reference_1m"} or source.startswith("qmt_"):
        return 0
    if reason in {"push", "once"} or reason.startswith("request:"):
        return 1
    return 2


def normalize_symbol(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    lower = value.lower()
    if lower.startswith("fx_"):
        return lower
    if lower.startswith("hf_"):
        return "HF_" + value[3:].upper()
    if lower.startswith("nf_"):
        return "nf_" + value[3:].upper()
    if lower.startswith("znb_"):
        return "znb_" + value[4:].upper()
    if lower.startswith("gb_"):
        return "gb_" + value[3:].lower()
    if len(value) >= 3 and value[:2].upper() in {"SH", "SZ", "BJ"}:
        return value[:2].upper() + value[2:]
    return value.upper()


def normalize_date(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return ""


def float_or_zero(value) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def select_changed_quotes(quotes: dict[str, dict], last_sent: dict[str, tuple], full: bool) -> list[dict]:
    selected: list[dict] = []
    for symbol, quote in quotes.items():
        fingerprint = (
            quote.get("price"),
            quote.get("prev_close"),
            quote.get("quote_date"),
            quote.get("quote_time"),
            quote.get("change_pct"),
        )
        if full or last_sent.get(symbol) != fingerprint:
            selected.append(quote)
            last_sent[symbol] = fingerprint
    return selected


def send_quotes(conn, msg_type: str, source: str, seq: int, request_id: str, quotes: list[dict]) -> int:
    sent = 0
    for batch in chunks(quotes, 500):
        send_json(
            conn,
            {
                "type": msg_type,
                "source": source,
                "seq": seq,
                "request_id": request_id,
                "sent_at": now_iso(),
                "quotes": batch,
            },
        )
        sent += 1
    return sent


def wait_for_acks(conn, count: int, timeout: float) -> int:
    accepted = 0
    deadline = time.time() + timeout
    while count > 0 and time.time() < deadline:
        msg = recv_json(conn, timeout=min(0.5, max(0.05, deadline - time.time())))
        if not msg:
            continue
        if msg.get("type") == "ack":
            accepted += int(msg.get("accepted") or 0)
            count -= 1
        elif msg.get("type") == "error":
            raise RuntimeError(msg.get("error") or "server returned ws error")
    if count > 0:
        raise RuntimeError("timed out waiting for ws ack")
    return accepted


def store_quotes(root: str, quotes: list[dict], reason: str, store_day: str = "") -> None:
    if not root or not quotes:
        return
    day = store_day or datetime.now().strftime("%Y%m%d")
    folder = os.path.join(root, day)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "quotes.csv")
    exists = os.path.exists(path)
    fields = [
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
        "quote_timezone",
        "source",
        "source_symbol",
        "quote_session",
    ]
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        stored_at = now_iso()
        for quote in quotes:
            row = {field: quote.get(field, "") for field in fields}
            row["stored_at"] = stored_at
            row["reason"] = reason
            writer.writerow(row)


def connect_ws(
    server: str,
    token: str,
    source: str,
    timeout: float,
    origin_ip: str = "",
    origin_tls_insecure: bool = False,
    origin_ca_file: str = "",
):
    parsed = urllib.parse.urlparse(server)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if scheme == "wss" else 80)
    connect_host = origin_ip.strip() or host
    query = urllib.parse.urlencode({"source": source})
    path = "/api/v1/uploads/quotes/ws?" + query
    sock = socket.create_connection((connect_host, port), timeout=timeout)
    if scheme == "wss":
        context = base.origin_ssl_context(origin_tls_insecure, origin_ca_file)
        sock = context.wrap_socket(sock, server_hostname=host)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    host_header = host if parsed.port is None else f"{host}:{port}"
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        f"Origin: {'https' if scheme == 'wss' else 'http'}://{host_header}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"X-Upload-Token: {token}\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response, leftover = read_http_response(sock)
    if " 101 " not in response.splitlines()[0]:
        raise RuntimeError("websocket upgrade failed: " + response.splitlines()[0])
    expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    if expected not in response:
        raise RuntimeError("websocket accept header mismatch")
    return BufferedSocket(sock, leftover)


class BufferedSocket:
    def __init__(self, sock, buffer: bytes):
        self.sock = sock
        self.buffer = bytearray(buffer)

    def sendall(self, data: bytes) -> None:
        self.sock.sendall(data)

    def recv(self, size: int) -> bytes:
        if self.buffer:
            out = bytes(self.buffer[:size])
            del self.buffer[:size]
            return out
        return self.sock.recv(size)

    def settimeout(self, timeout: float) -> None:
        self.sock.settimeout(timeout)

    def gettimeout(self):
        return self.sock.gettimeout()

    def close(self) -> None:
        self.sock.close()


def read_http_response(sock) -> tuple[str, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    head, sep, rest = data.partition(b"\r\n\r\n")
    return (head + sep).decode("iso-8859-1", errors="replace"), rest


def send_json(sock, payload: dict) -> None:
    send_frame(sock, json.dumps(payload, ensure_ascii=False).encode("utf-8"), 0x1)


def recv_json(sock, timeout: Optional[float] = None) -> Optional[dict]:
    previous = sock.gettimeout()
    if timeout is not None:
        sock.settimeout(timeout)
    try:
        payload, opcode = recv_frame(sock)
    except socket.timeout:
        return None
    finally:
        if timeout is not None:
            sock.settimeout(previous)
    if opcode == 0x8:
        raise RuntimeError("websocket closed")
    if opcode == 0x9:
        send_frame(sock, payload, 0xA)
        return None
    if opcode == 0xA:
        return None
    if opcode != 0x1:
        return None
    return json.loads(payload.decode("utf-8"))


def drain_messages(sock) -> list[dict]:
    messages: list[dict] = []
    while True:
        msg = recv_json(sock, timeout=0.01)
        if msg is None:
            break
        messages.append(msg)
    return messages


def send_frame(sock, payload: bytes, opcode: int) -> None:
    first = 0x80 | opcode
    mask_bit = 0x80
    length = len(payload)
    header = bytearray([first])
    if length < 126:
        header.append(mask_bit | length)
    elif length <= 0xFFFF:
        header.append(mask_bit | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(mask_bit | 127)
        header.extend(struct.pack("!Q", length))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytes(payload[i] ^ mask[i % 4] for i in range(length))
    sock.sendall(bytes(header) + masked)


def recv_frame(sock) -> tuple[bytes, int]:
    header = read_exact(sock, 2)
    first, second = header
    opcode = first & 0x0F
    masked = second & 0x80
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(sock, 8))[0]
    mask = read_exact(sock, 4) if masked else b""
    payload = read_exact(sock, length) if length else b""
    if masked:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(length))
    return payload, opcode


def read_exact(sock, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise RuntimeError("socket closed")
        data += chunk
    return data


def close_ws(sock) -> None:
    try:
        send_frame(sock, b"", 0x8)
    except Exception:
        pass
    try:
        sock.close()
    except Exception:
        pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def quote_observed_at(quote: Optional[dict]) -> str:
    if not quote:
        return ""
    observed_at = str(quote.get("observed_at") or "").strip()
    if observed_at:
        return observed_at
    quote_date = normalize_date(str(quote.get("quote_date") or ""))
    quote_time = str(quote.get("quote_time") or "").strip()
    quote_timezone = str(quote.get("quote_timezone") or "").strip()
    if not quote_date or not quote_time or not quote_timezone:
        return ""
    try:
        parsed = datetime.fromisoformat(f"{quote_date}T{quote_time}").replace(tzinfo=ZoneInfo(quote_timezone))
    except Exception:
        return ""
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())

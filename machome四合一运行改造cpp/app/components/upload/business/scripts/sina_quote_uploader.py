#!/usr/bin/env python3
import argparse
import http.client
import json
import os
import re
import signal
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


DEFAULT_INTERVAL = 5.0
DEFAULT_TIMEOUT = 8.0
BATCH_SIZE = 80
STOP = False
CHROME_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)
SINA_REQUEST_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": CHROME_USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
YAHOO_REQUEST_HEADERS = {
    "User-Agent": CHROME_USER_AGENT,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
YAHOO_FUTURE_SYMBOLS = {
    "HF_CL": "CL=F",
    "HF_GC": "GC=F",
    "HF_SI": "SI=F",
    "HF_ZN": "ZN=F",
    "HF_NQ": "NQ=F",
    "HF_ES": "ES=F",
}


def neutralize_source_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    replacements = (
        ("mac_store:ibkr_anchor_1m:", "mac_store:anchor_1m:"),
        ("mac_store:ibkr_1m:", "mac_store:anchor_1m:"),
        ("mac_store:ibkr_us_overnight_live:", "mac_store:us_overnight_live:"),
        ("mac_store:ibkr_us_overnight_1m:", "mac_store:us_overnight_1m:"),
        ("mac_store:ibkr_us_close_1m", "mac_store:daily"),
        ("mac_store:ibkr_future_1m", "mac_store:daily"),
        ("mac_store:ibkr_hk_1m", "mac_store:daily"),
        ("mac_store:ibkr_daily", "mac_store:daily"),
        ("ibkr_anchor_1m:", "anchor_1m:"),
        ("ibkr_1m:", "anchor_1m:"),
        ("ibkr_us_overnight_live:", "us_overnight_live:"),
        ("ibkr_us_overnight_1m:", "us_overnight_1m:"),
        ("ibkr_us_close_1m", "daily"),
        ("ibkr_us_live", "us_live"),
        ("ibkr_future_1m", "daily"),
        ("ibkr_hk_1m", "daily"),
        ("ibkr_daily", "daily"),
        ("ibkr_reference_1m", "reference_1m"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def neutralize_upload_source(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    replacements = (
        ("home-mac-ibkr-anchor", "home-mac-anchor"),
        ("home-mac-ibkr-1m", "home-mac-1m"),
        ("home-mac-ibkr", "home-mac"),
        ("-ib-reference", "-reference"),
        ("ibkr", "daily"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def neutralize_capture_status(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    replacements = (
        ("ibkr_local_confirmed", "anchor_local_confirmed"),
        ("ibkr_backfill", "anchor_backfill"),
        ("ibkr_futures_backfill", "futures_backfill"),
        ("ibkr_explicit_backfill", "daily_backfill"),
        ("ibkr", "daily"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def server_request_headers(server: str, token: str, content_type: str = "") -> dict[str, str]:
    headers = {
        "X-Upload-Token": token,
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": server.rstrip("/") + "/",
        "User-Agent": CHROME_USER_AGENT,
    }
    data_view_token = str(os.getenv("NNN_DATA_VIEW_TOKEN", os.getenv("DATA_VIEW_TOKEN", ""))).strip()
    if data_view_token:
        headers["X-Data-View-Token"] = data_view_token
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def main() -> int:
    load_env_file(".sina-uploader.env")
    parser = argparse.ArgumentParser(description="Fetch Sina quotes locally and upload to newnavnav.")
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument(
        "--origin-tls-insecure",
        action="store_true",
        default=is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")),
    )
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--source", default=os.getenv("NNN_UPLOAD_SOURCE", "home-mac-py"))
    parser.add_argument("--interval", default=os.getenv("NNN_UPLOAD_INTERVAL", f"{DEFAULT_INTERVAL}s"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", DEFAULT_TIMEOUT)))
    parser.add_argument("--symbols", default=os.getenv("NNN_UPLOAD_SYMBOLS", ""))
    parser.add_argument("--once", action="store_true", default=is_true(os.getenv("NNN_UPLOAD_ONCE", "")))
    args = parser.parse_args()

    if not args.token.strip():
        print("NNN_UPLOAD_TOKEN or --token is required", file=sys.stderr)
        return 2

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    interval = parse_duration(args.interval, DEFAULT_INTERVAL)
    run_once(args)
    while not args.once and not STOP:
        sleep_until_stop(interval)
        if not STOP:
            run_once(args)
    return 0


def run_once(args) -> None:
    try:
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
        quotes = fetch_sina_quotes(symbols, args.timeout)
        accepted, enabled = upload_quotes(
            args.server,
            args.token,
            args.source,
            list(quotes.values()),
            args.timeout,
            args.origin_ip,
            args.origin_tls_insecure,
            args.origin_ca_file,
        )
        print(
            f"{datetime.now().isoformat(timespec='seconds')} quotes uploaded "
            f"symbols={len(symbols)} quotes={len(quotes)} accepted={accepted} enabled={str(enabled).lower()}",
            flush=True,
        )
    except Exception as exc:
        print(f"{datetime.now().isoformat(timespec='seconds')} upload cycle failed: {exc}", file=sys.stderr, flush=True)


def fetch_required_symbols(
    server: str,
    token: str,
    timeout: float,
    origin_ip: str = "",
    origin_tls_insecure: bool = False,
    origin_ca_file: str = "",
) -> list[str]:
    url = server.rstrip("/") + "/api/v1/uploads/quotes/required-symbols"
    req = urllib.request.Request(url, headers=server_request_headers(server, token))
    with open_server_request(req, timeout, origin_ip, origin_tls_insecure, origin_ca_file) as resp:
        body = resp.read()
    parsed = json.loads(body.decode("utf-8"))
    symbols = parsed.get("symbols") or []
    if not symbols:
        raise RuntimeError("server returned no required symbols")
    return symbols


def fetch_sina_quotes(symbols: list[str], timeout: float) -> dict[str, dict]:
    out: dict[str, dict] = {}
    fallback_symbols: list[str] = []
    for batch in chunks(symbols, BATCH_SIZE):
        sina_symbols: list[str] = []
        reverse: dict[str, str] = {}
        for symbol in batch:
            sina_symbol = to_sina_symbol(symbol)
            if not sina_symbol:
                fallback_symbols.append(symbol)
                continue
            sina_symbols.append(sina_symbol)
            reverse[sina_symbol] = symbol
        if not sina_symbols:
            continue
        url = "https://hq.sinajs.cn/list=" + ",".join(urllib.parse.quote(s) for s in sina_symbols)
        req = urllib.request.Request(url, headers=SINA_REQUEST_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("gb18030", errors="replace")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"sina status {exc.code}") from exc
        parsed = parse_sina_response(raw, reverse)
        out.update(parsed)
        for symbol in batch:
            if symbol not in parsed:
                fallback_symbols.append(symbol)
    if fallback_symbols:
        out.update(fetch_fallback_quotes(fallback_symbols, timeout))
    return out


def fetch_fallback_quotes(symbols: list[str], timeout: float) -> dict[str, dict]:
    unique: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = symbol.strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    yahoo_candidates = [symbol for symbol in unique if to_yahoo_symbol(symbol)]
    if not yahoo_candidates:
        return {}
    return fetch_yahoo_quotes(yahoo_candidates, timeout)


def fetch_yahoo_quotes(symbols: list[str], timeout: float) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for symbol in symbols:
        yahoo_symbol = to_yahoo_symbol(symbol)
        if not yahoo_symbol:
            continue
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            + urllib.parse.quote(yahoo_symbol, safe="=")
            + "?range=2d&interval=1m&includePrePost=true"
        )
        req = urllib.request.Request(url, headers=YAHOO_REQUEST_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError:
            continue
        except urllib.error.URLError:
            continue
        quote = parse_yahoo_chart_quote(symbol, yahoo_symbol, payload)
        if quote.get("price", 0) > 0:
            out[symbol] = quote
    return out


def upload_quotes(
    server: str,
    token: str,
    source: str,
    quotes: list[dict],
    timeout: float,
    origin_ip: str = "",
    origin_tls_insecure: bool = False,
    origin_ca_file: str = "",
) -> tuple[int, bool]:
    valid = [q for q in quotes if q.get("price", 0) > 0 and not q.get("error")]
    if not valid:
        raise RuntimeError("no valid quotes to upload")
    allowed_fields = {
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
        "limit_up",
        "limit_down",
        "bid_levels",
        "ask_levels",
        "quote_date",
        "quote_time",
        "source",
        "source_symbol",
        "quote_session",
    }
    accepted = 0
    enabled = False
    for batch in chunks(valid, 500):
        normalized_batch = [{key: value for key, value in quote.items() if key in allowed_fields} for quote in batch]
        payload = json.dumps({"source": source, "quotes": normalized_batch}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            server.rstrip("/") + "/api/v1/uploads/quotes",
            data=payload,
            method="POST",
            headers=server_request_headers(server, token, "application/json"),
        )
        try:
            with open_server_request(req, timeout, origin_ip, origin_tls_insecure, origin_ca_file) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"upload status {exc.code}: {detail}") from exc
        parsed = json.loads(body.decode("utf-8"))
        if parsed.get("error"):
            raise RuntimeError(parsed["error"])
        accepted += int(parsed.get("accepted") or 0)
        enabled = bool(parsed.get("enabled"))
    return accepted, enabled


class OriginHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, *args, origin_ip: str = "", **kwargs):
        self.origin_ip = origin_ip
        super().__init__(host, *args, **kwargs)

    def connect(self) -> None:
        self.sock = self._create_connection((self.origin_ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self._tunnel()


class OriginHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *args, origin_ip: str = "", **kwargs):
        self.origin_ip = origin_ip
        super().__init__(host, *args, **kwargs)

    def connect(self) -> None:
        sock = self._create_connection((self.origin_ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class OriginHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, origin_ip: str):
        self.origin_ip = origin_ip
        super().__init__()

    def http_open(self, req):
        def factory(host, **kwargs):
            return OriginHTTPConnection(host, origin_ip=self.origin_ip, **kwargs)

        return self.do_open(factory, req)


class OriginHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, origin_ip: str, origin_tls_insecure: bool = False, origin_ca_file: str = ""):
        self.origin_ip = origin_ip
        super().__init__(context=origin_ssl_context(origin_tls_insecure, origin_ca_file))

    def https_open(self, req):
        def factory(host, **kwargs):
            return OriginHTTPSConnection(host, origin_ip=self.origin_ip, context=self._context, **kwargs)

        return self.do_open(factory, req)


def origin_ssl_context(origin_tls_insecure: bool = False, origin_ca_file: str = "") -> ssl.SSLContext:
    if origin_tls_insecure:
        return ssl._create_unverified_context()
    if origin_ca_file.strip():
        return ssl.create_default_context(cafile=origin_ca_file.strip())
    return ssl.create_default_context()


def open_server_request(
    req: urllib.request.Request,
    timeout: float,
    origin_ip: str = "",
    origin_tls_insecure: bool = False,
    origin_ca_file: str = "",
):
    origin_ip = origin_ip.strip()
    if not origin_ip:
        return urllib.request.urlopen(req, timeout=timeout)
    opener = urllib.request.build_opener(
        OriginHTTPHandler(origin_ip),
        OriginHTTPSHandler(origin_ip, origin_tls_insecure, origin_ca_file),
    )
    return opener.open(req, timeout=timeout)


def parse_sina_response(raw: str, reverse: dict[str, str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        left, right = line.split("=", 1)
        sina_symbol = left.strip().removeprefix("var hq_str_")
        symbol = reverse.get(sina_symbol)
        if not symbol:
            continue
        payload = right.strip().rstrip(";").strip('"')
        quote = parse_payload(sina_symbol, symbol, payload)
        if quote.get("price", 0) > 0:
            out[symbol] = quote
    return out


def parse_payload(sina_symbol: str, symbol: str, payload: str) -> dict:
    if sina_symbol.startswith("rt_hk"):
        return parse_hk(symbol, sina_symbol, payload)
    if sina_symbol.startswith("gb_"):
        return parse_us(symbol, sina_symbol, payload)
    if sina_symbol.startswith("fx_"):
        return parse_fx(symbol, sina_symbol, payload)
    if sina_symbol.startswith("hf_"):
        return parse_hf(symbol, sina_symbol, payload)
    if sina_symbol.startswith("nf_"):
        return parse_nf(symbol, sina_symbol, payload)
    if sina_symbol.startswith("znb_"):
        return parse_global_index(symbol, sina_symbol, payload, "sina_znb")
    if sina_symbol.startswith("b_"):
        return parse_global_index(symbol, sina_symbol, payload, "sina_b_index")
    return parse_a_stock(symbol, sina_symbol, payload)


def base_quote(symbol: str, sina_symbol: str, source: str, session: str = "", quote_timezone: str = "") -> dict:
    return {
        "symbol": symbol,
        "name": "",
        "price": 0.0,
        "prev_close": 0.0,
        "open": 0.0,
        "high": 0.0,
        "low": 0.0,
        "volume": 0.0,
        "amount": 0.0,
        "change_pct": 0.0,
        "quote_date": "",
        "quote_time": "",
        "source": source,
        "source_symbol": sina_symbol,
        "quote_session": session,
        "quote_timezone": quote_timezone,
    }


def parse_a_stock(symbol: str, sina_symbol: str, payload: str) -> dict:
    parts = payload.split(",")
    q = base_quote(symbol, sina_symbol, "sina", "cn_regular", "Asia/Shanghai")
    if len(parts) < 4 or not parts[0]:
        q["error"] = "empty sina payload"
        return q
    q.update(
        name=parts[0].strip(),
        open=num(parts, 1),
        prev_close=num(parts, 2),
        price=num(parts, 3) or num(parts, 2),
        high=num(parts, 4),
        low=num(parts, 5),
        volume=num(parts, 8),
        amount=num(parts, 9),
        bid_levels=parse_a_stock_levels(parts, 10),
        ask_levels=parse_a_stock_levels(parts, 20),
    )
    if q["prev_close"] > 0:
        q["limit_up"] = round_tick(q["prev_close"] * 1.1)
        q["limit_down"] = round_tick(q["prev_close"] * 0.9)
    if len(parts) > 31:
        q["quote_date"] = parts[30].strip()
        q["quote_time"] = trim_time(parts[31])
    apply_change(q)
    return q


def parse_hk(symbol: str, sina_symbol: str, payload: str) -> dict:
    parts = payload.split(",")
    q = base_quote(symbol, sina_symbol, "sina_hk", "hk_regular", "Asia/Hong_Kong")
    if len(parts) < 19 or not part(parts, 1):
        q["error"] = "empty sina hk payload"
        return q
    q.update(
        name=part(parts, 1),
        open=num(parts, 2),
        prev_close=num(parts, 3),
        high=num(parts, 4),
        low=num(parts, 5),
        price=num(parts, 6),
        amount=num(parts, 11),
        volume=num(parts, 12),
        quote_date=part(parts, 17).replace("/", "-"),
        quote_time=trim_time(part(parts, 18)),
    )
    apply_change(q)
    return q


def parse_us(symbol: str, sina_symbol: str, payload: str) -> dict:
    parts = payload.split(",")
    q = base_quote(symbol, sina_symbol, "sina_us", "regular", "America/New_York")
    if len(parts) < 8 or not parts[0]:
        q["error"] = "empty sina us payload"
        return q
    q.update(
        name=parts[0].strip(),
        price=num(parts, 1),
        change_pct=num(parts, 2),
        open=num(parts, 5),
        high=num(parts, 6),
        low=num(parts, 7),
        volume=num(parts, 10),
        prev_close=num(parts, 26),
    )
    date, tm = split_datetime(part(parts, 3))
    q["quote_date"] = date
    q["quote_time"] = tm
    if q["prev_close"] == 0 and q["change_pct"] != -100:
        q["prev_close"] = safe_prev(q["price"], q["change_pct"])
    after_price = num(parts, 21)
    if after_price > 0:
        q["price"] = after_price
        q["change_pct"] = num(parts, 22)
        regular_price = num(parts, 1)
        if regular_price > 0:
            q["prev_close"] = regular_price
        if q["prev_close"] == 0 and q["change_pct"] != -100:
            q["prev_close"] = safe_prev(q["price"], q["change_pct"])
        q["source"] = "sina_us_after_hours"
        q["quote_session"] = "extended"
    return q


def parse_fx(symbol: str, sina_symbol: str, payload: str) -> dict:
    parts = payload.split(",")
    q = base_quote(symbol, sina_symbol, "sina_fx", "fx_weekday", "Asia/Shanghai")
    if len(parts) < 9:
        q["error"] = "empty sina fx payload"
        return q
    q.update(
        quote_time=trim_time(part(parts, 0)),
        price=num(parts, 1),
        open=num(parts, 2),
        high=num(parts, 3),
        low=num(parts, 6),
        name=part(parts, 8),
        change_pct=num(parts, 9),
        quote_date=part(parts, len(parts) - 1),
    )
    return q


def parse_hf(symbol: str, sina_symbol: str, payload: str) -> dict:
    parts = payload.split(",")
    q = base_quote(symbol, sina_symbol, "sina_hf", "global_future", "Asia/Shanghai")
    if len(parts) < 14:
        q["error"] = "empty sina hf payload"
        return q
    q.update(
        price=num(parts, 0),
        high=num(parts, 4),
        low=num(parts, 5),
        quote_time=trim_time(part(parts, 6)),
        prev_close=num(parts, 7),
        open=num(parts, 8),
        volume=num(parts, 9),
        quote_date=part(parts, 12),
        name=part(parts, 13),
    )
    apply_change(q)
    return q


def parse_nf(symbol: str, sina_symbol: str, payload: str) -> dict:
    parts = payload.split(",")
    q = base_quote(symbol, sina_symbol, "sina_nf", "cn_future", "Asia/Shanghai")
    if len(parts) < 18 or not parts[0]:
        q["error"] = "empty sina nf payload"
        return q
    q.update(
        name=part(parts, 0),
        quote_time=normalize_compact_time(part(parts, 1)),
        price=num(parts, 2),
        high=num(parts, 3),
        low=num(parts, 4),
        open=num(parts, 6),
        prev_close=num(parts, 10),
        amount=num(parts, 13),
        volume=num(parts, 14),
        quote_date=part(parts, 17),
    )
    apply_change(q)
    return q


def parse_global_index(symbol: str, sina_symbol: str, payload: str, source: str) -> dict:
    parts = payload.split(",")
    q = base_quote(symbol, sina_symbol, source, "global_index", global_index_timezone(symbol))
    if len(parts) < 6 or not parts[0]:
        q["error"] = f"empty {source} payload"
        return q
    q.update(name=part(parts, 0), price=num(parts, 1), change_pct=num(parts, 3))
    if len(parts) >= 8:
        q.update(
            quote_date=part(parts, 6),
            quote_time=trim_time(part(parts, 7)),
            open=num(parts, 8),
            prev_close=num(parts, 9),
            high=num(parts, 10),
            low=num(parts, 11),
            volume=num(parts, 12),
        )
    if q["prev_close"] == 0 and q["change_pct"] != -100:
        q["prev_close"] = safe_prev(q["price"], q["change_pct"])
    return q


def parse_a_stock_levels(parts: list[str], start: int) -> list[dict]:
    levels: list[dict] = []
    for level in range(1, 6):
        volume_idx = start + (level - 1) * 2
        price_idx = volume_idx + 1
        volume = num(parts, volume_idx)
        price = num(parts, price_idx)
        if price <= 0 and volume <= 0:
            continue
        levels.append({"level": level, "price": price, "volume": volume})
    return levels


def round_tick(value: float) -> float:
    return round(value + 1e-12, 3)


def to_sina_symbol(symbol: str) -> str:
    s = symbol.strip()
    if not s:
        return ""
    lower = s.lower()
    if lower.startswith("fx_"):
        return lower
    if lower.startswith("hf_"):
        return "hf_" + s[3:].upper()
    if lower.startswith("nf_"):
        return "nf_" + s[3:].upper()
    if lower.startswith("znb_"):
        return "znb_" + s[4:].upper()
    if lower.startswith("b_"):
        return "b_" + s[2:].upper()
    if lower.startswith("rt_hk"):
        return "rt_hk" + s[5:]
    if lower.startswith("gb_"):
        return "gb_" + s[3:].lower()
    if is_cn_symbol(s):
        return s[:2].lower() + s[2:]
    if re.fullmatch(r"\d{5}", s):
        return "rt_hk" + s
    if s.startswith("^") and len(s) > 1:
        return "b_" + s[1:].upper()
    if re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", s):
        return "gb_" + s.lower()
    return ""


def to_yahoo_symbol(symbol: str) -> str:
    return YAHOO_FUTURE_SYMBOLS.get(symbol.strip().upper(), "")


def parse_yahoo_chart_quote(symbol: str, yahoo_symbol: str, payload: dict) -> dict:
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    meta = (result or {}).get("meta") or {}
    indicators = (result or {}).get("indicators") or {}
    quotes = indicators.get("quote") or [{}]
    closes = (quotes[0] or {}).get("close") or []
    timestamps = result.get("timestamp") or []
    q = base_quote(symbol, yahoo_symbol, "yahoo_chart", "global_future", meta.get("exchangeTimezoneName") or "America/New_York")
    last_close, last_timestamp = last_non_null_pair(closes, timestamps)
    if last_close <= 0 or last_timestamp <= 0:
        q["error"] = "empty yahoo chart payload"
        return q
    previous_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0.0)
    open_price = float(meta.get("regularMarketOpen") or 0.0)
    day_high = float(meta.get("regularMarketDayHigh") or 0.0)
    day_low = float(meta.get("regularMarketDayLow") or 0.0)
    volume = float(meta.get("regularMarketVolume") or 0.0)
    quote_dt = datetime.fromtimestamp(last_timestamp, tz=timezone.utc)
    tz_name = q.get("quote_timezone") or "America/New_York"
    try:
        quote_dt = quote_dt.astimezone(ZoneInfo(str(tz_name)))
    except Exception:
        pass
    q.update(
        name=str(meta.get("shortName") or meta.get("symbol") or yahoo_symbol).strip(),
        price=last_close,
        prev_close=previous_close,
        open=open_price,
        high=day_high,
        low=day_low,
        volume=volume,
        quote_date=quote_dt.strftime("%Y-%m-%d"),
        quote_time=quote_dt.strftime("%H:%M:%S"),
        observed_at=quote_dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )
    apply_change(q)
    return q


def last_non_null_pair(values: list, timestamps: list) -> tuple[float, int]:
    count = min(len(values), len(timestamps))
    for index in range(count - 1, -1, -1):
        try:
            value = float(values[index])
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        try:
            timestamp = int(timestamps[index])
        except (TypeError, ValueError):
            continue
        if timestamp <= 0:
            continue
        return value, timestamp
    return 0.0, 0


def global_index_timezone(symbol: str) -> str:
    lower = symbol.strip().lower()
    if lower.startswith(("znb_nky", "znb_tpx")) or lower.endswith("-jp"):
        return "Asia/Tokyo"
    if lower.startswith(("znb_dax", "znb_cac")) or lower.endswith("-eu"):
        return "Europe/Berlin"
    return ""


def is_cn_symbol(symbol: str) -> bool:
    upper = symbol.upper().strip()
    return len(upper) == 8 and upper[:2] in {"SH", "SZ", "BJ"} and upper[2:].isdigit()


def apply_change(q: dict) -> None:
    if q.get("prev_close", 0) > 0:
        q["change_pct"] = (q.get("price", 0) / q["prev_close"] - 1) * 100


def safe_prev(price: float, change_pct: float) -> float:
    denom = 1 + change_pct / 100
    return price / denom if denom else 0.0


def num(parts: list[str], idx: int) -> float:
    if idx >= len(parts):
        return 0.0
    try:
        return float(parts[idx].strip())
    except ValueError:
        return 0.0


def part(parts: list[str], idx: int) -> str:
    if idx >= len(parts):
        return ""
    return parts[idx].strip()


def split_datetime(value: str) -> tuple[str, str]:
    fields = value.strip().split()
    if not fields:
        return "", ""
    if len(fields) == 1:
        return fields[0], ""
    return fields[0], trim_time(fields[1])


def trim_time(value: str) -> str:
    value = value.strip()
    return value[:8] if len(value) >= 8 else value


def normalize_compact_time(value: str) -> str:
    if re.fullmatch(r"\d{6}", value):
        return f"{value[:2]}:{value[2:4]}:{value[4:6]}"
    return trim_time(value)


def split_symbols(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def chunks(items, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def parse_duration(value: str, fallback: float) -> float:
    value = str(value).strip()
    if not value:
        return fallback
    try:
        return float(value)
    except ValueError:
        pass
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)(ms|s|m|h)", value)
    if not match:
        return fallback
    amount = float(match.group(1))
    unit = match.group(2)
    if unit == "ms":
        return amount / 1000
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    return amount * 3600


def is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def request_stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def sleep_until_stop(seconds: float) -> None:
    end = time.time() + seconds
    while not STOP and time.time() < end:
        time.sleep(min(0.25, end - time.time()))


if __name__ == "__main__":
    raise SystemExit(main())

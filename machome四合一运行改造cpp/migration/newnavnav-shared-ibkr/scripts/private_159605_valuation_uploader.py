#!/usr/bin/env python3
"""Upload isolated SZ159605 multi-market PCF valuation inputs from Mac-home.

SZ159605 is a full cash-substitution QDII ETF.  Unlike the SZ159518 XOP
proxy uploader, this collector keeps every official PCF component, obtains
Bid/Ask for its 23 HK shares and 7 US ADRs separately, and sends two CNY FX
rates.  The public Sina collector is deliberately not consulted here: the
server joins its five-level domestic quote only inside the private module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import private_valuation_uploader as common


SYMBOL = "SZ159605"
SECURITY_ID = "159605"
MODEL_VERSION = "private.full-cash-substitution.multi-market-pcf.v1"
INPUT_SCHEMA_VERSION = 1
EXPECTED_REDEMPTION_UNIT = Decimal("1000000")
EXPECTED_COMPONENT_COUNT = 30
EXPECTED_HK_COMPONENT_COUNT = 23
EXPECTED_US_COMPONENT_COUNT = 7
PCF_URL_TEMPLATE = (
    "https://reportdocs.static.szse.cn/files/text/ETFDown/"
    "pcf_159605_{yyyymmdd}.xml"
)
CFETS_PAIRS = ("USD/CNY", "HKD/CNY")
IB_SOURCE = "IBKR_TWS"
DEFAULT_SOURCE = "mac-home-private-159605-uploader"
DEFAULT_RUNTIME_DIR = "scripts/.runtime/private_159605"
DEFAULT_UPLOAD_INTERVAL = 3.0
DEFAULT_CFETS_INTERVAL = 60.0
DEFAULT_PCF_INTERVAL = 10.0 * 60.0
DEFAULT_PCF_START_AT = "08:30"
DEFAULT_PCF_SYMBOL_INTERVAL = 10.0
DEFAULT_TIMEOUT = 10.0
DEFAULT_LOOKBACK_DAYS = 10


class InputValidationError(ValueError):
    """A value cannot be safely used in the private full-cash model."""


class SourceUnavailableError(RuntimeError):
    """A required upstream source does not currently have a usable value."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_text(root: ET.Element, field: str, *, required: bool = True) -> str:
    values = [str(child.text or "").strip() for child in root if _local_name(child.tag) == field]
    if len(values) > 1 or (required and (len(values) != 1 or not values[0])):
        raise InputValidationError(f"PCF {field} must appear exactly once and be non-empty")
    return values[0] if values else ""


def _decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise InputValidationError(f"{field} must be numeric") from exc
    if not parsed.is_finite():
        raise InputValidationError(f"{field} must be finite")
    return parsed


def _positive_float(value: Any, field: str) -> float:
    parsed = _decimal(value, field)
    if parsed <= 0:
        raise InputValidationError(f"{field} must be positive")
    return float(parsed)


def _finite_float(value: Any, field: str) -> float:
    return float(_decimal(value, field))


def _parse_date(value: Any, field: str) -> date:
    text = str(value or "").strip()
    for layout in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, layout).date()
        except ValueError:
            continue
    raise InputValidationError(f"{field} must be YYYY-MM-DD or YYYYMMDD")


def _normalize_hk_symbol(value: str) -> str:
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d{1,5}", raw):
        raise InputValidationError(f"HK PCF identifier must be numeric: {raw!r}")
    return raw.zfill(4)


def pcf_url_for_day(trading_day: date) -> str:
    return PCF_URL_TEMPLATE.format(yyyymmdd=trading_day.strftime("%Y%m%d"))


def ib_contract_symbol(component: "Component") -> str:
    """Return the spelling accepted by IB for a PCF component contract.

    SZSE PCFs preserve Hong Kong identifiers as four or five digits (for
    example ``0700`` and ``0241``), while IB's SEHK contract lookup requires
    their unpadded ticker form (``700`` and ``241``).  The PCF identifier is
    still retained in all private payloads; this conversion is only for the
    outbound IB contract request.
    """

    return component.symbol.lstrip("0") or "0" if component.market == "HK" else component.symbol


@dataclass(frozen=True)
class Component:
    symbol: str
    name: str
    market: str
    currency: str
    quantity: float

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "currency": self.currency,
            "quantity": self.quantity,
        }


@dataclass(frozen=True)
class PCFInput:
    security_id: str
    trading_day: date
    pre_trading_day: date | None
    creation: str
    redemption: str
    creation_redemption_unit: float
    estimate_cash_component_cny: float
    nav_per_cu: float
    components: tuple[Component, ...]
    source_url: str
    sha256: str

    @property
    def component_count(self) -> int:
        return len(self.components)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "security_id": self.security_id,
            "trading_day": self.trading_day.isoformat(),
            "creation": self.creation,
            "redemption": self.redemption,
            "creation_redemption_unit": self.creation_redemption_unit,
            "estimate_cash_component_cny": self.estimate_cash_component_cny,
            "nav_per_cu": self.nav_per_cu,
            "component_count": self.component_count,
            "components": [component.to_payload() for component in self.components],
            "source_url": self.source_url,
            "sha256": self.sha256,
        }
        if self.pre_trading_day is not None:
            payload["pre_trading_day"] = self.pre_trading_day.isoformat()
        return payload


@dataclass(frozen=True)
class FXQuote:
    pair: str
    rate: float
    trading_day: date
    quote_time: str
    fetched_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "rate": self.rate,
            "trading_day": self.trading_day.isoformat(),
            "quote_time": self.quote_time,
            "source": common.CFETS_SOURCE,
            "fetched_at": common.iso_timestamp(self.fetched_at),
        }


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    market: str
    currency: str
    bid: float
    ask: float
    last: float | None
    market_data_type: str
    observed_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "currency": self.currency,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "market_data_type": self.market_data_type,
            "source": IB_SOURCE,
            "observed_at": common.iso_timestamp(self.observed_at),
        }


def parse_pcf_xml(raw_xml: bytes | str, source_url: str, expected_day: date | None = None) -> PCFInput:
    raw = raw_xml.encode("utf-8") if isinstance(raw_xml, str) else bytes(raw_xml)
    if not raw.strip():
        raise InputValidationError("PCF XML is empty")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise InputValidationError(f"PCF XML is invalid: {exc}") from exc
    if _local_name(root.tag) != "PCFFile":
        raise InputValidationError("PCF XML root must be PCFFile")

    security_id = _direct_text(root, "SecurityID")
    if security_id != SECURITY_ID:
        raise InputValidationError(f"PCF SecurityID must be {SECURITY_ID}")
    trading_day = _parse_date(_direct_text(root, "TradingDay"), "PCF TradingDay")
    if expected_day is not None and trading_day != expected_day:
        raise InputValidationError(
            f"PCF TradingDay {trading_day.isoformat()} does not match requested {expected_day.isoformat()}"
        )
    pre_text = _direct_text(root, "PreTradingDay", required=False)
    creation = _direct_text(root, "Creation").upper()
    redemption = _direct_text(root, "Redemption").upper()
    if creation not in {"Y", "N"} or redemption not in {"Y", "N"}:
        raise InputValidationError("PCF Creation and Redemption must be Y or N")
    redemption_unit = _decimal(_direct_text(root, "CreationRedemptionUnit"), "PCF CreationRedemptionUnit")
    if redemption_unit != EXPECTED_REDEMPTION_UNIT:
        raise InputValidationError("PCF CreationRedemptionUnit must equal 1000000")
    estimate_cash = _decimal(_direct_text(root, "EstimateCashComponent"), "PCF EstimateCashComponent")
    nav_per_cu = _decimal(_direct_text(root, "NAVperCU"), "PCF NAVperCU")
    if nav_per_cu <= 0:
        raise InputValidationError("PCF NAVperCU must be positive")

    component_nodes = [node for node in root.iter() if _local_name(node.tag) == "Component"]
    declared_count = _decimal(_direct_text(root, "TotalRecordNum"), "PCF TotalRecordNum")
    if declared_count != len(component_nodes):
        raise InputValidationError(
            f"PCF TotalRecordNum={declared_count} does not match XML Components={len(component_nodes)}"
        )
    components: list[Component] = []
    for node in component_nodes:
        fields = {_local_name(child.tag): str(child.text or "").strip() for child in node}
        shares = _decimal(fields.get("ComponentShare", ""), "PCF ComponentShare")
        if shares <= 0:
            continue  # excludes the 159900 cash virtual line
        source = fields.get("UnderlyingSecurityIDSource", "")
        raw_symbol = fields.get("UnderlyingSecurityID", "")
        name = fields.get("UnderlyingSymbol", "")
        if source == "103":
            symbol, market, currency = _normalize_hk_symbol(raw_symbol), "HK", "HKD"
        elif source == "9999":
            symbol, market, currency = raw_symbol.upper(), "US", "USD"
        else:
            raise InputValidationError(
                f"unexpected positive-share PCF component source {source!r} for {raw_symbol!r}"
            )
        if not symbol or not name:
            raise InputValidationError("PCF component identifier and name are required")
        components.append(Component(symbol, name, market, currency, float(shares)))

    pcf = PCFInput(
        security_id=security_id,
        trading_day=trading_day,
        pre_trading_day=_parse_date(pre_text, "PCF PreTradingDay") if pre_text else None,
        creation=creation,
        redemption=redemption,
        creation_redemption_unit=float(redemption_unit),
        estimate_cash_component_cny=float(estimate_cash),
        nav_per_cu=float(nav_per_cu),
        components=tuple(components),
        source_url=source_url.strip(),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    validate_pcf(pcf)
    return pcf


def validate_pcf(value: PCFInput) -> None:
    if value.security_id != SECURITY_ID:
        raise InputValidationError(f"PCF security_id must be {SECURITY_ID}")
    if value.creation not in {"Y", "N"} or value.redemption not in {"Y", "N"}:
        raise InputValidationError("PCF creation and redemption must be Y or N")
    if Decimal(str(value.creation_redemption_unit)) != EXPECTED_REDEMPTION_UNIT:
        raise InputValidationError("PCF creation_redemption_unit must equal 1000000")
    if not math.isfinite(value.estimate_cash_component_cny):
        raise InputValidationError("PCF estimate_cash_component_cny must be finite")
    if not math.isfinite(value.nav_per_cu) or value.nav_per_cu <= 0:
        raise InputValidationError("PCF nav_per_cu must be positive")
    if len(value.components) != EXPECTED_COMPONENT_COUNT:
        raise InputValidationError(f"PCF must contain exactly {EXPECTED_COMPONENT_COUNT} tradable components")
    hk_count = sum(component.market == "HK" for component in value.components)
    us_count = sum(component.market == "US" for component in value.components)
    if hk_count != EXPECTED_HK_COMPONENT_COUNT or us_count != EXPECTED_US_COMPONENT_COUNT:
        raise InputValidationError(f"PCF market composition must be {EXPECTED_HK_COMPONENT_COUNT} HK + {EXPECTED_US_COMPONENT_COUNT} US")
    keys = {(component.market, component.symbol) for component in value.components}
    if len(keys) != len(value.components):
        raise InputValidationError("PCF contains duplicate market/symbol components")
    if value.source_url != pcf_url_for_day(value.trading_day):
        raise InputValidationError("PCF source_url does not match its SZSE trading day URL")
    if not re.fullmatch(r"[0-9a-f]{64}", value.sha256):
        raise InputValidationError("PCF sha256 must be lowercase SHA-256")


class PCFClient:
    def __init__(
        self,
        runtime_dir: str | os.PathLike[str],
        *,
        timeout: float,
        refresh_seconds: float,
        lookback_days: int,
        request_pacer: common.PCFRequestPacer,
        fetch_bytes: Callable[..., bytes] | None = None,
    ) -> None:
        self.cache_root = Path(runtime_dir).expanduser().resolve() / "pcf"
        self.timeout = timeout
        self.refresh_seconds = max(0.0, refresh_seconds)
        self.lookback_days = max(0, lookback_days)
        self.request_pacer = request_pacer
        self.fetch_bytes = fetch_bytes or common._http_fetch

    def cache_path(self, trading_day: date) -> Path:
        return self.cache_root / trading_day.isoformat() / f"{SECURITY_ID}.xml"

    def _cached(self, trading_day: date) -> PCFInput | None:
        path = self.cache_path(trading_day)
        if not path.is_file():
            return None
        try:
            return parse_pcf_xml(path.read_bytes(), pcf_url_for_day(trading_day), trading_day)
        except (OSError, InputValidationError) as exc:
            common.log(f"ignoring invalid {SYMBOL} PCF cache {path}: {exc}", error=True)
            return None

    def fetch_latest(self, as_of: date, *, force_refresh: bool = False) -> PCFInput:
        errors: list[str] = []
        for offset in range(self.lookback_days + 1):
            candidate = as_of - timedelta(days=offset)
            if candidate.weekday() >= 5:
                continue
            path = self.cache_path(candidate)
            cached = self._cached(candidate)
            cache_fresh = cached is not None and path.exists() and time.time() - path.stat().st_mtime < self.refresh_seconds
            if cached is not None and (candidate < as_of or (cache_fresh and not force_refresh)):
                return cached
            try:
                self.request_pacer.wait()
                raw = self.fetch_bytes(pcf_url_for_day(candidate), None, common.SOURCE_HEADERS, self.timeout)
                parsed = parse_pcf_xml(raw, pcf_url_for_day(candidate), candidate)
                common._atomic_write_bytes(path, bytes(raw))
                return parsed
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, InputValidationError) as exc:
                errors.append(f"{candidate.isoformat()}: {exc}")
                if cached is not None:
                    return cached
        suffix = " | ".join(errors[-3:])
        raise SourceUnavailableError(f"no strict {SECURITY_ID} PCF in {self.lookback_days} day lookback" + (f": {suffix}" if suffix else ""))


def _cfets_query_url(trading_day: date) -> str:
    date_text = f"{trading_day.day:02d} {common.ENGLISH_MONTHS[trading_day.month - 1]} {trading_day.year:04d}"
    query = urllib.parse.urlencode({
        "lang": "cn",
        "startDateTool": date_text,
        "endDateTool": date_text,
        "currencyCode": "ALL",
    })
    return f"{common.CFETS_REF_RATE_URL}?{query}"


def parse_cfets_rates(raw_payload: bytes | str | dict[str, Any], requested_day: date, fetched_at: datetime) -> tuple[FXQuote, ...]:
    if isinstance(raw_payload, dict):
        payload = raw_payload
    else:
        try:
            payload = json.loads(raw_payload.decode("utf-8") if isinstance(raw_payload, bytes) else raw_payload)
        except json.JSONDecodeError as exc:
            raise InputValidationError(f"CFETS response is not JSON: {exc}") from exc
    nested_data = payload.get("data")
    records = payload.get("records") or (nested_data.get("records") if isinstance(nested_data, dict) else []) or []
    found: dict[str, FXQuote] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        pair = str(record.get("ccyPair") or "").strip().upper()
        if pair not in CFETS_PAIRS:
            continue
        try:
            actual_day = _parse_date(record.get("dealDate"), "CFETS dealDate")
        except InputValidationError:
            continue
        if actual_day != requested_day:
            continue
        hourly: list[tuple[int, float]] = []
        for hour in range(10, 19):
            raw_rate = str(record.get(f"rateOf{hour:02d}hour") or "").strip()
            if raw_rate in {"", "---", "/"}:
                continue
            try:
                hourly.append((hour, _positive_float(raw_rate, f"CFETS {pair} rateOf{hour:02d}hour")))
            except InputValidationError:
                continue
        if hourly:
            hour, rate = max(hourly, key=lambda item: item[0])
            found[pair] = FXQuote(pair, rate, actual_day, f"{hour:02d}:00", fetched_at)
    if set(found) != set(CFETS_PAIRS):
        missing = ", ".join(pair for pair in CFETS_PAIRS if pair not in found)
        raise SourceUnavailableError(f"CFETS {requested_day.isoformat()} missing {missing}")
    return tuple(found[pair] for pair in CFETS_PAIRS)


class CFETSClient:
    def __init__(self, *, timeout: float, fetch_bytes: Callable[..., bytes] | None = None) -> None:
        self.timeout = timeout
        self.fetch_bytes = fetch_bytes or common._http_fetch

    def fetch_for_day(self, trading_day: date) -> tuple[FXQuote, ...]:
        fetched_at = datetime.now(common.SHANGHAI)
        try:
            raw = self.fetch_bytes(_cfets_query_url(trading_day), b"", common.SOURCE_HEADERS, self.timeout)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SourceUnavailableError(f"CFETS fetch failed: {exc}") from exc
        return parse_cfets_rates(raw, trading_day, fetched_at)


class IBMultiQuoteStream:
    """Thirty component leases multiplexed over the native TWS bridge."""

    def __init__(self, host: str, port: int, client_id: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.shared = None
        self.subscriptions: dict[tuple[str, str], tuple[Component, str]] = {}

    def connect(self, components: tuple[Component, ...]) -> None:
        self.disconnect()
        from machome_ibkr_bridge_client import ContractSubscription, SharedMultiQuoteStream
        shared_subscriptions = []
        mappings: dict[tuple[str, str], tuple[Component, str]] = {}
        for component in components:
            subscription = ContractSubscription.create(
                symbol=ib_contract_symbol(component),
                security_type="STK",
                exchange="SEHK" if component.market == "HK" else "SMART",
                currency=component.currency,
            )
            shared_subscriptions.append(subscription)
            mappings[(component.market, component.symbol)] = (component, subscription.subscription_id)
        shared = SharedMultiQuoteStream(shared_subscriptions, timeout=self.timeout)
        try:
            shared.connect()
        except Exception as exc:
            shared.close()
            self.subscriptions = {}
            raise SourceUnavailableError(f"native IBKR bridge unavailable: {exc}") from exc
        self.shared = shared
        self.subscriptions = mappings

    def is_connected(self) -> bool:
        return bool(self.shared is not None and self.shared.is_connected())

    def poll(self, wait_seconds: float = 0.05) -> tuple[MarketQuote, ...] | None:
        if not self.is_connected():
            raise SourceUnavailableError("IB TWS stream is disconnected")
        try:
            values = self.shared.poll(wait_seconds)
        except Exception as exc:
            raise SourceUnavailableError(f"native IBKR bridge poll failed: {exc}") from exc
        quotes: list[MarketQuote] = []
        for component, subscription_id in self.subscriptions.values():
            value = values.get(subscription_id)
            if value is None:
                return None
            quotes.append(MarketQuote(
                symbol=component.symbol,
                market=component.market,
                currency=component.currency,
                bid=value.bid,
                ask=value.ask,
                last=value.last,
                market_data_type=value.market_data_type,
                observed_at=value.observed_at.astimezone(common.SHANGHAI),
            ))
        return tuple(quotes) if len(quotes) == EXPECTED_COMPONENT_COUNT else None

    def disconnect(self) -> None:
        if self.shared is not None:
            self.shared.close()
        self.shared = None
        self.subscriptions = {}


def build_private_payload(
    pcf: PCFInput,
    fx_rates: tuple[FXQuote, ...],
    market_quotes: tuple[MarketQuote, ...],
    *,
    generated_at: datetime | None = None,
    source: str = DEFAULT_SOURCE,
) -> dict[str, Any]:
    validate_pcf(pcf)
    if tuple(rate.pair for rate in fx_rates) != CFETS_PAIRS:
        raise InputValidationError("FX payload must contain USD/CNY then HKD/CNY")
    if len(market_quotes) != len(pcf.components):
        raise InputValidationError("market quote count must equal PCF component count")
    component_keys = {(component.market, component.symbol) for component in pcf.components}
    quote_keys = {(quote.market, quote.symbol) for quote in market_quotes}
    if quote_keys != component_keys:
        raise InputValidationError("market quote symbols must exactly match the PCF components")
    for quote in market_quotes:
        if not math.isfinite(quote.bid) or quote.bid <= 0 or not math.isfinite(quote.ask) or quote.ask < quote.bid:
            raise InputValidationError(f"invalid IB Bid/Ask for {quote.market}:{quote.symbol}")
    now = generated_at or datetime.now(common.SHANGHAI)
    if now.tzinfo is None:
        now = now.replace(tzinfo=common.SHANGHAI)
    if not str(source).strip():
        raise InputValidationError("source is required")
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "symbol": SYMBOL,
        "model_version": MODEL_VERSION,
        "pcf": pcf.to_payload(),
        "fx_rates": [rate.to_payload() for rate in fx_rates],
        "market_quotes": [quote.to_payload() for quote in market_quotes],
        "source": str(source).strip(),
        "generated_at": common.iso_timestamp(now),
    }


def post_private_inputs(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    body = common.gzip_json_body(payload)
    request = urllib.request.Request(
        args.server.rstrip("/") + f"/api/v1/private/inputs/{SYMBOL}",
        data=body,
        method="POST",
        headers=common.gzip_server_headers(args.server, args.token),
    )
    try:
        with common.open_server_request(
            request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file
        ) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{SYMBOL} private upload status {exc.code}: {detail}") from exc
    try:
        parsed = json.loads(response_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("private upload returned invalid JSON") from exc
    if not isinstance(parsed, dict) or parsed.get("ok") is not True:
        raise RuntimeError(f"private upload was not acknowledged: {parsed}")
    return parsed


@dataclass
class CollectorState:
    pcf: PCFInput | None = None
    fx_rates: tuple[FXQuote, ...] | None = None
    market_quotes: tuple[MarketQuote, ...] | None = None

    def missing(self) -> list[str]:
        return [name for name in ("pcf", "fx_rates", "market_quotes") if getattr(self, name) is None]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect SZ159605 PCF/CFETS/multi-market IB inputs for /private.")
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", DEFAULT_TIMEOUT)))
    parser.add_argument("--source", default=os.getenv("NNN_PRIVATE_159605_UPLOAD_SOURCE", DEFAULT_SOURCE))
    parser.add_argument("--runtime-dir", default=os.getenv("NNN_PRIVATE_159605_RUNTIME_DIR", DEFAULT_RUNTIME_DIR))
    parser.add_argument("--upload-interval", default=os.getenv("NNN_PRIVATE_159605_UPLOAD_INTERVAL", "3s"))
    parser.add_argument("--cfets-interval", default=os.getenv("NNN_PRIVATE_159605_CFETS_INTERVAL", "60s"))
    parser.add_argument("--pcf-interval", default=os.getenv("NNN_PRIVATE_159605_PCF_INTERVAL", "10m"))
    parser.add_argument("--pcf-start-at", default=os.getenv("NNN_PRIVATE_159605_PCF_START_AT", DEFAULT_PCF_START_AT))
    parser.add_argument("--pcf-symbol-interval", default=os.getenv("NNN_PRIVATE_PCF_SYMBOL_INTERVAL", "10s"))
    parser.add_argument("--pcf-lookback-days", type=int, default=int(os.getenv("NNN_PRIVATE_159605_PCF_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)))
    parser.add_argument("--ib-host", default=os.getenv("NNN_PRIVATE_159605_IB_HOST", os.getenv("NNN_IB_HOST", "127.0.0.1")))
    parser.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_PRIVATE_159605_IB_PORT", os.getenv("NNN_IB_PORT", "7496"))))
    parser.add_argument("--ib-client-id", type=int, default=int(os.getenv("NNN_PRIVATE_159605_IB_CLIENT_ID", "159605")))
    parser.add_argument("--ib-ready-timeout", type=float, default=float(os.getenv("NNN_PRIVATE_159605_IB_READY_TIMEOUT", "30")))
    parser.add_argument("--once", action="store_true", default=common.is_true(os.getenv("NNN_PRIVATE_159605_UPLOAD_ONCE", "")))
    return parser


def run_once(args: argparse.Namespace, pcf_client: PCFClient, cfets_client: CFETSClient) -> int:
    now = datetime.now(common.SHANGHAI)
    if not common.ib_collection_window(now):
        common.log("SZ159605 IB collection is scheduled for Shanghai weekdays 09:00-15:05; nothing uploaded", error=True)
        return 1
    pcf = pcf_client.fetch_latest(now.date(), force_refresh=True)
    if pcf.trading_day != now.date():
        raise SourceUnavailableError(f"today's {SYMBOL} PCF is not ready")
    fx_rates = cfets_client.fetch_for_day(now.date())
    stream = IBMultiQuoteStream(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
    try:
        stream.connect(pcf.components)
        deadline = time.monotonic() + max(0.1, args.ib_ready_timeout)
        quotes = None
        while time.monotonic() < deadline and not common.STOP_EVENT.is_set():
            quotes = stream.poll(0.2)
            if quotes is not None:
                break
        if quotes is None:
            raise SourceUnavailableError("TWS did not return all 30 SZ159605 component Bid/Ask quotes")
    finally:
        stream.disconnect()
    post_private_inputs(args, build_private_payload(pcf, fx_rates, quotes, source=args.source))
    common.log(f"{SYMBOL} private inputs uploaded once pcf={pcf.trading_day.isoformat()} components={len(quotes)}")
    return 0


def run_forever(args: argparse.Namespace, pcf_client: PCFClient, cfets_client: CFETSClient) -> int:
    state = CollectorState()
    upload_interval = max(0.1, common.parse_duration(args.upload_interval, DEFAULT_UPLOAD_INTERVAL))
    cfets_interval = max(1.0, common.parse_duration(args.cfets_interval, DEFAULT_CFETS_INTERVAL))
    pcf_retry_interval = max(10.0, common.parse_duration(args.pcf_interval, DEFAULT_PCF_INTERVAL))
    pcf_start_minute = common.parse_clock_minute(args.pcf_start_at, 8 * 60 + 30)
    next_pcf = next_cfets = next_upload = next_ib_connect = 0.0
    pcf_ready_for_day: date | None = None
    ib_backoff = 1.0
    upload_backoff = upload_interval
    stream: IBMultiQuoteStream | None = None
    try:
        while not common.STOP_EVENT.is_set():
            now_mono, now_local = time.monotonic(), datetime.now(common.SHANGHAI)
            today = now_local.date()
            if now_mono >= next_pcf and pcf_ready_for_day != today and common.pcf_collection_window(now_local, pcf_start_minute):
                try:
                    pcf = pcf_client.fetch_latest(today, force_refresh=True)
                    if pcf.trading_day != today:
                        raise SourceUnavailableError(f"today's {SYMBOL} PCF is not ready")
                    if state.pcf is None or state.pcf.sha256 != pcf.sha256:
                        if stream is not None:
                            stream.disconnect()
                            stream = None
                        state.market_quotes = None
                    state.pcf, pcf_ready_for_day = pcf, today
                    next_pcf = time.monotonic() + 24 * 60 * 60
                    common.log(f"{SYMBOL} PCF ready components={pcf.component_count} sha256={pcf.sha256[:12]}...")
                except Exception as exc:
                    if state.pcf is not None and state.pcf.trading_day != today:
                        state.pcf = None
                    common.log(f"{SYMBOL} PCF refresh failed: {exc}", error=True)
                    next_pcf = time.monotonic() + min(60.0, pcf_retry_interval)

            if time.monotonic() >= next_cfets:
                try:
                    state.fx_rates = cfets_client.fetch_for_day(today)
                    next_cfets = time.monotonic() + cfets_interval
                except Exception as exc:
                    state.fx_rates = None
                    common.log(f"{SYMBOL} CFETS refresh failed: {exc}", error=True)
                    next_cfets = time.monotonic() + min(15.0, cfets_interval)

            now_local = datetime.now(common.SHANGHAI)
            if not common.ib_collection_window(now_local):
                if stream is not None:
                    stream.disconnect()
                    stream = None
                state.market_quotes = None
                next_ib_connect = 0.0
                common.STOP_EVENT.wait(30.0)
                continue

            if state.pcf is not None and (stream is None or not stream.is_connected()) and time.monotonic() >= next_ib_connect:
                if stream is not None:
                    stream.disconnect()
                stream = IBMultiQuoteStream(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
                try:
                    stream.connect(state.pcf.components)
                    ib_backoff = 1.0
                    common.log(f"{SYMBOL} IB connected with {state.pcf.component_count} market-data subscriptions")
                except Exception as exc:
                    stream.disconnect()
                    stream = None
                    state.market_quotes = None
                    common.log(f"{SYMBOL} IB connect failed; retry in {ib_backoff:.0f}s: {exc}", error=True)
                    next_ib_connect = time.monotonic() + ib_backoff
                    ib_backoff = min(common.MAX_RECONNECT_BACKOFF, ib_backoff * 2)

            if stream is not None and stream.is_connected():
                try:
                    quotes = stream.poll(0.05)
                    if quotes is not None:
                        state.market_quotes = quotes
                except Exception as exc:
                    common.log(f"{SYMBOL} IB stream failed; reconnecting: {exc}", error=True)
                    stream.disconnect()
                    stream = None
                    state.market_quotes = None
                    next_ib_connect = time.monotonic() + ib_backoff
                    ib_backoff = min(common.MAX_RECONNECT_BACKOFF, ib_backoff * 2)

            if time.monotonic() >= next_upload:
                missing = state.missing()
                if missing:
                    next_upload = time.monotonic() + upload_interval
                else:
                    try:
                        payload = build_private_payload(state.pcf, state.fx_rates, state.market_quotes, source=args.source)
                        post_private_inputs(args, payload)
                        common.log(f"{SYMBOL} private inputs uploaded pcf={state.pcf.trading_day.isoformat()} components={len(state.market_quotes)}")
                        upload_backoff, next_upload = upload_interval, time.monotonic() + upload_interval
                    except Exception as exc:
                        common.log(f"{SYMBOL} upload failed; retry in {upload_backoff:.0f}s: {exc}", error=True)
                        next_upload = time.monotonic() + upload_backoff
                        upload_backoff = min(common.MAX_RECONNECT_BACKOFF, max(upload_interval, upload_backoff * 2))
            if stream is None:
                common.STOP_EVENT.wait(0.1)
    finally:
        if stream is not None:
            stream.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    common.load_env_file(".sina-uploader.env")
    args = build_parser().parse_args(argv)
    if not str(args.token or "").strip():
        print("NNN_UPLOAD_TOKEN or --token is required", file=sys.stderr)
        return 2
    if args.pcf_lookback_days < 0:
        print("--pcf-lookback-days must be non-negative", file=sys.stderr)
        return 2
    signal.signal(signal.SIGINT, common._request_stop)
    signal.signal(signal.SIGTERM, common._request_stop)
    pcf_pacer = common.PCFRequestPacer(common.parse_duration(args.pcf_symbol_interval, DEFAULT_PCF_SYMBOL_INTERVAL))
    pcf_client = PCFClient(
        args.runtime_dir,
        timeout=args.timeout,
        refresh_seconds=common.parse_duration(args.pcf_interval, DEFAULT_PCF_INTERVAL),
        lookback_days=args.pcf_lookback_days,
        request_pacer=pcf_pacer,
    )
    cfets_client = CFETSClient(timeout=args.timeout)
    try:
        return run_once(args, pcf_client, cfets_client) if args.once else run_forever(args, pcf_client, cfets_client)
    except (InputValidationError, SourceUnavailableError, RuntimeError) as exc:
        common.log(str(exc), error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

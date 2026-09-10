#!/usr/bin/env python3
"""Collect isolated SZ159518 private-valuation inputs on Mac-home.

The process intentionally uploads only PCF/CFETS/IB raw inputs.  Public Sina
quotes stay on the existing quote path and are joined by the private service on
the server.  No public valuation payload or cache is read by this collector.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import http.client
import json
import math
import os
import re
import signal
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import upload_monitor_status as upload_health


SYMBOL = "SZ159518"
SECURITY_ID = "159518"
REFERENCE_SYMBOL = "XOP"
MODEL_VERSION = "private.total-basket.xop-cfets-pcf.v1"
INPUT_SCHEMA_VERSION = 1
EXPECTED_REDEMPTION_UNIT = Decimal("1000000")
XOP_CON_ID = 413951498
XOP_SEC_TYPE = "STK"
XOP_EXCHANGE = "SMART"
XOP_PRIMARY_EXCHANGE = "ARCA"
XOP_CURRENCY = "USD"

PCF_URL_TEMPLATE = (
    "https://reportdocs.static.szse.cn/files/text/ETFDown/"
    "pcf_159518_{yyyymmdd}.xml"
)
CFETS_REF_RATE_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-fx/RefRateHis"
CFETS_SOURCE = "CFETS_REFERENCE_RATE"
CFETS_SPOT_SOURCE = "CFETS_SPOT_RATE"
CFETS_PREOPEN_FALLBACK_SOURCE = "CFETS_PREOPEN_FALLBACK"
IB_SOURCE = "IBKR_TWS"
DEFAULT_SOURCE = "mac-home-private-uploader"

DEFAULT_UPLOAD_INTERVAL = 3.0
DEFAULT_CFETS_INTERVAL = 60.0
DEFAULT_PCF_INTERVAL = 10.0 * 60.0
DEFAULT_PCF_START_AT = "08:30"
DEFAULT_PCF_SYMBOL_INTERVAL = 10.0
DEFAULT_TIMEOUT = 10.0
DEFAULT_LOOKBACK_DAYS = 10
MAX_RECONNECT_BACKOFF = 60.0
SHANGHAI = ZoneInfo("Asia/Shanghai")
STOP_EVENT = threading.Event()
PCF_START_MINUTE = 8 * 60 + 30
IB_COLLECTION_START_MINUTE = 9 * 60
IB_COLLECTION_END_MINUTE = 15 * 60

CHROME_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)
SOURCE_HEADERS = {
    "User-Agent": CHROME_USER_AGENT,
    "Accept": "application/json,application/xml,text/xml,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
ALLOWED_MARKET_DATA_TYPES = {"Live", "Frozen", "Delayed", "DelayedFrozen"}
MARKET_DATA_TYPE_NAMES = {
    1: "Live",
    2: "Frozen",
    3: "Delayed",
    4: "DelayedFrozen",
}
ENGLISH_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class InputValidationError(ValueError):
    """Raised when an input is incomplete or could select an unsafe field."""


class SourceUnavailableError(RuntimeError):
    """Raised when a required upstream source has no usable observation."""


@dataclass(frozen=True)
class PCFInput:
    security_id: str
    trading_day: date
    pre_trading_day: date | None
    redemption: str
    creation_redemption_unit: float
    estimate_cash_component_cny: float
    nav_per_cu: float
    component_count: int
    source_url: str
    sha256: str

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "security_id": self.security_id,
            "trading_day": self.trading_day.isoformat(),
            "redemption": self.redemption,
            "creation_redemption_unit": self.creation_redemption_unit,
            "estimate_cash_component_cny": self.estimate_cash_component_cny,
            "nav_per_cu": self.nav_per_cu,
            "component_count": self.component_count,
            "source_url": self.source_url,
            "sha256": self.sha256,
        }
        if self.pre_trading_day is not None:
            payload["pre_trading_day"] = self.pre_trading_day.isoformat()
        return payload


@dataclass(frozen=True)
class CFETSQuote:
    rate: float
    trading_day: date
    quote_time: str
    fetched_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "pair": "USD/CNY",
            "rate": self.rate,
            "trading_day": self.trading_day.isoformat(),
            "quote_time": self.quote_time,
            "source": CFETS_SOURCE,
            "fetched_at": iso_timestamp(self.fetched_at),
        }


@dataclass(frozen=True)
class CFETSSpotQuote:
    pair: str
    rate: float
    trading_day: date
    quote_time: str
    fetched_at: datetime
    source: str = CFETS_SPOT_SOURCE
    source_observed_at: datetime | None = None
    fallback_reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "rate": self.rate,
            "trading_day": self.trading_day.isoformat(),
            "quote_time": self.quote_time,
            "source": self.source,
            **({"source_observed_at": iso_timestamp(self.source_observed_at)} if self.source_observed_at else {}),
            **({"fallback_reason": self.fallback_reason} if self.fallback_reason else {}),
            "fetched_at": iso_timestamp(self.fetched_at),
        }


@dataclass(frozen=True)
class IBQuote:
    bid: float
    ask: float
    last: float | None
    market_data_type: str
    observed_at: datetime
    stream_checked_at: datetime | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "symbol": REFERENCE_SYMBOL,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "market_data_type": self.market_data_type,
            "source": IB_SOURCE,
            "observed_at": iso_timestamp(self.observed_at),
        }
        if self.stream_checked_at is not None:
            payload["stream_checked_at"] = iso_timestamp(self.stream_checked_at)
        return payload


@dataclass(frozen=True)
class SeedInputs:
    pcf: PCFInput | None
    fx: CFETSQuote
    ib: IBQuote
    as_of: date


@dataclass
class CollectorState:
    pcf: PCFInput | None = None
    fx: CFETSQuote | None = None
    ib: IBQuote | None = None

    def missing(self) -> list[str]:
        return [name for name in ("pcf", "fx", "ib") if getattr(self, name) is None]


class PCFRequestPacer:
    """Serialize PCF upstream requests across private symbols.

    This collector currently owns SZ159518 only.  Future symbol collectors must
    share this pacer so the SZSE endpoint sees at most one PCF request every
    configured interval (10 seconds by default), rather than a startup burst.
    """

    def __init__(
        self,
        interval_seconds: float,
        state_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.interval_seconds = max(0.0, interval_seconds)
        self.next_allowed_at = 0.0
        self.lock = threading.Lock()
        default_state_path = Path(tempfile.gettempdir()) / "newnavnav-private-pcf-pacer.json"
        self.state_path = Path(
            state_path
            or os.getenv("NNN_PRIVATE_PCF_PACER_STATE", str(default_state_path))
        ).expanduser()

    def wait(self) -> None:
        """Reserve the next PCF slot across all private collector processes.

        The old in-process timer allowed separately launched fund collectors to
        hit SZSE at the same instant.  A small flock-protected state file keeps
        the configured gap (10 seconds by default) even when every fund has an
        independent launchd job.
        """
        with self.lock:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with self.state_path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    handle.seek(0)
                    try:
                        persisted = json.load(handle)
                        next_allowed_at = float(persisted.get("next_allowed_at", 0.0))
                    except (ValueError, TypeError, json.JSONDecodeError):
                        next_allowed_at = 0.0
                    now = time.time()
                    delay = max(0.0, next_allowed_at - now)
                    if delay and STOP_EVENT.wait(delay):
                        return
                    now = time.time()
                    self.next_allowed_at = now + self.interval_seconds
                    handle.seek(0)
                    handle.truncate()
                    json.dump({"next_allowed_at": self.next_allowed_at}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def shanghai_minute_of_day(value: datetime) -> int:
    local = value.astimezone(SHANGHAI) if value.tzinfo is not None else value.replace(tzinfo=SHANGHAI)
    return local.hour * 60 + local.minute


def is_shanghai_weekday(value: datetime) -> bool:
    local = value.astimezone(SHANGHAI) if value.tzinfo is not None else value.replace(tzinfo=SHANGHAI)
    return local.weekday() < 5


def ib_collection_window(value: datetime) -> bool:
    """IB TWS is contacted only on a Shanghai weekday from 09:00 until 15:00."""

    minute = shanghai_minute_of_day(value)
    return is_shanghai_weekday(value) and IB_COLLECTION_START_MINUTE <= minute < IB_COLLECTION_END_MINUTE


def pcf_collection_window(value: datetime, start_minute: int = PCF_START_MINUTE) -> bool:
    """The daily PCF attempt starts at 08:30, after its normal 08:15 publication."""

    return is_shanghai_weekday(value) and shanghai_minute_of_day(value) >= start_minute


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_values(root: ET.Element, field: str) -> list[str]:
    return [str(child.text or "").strip() for child in root if local_name(child.tag) == field]


def _required_xml_text(root: ET.Element, field: str) -> str:
    values = _direct_values(root, field)
    if len(values) != 1 or not values[0]:
        raise InputValidationError(f"PCF {field} must appear exactly once and be non-empty")
    return values[0]


def _optional_xml_text(root: ET.Element, field: str) -> str:
    values = _direct_values(root, field)
    if len(values) > 1:
        raise InputValidationError(f"PCF {field} must not be duplicated")
    return values[0] if values else ""


def parse_date(value: Any, field: str) -> date:
    text = str(value or "").strip()
    for layout in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, layout).date()
        except ValueError:
            continue
    raise InputValidationError(f"{field} must be YYYY-MM-DD or YYYYMMDD")


def parse_datetime(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise InputValidationError(f"{field} is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InputValidationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed


def iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI)
    return value.isoformat(timespec="milliseconds")


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


def _optional_positive_float(value: Any, field: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return _positive_float(value, field)


def normalize_hour(value: Any, field: str = "quote_time") -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):00(?::00)?", text)
    if not match:
        raise InputValidationError(f"{field} must be an hourly value from 10:00 to 18:00")
    hour = int(match.group(1))
    if hour < 10 or hour > 18:
        raise InputValidationError(f"{field} must be an hourly value from 10:00 to 18:00")
    return f"{hour:02d}:00"


def pcf_url_for_day(trading_day: date) -> str:
    return PCF_URL_TEMPLATE.format(yyyymmdd=trading_day.strftime("%Y%m%d"))


def parse_pcf_xml(
    raw_xml: bytes | str,
    source_url: str,
    expected_trading_day: date | None = None,
) -> PCFInput:
    """Parse the SZSE PCF without ever consulting cash/substitution aliases."""

    if isinstance(raw_xml, str):
        raw_bytes = raw_xml.encode("utf-8")
    else:
        raw_bytes = bytes(raw_xml)
    if not raw_bytes.strip():
        raise InputValidationError("PCF XML is empty")
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        raise InputValidationError(f"PCF XML is invalid: {exc}") from exc
    if local_name(root.tag) != "PCFFile":
        raise InputValidationError("PCF XML root must be PCFFile")

    security_id = _required_xml_text(root, "SecurityID")
    if security_id != SECURITY_ID:
        raise InputValidationError(f"PCF SecurityID must be {SECURITY_ID}")
    trading_day = parse_date(_required_xml_text(root, "TradingDay"), "PCF TradingDay")
    if expected_trading_day is not None and trading_day != expected_trading_day:
        raise InputValidationError(
            f"PCF TradingDay {trading_day.isoformat()} does not match requested "
            f"{expected_trading_day.isoformat()}"
        )
    pre_day_text = _optional_xml_text(root, "PreTradingDay")
    pre_trading_day = parse_date(pre_day_text, "PCF PreTradingDay") if pre_day_text else None

    redemption = _required_xml_text(root, "Redemption").upper()
    if redemption not in {"Y", "N"}:
        raise InputValidationError("PCF Redemption must be Y or N")
    redemption_unit = _decimal(
        _required_xml_text(root, "CreationRedemptionUnit"),
        "PCF CreationRedemptionUnit",
    )
    if redemption_unit != EXPECTED_REDEMPTION_UNIT:
        raise InputValidationError(
            f"PCF CreationRedemptionUnit must equal {EXPECTED_REDEMPTION_UNIT}"
        )

    # Deliberately exact: CashComponent and CreationCashSubstitute are different
    # accounting legs and must never be fallbacks for EstimateCashComponent.
    estimate_cash = _decimal(
        _required_xml_text(root, "EstimateCashComponent"),
        "PCF EstimateCashComponent",
    )
    nav_per_cu = _decimal(_required_xml_text(root, "NAVperCU"), "PCF NAVperCU")
    if nav_per_cu <= 0:
        raise InputValidationError("PCF NAVperCU must be positive")

    components = [element for element in root.iter() if local_name(element.tag) == "Component"]
    xml_component_count = len(components)
    if xml_component_count <= 0:
        raise InputValidationError("PCF must contain at least one Component")
    declared_count = int(
        _decimal(_required_xml_text(root, "TotalRecordNum"), "PCF TotalRecordNum")
    )
    if declared_count <= 0 or Decimal(declared_count) != _decimal(
        _required_xml_text(root, "TotalRecordNum"), "PCF TotalRecordNum"
    ):
        raise InputValidationError("PCF TotalRecordNum must be a positive integer")
    if declared_count != xml_component_count:
        raise InputValidationError(
            f"PCF component count mismatch: TotalRecordNum={declared_count}, "
            f"XML Components={xml_component_count}"
        )

    # The calibrated 996 coefficient was derived from the overseas security
    # basket only. Exclude the 159900 subscription/redemption cash record and
    # any non-overseas/zero-share rows from the component signature.
    security_component_count = 0
    for component in components:
        fields = {
            local_name(child.tag): str(child.text or "").strip()
            for child in component
        }
        try:
            share = _decimal(fields.get("ComponentShare", ""), "PCF ComponentShare")
        except InputValidationError:
            continue
        if fields.get("UnderlyingSecurityIDSource") == "9999" and share > 0:
            security_component_count += 1
    if security_component_count <= 0:
        raise InputValidationError("PCF contains no overseas security components")

    snapshot = PCFInput(
        security_id=security_id,
        trading_day=trading_day,
        pre_trading_day=pre_trading_day,
        redemption=redemption,
        creation_redemption_unit=float(redemption_unit),
        estimate_cash_component_cny=float(estimate_cash),
        nav_per_cu=float(nav_per_cu),
        component_count=security_component_count,
        source_url=str(source_url).strip(),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )
    validate_pcf_input(snapshot)
    return snapshot


def validate_pcf_input(value: PCFInput) -> None:
    if value.security_id != SECURITY_ID:
        raise InputValidationError(f"PCF security_id must be {SECURITY_ID}")
    if value.redemption not in {"Y", "N"}:
        raise InputValidationError("PCF redemption must be Y or N")
    if Decimal(str(value.creation_redemption_unit)) != EXPECTED_REDEMPTION_UNIT:
        raise InputValidationError("PCF creation_redemption_unit must equal 1000000")
    if not math.isfinite(value.estimate_cash_component_cny):
        raise InputValidationError("PCF estimate_cash_component_cny is required and finite")
    if not math.isfinite(value.nav_per_cu) or value.nav_per_cu <= 0:
        raise InputValidationError("PCF nav_per_cu must be positive")
    if value.component_count <= 0:
        raise InputValidationError("PCF component_count must be positive")
    expected_url = pcf_url_for_day(value.trading_day)
    if value.source_url != expected_url:
        raise InputValidationError(f"PCF source_url must be the predictable SZSE URL {expected_url}")
    if not re.fullmatch(r"[0-9a-f]{64}", value.sha256):
        raise InputValidationError("PCF sha256 must be 64 lowercase hexadecimal characters")


def parse_pcf_mapping(mapping: dict[str, Any]) -> PCFInput:
    required = (
        "security_id",
        "trading_day",
        "redemption",
        "creation_redemption_unit",
        "estimate_cash_component_cny",
        "nav_per_cu",
        "component_count",
        "source_url",
        "sha256",
    )
    missing = [key for key in required if key not in mapping or mapping[key] is None]
    if missing:
        raise InputValidationError(f"strict PCF seed is missing: {', '.join(missing)}")
    trading_day = parse_date(mapping["trading_day"], "pcf.trading_day")
    pre_value = mapping.get("pre_trading_day")
    value = PCFInput(
        security_id=str(mapping["security_id"]).strip(),
        trading_day=trading_day,
        pre_trading_day=(
            parse_date(pre_value, "pcf.pre_trading_day") if str(pre_value or "").strip() else None
        ),
        redemption=str(mapping["redemption"]).strip().upper(),
        creation_redemption_unit=_positive_float(
            mapping["creation_redemption_unit"], "pcf.creation_redemption_unit"
        ),
        estimate_cash_component_cny=_finite_float(
            mapping["estimate_cash_component_cny"], "pcf.estimate_cash_component_cny"
        ),
        nav_per_cu=_positive_float(mapping["nav_per_cu"], "pcf.nav_per_cu"),
        component_count=int(mapping["component_count"]),
        source_url=str(mapping["source_url"]).strip(),
        sha256=str(mapping["sha256"]).strip().lower(),
    )
    validate_pcf_input(value)
    return value


def cfets_query_url(trading_day: date) -> str:
    date_text = f"{trading_day.day:02d} {ENGLISH_MONTHS[trading_day.month - 1]} {trading_day.year:04d}"
    query = urllib.parse.urlencode(
        {
            "lang": "cn",
            "startDateTool": date_text,
            "endDateTool": date_text,
            "currencyCode": "ALL",
        }
    )
    return f"{CFETS_REF_RATE_URL}?{query}"


def parse_cfets_response(
    raw_payload: bytes | str | dict[str, Any],
    requested_day: date,
    fetched_at: datetime | None = None,
) -> CFETSQuote | None:
    if isinstance(raw_payload, dict):
        payload = raw_payload
    else:
        text = raw_payload.decode("utf-8") if isinstance(raw_payload, bytes) else raw_payload
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputValidationError(f"CFETS response is not JSON: {exc}") from exc
    records = payload.get("records") or []
    if not records and isinstance(payload.get("data"), dict):
        records = payload["data"].get("records") or []
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("ccyPair") or "").strip().upper() != "USD/CNY":
            continue
        try:
            actual_day = parse_date(record.get("dealDate"), "CFETS dealDate")
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
                rate = _positive_float(raw_rate, f"CFETS rateOf{hour:02d}hour")
            except InputValidationError:
                continue
            hourly.append((hour, rate))
        if not hourly:
            return None
        hour, rate = max(hourly, key=lambda item: item[0])
        return CFETSQuote(
            rate=rate,
            trading_day=actual_day,
            quote_time=f"{hour:02d}:00",
            fetched_at=fetched_at or datetime.now(SHANGHAI),
        )
    return None


def validate_cfets_quote(value: CFETSQuote | CFETSSpotQuote) -> None:
    if not math.isfinite(value.rate) or value.rate <= 0:
        raise InputValidationError("CFETS USD/CNY rate must be positive")
    if isinstance(value, CFETSSpotQuote):
        if value.pair != "USD/CNY":
            raise InputValidationError("XOP CFETS spot pair must be USD/CNY")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value.quote_time):
            raise InputValidationError("CFETS spot fx.quote_time must be HH:MM")
    else:
        normalize_hour(value.quote_time, "fx.quote_time")
    if value.fetched_at.tzinfo is None:
        raise InputValidationError("fx.fetched_at must include a timezone")


def parse_cfets_mapping(mapping: dict[str, Any], fallback_timestamp: Any = None) -> CFETSQuote:
    if str(mapping.get("pair") or "").strip().upper() != "USD/CNY":
        raise InputValidationError("fx.pair must be USD/CNY")
    source = str(mapping.get("source") or "").strip().upper()
    if source not in {CFETS_SOURCE, "CFETS"}:
        raise InputValidationError("fx.source must be CFETS, never SAFE")
    fetched_value = mapping.get("fetched_at") or fallback_timestamp
    value = CFETSQuote(
        rate=_positive_float(mapping.get("rate"), "fx.rate"),
        trading_day=parse_date(mapping.get("trading_day"), "fx.trading_day"),
        quote_time=normalize_hour(mapping.get("quote_time"), "fx.quote_time"),
        fetched_at=parse_datetime(fetched_value, "fx.fetched_at"),
    )
    validate_cfets_quote(value)
    return value


def validate_ib_quote(value: IBQuote) -> None:
    if not math.isfinite(value.bid) or value.bid <= 0:
        raise InputValidationError("IB bid must be positive")
    if not math.isfinite(value.ask) or value.ask <= 0:
        raise InputValidationError("IB ask must be positive")
    if value.ask < value.bid:
        raise InputValidationError("IB ask must be greater than or equal to bid")
    if value.last is not None and (not math.isfinite(value.last) or value.last <= 0):
        raise InputValidationError("IB last must be positive when present")
    if value.market_data_type not in ALLOWED_MARKET_DATA_TYPES:
        raise InputValidationError(
            "IB market_data_type must be Live, Frozen, Delayed, or DelayedFrozen"
        )
    if value.observed_at.tzinfo is None:
        raise InputValidationError("ib.observed_at must include a timezone")
    if value.stream_checked_at is not None and value.stream_checked_at.tzinfo is None:
        raise InputValidationError("ib.stream_checked_at must include a timezone")


def parse_ib_mapping(mapping: dict[str, Any]) -> IBQuote:
    if str(mapping.get("symbol") or "").strip().upper() != REFERENCE_SYMBOL:
        raise InputValidationError(f"ib.symbol must be {REFERENCE_SYMBOL}")
    source = str(mapping.get("source") or IB_SOURCE).strip().upper()
    if source not in {IB_SOURCE, "IBKR", "IB"}:
        raise InputValidationError("ib.source must be IBKR_TWS")
    value = IBQuote(
        bid=_positive_float(mapping.get("bid"), "ib.bid"),
        ask=_positive_float(mapping.get("ask"), "ib.ask"),
        last=_optional_positive_float(mapping.get("last"), "ib.last"),
        market_data_type=str(mapping.get("market_data_type") or "").strip(),
        observed_at=parse_datetime(
            mapping.get("observed_at") or mapping.get("received_at"),
            "ib.observed_at",
        ),
        stream_checked_at=(
            parse_datetime(mapping.get("stream_checked_at"), "ib.stream_checked_at")
            if mapping.get("stream_checked_at") else None
        ),
    )
    validate_ib_quote(value)
    return value


def build_private_payload(
    pcf: PCFInput,
    fx: CFETSQuote | CFETSSpotQuote,
    ib: IBQuote,
    *,
    generated_at: datetime | None = None,
    source: str = DEFAULT_SOURCE,
) -> dict[str, Any]:
    validate_pcf_input(pcf)
    validate_cfets_quote(fx)
    validate_ib_quote(ib)
    source = str(source or "").strip()
    if not source:
        raise InputValidationError("source is required")
    now = generated_at or datetime.now(SHANGHAI)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI)
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "symbol": SYMBOL,
        "model_version": MODEL_VERSION,
        "pcf": pcf.to_payload(),
        "fx": fx.to_payload(),
        "ib": ib.to_payload(),
        "source": source,
        "generated_at": iso_timestamp(now),
    }


# Short alias kept convenient for tests and small deployment helpers.
build_payload = build_private_payload


def load_input_file(path: str | os.PathLike[str]) -> SeedInputs:
    input_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputValidationError(f"cannot read --input-file {input_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InputValidationError("--input-file must contain one JSON object")

    if all(isinstance(payload.get(key), dict) for key in ("pcf", "fx", "ib")):
        if int(payload.get("schema_version") or 0) != INPUT_SCHEMA_VERSION:
            raise InputValidationError("input seed schema_version must be 1")
        if str(payload.get("symbol") or "").strip().upper() != SYMBOL:
            raise InputValidationError(f"input seed symbol must be {SYMBOL}")
        model = str(payload.get("model_version") or MODEL_VERSION).strip()
        if model != MODEL_VERSION:
            raise InputValidationError(f"input seed model_version must be {MODEL_VERSION}")
        pcf = parse_pcf_mapping(payload["pcf"])
        fx = parse_cfets_mapping(payload["fx"], payload.get("generated_at"))
        ib = parse_ib_mapping(payload["ib"])
        return SeedInputs(pcf=pcf, fx=fx, ib=ib, as_of=pcf.trading_day)

    valuation = payload.get("valuation")
    if not isinstance(valuation, dict):
        raise InputValidationError(
            "--input-file must be a private input contract or calculator realtime.json"
        )
    if str(payload.get("symbol") or "").strip().upper() != SYMBOL:
        raise InputValidationError(f"realtime.json symbol must be {SYMBOL}")
    xop = valuation.get("xop")
    fx_mapping = valuation.get("fx")
    if not isinstance(xop, dict) or not isinstance(fx_mapping, dict):
        raise InputValidationError("realtime.json must contain valuation.xop and valuation.fx")
    ib = parse_ib_mapping(xop)
    fx = parse_cfets_mapping(fx_mapping, payload.get("generated_at"))
    raw_trade_day = payload.get("trade_date") or fx.trading_day.isoformat()
    as_of = parse_date(raw_trade_day, "realtime.json trade_date")

    # realtime.json historically contained only a cash number, without the XML
    # identity, redemption flag, NAVperCU, component count, source URL or hash.
    # It is therefore never accepted as a PCF seed; the strict SZSE XML path is
    # fetched or loaded from the isolated cache instead.
    return SeedInputs(pcf=None, fx=fx, ib=ib, as_of=as_of)


def _http_fetch(
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bytes:
    request = urllib.request.Request(url, data=data, headers=headers or SOURCE_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class PCFClient:
    def __init__(
        self,
        cache_root: str | os.PathLike[str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        refresh_seconds: float = DEFAULT_PCF_INTERVAL,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        fetch_bytes: Callable[..., bytes] | None = None,
        request_pacer: PCFRequestPacer | None = None,
    ) -> None:
        self.cache_root = Path(cache_root).expanduser().resolve() / "pcf"
        self.timeout = timeout
        self.refresh_seconds = max(0.0, refresh_seconds)
        self.lookback_days = max(0, lookback_days)
        self.fetch_bytes = fetch_bytes or _http_fetch
        self.request_pacer = request_pacer

    def cache_path(self, trading_day: date) -> Path:
        return self.cache_root / trading_day.isoformat() / f"{SECURITY_ID}.xml"

    def _load_cache(self, trading_day: date) -> PCFInput | None:
        path = self.cache_path(trading_day)
        if not path.is_file():
            return None
        try:
            return parse_pcf_xml(path.read_bytes(), pcf_url_for_day(trading_day), trading_day)
        except (OSError, InputValidationError) as exc:
            log(f"ignoring invalid private PCF cache {path}: {exc}", error=True)
            return None

    def fetch_latest(self, as_of: date | None = None, *, force_refresh: bool = False) -> PCFInput:
        current = as_of or datetime.now(SHANGHAI).date()
        errors: list[str] = []
        for offset in range(self.lookback_days + 1):
            candidate = current - timedelta(days=offset)
            if candidate.weekday() >= 5:
                continue
            cache_path = self.cache_path(candidate)
            cached = self._load_cache(candidate)
            cache_fresh = False
            if cached is not None and cache_path.exists():
                cache_fresh = time.time() - cache_path.stat().st_mtime < self.refresh_seconds
            # Historical files are immutable.  Today's file is refreshed every
            # configured PCF interval, with its last validated cache as fallback.
            if cached is not None and (candidate < current or (cache_fresh and not force_refresh)):
                return cached

            source_url = pcf_url_for_day(candidate)
            try:
                if self.request_pacer is not None:
                    self.request_pacer.wait()
                raw = self.fetch_bytes(source_url, None, SOURCE_HEADERS, self.timeout)
                parsed = parse_pcf_xml(raw, source_url, candidate)
                _atomic_write_bytes(cache_path, bytes(raw))
                return parsed
            except urllib.error.HTTPError as exc:
                if exc.code in {404, 410}:
                    errors.append(f"{candidate.isoformat()}: HTTP {exc.code}")
                    continue
                errors.append(f"{candidate.isoformat()}: HTTP {exc.code}")
            except (urllib.error.URLError, TimeoutError, OSError, InputValidationError) as exc:
                errors.append(f"{candidate.isoformat()}: {exc}")
            if cached is not None:
                return cached
        suffix = " | ".join(errors[-3:])
        raise SourceUnavailableError(
            f"no strict {SECURITY_ID} SZSE PCF in {self.lookback_days} day lookback"
            + (f": {suffix}" if suffix else "")
        )


class CFETSClient:
    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        fetch_bytes: Callable[..., bytes] | None = None,
    ) -> None:
        self.timeout = timeout
        self.lookback_days = max(0, lookback_days)
        self.fetch_bytes = fetch_bytes or _http_fetch

    def fetch_latest(self, as_of: date | None = None) -> CFETSQuote:
        current = as_of or datetime.now(SHANGHAI).date()
        errors: list[str] = []
        for offset in range(self.lookback_days + 1):
            candidate = current - timedelta(days=offset)
            if candidate.weekday() >= 5:
                continue
            fetched_at = datetime.now(SHANGHAI)
            try:
                # Chinamoney's RefRateHis endpoint expects an empty POST body.
                raw = self.fetch_bytes(
                    cfets_query_url(candidate),
                    b"",
                    SOURCE_HEADERS,
                    self.timeout,
                )
                quote = parse_cfets_response(raw, candidate, fetched_at)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, InputValidationError) as exc:
                errors.append(f"{candidate.isoformat()}: {exc}")
                continue
            if quote is not None:
                return quote
            errors.append(f"{candidate.isoformat()}: no USD/CNY 10:00-18:00 hour")
        suffix = " | ".join(errors[-3:])
        raise SourceUnavailableError(
            f"no CFETS USD/CNY hourly reference rate in {self.lookback_days} day lookback"
            + (f": {suffix}" if suffix else "")
        )


def parse_rfc3339_nano(value: Any, field: str) -> datetime:
    """Parse Go RFC3339Nano timestamps on the Mac-home Python 3.10 runtime."""
    text = str(value or "").strip()
    if not text:
        raise InputValidationError(f"{field} is required")
    text = re.sub(
        r"(\.\d{6})\d+(?=(?:Z|[+-]\d{2}:\d{2})$)",
        r"\1",
        text,
    )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputValidationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise InputValidationError(f"{field} must carry a timezone")
    return parsed


class PrivateCFETSSpotClient:
    """Read the website's shared two-minute ChinaMoney spot snapshot.

    This client never calls ChinaMoney.  The dmit HK Connect service owns the
    single upstream request and exposes all required currency pairs from that
    one response through /api/v1/private/hk-connect-fx.
    """

    _UPSTREAM_PAIRS = {
        "USD/CNY": ("USD/CNY", 1.0),
        "HKD/CNY": ("HKD/CNY", 1.0),
        "EUR/CNY": ("EUR/CNY", 1.0),
        "JPY/CNY": ("100JPY/CNY", 100.0),
    }

    def __init__(
        self,
        server: str,
        token: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        origin_ip: str = "",
        origin_tls_insecure: bool = False,
        origin_ca_file: str = "",
    ) -> None:
        self.server = server.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.origin_ip = origin_ip
        self.origin_tls_insecure = origin_tls_insecure
        self.origin_ca_file = origin_ca_file

    def fetch_many(
        self, pairs: tuple[str, ...] | list[str], as_of: date | None = None,
    ) -> tuple[CFETSSpotQuote, ...]:
        requested = tuple(str(pair).strip().upper() for pair in pairs)
        if not requested or len(set(requested)) != len(requested):
            raise InputValidationError("CFETS spot pairs must be non-empty and unique")
        unsupported = [pair for pair in requested if pair not in self._UPSTREAM_PAIRS]
        if unsupported:
            raise InputValidationError("unsupported CFETS spot pair(s): " + ", ".join(unsupported))
        request = urllib.request.Request(
            self.server + "/api/v1/private/hk-connect-fx",
            headers=server_headers(self.server, self.token),
        )
        try:
            with open_server_request(
                request,
                self.timeout,
                self.origin_ip,
                self.origin_tls_insecure,
                self.origin_ca_file,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise SourceUnavailableError(f"shared CFETS spot snapshot unavailable: {exc}") from exc
        rows = payload.get("fx_quotes") if isinstance(payload, dict) else None
        if not isinstance(rows, dict):
            raise SourceUnavailableError("shared CFETS spot snapshot has no fx_quotes map")
        expected_day = as_of or datetime.now(SHANGHAI).date()
        result: list[CFETSSpotQuote] = []
        for pair in requested:
            upstream_pair, divisor = self._UPSTREAM_PAIRS[pair]
            row = rows.get(upstream_pair)
            if not isinstance(row, dict) or not row.get("healthy"):
                detail = str(row.get("error") or "unhealthy") if isinstance(row, dict) else "missing"
                raise SourceUnavailableError(f"shared CFETS spot {upstream_pair} is {detail}")
            try:
                bid = float(row["bid"])
                ask = float(row["ask"])
                observed_at = parse_rfc3339_nano(row["observed_at"], f"{upstream_pair}.observed_at")
                received_at = parse_rfc3339_nano(row["received_at"], f"{upstream_pair}.received_at")
            except (KeyError, TypeError, ValueError, InputValidationError) as exc:
                raise SourceUnavailableError(f"shared CFETS spot {upstream_pair} is malformed") from exc
            if (
                not math.isfinite(bid)
                or not math.isfinite(ask)
                or bid <= 0
                or ask < bid
                or observed_at.tzinfo is None
                or received_at.tzinfo is None
            ):
                raise SourceUnavailableError(f"shared CFETS spot {upstream_pair} has invalid price or timestamp")
            local_observed = observed_at.astimezone(SHANGHAI)
            if local_observed.date() != expected_day:
                raise SourceUnavailableError(
                    f"shared CFETS spot {upstream_pair} day {local_observed.date()} is not {expected_day}"
                )
            now = datetime.now(SHANGHAI)
            if expected_day == now.date() and not (-30 <= (now-observed_at).total_seconds() <= 180):
                raise SourceUnavailableError(f"shared CFETS spot {upstream_pair} source quote is stale")
            result.append(CFETSSpotQuote(
                pair=pair,
                rate=((bid + ask) / 2.0) / divisor,
                trading_day=local_observed.date(),
                quote_time=local_observed.strftime("%H:%M"),
                fetched_at=received_at.astimezone(SHANGHAI),
                source_observed_at=observed_at.astimezone(SHANGHAI),
            ))
        return tuple(result)

    def fetch(self, pair: str, as_of: date | None = None) -> CFETSSpotQuote:
        return self.fetch_many((pair,), as_of)[0]

    def fetch_preopen_fallback(self, pair: str, as_of: date, now: datetime) -> CFETSSpotQuote:
        """Return the retained last healthy spot only for 09:15–09:35 BJT.

        The public snapshot deliberately retains its last valid BID/ASK while
        marking it unhealthy when ChinaMoney publishes placeholders before the
        opening quote.  This method keeps that original trading day/time and
        makes the exceptional source explicit; it never manufactures a current
        CFETS observation.
        """
        local_now = now.astimezone(SHANGHAI)
        minute = local_now.hour * 60 + local_now.minute
        if now.tzinfo is None or local_now.date() != as_of or local_now.weekday() >= 5 or minute < 9 * 60 + 15 or minute >= 9 * 60 + 35:
            raise SourceUnavailableError("CFETS pre-open fallback is outside the 09:15–09:35 Shanghai window")
        requested = str(pair).strip().upper()
        if requested not in self._UPSTREAM_PAIRS:
            raise InputValidationError(f"unsupported CFETS spot pair {requested}")
        upstream_pair, divisor = self._UPSTREAM_PAIRS[requested]
        request = urllib.request.Request(
            self.server + "/api/v1/private/hk-connect-fx",
            headers=server_headers(self.server, self.token),
        )
        try:
            with open_server_request(request, self.timeout, self.origin_ip, self.origin_tls_insecure, self.origin_ca_file) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise SourceUnavailableError(f"shared CFETS pre-open snapshot unavailable: {exc}") from exc
        rows = (payload.get("last_healthy_fx_quotes") or payload.get("fx_quotes")) if isinstance(payload, dict) else None
        row = rows.get(upstream_pair) if isinstance(rows, dict) else None
        if not isinstance(row, dict):
            raise SourceUnavailableError(f"shared CFETS pre-open {upstream_pair} is missing")
        try:
            bid, ask = float(row["bid"]), float(row["ask"])
            observed_at = parse_rfc3339_nano(row["observed_at"], f"{upstream_pair}.observed_at")
            received_at = parse_rfc3339_nano(row["received_at"], f"{upstream_pair}.received_at")
        except (KeyError, TypeError, ValueError, InputValidationError) as exc:
            raise SourceUnavailableError(f"shared CFETS pre-open {upstream_pair} is malformed") from exc
        local_observed = observed_at.astimezone(SHANGHAI)
        if row.get("source") != "CFETS_CHINAMONEY" or not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask < bid:
            raise SourceUnavailableError(f"shared CFETS pre-open {upstream_pair} has no retained prior quote")
        if local_observed.date() >= as_of:
            raise SourceUnavailableError(f"shared CFETS pre-open {upstream_pair} day {local_observed.date()} is not a recent prior trading day")
        return CFETSSpotQuote(
            pair=requested,
            rate=((bid + ask) / 2.0) / divisor,
            trading_day=local_observed.date(),
            quote_time=local_observed.strftime("%H:%M"),
            fetched_at=received_at.astimezone(SHANGHAI),
            source=CFETS_PREOPEN_FALLBACK_SOURCE,
            source_observed_at=local_observed,
            fallback_reason="CURRENT_DAY_CFETS_UNAVAILABLE",
        )


class IBQuoteStream:
    """Small ib_insync stream pinned to the audited XOP contract identity."""

    def __init__(self, host: str, port: int, client_id: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.ib = None
        self.contract = None
        self.ticker = None

    def connect(self) -> None:
        try:
            from ib_insync import Contract, IB
        except ImportError as exc:
            raise RuntimeError(
                "ib_insync is required; install it in the Python used by launchd"
            ) from exc
        self.disconnect()
        ib = IB()
        contract = Contract(
            conId=XOP_CON_ID,
            symbol=REFERENCE_SYMBOL,
            secType=XOP_SEC_TYPE,
            exchange=XOP_EXCHANGE,
            primaryExchange=XOP_PRIMARY_EXCHANGE,
            currency=XOP_CURRENCY,
        )
        try:
            # The quote-only collector avoids account/position synchronization.
            ib.wrapper.clientId = self.client_id
            ib.client.connect(self.host, self.port, self.client_id, timeout=self.timeout)
            if not ib.isConnected():
                raise RuntimeError("TWS API socket did not become connected")
            qualified = ib.qualifyContracts(contract)
            if not qualified:
                raise RuntimeError("TWS could not qualify XOP conId 413951498")
            contract = qualified[0]
            if int(getattr(contract, "conId", 0) or 0) != XOP_CON_ID:
                raise RuntimeError(f"qualified unexpected XOP conId {getattr(contract, 'conId', None)}")
            if str(getattr(contract, "secType", "") or "") != XOP_SEC_TYPE:
                raise RuntimeError("qualified XOP secType is not STK")
            if str(getattr(contract, "currency", "") or "") != XOP_CURRENCY:
                raise RuntimeError("qualified XOP currency is not USD")
            ib.reqMarketDataType(1)
            ticker = ib.reqMktData(contract, "", False, False)
        except Exception:
            if ib.isConnected():
                ib.disconnect()
            raise
        self.ib = ib
        self.contract = contract
        self.ticker = ticker

    def is_connected(self) -> bool:
        return bool(self.ib is not None and self.ib.isConnected())

    def poll(self, wait_seconds: float = 0.05) -> IBQuote | None:
        if not self.is_connected() or self.ticker is None:
            raise SourceUnavailableError("IB TWS stream is disconnected")
        self.ib.sleep(max(0.0, wait_seconds))
        if not self.is_connected():
            raise SourceUnavailableError("IB TWS stream disconnected while polling")
        bid = raw_positive_price(getattr(self.ticker, "bid", None))
        ask = raw_positive_price(getattr(self.ticker, "ask", None))
        last = raw_positive_price(getattr(self.ticker, "last", None))
        market_type_id = int(getattr(self.ticker, "marketDataType", 0) or 0)
        market_data_type = MARKET_DATA_TYPE_NAMES.get(market_type_id)
        observed_at = ib_ticker_observed_at(getattr(self.ticker, "time", None))
        if (
            bid is None
            or ask is None
            or market_data_type is None
            or ask < bid
            or observed_at is None
        ):
            return None
        quote = IBQuote(
            bid=bid,
            ask=ask,
            last=last,
            market_data_type=market_data_type,
            # Ticker.time is set by ib_insync when a market-data packet arrives.
            # Never replace it with the local poll time: doing so would make a
            # closed or frozen market look continuously fresh.
            observed_at=observed_at,
            stream_checked_at=datetime.now(SHANGHAI),
        )
        validate_ib_quote(quote)
        return quote

    def disconnect(self) -> None:
        ib = self.ib
        if ib is None:
            return
        if self.contract is not None and ib.isConnected():
            try:
                ib.cancelMktData(self.contract)
            except Exception:
                pass
        if ib.isConnected():
            ib.disconnect()
        self.ib = None
        self.contract = None
        self.ticker = None


def raw_positive_price(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def ib_ticker_observed_at(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SHANGHAI)


class OriginHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, *args, origin_ip: str = "", **kwargs):
        self.origin_ip = origin_ip
        super().__init__(host, *args, **kwargs)

    def connect(self) -> None:
        self.sock = self._create_connection(
            (self.origin_ip, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            self._tunnel()


class OriginHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, *args, origin_ip: str = "", **kwargs):
        self.origin_ip = origin_ip
        super().__init__(host, *args, **kwargs)

    def connect(self) -> None:
        sock = self._create_connection(
            (self.origin_ip, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class OriginHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, origin_ip: str):
        self.origin_ip = origin_ip
        super().__init__()

    def http_open(self, request):
        def factory(host, **kwargs):
            return OriginHTTPConnection(host, origin_ip=self.origin_ip, **kwargs)

        return self.do_open(factory, request)


class OriginHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(
        self,
        origin_ip: str,
        origin_tls_insecure: bool = False,
        origin_ca_file: str = "",
    ) -> None:
        self.origin_ip = origin_ip
        super().__init__(context=origin_ssl_context(origin_tls_insecure, origin_ca_file))

    def https_open(self, request):
        def factory(host, **kwargs):
            return OriginHTTPSConnection(
                host,
                origin_ip=self.origin_ip,
                context=self._context,
                **kwargs,
            )

        return self.do_open(factory, request)


def origin_ssl_context(
    origin_tls_insecure: bool = False,
    origin_ca_file: str = "",
) -> ssl.SSLContext:
    if origin_tls_insecure:
        return ssl._create_unverified_context()
    if origin_ca_file.strip():
        return ssl.create_default_context(cafile=origin_ca_file.strip())
    return ssl.create_default_context()


def open_server_request(
    request: urllib.request.Request,
    timeout: float,
    origin_ip: str = "",
    origin_tls_insecure: bool = False,
    origin_ca_file: str = "",
):
    origin_ip = origin_ip.strip()
    if not origin_ip:
        return urllib.request.urlopen(request, timeout=timeout)
    opener = urllib.request.build_opener(
        OriginHTTPHandler(origin_ip),
        OriginHTTPSHandler(origin_ip, origin_tls_insecure, origin_ca_file),
    )
    return opener.open(request, timeout=timeout)


def server_headers(server: str, token: str) -> dict[str, str]:
    headers = {
        "X-Upload-Token": token,
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Content-Type": "application/json",
        "Referer": server.rstrip("/") + "/private/",
        "User-Agent": CHROME_USER_AGENT,
    }
    data_view_token = str(
        os.getenv("NNN_DATA_VIEW_TOKEN", os.getenv("DATA_VIEW_TOKEN", ""))
    ).strip()
    if data_view_token:
        headers["X-Data-View-Token"] = data_view_token
    return headers


def gzip_json_body(payload: dict[str, Any]) -> bytes:
    return gzip.compress(
        json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        compresslevel=6,
    )


def gzip_server_headers(server: str, token: str) -> dict[str, str]:
    headers = server_headers(server, token)
    headers["Content-Encoding"] = "gzip"
    return headers


def post_private_inputs(
    server: str,
    token: str,
    payload: dict[str, Any],
    timeout: float,
    origin_ip: str = "",
    origin_tls_insecure: bool = False,
    origin_ca_file: str = "",
) -> dict[str, Any]:
    body = gzip_json_body(payload)
    request = urllib.request.Request(
        server.rstrip("/") + f"/api/v1/private/inputs/{SYMBOL}",
        data=body,
        method="POST",
        headers=gzip_server_headers(server, token),
    )
    try:
        with open_server_request(
            request,
            timeout,
            origin_ip,
            origin_tls_insecure,
            origin_ca_file,
        ) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"private upload status {exc.code}: {detail}") from exc
    try:
        parsed = json.loads(response_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("private upload returned invalid JSON") from exc
    if not isinstance(parsed, dict) or parsed.get("error"):
        raise RuntimeError(str(parsed.get("error") if isinstance(parsed, dict) else parsed))
    if parsed.get("ok") is not True:
        raise RuntimeError(f"private upload was not acknowledged: {parsed}")
    return parsed


def post_private_input_batch(
    server: str,
    token: str,
    inputs: list[dict[str, Any]],
    timeout: float,
    *,
    source: str,
    generated_at: datetime | None = None,
    batch_id: str = "",
    origin_ip: str = "",
    origin_tls_insecure: bool = False,
    origin_ca_file: str = "",
) -> dict[str, Any]:
    if not inputs:
        raise InputValidationError("private input batch must not be empty")
    symbols = [str(item.get("symbol") or "").strip().upper() for item in inputs]
    if any(not symbol for symbol in symbols) or len(set(symbols)) != len(symbols):
        raise InputValidationError("private input batch symbols must be non-empty and unique")
    source = str(source or "").strip()
    if not source:
        raise InputValidationError("private input batch source is required")
    timestamp = generated_at or datetime.now(SHANGHAI)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SHANGHAI)
    batch_id = str(batch_id or "").strip() or (
        f"{source}-{timestamp.strftime('%Y%m%dT%H%M%S.%f')}-{os.getpid()}"
    )
    payload = {
        "schema_version": 1,
        "batch_id": batch_id,
        "source": source,
        "generated_at": iso_timestamp(timestamp),
        "inputs": inputs,
    }
    try:
        request = urllib.request.Request(
            server.rstrip("/") + "/api/v1/private/inputs/batch",
            data=gzip_json_body(payload),
            method="POST",
            headers=gzip_server_headers(server, token),
        )
        try:
            with open_server_request(
                request,
                timeout,
                origin_ip,
                origin_tls_insecure,
                origin_ca_file,
            ) as response:
                response_body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"private batch upload status {exc.code}: {detail}") from exc
        try:
            parsed = json.loads(response_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("private batch upload returned invalid JSON") from exc
        if not isinstance(parsed, dict) or parsed.get("error"):
            raise RuntimeError(str(parsed.get("error") if isinstance(parsed, dict) else parsed))
        if parsed.get("batch_id") != batch_id:
            raise RuntimeError("private batch upload acknowledgement has the wrong batch_id")
        accepted = parsed.get("accepted")
        rejected = parsed.get("rejected")
        if not isinstance(accepted, list) or not isinstance(rejected, dict):
            raise RuntimeError(f"private batch upload acknowledgement is incomplete: {parsed}")
    except Exception as exc:
        upload_health.record_failure(source, stage="private_batch_upload", error=exc)
        raise
    accepted_symbols = {str(item).strip().upper() for item in accepted}
    rejected_symbols = {str(item).strip().upper() for item in rejected}
    if rejected_symbols or accepted_symbols != set(symbols):
        upload_health.record_failure(
            source,
            stage="private_batch_ack",
            error=f"accepted={len(accepted_symbols)}/{len(symbols)} rejected={sorted(rejected_symbols)}",
        )
    else:
        upload_health.record_success(
            source,
            stage="private_batch_ack",
            accepted=len(accepted_symbols),
            symbols=symbols,
        )
    return parsed


def parse_duration(value: Any, fallback: float) -> float:
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        parsed = float(text)
    except ValueError:
        parsed = -1.0
    if parsed >= 0:
        return parsed
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)(ms|s|m|h)", text)
    if not match:
        raise InputValidationError(f"invalid duration: {text}")
    amount = float(match.group(1))
    factor = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group(2)]
    return amount * factor


def parse_clock_minute(value: Any, fallback: int) -> int:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not match:
        return fallback
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return fallback
    return hour * 60 + minute


def is_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_env_file(path: str | os.PathLike[str]) -> None:
    env_path = Path(path)
    if not env_path.is_file():
        return
    with env_path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def log(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    timestamp = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    print(f"{timestamp} {message}", file=stream, flush=True)


def _request_stop(_signum, _frame) -> None:
    STOP_EVENT.set()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect strict SZ159518 PCF/CFETS/XOP inputs for /private."
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
    parser.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", DEFAULT_TIMEOUT)))
    parser.add_argument(
        "--source",
        default=os.getenv("NNN_PRIVATE_UPLOAD_SOURCE", DEFAULT_SOURCE),
    )
    parser.add_argument(
        "--runtime-dir",
        default=os.getenv(
            "NNN_PRIVATE_RUNTIME_DIR",
            "scripts/.runtime/private_valuation",
        ),
    )
    parser.add_argument(
        "--upload-interval",
        default=os.getenv("NNN_PRIVATE_UPLOAD_INTERVAL", "3s"),
    )
    parser.add_argument(
        "--cfets-interval",
        default=os.getenv("NNN_PRIVATE_CFETS_INTERVAL", "60s"),
    )
    parser.add_argument(
        "--pcf-interval",
        default=os.getenv("NNN_PRIVATE_PCF_INTERVAL", "10m"),
    )
    parser.add_argument(
        "--pcf-start-at",
        default=os.getenv("NNN_PRIVATE_PCF_START_AT", DEFAULT_PCF_START_AT),
        help="Shanghai daily PCF start time; defaults to 08:30 after the usual 08:15 publication.",
    )
    parser.add_argument(
        "--pcf-symbol-interval",
        default=os.getenv("NNN_PRIVATE_PCF_SYMBOL_INTERVAL", "10s"),
        help="Shared sequential PCF pacing interval for future multi-symbol collection.",
    )
    parser.add_argument(
        "--cfets-lookback-days",
        type=int,
        default=int(os.getenv("NNN_PRIVATE_CFETS_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)),
    )
    parser.add_argument(
        "--pcf-lookback-days",
        type=int,
        default=int(os.getenv("NNN_PRIVATE_PCF_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)),
    )
    parser.add_argument(
        "--ib-host",
        default=os.getenv("NNN_PRIVATE_IB_HOST", os.getenv("NNN_IB_HOST", "127.0.0.1")),
    )
    parser.add_argument(
        "--ib-port",
        type=int,
        default=int(os.getenv("NNN_PRIVATE_IB_PORT", os.getenv("NNN_IB_PORT", "7496"))),
    )
    parser.add_argument(
        "--ib-client-id",
        type=int,
        default=int(os.getenv("NNN_PRIVATE_IB_CLIENT_ID", "15918")),
    )
    parser.add_argument(
        "--ib-ready-timeout",
        type=float,
        default=float(os.getenv("NNN_PRIVATE_IB_READY_TIMEOUT", "20")),
    )
    parser.add_argument(
        "--input-file",
        default=os.getenv("NNN_PRIVATE_INPUT_FILE", ""),
        help="Optional private contract or calculator realtime.json seed.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=is_true(os.getenv("NNN_PRIVATE_UPLOAD_ONCE", "")),
    )
    return parser


def run_once(args, pcf_client: PCFClient, cfets_client: CFETSClient) -> int:
    if not ib_collection_window(datetime.now(SHANGHAI)):
        log("IB collection is scheduled for Shanghai weekdays 09:00-15:05; nothing uploaded", error=True)
        return 1
    seed = load_input_file(args.input_file) if args.input_file else None
    as_of = seed.as_of if seed is not None else datetime.now(SHANGHAI).date()
    state = CollectorState(
        pcf=seed.pcf if seed else None,
        fx=seed.fx if seed else None,
        ib=seed.ib if seed else None,
    )
    try:
        state.pcf = pcf_client.fetch_latest(as_of)
    except Exception as exc:
        if state.pcf is None:
            log(f"PCF unavailable; fail closed without upload: {exc}", error=True)
            return 1
        log(f"using strict PCF seed after refresh failure: {exc}", error=True)

    if state.fx is None:
        try:
            state.fx = cfets_client.fetch_latest(as_of)
        except Exception as exc:
            log(f"CFETS unavailable; fail closed without upload: {exc}", error=True)
            return 1

    stream = None
    if state.ib is None:
        stream = IBQuoteStream(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
        try:
            stream.connect()
            deadline = time.monotonic() + max(0.1, args.ib_ready_timeout)
            while time.monotonic() < deadline and not STOP_EVENT.is_set():
                quote = stream.poll(0.1)
                if quote is not None:
                    state.ib = quote
                    break
        except Exception as exc:
            log(f"IB unavailable; fail closed without upload: {exc}", error=True)
        finally:
            stream.disconnect()
    if state.missing():
        log(f"missing required private inputs {state.missing()}; nothing uploaded", error=True)
        return 1

    payload = build_private_payload(
        state.pcf,
        state.fx,
        state.ib,
        source=args.source,
    )
    post_private_inputs(
        args.server,
        args.token,
        payload,
        args.timeout,
        args.origin_ip,
        args.origin_tls_insecure,
        args.origin_ca_file,
    )
    log(
        "private inputs uploaded once "
        f"pcf={state.pcf.trading_day.isoformat()} "
        f"cfets={state.fx.trading_day.isoformat()}T{state.fx.quote_time} "
        f"ib={state.ib.market_data_type}"
    )
    return 0


def run_forever(args, pcf_client: PCFClient, cfets_client: CFETSClient) -> int:
    state = CollectorState()
    if args.input_file:
        seed = load_input_file(args.input_file)
        state = CollectorState(seed.pcf, seed.fx, seed.ib)
        log("loaded --input-file as a startup seed; live sources will replace FX/IB")

    upload_interval = max(0.1, parse_duration(args.upload_interval, DEFAULT_UPLOAD_INTERVAL))
    cfets_interval = max(1.0, parse_duration(args.cfets_interval, DEFAULT_CFETS_INTERVAL))
    pcf_retry_interval = max(10.0, parse_duration(args.pcf_interval, DEFAULT_PCF_INTERVAL))
    pcf_start_minute = parse_clock_minute(args.pcf_start_at, PCF_START_MINUTE)
    stream = IBQuoteStream(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
    next_pcf = 0.0
    next_cfets = 0.0
    next_ib_connect = 0.0
    next_upload = 0.0
    next_missing_log = 0.0
    pcf_ready_for_day: date | None = None
    ib_backoff = 1.0
    upload_backoff = upload_interval

    try:
        while not STOP_EVENT.is_set():
            now_mono = time.monotonic()
            now_local = datetime.now(SHANGHAI)
            today = now_local.date()
            if (
                now_mono >= next_pcf
                and pcf_ready_for_day != today
                and pcf_collection_window(now_local, pcf_start_minute)
            ):
                try:
                    pcf = pcf_client.fetch_latest(today, force_refresh=True)
                    if pcf.trading_day != today:
                        raise SourceUnavailableError(
                            f"today's strict PCF is not ready (got {pcf.trading_day.isoformat()})"
                        )
                    state.pcf = pcf
                    pcf_ready_for_day = today
                    log(
                        f"strict PCF ready trading_day={state.pcf.trading_day.isoformat()} "
                        f"components={state.pcf.component_count} sha256={state.pcf.sha256[:12]}..."
                    )
                    next_pcf = time.monotonic() + 24 * 60 * 60
                except Exception as exc:
                    if state.pcf is not None and state.pcf.trading_day != today:
                        state.pcf = None
                    log(f"PCF refresh failed: {exc}", error=True)
                    next_pcf = time.monotonic() + min(60.0, pcf_retry_interval)

            now_mono = time.monotonic()
            if now_mono >= next_cfets:
                try:
                    state.fx = cfets_client.fetch_latest(today)
                    log(
                        f"CFETS ready trading_day={state.fx.trading_day.isoformat()} "
                        f"quote_time={state.fx.quote_time} rate={state.fx.rate}"
                    )
                    next_cfets = time.monotonic() + cfets_interval
                except Exception as exc:
                    log(f"CFETS refresh failed: {exc}", error=True)
                    next_cfets = time.monotonic() + min(15.0, cfets_interval)

            now_local = datetime.now(SHANGHAI)
            if not ib_collection_window(now_local):
                if stream.is_connected():
                    stream.disconnect()
                    log("IB collection window closed; disconnected from TWS")
                # Do not retain a quote past 15:05 or before the next 09:00
                # attempt.  This prevents a late upload from refreshing a
                # private snapshot outside the intended operating window.
                state.ib = None
                next_ib_connect = 0.0
                STOP_EVENT.wait(30.0)
                continue

            now_mono = time.monotonic()
            if not stream.is_connected() and now_mono >= next_ib_connect:
                try:
                    stream.connect()
                    ib_backoff = 1.0
                    log(
                        "IB connected XOP conId=413951498 "
                        "STK/SMART primaryExchange=ARCA currency=USD"
                    )
                except Exception as exc:
                    state.ib = None
                    log(f"IB connect failed; retry in {ib_backoff:.0f}s: {exc}", error=True)
                    next_ib_connect = time.monotonic() + ib_backoff
                    ib_backoff = min(MAX_RECONNECT_BACKOFF, ib_backoff * 2.0)

            if stream.is_connected():
                try:
                    quote = stream.poll(0.05)
                    if quote is not None:
                        state.ib = quote
                except Exception as exc:
                    log(f"IB stream failed; reconnecting: {exc}", error=True)
                    stream.disconnect()
                    state.ib = None
                    next_ib_connect = time.monotonic() + ib_backoff
                    ib_backoff = min(MAX_RECONNECT_BACKOFF, ib_backoff * 2.0)

            now_mono = time.monotonic()
            if now_mono >= next_upload:
                missing = state.missing()
                if missing:
                    if now_mono >= next_missing_log:
                        log(
                            f"fail closed: missing private inputs {missing}; upload skipped",
                            error=True,
                        )
                        next_missing_log = now_mono + 30.0
                    next_upload = now_mono + upload_interval
                else:
                    try:
                        payload = build_private_payload(
                            state.pcf,
                            state.fx,
                            state.ib,
                            source=args.source,
                        )
                        post_private_inputs(
                            args.server,
                            args.token,
                            payload,
                            args.timeout,
                            args.origin_ip,
                            args.origin_tls_insecure,
                            args.origin_ca_file,
                        )
                        log(
                            "private inputs uploaded "
                            f"pcf={state.pcf.trading_day.isoformat()} "
                            f"cfets={state.fx.trading_day.isoformat()}T{state.fx.quote_time} "
                            f"ib={state.ib.market_data_type} bid={state.ib.bid} ask={state.ib.ask}"
                        )
                        upload_backoff = upload_interval
                        next_upload = time.monotonic() + upload_interval
                    except Exception as exc:
                        log(
                            f"private upload failed; retry in {upload_backoff:.0f}s: {exc}",
                            error=True,
                        )
                        next_upload = time.monotonic() + upload_backoff
                        upload_backoff = min(
                            MAX_RECONNECT_BACKOFF,
                            max(upload_interval, upload_backoff * 2.0),
                        )

            if not stream.is_connected():
                STOP_EVENT.wait(0.1)
    finally:
        stream.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env_file(".sina-uploader.env")
    args = build_parser().parse_args(argv)
    if not str(args.token or "").strip():
        print("NNN_UPLOAD_TOKEN or --token is required", file=sys.stderr)
        return 2
    if args.pcf_lookback_days < 0 or args.cfets_lookback_days < 0:
        print("lookback days must be non-negative", file=sys.stderr)
        return 2
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    runtime_root = Path(args.runtime_dir).expanduser().resolve()
    pcf_interval = parse_duration(args.pcf_interval, DEFAULT_PCF_INTERVAL)
    pcf_pacer = PCFRequestPacer(
        parse_duration(args.pcf_symbol_interval, DEFAULT_PCF_SYMBOL_INTERVAL)
    )
    pcf_client = PCFClient(
        runtime_root,
        timeout=args.timeout,
        refresh_seconds=pcf_interval,
        lookback_days=args.pcf_lookback_days,
        request_pacer=pcf_pacer,
    )
    cfets_client = CFETSClient(
        timeout=args.timeout,
        lookback_days=args.cfets_lookback_days,
    )
    try:
        if args.once:
            return run_once(args, pcf_client, cfets_client)
        return run_forever(args, pcf_client, cfets_client)
    except (InputValidationError, SourceUnavailableError, RuntimeError) as exc:
        log(str(exc), error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

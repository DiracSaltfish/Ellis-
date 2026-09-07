#!/usr/bin/env python3
"""Collect fixed-coefficient private valuation inputs for SH513350.

Each 1,000,000-share creation/redemption unit is valued as 1,046 XOP shares.
The official PCF still supplies the dated cash component and audit fields, but
it never changes that fixed XOP quantity.  A prior completed 15:59 ET XOP
reference is retained in the local audit record; live XOP bid/ask and CFETS
USD/CNY move the intraday estimate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_valuation_uploader as common  # noqa: E402


SYMBOL = "SH513350"
SECURITY_ID = "513350"
MODEL_VERSION = "private.total-basket.xop-cfets-pcf.sh513350.v1"
DEFAULT_SOURCE = "mac-home-private-513350-uploader"
FULLGOAL_PCF_API = "https://wap.fullgoal.com.cn/ws-business-server/fund/getFundSg"
EXPECTED_REDEMPTION_UNIT = 1_000_000.0
FIXED_XOP_EQUIVALENT_SHARES = 1_046.0
DEFAULT_UPLOAD_INTERVAL = 3.0
DEFAULT_CFETS_INTERVAL = 60.0
DEFAULT_PCF_INTERVAL = 10.0 * 60.0
DEFAULT_PCF_START_AT = "08:30"
DEFAULT_TIMEOUT = 10.0
MAX_RECONNECT_BACKOFF = 60.0
STOP_EVENT = threading.Event()
NEW_YORK = ZoneInfo("America/New_York")


class SourceUnavailableError(RuntimeError):
    """A mandatory source has no safe value for a private upload."""


@dataclass(frozen=True)
class PCFInput:
    trading_day: date
    pre_trading_day: date | None
    redemption: str
    creation_redemption_unit: float
    estimate_cash_component_cny: float
    nav_per_cu: float
    component_count: int
    source_url: str
    sha256: str

    def to_payload(self, xop_equivalent_shares: float) -> dict[str, Any]:
        return {
            "security_id": SECURITY_ID,
            "trading_day": self.trading_day.isoformat(),
            "pre_trading_day": self.pre_trading_day.isoformat() if self.pre_trading_day else None,
            "redemption": self.redemption,
            "creation_redemption_unit": self.creation_redemption_unit,
            "estimate_cash_component_cny": self.estimate_cash_component_cny,
            "nav_per_cu": self.nav_per_cu,
            "component_count": self.component_count,
            "xop_equivalent_shares": xop_equivalent_shares,
            "source_url": self.source_url,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class DailyCalibration:
    trading_day: date
    pcf_sha256: str
    xop_equivalent_shares: float
    xop_reference_day: date
    xop_mid: float
    fx_rate: float
    calibrated_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "trading_day": self.trading_day.isoformat(),
            "pcf_sha256": self.pcf_sha256,
            "xop_equivalent_shares": self.xop_equivalent_shares,
            "xop_reference_day": self.xop_reference_day.isoformat(),
            "xop_mid": self.xop_mid,
            "fx_rate": self.fx_rate,
            "calibrated_at": common.iso_timestamp(self.calibrated_at),
        }


@dataclass
class CollectorState:
    pcf: PCFInput | None = None
    fx: common.CFETSQuote | None = None
    ib: common.IBQuote | None = None
    reference: XOPRegularSessionReference | None = None
    calibration: DailyCalibration | None = None

    def missing(self) -> list[str]:
        missing = [name for name in ("pcf", "fx", "ib") if getattr(self, name) is None]
        if self.calibration is None:
            missing.append("daily_xop_equivalent_calibration")
        return missing


@dataclass(frozen=True)
class XOPRegularSessionReference:
    """A regular-session XOP bid/ask used only to derive the daily coefficient."""

    session_day: date
    bid: float
    ask: float
    observed_at: datetime


def pcf_url_for_day(trading_day: date) -> str:
    return FULLGOAL_PCF_API + "?" + urllib.parse.urlencode({
        "siteno": "main",
        "merchantId": "",
        "productCode": SECURITY_ID,
        "tradeDate": trading_day.isoformat(),
    })


def finite(value: Any, field: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise common.InputValidationError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        qualifier = "a positive finite" if positive else "finite"
        raise common.InputValidationError(f"{field} must be {qualifier} number")
    return number


def parse_pcf_response(raw: bytes | str, source_url: str, expected_day: date) -> PCFInput:
    raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
        data = payload["data"]
    except (UnicodeDecodeError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise common.InputValidationError("Fullgoal PCF response is not a data object") from exc
    if not isinstance(payload, dict) or int(payload.get("code", -1)) != 0 or not isinstance(data, dict):
        raise common.InputValidationError("Fullgoal PCF request was not acknowledged")
    trading_day = common.parse_date(data.get("tradingDay") or data.get("tradeDate"), "PCF trading day")
    if trading_day != expected_day:
        raise common.InputValidationError(
            f"PCF trading day {trading_day.isoformat()} does not match {expected_day.isoformat()}"
        )
    pre_value = str(data.get("ptradeDate") or "").strip()
    pre_trading_day = common.parse_date(pre_value, "PCF pre-trading day") if pre_value else None
    unit = finite(data.get("minShdy"), "PCF creation_redemption_unit", positive=True)
    cash = finite(data.get("minYgcash"), "PCF estimate_cash_component_cny")
    nav = finite(data.get("minShnav"), "PCF nav_per_cu", positive=True)
    try:
        component_count = int(float(data.get("recordNumber")))
    except (TypeError, ValueError) as exc:
        raise common.InputValidationError("PCF recordNumber must be a positive integer") from exc
    if unit != EXPECTED_REDEMPTION_UNIT:
        raise common.InputValidationError("PCF creation_redemption_unit must equal 1000000")
    if component_count <= 0:
        raise common.InputValidationError("PCF recordNumber must be positive")
    return PCFInput(
        trading_day=trading_day,
        pre_trading_day=pre_trading_day,
        redemption="Y" if "赎回" in str(data.get("ifSh") or "") else "N",
        creation_redemption_unit=unit,
        estimate_cash_component_cny=cash,
        nav_per_cu=nav,
        component_count=component_count,
        source_url=source_url,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


class PCFClient:
    def __init__(
        self,
        runtime_dir: str | os.PathLike[str],
        *,
        timeout: float,
        request_pacer: common.PCFRequestPacer | None = None,
        fetch_bytes: Callable[[str, float], bytes] | None = None,
    ) -> None:
        self.cache_root = Path(runtime_dir).expanduser().resolve() / "pcf"
        self.timeout = timeout
        self.request_pacer = request_pacer
        self.fetch_bytes = fetch_bytes or self._fetch_bytes

    def cache_path(self, trading_day: date) -> Path:
        return self.cache_root / trading_day.isoformat() / f"{SECURITY_ID}.json"

    def _fetch_bytes(self, source_url: str, timeout: float) -> bytes:
        request = urllib.request.Request(source_url, headers=common.SOURCE_HEADERS)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def fetch_today(self, trading_day: date) -> PCFInput:
        cache_path = self.cache_path(trading_day)
        if cache_path.is_file():
            try:
                return parse_pcf_response(cache_path.read_bytes(), pcf_url_for_day(trading_day), trading_day)
            except (OSError, common.InputValidationError) as exc:
                common.log(f"ignoring invalid SH513350 PCF cache {cache_path}: {exc}", error=True)
        if self.request_pacer is not None:
            self.request_pacer.wait()
        raw = self.fetch_bytes(pcf_url_for_day(trading_day), self.timeout)
        parsed = parse_pcf_response(raw, pcf_url_for_day(trading_day), trading_day)
        common._atomic_write_bytes(cache_path, raw)
        return parsed


class OvernightXOPQuoteStream:
    """Live XOP stream on IBKR's OVERNIGHT venue for Shanghai-day trading."""

    def __init__(self, host: str, port: int, client_id: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.shared = None

    def connect(self) -> None:
        self.disconnect()
        from machome_ibkr_bridge_client import ContractSubscription, SharedSingleQuoteStream
        shared = SharedSingleQuoteStream(
            ContractSubscription.create(
                subscription_id="XOP.OVERNIGHT",
                symbol="XOP",
                security_type="STK",
                exchange="OVERNIGHT",
                primary_exchange="ARCA",
                currency="USD",
            ),
            timeout=self.timeout,
        )
        try:
            shared.connect()
        except Exception as exc:
            shared.close()
            raise SourceUnavailableError(f"native IBKR bridge unavailable: {exc}") from exc
        self.shared = shared

    def is_connected(self) -> bool:
        return bool(self.shared is not None and self.shared.is_connected())

    def poll(self, wait_seconds: float = 0.05) -> common.IBQuote | None:
        if self.shared is None:
            raise SourceUnavailableError("native IBKR bridge XOP / OVERNIGHT stream is disconnected")
        try:
            value = self.shared.poll(wait_seconds)
        except Exception as exc:
            raise SourceUnavailableError(f"native IBKR bridge poll failed: {exc}") from exc
        if value is None:
            return None
        quote = common.IBQuote(
            value.bid, value.ask, value.last, value.market_data_type,
            value.observed_at.astimezone(common.SHANGHAI),
            datetime.now(common.SHANGHAI),
        )
        common.validate_ib_quote(quote)
        return quote

    def regular_session_reference(self, pcf_day: date) -> XOPRegularSessionReference:
        """Return the prior completed US regular-session bid/ask, never 09:30 ET.

        A China-dated PCF is available the following Shanghai morning.  The
        latest completed US RTH session then ends at 16:00 New York time and
        supplies a stable closing reference before the China market opens.
        """
        try:
            from ib_insync import IB
        except ImportError as exc:
            raise RuntimeError("ib_insync is required by the private SH513350 collector") from exc
        ib = IB()
        try:
            ib.connect(self.host, self.port, clientId=self.client_id,
                       timeout=self.timeout, readonly=True)
            if not ib.isConnected():
                raise SourceUnavailableError("TWS history session did not connect")
            return self._regular_session_reference_from_ib(ib, pcf_day)
        finally:
            if ib.isConnected():
                ib.disconnect()

    def _regular_session_reference_from_ib(self, ib: Any, pcf_day: date) -> XOPRegularSessionReference:
        from ib_insync import Stock
        contract = Stock("XOP", "SMART", "USD", primaryExchange="ARCA")
        qualified = ib.qualifyContracts(contract)
        if len(qualified) != 1:
            raise SourceUnavailableError("IBKR could not uniquely qualify XOP / SMART for RTH calibration")
        cutoff = datetime(pcf_day.year, pcf_day.month, pcf_day.day, 8, 30, tzinfo=common.SHANGHAI)
        latest_possible_session_day = cutoff.astimezone(NEW_YORK).date()
        try:
            bids = ib.reqHistoricalData(qualified[0], cutoff, "10 D", "1 min", "BID", True, 2, False, [], 60)
            asks = ib.reqHistoricalData(qualified[0], cutoff, "10 D", "1 min", "ASK", True, 2, False, [], 60)
        except Exception as exc:
            raise SourceUnavailableError(f"IBKR RTH BID/ASK history is unavailable: {exc}") from exc
        bid_by_day = regular_session_prices(bids)
        ask_by_day = regular_session_prices(asks)
        candidate_days = sorted(
            (day for day in set(bid_by_day) & set(ask_by_day) if day <= latest_possible_session_day),
            reverse=True,
        )
        if not candidate_days:
            raise SourceUnavailableError("no recent XOP regular-session BID/ASK history before PCF publication")
        session_day = candidate_days[0]
        bid_by_minute = bid_by_day[session_day]
        ask_by_minute = ask_by_day[session_day]
        common_minutes = sorted(set(bid_by_minute) & set(ask_by_minute))
        if not common_minutes:
            raise SourceUnavailableError(f"no XOP regular-session BID/ASK bars for {session_day.isoformat()}")
        # 15:59 ET is the final full regular-session minute.  If IBKR omits it,
        # use the closest earlier minute but never 09:30 (the opening minute).
        minute = common_minutes[-1]
        bid, ask = bid_by_minute[minute], ask_by_minute[minute]
        if ask < bid:
            raise SourceUnavailableError("XOP regular-session Ask is below Bid")
        observed_at = datetime.combine(session_day, datetime.min.time(), tzinfo=NEW_YORK).replace(
            hour=minute // 60, minute=minute % 60
        ).astimezone(common.SHANGHAI)
        return XOPRegularSessionReference(session_day, bid, ask, observed_at)

    def disconnect(self) -> None:
        if self.shared is not None:
            self.shared.close()
        self.shared = None


def regular_session_prices(bars: Any) -> dict[date, dict[int, float]]:
    """Group valid 09:31--15:59 ET minute closes by their actual US session."""
    prices: dict[date, dict[int, float]] = {}
    for bar in bars:
        value = getattr(bar, "date", None)
        if isinstance(value, str):
            try:
                from ib_insync import util
                value = util.parseIBDatetime(value)
            except Exception:
                continue
        if not isinstance(value, datetime):
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=NEW_YORK)
        local = value.astimezone(NEW_YORK)
        minute = local.hour * 60 + local.minute
        price = common.raw_positive_price(getattr(bar, "close", None))
        if 9 * 60 + 31 <= minute <= 15 * 60 + 59 and price is not None:
            prices.setdefault(local.date(), {})[minute] = price
    return prices


def calibration_path(runtime_dir: str | os.PathLike[str], trading_day: date) -> Path:
    return Path(runtime_dir).expanduser().resolve() / "calibrations" / f"{SECURITY_ID}-{trading_day.isoformat()}.json"


def load_calibration(runtime_dir: str | os.PathLike[str], pcf: PCFInput) -> DailyCalibration | None:
    path = calibration_path(runtime_dir, pcf.trading_day)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        calibration = DailyCalibration(
            trading_day=common.parse_date(payload.get("trading_day"), "calibration trading_day"),
            pcf_sha256=str(payload.get("pcf_sha256") or ""),
            xop_equivalent_shares=finite(payload.get("xop_equivalent_shares"), "xop_equivalent_shares", positive=True),
            xop_reference_day=common.parse_date(payload.get("xop_reference_day"), "xop_reference_day"),
            xop_mid=finite(payload.get("xop_mid"), "xop_mid", positive=True),
            fx_rate=finite(payload.get("fx_rate"), "fx_rate", positive=True),
            calibrated_at=common.parse_datetime(payload.get("calibrated_at"), "calibrated_at"),
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError, common.InputValidationError):
        return None
    if (
        calibration.trading_day != pcf.trading_day
        or calibration.pcf_sha256 != pcf.sha256
        or calibration.xop_equivalent_shares != FIXED_XOP_EQUIVALENT_SHARES
    ):
        return None
    return calibration


def calibrate(
    pcf: PCFInput,
    fx: common.CFETSQuote,
    reference: XOPRegularSessionReference,
    now: datetime | None = None,
) -> DailyCalibration:
    if fx.trading_day != pcf.trading_day:
        raise common.InputValidationError("CFETS day must equal the PCF day before calibration")
    xop_mid = (reference.bid + reference.ask) / 2.0
    return DailyCalibration(
        trading_day=pcf.trading_day,
        pcf_sha256=pcf.sha256,
        xop_equivalent_shares=FIXED_XOP_EQUIVALENT_SHARES,
        xop_reference_day=reference.session_day,
        xop_mid=xop_mid,
        fx_rate=fx.rate,
        calibrated_at=now or datetime.now(common.SHANGHAI),
    )


def save_calibration(runtime_dir: str | os.PathLike[str], calibration: DailyCalibration) -> None:
    body = json.dumps(calibration.to_json(), ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    common._atomic_write_bytes(calibration_path(runtime_dir, calibration.trading_day), body)


def build_private_payload(
    pcf: PCFInput,
    fx: common.CFETSQuote,
    ib: common.IBQuote,
    calibration: DailyCalibration,
    *,
    generated_at: datetime | None = None,
    source: str = DEFAULT_SOURCE,
) -> dict[str, Any]:
    if calibration.trading_day != pcf.trading_day or calibration.pcf_sha256 != pcf.sha256:
        raise common.InputValidationError("daily calibration does not match the current official PCF")
    if fx.trading_day != pcf.trading_day:
        raise common.InputValidationError("CFETS day must equal PCF day")
    common.validate_cfets_quote(fx)
    common.validate_ib_quote(ib)
    now = generated_at or datetime.now(common.SHANGHAI)
    return {
        "schema_version": 1,
        "symbol": SYMBOL,
        "model_version": MODEL_VERSION,
        "pcf": pcf.to_payload(calibration.xop_equivalent_shares),
        "fx": fx.to_payload(),
        "ib": ib.to_payload(),
        "source": source,
        "generated_at": common.iso_timestamp(now),
    }


def post_private_inputs(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(
        args.server.rstrip("/") + f"/api/v1/private/inputs/{SYMBOL}",
        data=body,
        method="POST",
        headers=common.server_headers(args.server, args.token),
    )
    try:
        with common.open_server_request(
            request, args.timeout, args.origin_ip, args.origin_tls_insecure, args.origin_ca_file
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"private SH513350 upload status {exc.code}: {exc.read().decode('utf-8', errors='replace')}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"private SH513350 upload was not acknowledged: {payload}")


def update_calibration_if_ready(
    state: CollectorState,
    runtime_dir: str | os.PathLike[str],
    stream: OvernightXOPQuoteStream,
) -> bool:
    if state.pcf is None or state.fx is None or state.ib is None:
        return False
    if state.calibration is not None:
        return True
    if state.fx.trading_day != state.pcf.trading_day or state.ib.market_data_type != "Live":
        return False
    state.calibration = load_calibration(runtime_dir, state.pcf)
    if state.calibration is None:
        state.reference = state.reference or stream.regular_session_reference(state.pcf.trading_day)
        state.calibration = calibrate(state.pcf, state.fx, state.reference)
        save_calibration(runtime_dir, state.calibration)
        common.log(
            "SH513350 fixed XOP equivalent ready "
            f"pcf={state.pcf.trading_day.isoformat()} shares={state.calibration.xop_equivalent_shares:.4f} "
            f"rth_day={state.calibration.xop_reference_day.isoformat()} "
            f"rth_mid={state.calibration.xop_mid:.4f} fx={state.calibration.fx_rate:.6f}"
        )
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    parser.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    parser.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    parser.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    parser.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("NNN_PRIVATE_513350_UPLOAD_TIMEOUT", DEFAULT_TIMEOUT)))
    parser.add_argument("--source", default=os.getenv("NNN_PRIVATE_513350_UPLOAD_SOURCE", DEFAULT_SOURCE))
    parser.add_argument("--runtime-dir", default=os.getenv("NNN_PRIVATE_513350_RUNTIME_DIR", "scripts/.runtime/private_513350"))
    parser.add_argument("--upload-interval", default=os.getenv("NNN_PRIVATE_513350_UPLOAD_INTERVAL", "3s"))
    parser.add_argument("--cfets-interval", default=os.getenv("NNN_PRIVATE_513350_CFETS_INTERVAL", "60s"))
    parser.add_argument("--pcf-interval", default=os.getenv("NNN_PRIVATE_513350_PCF_INTERVAL", "10m"))
    parser.add_argument("--pcf-start-at", default=os.getenv("NNN_PRIVATE_513350_PCF_START_AT", DEFAULT_PCF_START_AT))
    parser.add_argument("--pcf-symbol-interval", default=os.getenv("NNN_PRIVATE_PCF_SYMBOL_INTERVAL", "10s"))
    parser.add_argument("--pcf-pacer-state", default=os.getenv("NNN_PRIVATE_PCF_PACER_STATE", "/tmp/newnavnav-private-pcf-pacer.json"))
    parser.add_argument("--cfets-lookback-days", type=int, default=int(os.getenv("NNN_PRIVATE_513350_CFETS_LOOKBACK_DAYS", "10")))
    parser.add_argument("--ib-host", default=os.getenv("NNN_PRIVATE_513350_IB_HOST", os.getenv("NNN_IB_HOST", "127.0.0.1")))
    parser.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_PRIVATE_513350_IB_PORT", os.getenv("NNN_IB_PORT", "7496"))))
    parser.add_argument("--ib-client-id", type=int, default=int(os.getenv("NNN_PRIVATE_513350_IB_CLIENT_ID", "513350")))
    return parser


def run_forever(args: argparse.Namespace, pcf_client: PCFClient, cfets_client: common.CFETSClient) -> int:
    state = CollectorState()
    upload_interval = max(0.1, common.parse_duration(args.upload_interval, DEFAULT_UPLOAD_INTERVAL))
    cfets_interval = max(1.0, common.parse_duration(args.cfets_interval, DEFAULT_CFETS_INTERVAL))
    pcf_retry_interval = max(10.0, common.parse_duration(args.pcf_interval, DEFAULT_PCF_INTERVAL))
    pcf_start_minute = common.parse_clock_minute(args.pcf_start_at, 8 * 60 + 30)
    stream = OvernightXOPQuoteStream(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
    next_pcf = next_cfets = next_ib_connect = next_upload = next_missing_log = 0.0
    pcf_ready_for_day: date | None = None
    ib_backoff = 1.0
    upload_backoff = upload_interval
    try:
        while not STOP_EVENT.is_set():
            now_mono, now = time.monotonic(), datetime.now(common.SHANGHAI)
            today = now.date()
            if now_mono >= next_pcf and pcf_ready_for_day != today and common.pcf_collection_window(now, pcf_start_minute):
                try:
                    pcf = pcf_client.fetch_today(today)
                    state.pcf, state.reference, state.calibration, pcf_ready_for_day = pcf, None, load_calibration(args.runtime_dir, pcf), today
                    common.log(f"SH513350 strict PCF ready day={today.isoformat()} components={pcf.component_count} sha256={pcf.sha256[:12]}...")
                    next_pcf = time.monotonic() + 24 * 60 * 60
                except Exception as exc:
                    if state.pcf is not None and state.pcf.trading_day != today:
                        state.pcf, state.reference, state.calibration = None, None, None
                    common.log(f"SH513350 PCF refresh failed: {exc}", error=True)
                    next_pcf = time.monotonic() + min(60.0, pcf_retry_interval)

            if time.monotonic() >= next_cfets:
                try:
                    state.fx = cfets_client.fetch_latest(today)
                    next_cfets = time.monotonic() + cfets_interval
                except Exception as exc:
                    common.log(f"SH513350 CFETS refresh failed: {exc}", error=True)
                    next_cfets = time.monotonic() + min(15.0, cfets_interval)

            now = datetime.now(common.SHANGHAI)
            if not common.ib_collection_window(now):
                if stream.is_connected():
                    stream.disconnect()
                state.ib = None
                STOP_EVENT.wait(30.0)
                continue
            if not stream.is_connected() and time.monotonic() >= next_ib_connect:
                try:
                    stream.connect()
                    ib_backoff = 1.0
                    common.log("SH513350 IB connected XOP / OVERNIGHT / ARCA")
                except Exception as exc:
                    state.ib = None
                    common.log(f"SH513350 IB connection failed; retry in {ib_backoff:.0f}s: {exc}", error=True)
                    next_ib_connect = time.monotonic() + ib_backoff
                    ib_backoff = min(MAX_RECONNECT_BACKOFF, ib_backoff * 2.0)
            if stream.is_connected():
                try:
                    quote = stream.poll(0.05)
                    if quote is not None:
                        state.ib = quote
                except Exception as exc:
                    stream.disconnect()
                    state.ib = None
                    next_ib_connect = time.monotonic() + ib_backoff
                    ib_backoff = min(MAX_RECONNECT_BACKOFF, ib_backoff * 2.0)
                    common.log(f"SH513350 IB stream failed: {exc}", error=True)

            if time.monotonic() >= next_upload:
                update_calibration_if_ready(state, args.runtime_dir, stream)
                missing = state.missing()
                if missing:
                    if time.monotonic() >= next_missing_log:
                        common.log(f"SH513350 fail closed: missing {missing}; upload skipped", error=True)
                        next_missing_log = time.monotonic() + 30.0
                    next_upload = time.monotonic() + upload_interval
                else:
                    try:
                        assert state.pcf and state.fx and state.ib and state.calibration
                        post_private_inputs(args, build_private_payload(state.pcf, state.fx, state.ib, state.calibration, source=args.source))
                        upload_backoff = upload_interval
                        next_upload = time.monotonic() + upload_interval
                    except Exception as exc:
                        common.log(f"SH513350 upload failed; retry in {upload_backoff:.0f}s: {exc}", error=True)
                        next_upload = time.monotonic() + upload_backoff
                        upload_backoff = min(MAX_RECONNECT_BACKOFF, max(upload_interval, upload_backoff * 2.0))
            if not stream.is_connected():
                STOP_EVENT.wait(0.1)
    finally:
        stream.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    common.load_env_file(".sina-uploader.env")
    args = build_parser().parse_args(argv)
    if not str(args.token or "").strip():
        print("NNN_UPLOAD_TOKEN or --token is required", file=sys.stderr)
        return 2
    if args.cfets_lookback_days < 0:
        print("--cfets-lookback-days must be non-negative", file=sys.stderr)
        return 2
    signal.signal(signal.SIGINT, lambda *_: STOP_EVENT.set())
    signal.signal(signal.SIGTERM, lambda *_: STOP_EVENT.set())
    pcf_client = PCFClient(
        args.runtime_dir,
        timeout=args.timeout,
        request_pacer=common.PCFRequestPacer(common.parse_duration(args.pcf_symbol_interval, 10.0), args.pcf_pacer_state),
    )
    cfets_client = common.CFETSClient(timeout=args.timeout, lookback_days=args.cfets_lookback_days)
    try:
        return run_forever(args, pcf_client, cfets_client)
    except (common.InputValidationError, SourceUnavailableError, RuntimeError) as exc:
        common.log(str(exc), error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

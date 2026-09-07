#!/usr/bin/env python3
"""Upload non-actionable PCF/futures indicative valuations for index QDII ETFs.

This collector deliberately values the *dated PCF security basket* through an
index-futures contract equivalent. It is not a public NAV estimator and it is
not an execution signal: the server keeps every fund in this family blocked
until the fund-specific prospectus terms and realised redemption observations
are recorded.

The daily coefficient is calculated as::

    (NAVperCU - previous_cash_component) / prior central-parity FX
    ----------------------------------------------------------
          family calibration close anchor * contract multiplier

The first line is the PCF's previous security asset value.  During the Chinese
session the coefficient is then applied to live future Bid/Ask and the latest
available family-specific CFETS hourly reference rate; the current PCF
estimated cash component is kept as a separate CNY leg by the server.
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
from datetime import date, datetime, time as clock_time, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import private_valuation_uploader as common


SAFE_QUERY_URL = "https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do"
SAFE_SOURCE = "SAFE_CENTRAL_PARITY"
SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
# US PCFs use class-share spellings such as BF/B and BRK/B. They are audit
# identifiers here (not individual IB quote requests), so retain the exact
# exchange spelling rather than rejecting a valid basket.
TICKER_PATTERN = re.compile(r"[A-Z][A-Z0-9./-]{0,14}")
JAPAN_ETF_PATTERN = re.compile(r"\d{4}")
GERMAN_TICKER_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9./-]{0,14}")


@dataclass(frozen=True)
class FundConfig:
    symbol: str
    security_id: str
    name: str
    exchange: str
    redemption_unit: float
    component_count: int

    def source_url(self, trading_day: date) -> str:
        if self.exchange == "SZSE":
            return (
                "https://reportdocs.static.szse.cn/files/text/ETFDown/"
                f"pcf_{self.security_id}_{trading_day:%Y%m%d}.xml"
            )
        # SSE's download endpoint publishes the current PCF only. parse_pcf
        # verifies its embedded TradingDay, so a stale latest file is rejected.
        return "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=" + self.security_id


NQ_FUNDS: tuple[FundConfig, ...] = (
    # The live NDX PCFs can legitimately change their positive-quantity
    # constituent count when a security is removed or substituted.  Keep the
    # structural and index checks, but do not freeze a historical count into
    # the live uploader; otherwise a valid daily PCF fail-closes every NQ fund.
    FundConfig("SH513100", "513100", "纳指ETF国泰", "SSE", 1_000_000, 0),
    FundConfig("SH513110", "513110", "纳指100", "SSE", 1_000_000, 0),
    FundConfig("SH513300", "513300", "纳斯达克", "SSE", 750_000, 0),
    FundConfig("SH513390", "513390", "纳指基金", "SSE", 1_000_000, 0),
    FundConfig("SH513870", "513870", "纳指指数", "SSE", 1_000_000, 0),
    FundConfig("SZ159501", "159501", "纳指ETF嘉实", "SZSE", 1_000_000, 0),
    FundConfig("SZ159513", "159513", "纳斯达克100ETF大成", "SZSE", 1_000_000, 0),
    FundConfig("SZ159632", "159632", "纳斯达克ETF华安", "SZSE", 1_000_000, 0),
    FundConfig("SZ159659", "159659", "纳斯达克100ETF招商", "SZSE", 1_000_000, 0),
    FundConfig("SZ159660", "159660", "纳指ETF汇添富", "SZSE", 1_000_000, 0),
    FundConfig("SZ159696", "159696", "纳指ETF易方达", "SZSE", 1_000_000, 0),
    FundConfig("SZ159941", "159941", "纳指ETF广发", "SZSE", 1_300_000, 0),
)

SP500_FUNDS: tuple[FundConfig, ...] = (
    # The full PCF has 502/503 stock rows plus cash/substitution rows, while
    # only a fund-specific subset has a positive share quantity on any day.
    # Zero therefore means "require a non-empty unique US basket", not a
    # fixed component count.
    FundConfig("SH513500", "513500", "标普500", "SSE", 1_000_000, 0),
    FundConfig("SH513650", "513650", "标普ETF", "SSE", 1_000_000, 0),
    FundConfig("SZ159612", "159612", "标普500ETF国泰", "SZSE", 1_000_000, 0),
    FundConfig("SZ159655", "159655", "标普500ETF华夏", "SZSE", 1_000_000, 0),
)

NIKKEI225_FUNDS: tuple[FundConfig, ...] = (
    FundConfig("SH513000", "513000", "225ETF", "SSE", 500_000, 1),
    FundConfig("SH513520", "513520", "日经ETF", "SSE", 500_000, 1),
    FundConfig("SH513880", "513880", "日经225", "SSE", 500_000, 1),
    FundConfig("SZ159866", "159866", "日经ETF工银", "SZSE", 500_000, 1),
)

DAX_FUNDS: tuple[FundConfig, ...] = (
    FundConfig("SH513030", "513030", "德国ETF华安", "SSE", 500_000, 40),
    FundConfig("SZ159561", "159561", "德国ETF嘉实", "SZSE", 1_000_000, 40),
)


@dataclass(frozen=True)
class FamilyConfig:
    key: str
    display_name: str
    reference_symbol: str
    futures_multiplier_usd_per_point: float
    model_version: str
    source: str
    expected_szse_underlying: str
    funds: tuple[FundConfig, ...]
    future_exchange: str
    future_currency: str
    future_timezone: str
    regular_close_hour: int
    regular_close_minute: int
    component_market: str
    component_currency: str
    fx_pair: str
    cfets_pair: str
    central_parity_column: str
    central_parity_divisor: float
    supports_historical_replay: bool
    calibration_anchor_kind: str = "daily_rth_close"


NASDAQ_FAMILY = FamilyConfig(
    key="nasdaq", display_name="Nasdaq", reference_symbol="NQ", futures_multiplier_usd_per_point=20.0,
    model_version="private.total-basket.nq-cfets-pcf.pre-scan.v1",
    source="mac-home-private-nasdaq-pcf-nq-uploader", expected_szse_underlying="NDX", funds=NQ_FUNDS,
    future_exchange="CME", future_currency="USD", future_timezone="America/New_York", regular_close_hour=16, regular_close_minute=0,
    component_market="US", component_currency="USD", fx_pair="USD/CNY", cfets_pair="USD/CNY",
    central_parity_column="美元", central_parity_divisor=100.0, supports_historical_replay=True,
)
SP500_FAMILY = FamilyConfig(
    key="sp500", display_name="S&P 500", reference_symbol="ES", futures_multiplier_usd_per_point=50.0,
    model_version="private.total-basket.es-cfets-pcf.pre-scan.v1",
    source="mac-home-private-sp500-pcf-es-uploader", expected_szse_underlying="SPXNTR", funds=SP500_FUNDS,
    future_exchange="CME", future_currency="USD", future_timezone="America/New_York", regular_close_hour=16, regular_close_minute=0,
    component_market="US", component_currency="USD", fx_pair="USD/CNY", cfets_pair="USD/CNY",
    central_parity_column="美元", central_parity_divisor=100.0, supports_historical_replay=True,
)
NIKKEI225_FAMILY = FamilyConfig(
    key="nikkei225", display_name="Nikkei 225", reference_symbol="N225M", futures_multiplier_usd_per_point=100.0,
    model_version="private.total-basket.n225m-cfets-pcf.pre-scan.v1",
    source="mac-home-private-nikkei225-pcf-n225m-uploader", expected_szse_underlying="N225", funds=NIKKEI225_FUNDS,
    future_exchange="OSE.JPN", future_currency="JPY", future_timezone="Asia/Tokyo", regular_close_hour=15, regular_close_minute=45,
    component_market="JP", component_currency="JPY", fx_pair="JPY/CNY", cfets_pair="100JPY/CNY",
    central_parity_column="日元", central_parity_divisor=100.0, supports_historical_replay=True,
)
DAX_FAMILY = FamilyConfig(
    # IBKR exposes Mini-DAX through the DAX root; multiplier=5 selects FDXM.
    key="germany", display_name="DAX", reference_symbol="DAX", futures_multiplier_usd_per_point=5.0,
    model_version="private.total-basket.fdxm-cfets-pcf.xetra-1735-anchor.pre-scan.v2",
    source="mac-home-private-germany-pcf-fdxm-xetra1735-uploader", expected_szse_underlying="DAX", funds=DAX_FUNDS,
    future_exchange="EUREX", future_currency="EUR", future_timezone="Europe/Berlin", regular_close_hour=17, regular_close_minute=35,
    component_market="DE", component_currency="EUR", fx_pair="EUR/CNY", cfets_pair="EUR/CNY",
    central_parity_column="欧元", central_parity_divisor=100.0, supports_historical_replay=True,
    calibration_anchor_kind="xetra_close_auction_1735",
)
FAMILIES = {
    NASDAQ_FAMILY.key: NASDAQ_FAMILY,
    SP500_FAMILY.key: SP500_FAMILY,
    NIKKEI225_FAMILY.key: NIKKEI225_FAMILY,
    DAX_FAMILY.key: DAX_FAMILY,
}

# Compatibility alias retained for the Nasdaq regression tests and callers
# that import this collector as a module.
FUNDS = NQ_FUNDS


class SourceUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class XetraCloseAnchor:
    """FDXM observations aligned to the Xetra cash-market close.

    IBKR timestamps an intraday bar by its start.  The 17:29 and 17:34 bars
    therefore end at 17:30 (continuous close / auction start) and 17:35
    (scheduled official closing-auction price), respectively.
    """

    trading_day: date
    close_1730: float
    close_1735: float
    bar_start_1730: datetime
    bar_start_1735: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "method": "FDXM_TRADES_1M_ENDING_XETRA_1735_V2",
            "timezone": "Europe/Berlin",
            "trading_day": self.trading_day.isoformat(),
            "selected_close": self.close_1735,
            "comparison_close_1730": self.close_1730,
            "bar_start_1730": self.bar_start_1730.isoformat(),
            "bar_end_1730": (self.bar_start_1730 + timedelta(minutes=1)).isoformat(),
            "bar_start_1735": self.bar_start_1735.isoformat(),
            "bar_end_1735": (self.bar_start_1735 + timedelta(minutes=1)).isoformat(),
        }


@dataclass(frozen=True)
class Component:
    symbol: str
    name: str
    quantity: float
    market: str = "US"
    currency: str = "USD"

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "currency": self.currency,
            "quantity": self.quantity,
        }


@dataclass(frozen=True)
class PCF:
    fund: FundConfig
    trading_day: date
    pre_trading_day: date
    creation: str
    redemption: str
    estimate_cash_component_cny: float
    previous_cash_component_cny: float
    nav_per_cu: float
    components: tuple[Component, ...]
    source_url: str
    sha256: str

    def to_payload(self, futures_contract_equivalent: float) -> dict[str, Any]:
        return {
            "security_id": self.fund.security_id,
            "trading_day": self.trading_day.isoformat(),
            "pre_trading_day": self.pre_trading_day.isoformat(),
            "creation": self.creation,
            "redemption": self.redemption,
            "creation_redemption_unit": self.fund.redemption_unit,
            "estimate_cash_component_cny": self.estimate_cash_component_cny,
            "nav_per_cu": self.nav_per_cu,
            "component_count": len(self.components),
            "components": [component.to_payload() for component in self.components],
            # The wire field predates generic proxies. It carries a fractional
            # NQ, ES or N225M contract equivalent here, never an XOP share count.
            "xop_equivalent_shares": futures_contract_equivalent,
            "source_url": self.source_url,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class CentralParity:
    rate: float
    trading_day: date
    fetched_at: datetime


@dataclass(frozen=True)
class JPYCFETSQuote:
    """CFETS publishes 100JPY/CNY; the private wire contract uses JPY/CNY."""

    rate: float
    trading_day: date
    quote_time: str
    fetched_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "pair": "JPY/CNY",
            "rate": self.rate,
            "trading_day": self.trading_day.isoformat(),
            "quote_time": self.quote_time,
            "source": "CFETS_REFERENCE_RATE",
            "fetched_at": common.iso_timestamp(self.fetched_at),
        }


class JPYCFETSClient:
    """Load the latest same-day CFETS 100JPY/CNY hourly reference quote."""

    def __init__(self, timeout: float, lookback_days: int = 10, fetch_bytes: Any = None) -> None:
        self.timeout, self.lookback_days = timeout, max(0, lookback_days)
        self.fetch_bytes = fetch_bytes or common._http_fetch

    def fetch_latest(self, as_of: date | None = None, *, max_hour: int = 18) -> JPYCFETSQuote:
        if max_hour < 10 or max_hour > 18:
            raise SourceUnavailableError("CFETS JPY/CNY max_hour must be from 10 through 18")
        current = as_of or datetime.now(SHANGHAI).date()
        errors: list[str] = []
        for offset in range(self.lookback_days + 1):
            candidate = current - timedelta(days=offset)
            if candidate.weekday() >= 5:
                continue
            try:
                raw = self.fetch_bytes(
                    common.cfets_query_url(candidate), b"", common.SOURCE_HEADERS, self.timeout,
                )
                payload = json.loads(raw)
                records = payload.get("records") or payload.get("data", {}).get("records") or []
                for record in records:
                    if not isinstance(record, dict) or str(record.get("ccyPair") or "").upper() != "100JPY/CNY":
                        continue
                    if parse_day(str(record.get("dealDate") or ""), "CFETS dealDate") != candidate:
                        continue
                    hourly: list[tuple[int, float]] = []
                    for hour in range(10, max_hour + 1):
                        raw_rate = str(record.get(f"rateOf{hour:02d}hour") or "").strip()
                        if raw_rate in {"", "---", "/"}:
                            continue
                        hourly.append((hour, number(raw_rate, f"CFETS 100JPY rateOf{hour:02d}hour") / 100.0))
                    if hourly:
                        hour, rate = max(hourly, key=lambda item: item[0])
                        return JPYCFETSQuote(rate, candidate, f"{hour:02d}:00", datetime.now(SHANGHAI))
                errors.append(f"{candidate.isoformat()}: no 100JPY/CNY 10:00-{max_hour:02d}:00 hour")
            except (json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError, SourceUnavailableError) as exc:
                errors.append(f"{candidate.isoformat()}: {exc}")
        raise SourceUnavailableError("no CFETS JPY/CNY hourly reference rate: " + " | ".join(errors[-3:]))


@dataclass(frozen=True)
class EURCFETSQuote:
    rate: float
    trading_day: date
    quote_time: str
    fetched_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "pair": "EUR/CNY",
            "rate": self.rate,
            "trading_day": self.trading_day.isoformat(),
            "quote_time": self.quote_time,
            "source": "CFETS_REFERENCE_RATE",
            "fetched_at": common.iso_timestamp(self.fetched_at),
        }


class EURCFETSClient:
    """Load the latest same-day CFETS EUR/CNY hourly reference quote."""

    def __init__(self, timeout: float, lookback_days: int = 10, fetch_bytes: Any = None) -> None:
        self.timeout, self.lookback_days = timeout, max(0, lookback_days)
        self.fetch_bytes = fetch_bytes or common._http_fetch

    def fetch_latest(self, as_of: date | None = None, *, max_hour: int = 18) -> EURCFETSQuote:
        if max_hour < 10 or max_hour > 18:
            raise SourceUnavailableError("CFETS EUR/CNY max_hour must be from 10 through 18")
        current = as_of or datetime.now(SHANGHAI).date()
        errors: list[str] = []
        for offset in range(self.lookback_days + 1):
            candidate = current - timedelta(days=offset)
            if candidate.weekday() >= 5:
                continue
            try:
                raw = self.fetch_bytes(
                    common.cfets_query_url(candidate), b"", common.SOURCE_HEADERS, self.timeout,
                )
                payload = json.loads(raw)
                records = payload.get("records") or payload.get("data", {}).get("records") or []
                for record in records:
                    if not isinstance(record, dict) or str(record.get("ccyPair") or "").upper() != "EUR/CNY":
                        continue
                    if parse_day(str(record.get("dealDate") or ""), "CFETS dealDate") != candidate:
                        continue
                    hourly: list[tuple[int, float]] = []
                    for hour in range(10, max_hour + 1):
                        raw_rate = str(record.get(f"rateOf{hour:02d}hour") or "").strip()
                        if raw_rate in {"", "---", "/"}:
                            continue
                        hourly.append((hour, number(raw_rate, f"CFETS EUR rateOf{hour:02d}hour")))
                    if hourly:
                        hour, rate = max(hourly, key=lambda item: item[0])
                        return EURCFETSQuote(rate, candidate, f"{hour:02d}:00", datetime.now(SHANGHAI))
                errors.append(f"{candidate.isoformat()}: no EUR/CNY 10:00-{max_hour:02d}:00 hour")
            except (json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError, SourceUnavailableError) as exc:
                errors.append(f"{candidate.isoformat()}: {exc}")
        raise SourceUnavailableError("no CFETS EUR/CNY hourly reference rate: " + " | ".join(errors[-3:]))


@dataclass(frozen=True)
class NQQuote:
    symbol: str
    bid: float
    ask: float
    last: float | None
    observed_at: datetime
    market_data_type: str
    stream_checked_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "market_data_type": self.market_data_type,
            "source": "IBKR_TWS",
            "observed_at": common.iso_timestamp(self.observed_at),
            "stream_checked_at": common.iso_timestamp(self.stream_checked_at),
        }


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def direct_text(node: ET.Element, name: str, *, required: bool = True) -> str:
    values = [str(child.text or "").strip() for child in node if local_name(child.tag) == name]
    if len(values) > 1 or (required and (len(values) != 1 or not values[0])):
        raise SourceUnavailableError(f"PCF {name} must appear exactly once")
    return values[0] if values else ""


def parse_day(value: str, field: str) -> date:
    for layout in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), layout).date()
        except ValueError:
            continue
    raise SourceUnavailableError(f"PCF {field} is not a date: {value!r}")


def number(value: str, field: str, *, positive: bool = False) -> float:
    try:
        parsed = float(str(value).replace(",", "").strip())
    except ValueError as exc:
        raise SourceUnavailableError(f"PCF {field} is not numeric") from exc
    if not math.isfinite(parsed) or (positive and parsed <= 0):
        raise SourceUnavailableError(f"PCF {field} is invalid")
    return parsed


def sse_creation_redemption(switch: str) -> tuple[str, str]:
    values = {"0": ("N", "N"), "1": ("Y", "Y"), "2": ("Y", "N"), "3": ("N", "Y")}
    if switch not in values:
        raise SourceUnavailableError(f"unknown SSE CreationRedemptionSwitch {switch!r}")
    return values[switch]


def component(symbol: str, name: str, quantity: float, family: FamilyConfig = NASDAQ_FAMILY) -> Component:
    ticker = symbol.strip().upper()
    pattern = (
        JAPAN_ETF_PATTERN if family is NIKKEI225_FAMILY
        else GERMAN_TICKER_PATTERN if family is DAX_FAMILY
        else TICKER_PATTERN
    )
    if not pattern.fullmatch(ticker):
        raise SourceUnavailableError(f"invalid {family.component_market} PCF ticker {symbol!r}")
    return Component(
        ticker, name.strip() or ticker, quantity,
        market=family.component_market, currency=family.component_currency,
    )


def parse_pcf(config: FundConfig, raw: bytes, source_url: str, expected_day: date, family: FamilyConfig = NASDAQ_FAMILY) -> PCF:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceUnavailableError(f"{config.symbol} PCF is not XML: {exc}") from exc
    root_name = local_name(root.tag)
    components: list[Component] = []
    if config.exchange == "SZSE":
        if root_name != "PCFFile":
            raise SourceUnavailableError(f"{config.symbol} expected SZSE PCFFile")
        if direct_text(root, "SecurityID") != config.security_id:
            raise SourceUnavailableError(f"{config.symbol} PCF SecurityID mismatch")
        if direct_text(root, "UnderlyingSecurityID") != family.expected_szse_underlying:
            raise SourceUnavailableError(
                f"{config.symbol} PCF underlying index is not {family.expected_szse_underlying}"
            )
        trading_day = parse_day(direct_text(root, "TradingDay"), "TradingDay")
        pre_trading_day = parse_day(direct_text(root, "PreTradingDay"), "PreTradingDay")
        creation, redemption = direct_text(root, "Creation").upper(), direct_text(root, "Redemption").upper()
        unit = number(direct_text(root, "CreationRedemptionUnit"), "CreationRedemptionUnit", positive=True)
        estimated_cash = number(direct_text(root, "EstimateCashComponent"), "EstimateCashComponent")
        previous_cash = number(direct_text(root, "CashComponent"), "CashComponent")
        nav_per_cu = number(direct_text(root, "NAVperCU"), "NAVperCU", positive=True)
        declared = int(number(direct_text(root, "TotalRecordNum"), "TotalRecordNum", positive=True))
        nodes = [node for node in root.iter() if local_name(node.tag) == "Component"]
        if len(nodes) != declared:
            raise SourceUnavailableError(f"{config.symbol} PCF TotalRecordNum does not match XML")
        for node in nodes:
            fields = {local_name(child.tag): str(child.text or "").strip() for child in node}
            quantity = number(fields.get("ComponentShare", ""), "ComponentShare")
            if quantity <= 0:
                continue  # 159900 is a cash virtual row, never a stock.
            if fields.get("UnderlyingSecurityIDSource") != "9999":
                raise SourceUnavailableError(f"{config.symbol} PCF contains a non-{family.component_market} security row")
            components.append(component(
                fields.get("UnderlyingSecurityID", ""), fields.get("UnderlyingSymbol", ""), quantity, family,
            ))
    else:
        if root_name != "SSEPortfolioCompositionFile":
            raise SourceUnavailableError(f"{config.symbol} expected SSEPortfolioCompositionFile")
        if direct_text(root, "FundInstrumentID") != config.security_id:
            raise SourceUnavailableError(f"{config.symbol} PCF FundInstrumentID mismatch")
        trading_day = parse_day(direct_text(root, "TradingDay"), "TradingDay")
        pre_trading_day = parse_day(direct_text(root, "PreTradingDay"), "PreTradingDay")
        creation, redemption = sse_creation_redemption(direct_text(root, "CreationRedemptionSwitch"))
        unit = number(direct_text(root, "CreationRedemptionUnit"), "CreationRedemptionUnit", positive=True)
        estimated_cash = number(direct_text(root, "EstimatedCashComponent"), "EstimatedCashComponent")
        previous_cash = number(direct_text(root, "PreCashComponent"), "PreCashComponent")
        nav_per_cu = number(direct_text(root, "NAVperCU"), "NAVperCU", positive=True)
        declared = int(number(direct_text(root, "RecordNumber"), "RecordNumber", positive=True))
        nodes = [node for node in root.iter() if local_name(node.tag) == "Component"]
        if len(nodes) != declared:
            raise SourceUnavailableError(f"{config.symbol} PCF RecordNumber does not match XML")
        for node in nodes:
            fields = {local_name(child.tag): str(child.text or "").strip() for child in node}
            # SSE labels the component's listing venue with
            # UnderlyingSecurityID (not the optional Market field used by
            # some other manager exports).
            quantity = number(fields.get("Quantity", ""), "Quantity")
            if quantity <= 0:
                continue
            if fields.get("UnderlyingSecurityID") != "9999":
                raise SourceUnavailableError(f"{config.symbol} PCF contains a non-{family.component_market} security row")
            components.append(component(
                fields.get("InstrumentID", ""), fields.get("InstrumentName", ""),
                quantity, family,
            ))
    if trading_day != expected_day:
        raise SourceUnavailableError(f"{config.symbol} PCF date {trading_day} does not match requested {expected_day}")
    if unit != config.redemption_unit:
        raise SourceUnavailableError(f"{config.symbol} PCF unit {unit:.0f} does not match configured {config.redemption_unit:.0f}")
    if creation not in {"Y", "N"} or redemption not in {"Y", "N"}:
        raise SourceUnavailableError(f"{config.symbol} PCF creation/redemption flags are invalid")
    if not components or len({item.symbol for item in components}) != len(components):
        raise SourceUnavailableError(f"{config.symbol} PCF must contain a non-empty unique {family.component_market} security basket")
    if config.component_count and len(components) != config.component_count:
        raise SourceUnavailableError(f"{config.symbol} PCF securities changed: got {len(components)}, expected {config.component_count}")
    return PCF(
        config, trading_day, pre_trading_day, creation, redemption, estimated_cash, previous_cash,
        nav_per_cu, tuple(components), source_url, hashlib.sha256(raw).hexdigest(),
    )


class PCFStore:
    def __init__(self, root: Path, timeout: float, pacer: common.PCFRequestPacer) -> None:
        self.root, self.timeout, self.pacer = root, timeout, pacer

    def path(self, fund: FundConfig, trading_day: date) -> Path:
        return self.root / fund.security_id / f"{trading_day:%Y%m%d}.xml"

    def fetch(self, fund: FundConfig, trading_day: date, family: FamilyConfig = NASDAQ_FAMILY) -> PCF:
        path = self.path(fund, trading_day)
        if path.is_file():
            try:
                return parse_pcf(fund, path.read_bytes(), fund.source_url(trading_day), trading_day, family)
            except (OSError, SourceUnavailableError):
                pass
        self.pacer.wait()
        source_url = fund.source_url(trading_day)
        raw = bytes(common._http_fetch(source_url, None, common.SOURCE_HEADERS, self.timeout))
        parsed = parse_pcf(fund, raw, source_url, trading_day, family)
        common._atomic_write_bytes(path, raw)
        return parsed


class CoefficientStore:
    """Persist an audited same-PCF coefficient across uploader restarts."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, pcf: PCF) -> Path:
        return self.root / pcf.fund.security_id / f"{pcf.trading_day:%Y%m%d}.json"

    def parity_path(self, trading_day: date, family: FamilyConfig) -> Path:
        return self.root / "parity" / family.key / f"{trading_day:%Y%m%d}.json"

    def load_parity(self, trading_day: date, family: FamilyConfig) -> CentralParity | None:
        try:
            value = json.loads(self.parity_path(trading_day, family).read_text(encoding="utf-8"))
            rate = float(value["rate"])
            if (
                int(value.get("schema_version") or 0) != 1
                or value.get("family") != family.key
                or value.get("trading_day") != trading_day.isoformat()
                or not math.isfinite(rate)
                or rate <= 0
            ):
                return None
            return CentralParity(rate, trading_day, datetime.now(SHANGHAI))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def save_parity(self, parity: CentralParity, family: FamilyConfig) -> None:
        if not math.isfinite(parity.rate) or parity.rate <= 0:
            raise SourceUnavailableError(f"{family.fx_pair} coefficient parity cache is invalid")
        value = {
            "schema_version": 1,
            "family": family.key,
            "trading_day": parity.trading_day.isoformat(),
            "rate": parity.rate,
            "saved_at": datetime.now(SHANGHAI).isoformat(timespec="milliseconds"),
        }
        common._atomic_write_bytes(
            self.parity_path(parity.trading_day, family),
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )

    def load(self, pcf: PCF, family: FamilyConfig) -> float | None:
        try:
            value = json.loads(self.path(pcf).read_text(encoding="utf-8"))
            coefficient = float(value["coefficient"])
            parity_rate = float(value["parity_rate"])
            futures_close = float(value["futures_close"])
            if (
                int(value.get("schema_version") or 0) != 1
                or value.get("family") != family.key
                or str(value.get("symbol") or "").upper() != pcf.fund.symbol
                or value.get("pcf_sha256") != pcf.sha256
                or value.get("pcf_trading_day") != pcf.trading_day.isoformat()
                or value.get("pcf_pre_trading_day") != pcf.pre_trading_day.isoformat()
                or value.get("parity_day") != pcf.pre_trading_day.isoformat()
                or not math.isfinite(coefficient)
                or coefficient <= 0
                or not math.isfinite(parity_rate)
                or parity_rate <= 0
                or not math.isfinite(futures_close)
                or futures_close <= 0
            ):
                return None
            return coefficient
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def save(
        self,
        pcf: PCF,
        parity: CentralParity,
        futures_close: float,
        coefficient: float,
        family: FamilyConfig,
    ) -> None:
        if parity.trading_day != pcf.pre_trading_day or not math.isfinite(coefficient) or coefficient <= 0:
            raise SourceUnavailableError(f"{pcf.fund.symbol} coefficient cache inputs are invalid")
        self.save_parity(parity, family)
        value = {
            "schema_version": 1,
            "family": family.key,
            "symbol": pcf.fund.symbol,
            "pcf_sha256": pcf.sha256,
            "pcf_trading_day": pcf.trading_day.isoformat(),
            "pcf_pre_trading_day": pcf.pre_trading_day.isoformat(),
            "parity_day": parity.trading_day.isoformat(),
            "parity_rate": parity.rate,
            "futures_close": futures_close,
            "coefficient": coefficient,
            "saved_at": datetime.now(SHANGHAI).isoformat(timespec="milliseconds"),
        }
        common._atomic_write_bytes(
            self.path(pcf),
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )


class _SafeTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_table = self.in_row = self.in_cell = False
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "InfoTable":
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.in_row, self.row = True, []
        elif self.in_row and tag in {"td", "th"}:
            self.in_cell, self.cell = True, []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.in_cell:
            self.row.append("".join(self.cell).replace("\xa0", " ").strip())
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.row:
                self.rows.append(self.row)
            self.in_row = False
        elif tag == "table" and self.in_table:
            self.in_table = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell.append(data)


def fetch_safe_central_parity(
    trading_day: date, timeout: float, family: FamilyConfig = NASDAQ_FAMILY,
) -> CentralParity:
    body = urllib.parse.urlencode({
        "startDate": trading_day.isoformat(), "endDate": trading_day.isoformat(), "queryYN": "true",
    }).encode("utf-8")
    request = urllib.request.Request(
        SAFE_QUERY_URL, data=body,
        headers={**common.SOURCE_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceUnavailableError(f"SAFE {family.fx_pair} parity unavailable for {trading_day}: {exc}") from exc
    parser = _SafeTableParser()
    parser.feed(html)
    if len(parser.rows) < 2:
        raise SourceUnavailableError(f"SAFE has no central-parity table for {trading_day}")
    header = parser.rows[0]
    try:
        currency_index = header.index(family.central_parity_column)
    except ValueError as exc:
        raise SourceUnavailableError(f"SAFE central-parity table has no {family.central_parity_column} column") from exc
    row = next((item for item in parser.rows[1:] if item and item[0] == trading_day.isoformat()), None)
    if row is None or len(row) <= currency_index:
        raise SourceUnavailableError(f"SAFE has no {family.fx_pair} row for {trading_day}")
    rate = number(row[currency_index], f"SAFE {family.fx_pair}") / family.central_parity_divisor
    if rate <= 0:
        raise SourceUnavailableError(f"SAFE {family.fx_pair} parity must be positive")
    return CentralParity(rate, trading_day, datetime.now(SHANGHAI))


def contract_expiry(value: Any) -> date | None:
    digits = re.search(r"(\d{8})", str(value or ""))
    if not digits:
        return None
    try:
        return datetime.strptime(digits.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def select_front_nq_contract(details: list[Any], as_of: datetime) -> Any:
    today = as_of.date()
    candidates: list[tuple[date, Any]] = []
    for detail in details:
        contract = getattr(detail, "contract", detail)
        expiry = contract_expiry(getattr(contract, "lastTradeDateOrContractMonth", ""))
        if expiry is not None and expiry >= today:
            candidates.append((expiry, contract))
    if not candidates:
        raise SourceUnavailableError("IBKR returned no unexpired index future")
    return min(candidates, key=lambda item: item[0])[1]


def future_search_contract(family: FamilyConfig = NASDAQ_FAMILY) -> Any:
    try:
        from ib_insync import Future
    except ImportError as exc:
        raise SourceUnavailableError("ib_insync is required for index private valuation") from exc
    return Future(
        symbol=family.reference_symbol,
        lastTradeDateOrContractMonth="",
        exchange=family.future_exchange,
        currency=family.future_currency,
        multiplier=str(int(family.futures_multiplier_usd_per_point)),
    )


def xetra_close_auction_anchor(
    ib: Any, contract: Any, trading_day: date, timeout: float = 20,
) -> XetraCloseAnchor:
    """Return FDXM bars ending at Xetra 17:30 and 17:35 local time.

    Futures trade beyond the cash close, so neither the futures daily close
    nor the futures session settlement is a valid PCF calibration anchor.
    """
    berlin = ZoneInfo("Europe/Berlin")
    request_end = datetime.combine(trading_day, clock_time(17, 36), berlin)
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=request_end,
        durationStr="1800 S",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=False,
        formatDate=2,
        keepUpToDate=False,
        timeout=timeout,
    )
    wanted = {
        clock_time(17, 29): "1730",
        clock_time(17, 34): "1735",
    }
    found: dict[str, tuple[datetime, float]] = {}
    for bar in bars:
        timestamp = getattr(bar, "date", None)
        if not isinstance(timestamp, datetime):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=berlin)
        timestamp = timestamp.astimezone(berlin)
        label = wanted.get(timestamp.time().replace(tzinfo=None))
        price = common.raw_positive_price(getattr(bar, "close", None))
        if timestamp.date() == trading_day and label is not None and price is not None:
            found[label] = (timestamp, price)
    missing = [label for label in ("1730", "1735") if label not in found]
    if missing:
        raise SourceUnavailableError(
            f"IBKR has no complete FDXM one-minute TRADES bar ending at Xetra {', '.join(missing)} on {trading_day}"
        )
    start_1730, close_1730 = found["1730"]
    start_1735, close_1735 = found["1735"]
    return XetraCloseAnchor(trading_day, close_1730, close_1735, start_1730, start_1735)


class NQMarket:
    """IBKR index-future feed shared by NQ, ES, N225M and FDXM families.

    The historical close deliberately uses the same specific front contract as
    the live quote. A new contract selection must therefore trigger a new
    daily PCF/NAV coefficient rather than silently mixing continuous series.
    """

    def __init__(self, host: str, port: int, client_id: int, timeout: float, family: FamilyConfig = NASDAQ_FAMILY) -> None:
        self.host, self.port, self.client_id, self.timeout, self.family = host, port, client_id, timeout, family
        self.ib: Any = None
        self.contract: Any = None
        self.ticker: Any = None
        self.close_cache: dict[date, float] = {}

    def connect(self) -> None:
        if self.ib is not None and self.ib.isConnected() and self.ticker is not None:
            return
        self.close()
        try:
            from ib_insync import IB
        except ImportError as exc:
            raise SourceUnavailableError("ib_insync is required for index private valuation") from exc
        ib = IB()
        ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout, readonly=True)
        if not ib.isConnected():
            raise SourceUnavailableError("TWS API socket did not become connected")
        # IBKR returns live data when subscribed and delayed data otherwise.
        # The backend keeps delayed snapshots READY but non-actionable.
        ib.reqMarketDataType(3)
        details = ib.reqContractDetails(future_search_contract(self.family))
        contract = select_front_nq_contract(list(details), datetime.now(ZoneInfo(self.family.future_timezone)))
        qualified = ib.qualifyContracts(contract)
        if len(qualified) != 1:
            ib.disconnect()
            raise SourceUnavailableError(
                f"IBKR could not uniquely qualify the front {self.family.future_exchange} {self.family.reference_symbol} future"
            )
        self.ib, self.contract = ib, qualified[0]
        self.ticker = ib.reqMktData(self.contract, "", False, False)

    def close(self) -> None:
        if self.ib is not None:
            if self.contract is not None:
                try:
                    self.ib.cancelMktData(self.contract)
                except Exception:
                    pass
            try:
                self.ib.disconnect()
            except Exception:
                pass
        self.ib = self.contract = self.ticker = None

    def quote(self) -> NQQuote | None:
        self.connect()
        self.ib.sleep(0.05)
        bid = common.raw_positive_price(getattr(self.ticker, "bid", None))
        ask = common.raw_positive_price(getattr(self.ticker, "ask", None))
        observed_at = common.ib_ticker_observed_at(getattr(self.ticker, "time", None))
        market_type = common.MARKET_DATA_TYPE_NAMES.get(int(getattr(self.ticker, "marketDataType", 0) or 0))
        if bid is None or ask is None or ask < bid or observed_at is None or market_type is None:
            return None
        return NQQuote(
            self.family.reference_symbol, bid, ask,
            common.raw_positive_price(getattr(self.ticker, "last", None)), observed_at, market_type,
            datetime.now(SHANGHAI),
        )

    def close_for_day(self, trading_day: date) -> float:
        if trading_day in self.close_cache:
            return self.close_cache[trading_day]
        self.connect()
        if self.family.calibration_anchor_kind == "xetra_close_auction_1735":
            anchor = xetra_close_auction_anchor(self.ib, self.contract, trading_day, self.timeout)
            self.close_cache[trading_day] = anchor.close_1735
            common.log(
                f"{self.family.reference_symbol} Xetra anchor {trading_day} "
                f"17:30={anchor.close_1730:.2f} 17:35={anchor.close_1735:.2f} selected=17:35"
            )
            return anchor.close_1735
        future_timezone = ZoneInfo(self.family.future_timezone)
        end = datetime.combine(trading_day, datetime.min.time(), future_timezone).replace(
            hour=self.family.regular_close_hour, minute=self.family.regular_close_minute,
        )
        bars = self.ib.reqHistoricalData(
            self.contract, endDateTime=end, durationStr="4 D", barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=1, keepUpToDate=False,
        )
        close = next((common.raw_positive_price(getattr(bar, "close", None)) for bar in reversed(bars) if common.raw_positive_price(getattr(bar, "close", None)) is not None), None)
        if close is None:
            raise SourceUnavailableError(
                f"IBKR has no {self.family.reference_symbol} regular-session close for {trading_day}"
            )
        self.close_cache[trading_day] = close
        return close


def nq_contract_equivalent(
    pcf: PCF, parity: CentralParity, futures_close: float, family: FamilyConfig = NASDAQ_FAMILY,
) -> float:
    if parity.trading_day != pcf.pre_trading_day:
        raise SourceUnavailableError(f"SAFE parity day {parity.trading_day} must equal PCF pre-trading day {pcf.pre_trading_day}")
    security_value_cny = pcf.nav_per_cu - pcf.previous_cash_component_cny
    denominator = parity.rate * futures_close * family.futures_multiplier_usd_per_point
    if security_value_cny <= 0 or denominator <= 0:
        raise SourceUnavailableError(f"{family.reference_symbol} proxy coefficient inputs must be positive")
    return security_value_cny / denominator


def payload(
    pcf: PCF, cfets: common.CFETSQuote | JPYCFETSQuote | EURCFETSQuote, quote: NQQuote, contracts: float, generated_at: datetime,
    family: FamilyConfig = NASDAQ_FAMILY,
) -> dict[str, Any]:
    return {
        "schema_version": common.INPUT_SCHEMA_VERSION,
        "symbol": pcf.fund.symbol,
        "model_version": family.model_version,
        "pcf": pcf.to_payload(contracts),
        "fx": cfets.to_payload(),
        "ib": quote.to_payload(),
        "source": family.source,
        "generated_at": common.iso_timestamp(generated_at),
    }


def can_publish_index_input(pcf: PCF, fx: common.CFETSSpotQuote, quote: NQQuote, now: datetime) -> bool:
    local = now.astimezone(SHANGHAI)
    if pcf.trading_day != local.date():
        return False
    observed = fx.source_observed_at or fx.fetched_at
    if fx.source == common.CFETS_SPOT_SOURCE:
        return fx.trading_day == local.date() and -30 <= (now-observed).total_seconds() <= 180
    if fx.source != common.CFETS_PREOPEN_FALLBACK_SOURCE:
        return False
    minute = local.hour * 60 + local.minute
    return (local.weekday() < 5 and 555 <= minute < 575
            and pcf.pre_trading_day <= fx.trading_day < local.date()
            and quote.market_data_type == "Live"
            and quote.observed_at.astimezone(SHANGHAI).date() == local.date()
            and 0 <= (now-quote.observed_at).total_seconds() <= 15)


def post_batch(args: argparse.Namespace, values: list[dict[str, Any]], now: datetime, family: FamilyConfig) -> None:
    values = wire_values(values, now, os.getenv("NNN_PRIVATE_INDEX_FX_WIRE", "current"))
    result = common.post_private_input_batch(
        args.server, args.token, values, args.timeout,
        source=family.source, generated_at=now,
        origin_ip=args.origin_ip,
        origin_tls_insecure=args.origin_tls_insecure,
        origin_ca_file=args.origin_ca_file,
    )
    expected = {str(item["symbol"]).upper() for item in values}
    accepted = {str(symbol).upper() for symbol in result.get("accepted", [])}
    if result.get("rejected") or accepted != expected:
        raise SourceUnavailableError(f"{family.display_name} input batch was partially rejected: {result['rejected']}")


def wire_values(values: list[dict[str, Any]], now: datetime, mode: str) -> list[dict[str, Any]]:
    """Explicit compatibility for the pre-metadata server; never disguise fallback FX."""
    if mode == "current":
        return values
    if mode != "legacy_realtime":
        raise SourceUnavailableError("unknown index FX wire compatibility mode")
    result = []
    for item in values:
        fx = item["fx"]
        if (fx.get("source") != common.CFETS_SPOT_SOURCE
                or fx.get("trading_day") != now.astimezone(SHANGHAI).date().isoformat()
                or fx.get("fallback_reason")):
            raise SourceUnavailableError("legacy server cannot safely accept fallback FX metadata; upgrade backend")
        result.append({**item, "fx": {k: v for k, v in fx.items()
                                     if k not in {"source_observed_at", "fallback_reason"}}})
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--server", default=os.getenv("NNN_PRIVATE_SERVER", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    result.add_argument("--family", choices=sorted(FAMILIES), default=os.getenv("NNN_PRIVATE_US_INDEX_FAMILY", "nasdaq"))
    result.add_argument("--runtime-dir", default="")
    result.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", "12")))
    result.add_argument("--ib-host", default="")
    result.add_argument("--ib-port", type=int, default=0)
    result.add_argument("--ib-client-id", type=int, default=0)
    result.add_argument("--upload-interval", type=float, default=0)
    result.add_argument("--once", action="store_true")
    return result


def run(args: argparse.Namespace) -> int:
    if not str(args.token).strip():
        raise SourceUnavailableError("NNN_UPLOAD_TOKEN or --token is required")
    family = FAMILIES[args.family]
    env_prefix = {
        NASDAQ_FAMILY.key: "NASDAQ",
        SP500_FAMILY.key: "SP500",
        NIKKEI225_FAMILY.key: "NIKKEI225",
        DAX_FAMILY.key: "GERMANY",
    }[family.key]
    runtime_dir = args.runtime_dir or os.getenv(
        f"NNN_PRIVATE_{env_prefix}_RUNTIME_DIR", f"scripts/.runtime/private_{family.key}",
    )
    ib_host = args.ib_host or os.getenv(f"NNN_PRIVATE_{env_prefix}_IB_HOST", os.getenv("NNN_IB_HOST", "127.0.0.1"))
    ib_port = args.ib_port or int(os.getenv(f"NNN_PRIVATE_{env_prefix}_IB_PORT", os.getenv("NNN_IB_PORT", "7496")))
    default_client_id = {
        NASDAQ_FAMILY.key: "159659",
        SP500_FAMILY.key: "159655",
        NIKKEI225_FAMILY.key: "159866",
        DAX_FAMILY.key: "159561",
    }[family.key]
    ib_client_id = args.ib_client_id or int(os.getenv(f"NNN_PRIVATE_{env_prefix}_IB_CLIENT_ID", default_client_id))
    upload_interval = args.upload_interval or float(os.getenv(f"NNN_PRIVATE_{env_prefix}_UPLOAD_INTERVAL", "3"))
    runtime = Path(runtime_dir).expanduser().resolve()
    pacer = common.PCFRequestPacer(10.0)
    store = PCFStore(runtime / "pcf", args.timeout, pacer)
    coefficient_store = CoefficientStore(runtime / "coefficients")
    cfets_client = common.PrivateCFETSSpotClient(
        args.server,
        args.token,
        timeout=args.timeout,
        origin_ip=args.origin_ip,
        origin_tls_insecure=args.origin_tls_insecure,
        origin_ca_file=args.origin_ca_file,
    )
    market = NQMarket(ib_host, ib_port, ib_client_id, args.timeout, family)
    pcf_day: date | None = None
    pcfs: dict[str, PCF] = {}
    coefficients: dict[str, float] = {}
    cfets: common.CFETSSpotQuote | None = None
    next_static = next_fx = next_upload = 0.0
    try:
        while not common.STOP_EVENT.is_set():
            now = datetime.now(SHANGHAI)
            if not common.ib_collection_window(now):
                market.close()
                pcf_day, pcfs, coefficients, cfets = None, {}, {}, None
                next_static = 0.0
                common.STOP_EVENT.wait(30.0)
                continue
            if pcf_day != now.date():
                pcf_day, pcfs, coefficients = now.date(), {}, {}
                next_static = next_fx = 0.0
                cfets = None
            # Warm the stream even when PCF/coefficient/FX are still unavailable.
            try:
                market.quote()
            except Exception as exc:
                market.close()
                common.log(f"{family.display_name} IB warmup unavailable: {exc}", error=True)
            if len(pcfs) < len(family.funds) and time.monotonic() >= next_static:
                for fund in family.funds:
                    if fund.symbol in pcfs:
                        continue
                    try:
                        pcf = store.fetch(fund, now.date(), family)
                        coefficient = coefficient_store.load(pcf, family)
                        source = "validated cache"
                        if coefficient is None:
                            parity = coefficient_store.load_parity(pcf.pre_trading_day, family)
                            source = "validated parity cache"
                            if parity is None:
                                parity = fetch_safe_central_parity(pcf.pre_trading_day, args.timeout, family)
                                coefficient_store.save_parity(parity, family)
                                source = "fresh sources"
                            futures_close = market.close_for_day(pcf.pre_trading_day)
                            coefficient = nq_contract_equivalent(pcf, parity, futures_close, family)
                            coefficient_store.save(pcf, parity, futures_close, coefficient, family)
                    except Exception as exc:
                        common.log(f"{fund.symbol} {family.display_name} PCF/coefficient unavailable: {exc}", error=True)
                        continue
                    pcfs[fund.symbol] = pcf
                    coefficients[fund.symbol] = coefficient
                    common.log(
                        f"{fund.symbol} {family.reference_symbol} contracts={coefficient:.6f} "
                        f"components={len(pcf.components)} coefficient_source={source}"
                    )
                next_static = time.monotonic() + 15.0
                if not pcfs:
                    common.STOP_EVENT.wait(15.0)
                    continue
            if time.monotonic() >= next_fx:
                try:
                    cfets = cfets_client.fetch(family.fx_pair, now.date())
                except Exception as exc:
                    cfets = None
                    if common.is_true(os.getenv("NNN_PRIVATE_INDEX_PREOPEN_FX_FALLBACK", "true")):
                        try:
                            cfets = cfets_client.fetch_preopen_fallback(family.fx_pair, now.date(), now)
                        except Exception as fallback_exc:
                            common.log(f"{family.display_name} CFETS unavailable: {exc}; fallback: {fallback_exc}", error=True)
                next_fx = time.monotonic() + (10.0 if cfets is None or cfets.source == common.CFETS_PREOPEN_FALLBACK_SOURCE else 60.0)
            try:
                quote = market.quote()
            except Exception as exc:
                market.close()
                common.log(
                    f"{family.display_name} {family.reference_symbol} market data unavailable: {exc}",
                    error=True,
                )
                common.STOP_EVENT.wait(15.0)
                continue
            if quote is None or cfets is None or time.monotonic() < next_upload:
                common.STOP_EVENT.wait(0.2)
                continue
            # HTTP/PCF requests may cross 09:35; recheck the wall clock at publish time.
            now = datetime.now(SHANGHAI)
            active_pcfs = [pcfs[fund.symbol] for fund in family.funds if fund.symbol in pcfs
                           and can_publish_index_input(pcfs[fund.symbol], cfets, quote, now)]
            if not active_pcfs:
                common.STOP_EVENT.wait(0.2)
                continue
            values = [payload(pcf, cfets, quote, coefficients[pcf.fund.symbol], now, family) for pcf in active_pcfs]
            try:
                post_batch(args, values, now, family)
            except (SourceUnavailableError, RuntimeError, urllib.error.URLError, TimeoutError, OSError) as exc:
                common.log(f"{family.display_name} private batch upload unavailable; retrying in 3s: {exc}", error=True)
                next_upload = time.monotonic() + 3.0
                common.STOP_EVENT.wait(0.5)
                continue
            common.log(
                f"{family.display_name} private indicative inputs uploaded funds={len(active_pcfs)}/{len(family.funds)} "
                f"{family.reference_symbol}={quote.bid:.2f}/{quote.ask:.2f}"
            )
            next_upload = time.monotonic() + max(0.5, upload_interval)
            if args.once:
                return 0
    finally:
        market.close()
    return 0


def main() -> int:
    common.load_env_file(".sina-uploader.env")
    args = parser().parse_args()
    signal.signal(signal.SIGINT, common._request_stop)
    signal.signal(signal.SIGTERM, common._request_stop)
    try:
        return run(args)
    except (SourceUnavailableError, RuntimeError, urllib.error.URLError) as exc:
        common.log(str(exc), error=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

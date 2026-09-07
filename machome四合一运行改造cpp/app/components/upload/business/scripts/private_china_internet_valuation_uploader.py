#!/usr/bin/env python3
"""One shared-PCF collector for the four private China-internet QDII ETFs.

The process fetches each official daily PCF once and builds a de-duplicated set
of (market, symbol) component keys. US overnight components use IBKR, while HK
and CN components reuse the public Sina collector so this process neither
requires SEHK subscriptions nor consumes TWS lines for Hong Kong shares. Each
fund receives only quotes present in its own dated PCF and fails closed if any
required component is missing.
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
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import private_159605_valuation_uploader as legacy
import private_valuation_uploader as common
import sina_quote_uploader as sina


@dataclass(frozen=True)
class FundConfig:
    symbol: str
    security_id: str
    name: str
    model_version: str
    exchange: str
    expected_count: int
    expected_markets: tuple[tuple[str, int], ...]

    def source_url(self, trading_day: date) -> str:
        if self.exchange == "SZSE":
            return (
                "https://reportdocs.static.szse.cn/files/text/ETFDown/"
                f"pcf_{self.security_id}_{trading_day:%Y%m%d}.xml"
            )
        return "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=" + self.security_id


FUNDS: tuple[FundConfig, ...] = (
    FundConfig("SZ159605", "159605", "中概互联网ETF广发", "private.full-cash-substitution.multi-market-pcf.v1", "SZSE", 30, (("HK", 23), ("US", 7))),
    FundConfig("SZ159607", "159607", "中概互联网ETF嘉实", "private.full-cash-substitution.multi-market-pcf.159607.v1", "SZSE", 30, (("HK", 23), ("US", 7))),
    FundConfig("SH513050", "513050", "中概互联网ETF易方达", "private.full-cash-substitution.multi-market-pcf.513050.v1", "SSE", 35, (("HK", 25), ("US", 10))),
    FundConfig("SH513220", "513220", "中概互联ETF招商", "private.full-cash-substitution.multi-market-pcf.513220.v1", "SSE", 30, (("CN", 9), ("HK", 15), ("US", 6))),
)
FUND_BY_SYMBOL = {fund.symbol: fund for fund in FUNDS}
PCF_SOURCE = "official_exchange_pcf"
IB_SOURCE = "IBKR_TWS"
CN_SOURCE = "SINA_A_STOCK"
HK_SOURCE = "SINA_HK"
DEFAULT_RUNTIME_DIR = "scripts/.runtime/private_china_internet"
DEFAULT_SOURCE = "mac-home-private-china-internet-uploader"
DEFAULT_UPLOAD_INTERVAL = 3.0
DEFAULT_CFETS_INTERVAL = 60.0
DEFAULT_PCF_INTERVAL = 60.0
DEFAULT_PCF_START_AT = "08:30"
DEFAULT_PCF_SYMBOL_INTERVAL = 10.0
DEFAULT_TIMEOUT = 12.0


class SourceUnavailableError(RuntimeError):
    pass


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def direct_text(node: ET.Element, name: str, *, required: bool = True) -> str:
    values = [str(child.text or "").strip() for child in node if local_name(child.tag) == name]
    if len(values) > 1 or (required and (len(values) != 1 or not values[0])):
        raise SourceUnavailableError(f"PCF {name} must appear exactly once")
    return values[0] if values else ""


def as_date(value: str, field: str) -> date:
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            pass
    raise SourceUnavailableError(f"PCF {field} is not a date: {value!r}")


def finite(value: str, field: str, *, positive: bool = False) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise SourceUnavailableError(f"PCF {field} is not numeric") from exc
    if not math.isfinite(parsed) or (positive and parsed <= 0):
        raise SourceUnavailableError(f"PCF {field} is invalid")
    return parsed


def market_for(exchange_source: str, symbol: str) -> tuple[str, str, str]:
    value = symbol.strip().upper()
    if exchange_source == "9999":
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", value):
            raise SourceUnavailableError(f"invalid US component {symbol!r}")
        return value, "US", "USD"
    if exchange_source == "103":
        if not re.fullmatch(r"\d{1,5}", value):
            raise SourceUnavailableError(f"invalid HK component {symbol!r}")
        return value.zfill(4), "HK", "HKD"
    if exchange_source in {"101", "102"}:
        if not re.fullmatch(r"\d{6}", value):
            raise SourceUnavailableError(f"invalid CN component {symbol!r}")
        return value, "CN", "CNY"
    raise SourceUnavailableError(f"unsupported PCF exchange source {exchange_source!r} for {symbol!r}")


@dataclass(frozen=True)
class Component:
    symbol: str
    name: str
    market: str
    currency: str
    quantity: float

    @property
    def key(self) -> tuple[str, str]:
        return self.market, self.symbol

    def to_payload(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "name": self.name, "market": self.market, "currency": self.currency, "quantity": self.quantity}


@dataclass(frozen=True)
class PCF:
    fund: FundConfig
    trading_day: date
    pre_trading_day: date | None
    creation: str
    redemption: str
    redemption_unit: float
    estimate_cash_cny: float
    nav_per_cu: float
    components: tuple[Component, ...]
    source_url: str
    sha256: str

    def to_payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "security_id": self.fund.security_id,
            "trading_day": self.trading_day.isoformat(),
            "creation": self.creation,
            "redemption": self.redemption,
            "creation_redemption_unit": self.redemption_unit,
            "estimate_cash_component_cny": self.estimate_cash_cny,
            "nav_per_cu": self.nav_per_cu,
            "component_count": len(self.components),
            "components": [component.to_payload() for component in self.components],
            "source_url": self.source_url,
            "sha256": self.sha256,
        }
        if self.pre_trading_day is not None:
            result["pre_trading_day"] = self.pre_trading_day.isoformat()
        return result


@dataclass(frozen=True)
class Quote:
    component: Component
    bid: float
    ask: float
    last: float | None
    source: str
    market_data_type: str
    observed_at: datetime
    stream_checked_at: datetime

    def to_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.component.symbol,
            "market": self.component.market,
            "currency": self.component.currency,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "source": self.source,
            "market_data_type": self.market_data_type,
            "observed_at": common.iso_timestamp(self.observed_at),
            "stream_checked_at": common.iso_timestamp(self.stream_checked_at),
        }


def parse_pcf(
    config: FundConfig,
    raw: bytes,
    source_url: str,
    expected_day: date | None = None,
    *,
    enforce_expected_composition: bool = True,
) -> PCF:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceUnavailableError(f"{config.symbol} PCF is not XML: {exc}") from exc
    root_name = local_name(root.tag)
    if config.exchange == "SZSE":
        if root_name != "PCFFile":
            raise SourceUnavailableError(f"{config.symbol} expected SZSE PCFFile")
        security_id = direct_text(root, "SecurityID")
        trading_day = as_date(direct_text(root, "TradingDay"), "TradingDay")
        pre_text = direct_text(root, "PreTradingDay", required=False)
        creation, redemption = direct_text(root, "Creation").upper(), direct_text(root, "Redemption").upper()
        unit = finite(direct_text(root, "CreationRedemptionUnit"), "CreationRedemptionUnit", positive=True)
        cash = finite(direct_text(root, "EstimateCashComponent"), "EstimateCashComponent")
        nav = finite(direct_text(root, "NAVperCU"), "NAVperCU", positive=True)
        declared = int(finite(direct_text(root, "TotalRecordNum"), "TotalRecordNum", positive=True))
        nodes = [item for item in root.iter() if local_name(item.tag) == "Component"]
        if declared != len(nodes):
            raise SourceUnavailableError(f"{config.symbol} PCF component count does not match TotalRecordNum")
        components: list[Component] = []
        for node in nodes:
            fields = {local_name(child.tag): str(child.text or "").strip() for child in node}
            quantity = finite(fields.get("ComponentShare", ""), "ComponentShare")
            if quantity <= 0:
                continue
            symbol, market, currency = market_for(fields.get("UnderlyingSecurityIDSource", ""), fields.get("UnderlyingSecurityID", ""))
            name = fields.get("UnderlyingSymbol", "").strip() or symbol
            components.append(Component(symbol, name, market, currency, quantity))
    else:
        if root_name != "SSEPortfolioCompositionFile":
            raise SourceUnavailableError(f"{config.symbol} expected SSEPortfolioCompositionFile")
        security_id = direct_text(root, "FundInstrumentID")
        trading_day = as_date(direct_text(root, "TradingDay"), "TradingDay")
        pre_text = direct_text(root, "PreTradingDay", required=False)
        switch = direct_text(root, "CreationRedemptionSwitch")
        creation = redemption = "Y" if switch != "0" else "N"
        unit = finite(direct_text(root, "CreationRedemptionUnit"), "CreationRedemptionUnit", positive=True)
        cash = finite(direct_text(root, "EstimatedCashComponent"), "EstimatedCashComponent")
        nav = finite(direct_text(root, "NAVperCU"), "NAVperCU", positive=True)
        declared = int(finite(direct_text(root, "RecordNumber"), "RecordNumber", positive=True))
        nodes = [item for item in root.iter() if local_name(item.tag) == "Component"]
        if declared != len(nodes):
            raise SourceUnavailableError(f"{config.symbol} PCF component count does not match RecordNumber")
        components = []
        for node in nodes:
            fields = {local_name(child.tag): str(child.text or "").strip() for child in node}
            quantity = finite(fields.get("Quantity", ""), "Quantity", positive=True)
            symbol, market, currency = market_for(fields.get("UnderlyingSecurityID", ""), fields.get("InstrumentID", ""))
            name = fields.get("InstrumentName", "").strip() or symbol
            components.append(Component(symbol, name, market, currency, quantity))
    if security_id != config.security_id or (expected_day is not None and trading_day != expected_day):
        raise SourceUnavailableError(f"{config.symbol} PCF identity/day mismatch")
    if unit != 1_000_000.0 or creation not in {"Y", "N"} or redemption not in {"Y", "N"}:
        raise SourceUnavailableError(f"{config.symbol} PCF primary-market fields are invalid")
    counts: dict[str, int] = {}
    for component in components:
        counts[component.market] = counts.get(component.market, 0) + 1
    if enforce_expected_composition and (len(components) != config.expected_count or tuple(sorted(counts.items())) != tuple(sorted(config.expected_markets))):
        raise SourceUnavailableError(f"{config.symbol} PCF component composition changed: {counts}")
    if len({component.key for component in components}) != len(components):
        raise SourceUnavailableError(f"{config.symbol} PCF has duplicate market/symbol components")
    return PCF(config, trading_day, as_date(pre_text, "PreTradingDay") if pre_text else None, creation, redemption, unit, cash, nav, tuple(components), source_url, hashlib.sha256(raw).hexdigest())


class PCFStore:
    def __init__(self, root: Path, timeout: float, pacer: common.PCFRequestPacer) -> None:
        self.root, self.timeout, self.pacer = root, timeout, pacer

    def path(self, config: FundConfig, day: date) -> Path:
        return self.root / config.security_id / f"{day:%Y%m%d}.xml"

    def load(self, config: FundConfig, day: date) -> PCF | None:
        path = self.path(config, day)
        if not path.is_file():
            return None
        try:
            return parse_pcf(config, path.read_bytes(), config.source_url(day), day)
        except (OSError, SourceUnavailableError):
            return None

    def fetch_today(self, config: FundConfig, day: date) -> PCF:
        self.pacer.wait()
        source_url = config.source_url(day)
        raw = common._http_fetch(source_url, None, common.SOURCE_HEADERS, self.timeout)
        parsed = parse_pcf(config, bytes(raw), source_url, day)
        common._atomic_write_bytes(self.path(config, day), bytes(raw))
        return parsed


def component_map(pcfs: Iterable[PCF]) -> dict[tuple[str, str], Component]:
    result: dict[tuple[str, str], Component] = {}
    for pcf in pcfs:
        for component in pcf.components:
            existing = result.get(component.key)
            if existing is not None and (existing.currency != component.currency or existing.market != component.market):
                raise SourceUnavailableError(f"component key collision for {component.key}")
            result[component.key] = component
    return result


def sina_component_symbol(component: Component) -> str:
    if component.market == "HK":
        return component.symbol.zfill(5)
    if component.market == "CN":
        return ("SH" if component.symbol.startswith("6") else "SZ") + component.symbol
    raise SourceUnavailableError(f"Sina does not support component {component.market}:{component.symbol}")


def sina_quote_observed_at(value: dict[str, Any]) -> datetime | None:
    quote_date = str(value.get("quote_date") or "").strip()
    quote_time = str(value.get("quote_time") or "").strip()
    try:
        return datetime.fromisoformat(f"{quote_date}T{quote_time}").replace(tzinfo=common.SHANGHAI)
    except ValueError:
        return None


def quote_from_sina(
    component: Component, value: dict[str, Any], checked_at: datetime
) -> Quote | None:
    observed_at = sina_quote_observed_at(value)
    if observed_at is None:
        return None
    price = common.raw_positive_price(value.get("price"))
    if component.market == "HK":
        # Sina HK exposes a trade/last, not a two-sided book. Keep the value
        # visible but mark it non-Live so the backend can never make the
        # full-cash-substitution estimate actionable.
        if value.get("source") != "sina_hk" or price is None:
            return None
        return Quote(
            component, price, price, price, HK_SOURCE, "SinaLast", observed_at, checked_at
        )
    if component.market == "CN":
        bids, asks = value.get("bid_levels") or [], value.get("ask_levels") or []
        bid = next(
            (float(level.get("price") or 0) for level in bids if float(level.get("price") or 0) > 0),
            None,
        )
        ask = next(
            (float(level.get("price") or 0) for level in asks if float(level.get("price") or 0) > 0),
            None,
        )
        if bid is None or ask is None or ask < bid or value.get("source") != "sina":
            return None
        return Quote(component, bid, ask, price, CN_SOURCE, "Live", observed_at, checked_at)
    return None


class MarketHub:
    def __init__(self, host: str, port: int, client_id: int, timeout: float) -> None:
        self.host, self.port, self.client_id, self.timeout = host, port, client_id, timeout
        self.ib: Any = None
        self.subscriptions: dict[tuple[str, str], tuple[Component, Any, Any]] = {}
        self.sina_components: dict[tuple[str, str], Component] = {}
        self.keys: set[tuple[str, str]] = set()

    def close(self) -> None:
        if self.ib is not None:
            for _component, contract, _ticker in self.subscriptions.values():
                try:
                    self.ib.cancelMktData(contract)
                except Exception:
                    pass
            try:
                self.ib.disconnect()
            except Exception:
                pass
        self.ib, self.subscriptions, self.sina_components, self.keys = None, {}, {}, set()

    def sync(self, components: dict[tuple[str, str], Component]) -> None:
        if set(components) == self.keys and self.ib is not None and self.ib.isConnected():
            return
        self.close()
        try:
            from ib_insync import IB, Stock
        except ImportError as exc:
            raise SourceUnavailableError("ib_insync is required for private China-internet valuation") from exc
        self.sina_components = {
            key: value for key, value in components.items() if value.market in {"CN", "HK"}
        }
        ib_components = {key: value for key, value in components.items() if value.market == "US"}
        ib = IB()
        ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout, readonly=True)
        if not ib.isConnected():
            raise SourceUnavailableError("TWS API socket did not become connected")
        # Delayed mode still yields Live when entitled, and otherwise exposes
        # an explicitly delayed US quote. The backend preserves that label and
        # blocks actionability instead of silently dropping the whole basket.
        ib.reqMarketDataType(3)
        self.ib = ib
        try:
            for key, component in ib_components.items():
                contract = Stock(component.symbol, "OVERNIGHT", "USD")
                qualified = ib.qualifyContracts(contract)
                if len(qualified) != 1:
                    raise SourceUnavailableError(f"TWS could not uniquely qualify {component.market}:{component.symbol}")
                self.subscriptions[key] = (component, qualified[0], ib.reqMktData(qualified[0], "", False, False))
        except Exception:
            self.close()
            raise
        self.keys = set(components)
        common.log(
            "China-internet market hub ready "
            f"ib_us={len(ib_components)} "
            f"sina_hk={sum(item.market == 'HK' for item in self.sina_components.values())} "
            f"sina_cn={sum(item.market == 'CN' for item in self.sina_components.values())}"
        )

    def poll(self) -> dict[tuple[str, str], Quote] | None:
        if self.ib is None or not self.ib.isConnected():
            raise SourceUnavailableError("TWS stream is disconnected")
        self.ib.sleep(0.05)
        now = datetime.now(common.SHANGHAI)
        quotes: dict[tuple[str, str], Quote] = {}
        for key, (component, _contract, ticker) in self.subscriptions.items():
            bid = common.raw_positive_price(getattr(ticker, "bid", None))
            ask = common.raw_positive_price(getattr(ticker, "ask", None))
            observed = common.ib_ticker_observed_at(getattr(ticker, "time", None))
            market_type = common.MARKET_DATA_TYPE_NAMES.get(int(getattr(ticker, "marketDataType", 0) or 0))
            if bid is None or ask is None or ask < bid or observed is None or market_type is None:
                return None
            quotes[key] = Quote(component, bid, ask, common.raw_positive_price(getattr(ticker, "last", None)), IB_SOURCE, market_type, observed, now)
        if self.sina_components:
            symbols = [sina_component_symbol(item) for item in self.sina_components.values()]
            raw = sina.fetch_sina_quotes(symbols, self.timeout)
            for key, component in self.sina_components.items():
                quote = quote_from_sina(
                    component, raw.get(sina_component_symbol(component)) or {}, now
                )
                if quote is None:
                    return None
                quotes[key] = quote
        return quotes if len(quotes) == len(self.keys) else None


def payload(pcf: PCF, rates: tuple[common.CFETSSpotQuote, ...], quotes: dict[tuple[str, str], Quote], now: datetime, source: str) -> dict[str, Any]:
    selected: list[Quote] = []
    for component in pcf.components:
        quote = quotes.get(component.key)
        if quote is None:
            raise SourceUnavailableError(f"{pcf.fund.symbol} missing {component.market}:{component.symbol} quote")
        selected.append(quote)
    return {
        "schema_version": 1,
        "symbol": pcf.fund.symbol,
        "model_version": pcf.fund.model_version,
        "pcf": pcf.to_payload(),
        "fx_rates": [rate.to_payload() for rate in rates],
        "market_quotes": [quote.to_payload() for quote in selected],
        "source": source,
        "generated_at": common.iso_timestamp(now),
    }


def post_batch(args: argparse.Namespace, items: list[dict[str, Any]], now: datetime) -> None:
    result = common.post_private_input_batch(
        args.server, args.token, items, args.timeout,
        source=args.source, generated_at=now,
        origin_ip=args.origin_ip,
        origin_tls_insecure=args.origin_tls_insecure,
        origin_ca_file=args.origin_ca_file,
    )
    expected = {str(item["symbol"]).upper() for item in items}
    accepted = {str(symbol).upper() for symbol in result.get("accepted", [])}
    if result.get("rejected") or accepted != expected:
        raise SourceUnavailableError(f"private input batch was partially rejected: {result['rejected']}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    result.add_argument("--runtime-dir", default=os.getenv("NNN_PRIVATE_CHINA_INTERNET_RUNTIME_DIR", DEFAULT_RUNTIME_DIR))
    result.add_argument("--source", default=os.getenv("NNN_PRIVATE_CHINA_INTERNET_UPLOAD_SOURCE", DEFAULT_SOURCE))
    result.add_argument("--upload-interval", default=os.getenv("NNN_PRIVATE_CHINA_INTERNET_UPLOAD_INTERVAL", "3s"))
    result.add_argument("--cfets-interval", default=os.getenv("NNN_PRIVATE_CHINA_INTERNET_CFETS_INTERVAL", "60s"))
    result.add_argument("--pcf-interval", default=os.getenv("NNN_PRIVATE_CHINA_INTERNET_PCF_INTERVAL", "60s"))
    result.add_argument("--pcf-start-at", default=os.getenv("NNN_PRIVATE_CHINA_INTERNET_PCF_START_AT", DEFAULT_PCF_START_AT))
    result.add_argument("--pcf-symbol-interval", default=os.getenv("NNN_PRIVATE_PCF_SYMBOL_INTERVAL", "10s"))
    result.add_argument("--ib-host", default=os.getenv("NNN_PRIVATE_CHINA_INTERNET_IB_HOST", os.getenv("NNN_IB_HOST", "127.0.0.1")))
    result.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_PRIVATE_CHINA_INTERNET_IB_PORT", os.getenv("NNN_IB_PORT", "7496"))))
    result.add_argument("--ib-client-id", type=int, default=int(os.getenv("NNN_PRIVATE_CHINA_INTERNET_IB_CLIENT_ID", "159607")))
    result.add_argument("--timeout", type=float, default=float(os.getenv("NNN_UPLOAD_TIMEOUT", DEFAULT_TIMEOUT)))
    result.add_argument("--once", action="store_true")
    return result


def collect_pcfs(store: PCFStore, today: date) -> dict[str, PCF]:
    values: dict[str, PCF] = {}
    for config in FUNDS:
        try:
            values[config.symbol] = store.fetch_today(config, today)
            common.log(f"{config.symbol} PCF ready components={config.expected_count}")
        except Exception as exc:
            common.log(f"{config.symbol} PCF unavailable: {exc}", error=True)
    return values


def run(args: argparse.Namespace) -> int:
    runtime = Path(args.runtime_dir).expanduser().resolve()
    # Omit an explicit path so every private collector on this Mac shares the
    # flock-protected state named by NNN_PRIVATE_PCF_PACER_STATE.
    store = PCFStore(runtime / "pcf", args.timeout, common.PCFRequestPacer(common.parse_duration(args.pcf_symbol_interval, DEFAULT_PCF_SYMBOL_INTERVAL)))
    cfets = common.PrivateCFETSSpotClient(
        args.server,
        args.token,
        timeout=args.timeout,
        origin_ip=args.origin_ip,
        origin_tls_insecure=args.origin_tls_insecure,
        origin_ca_file=args.origin_ca_file,
    )
    hub = MarketHub(args.ib_host, args.ib_port, args.ib_client_id, args.timeout)
    pcf_state: dict[str, PCF] = {}
    rates: tuple[common.CFETSSpotQuote, ...] | None = None
    next_pcf = next_fx = next_upload = 0.0
    pcf_day: date | None = None
    try:
        while not common.STOP_EVENT.is_set():
            now, monotonic = datetime.now(common.SHANGHAI), time.monotonic()
            if not common.ib_collection_window(now):
                hub.close()
                pcf_state, rates, pcf_day = {}, None, None
                common.STOP_EVENT.wait(30.0)
                continue
            if pcf_day != now.date() and monotonic >= next_pcf and common.pcf_collection_window(now, common.parse_clock_minute(args.pcf_start_at, 8 * 60 + 30)):
                collected = collect_pcfs(store, now.date())
                pcf_state = collected if len(collected) == len(FUNDS) else {}
                pcf_day = now.date() if pcf_state else None
                next_pcf = time.monotonic() + max(30.0, common.parse_duration(args.pcf_interval, DEFAULT_PCF_INTERVAL))
                if pcf_state:
                    hub.sync(component_map(pcf_state.values()))
                else:
                    # Do not price a subset against a potentially stale union
                    # when one of the day's official baskets is unavailable.
                    hub.close()
            if monotonic >= next_fx:
                try:
                    rates = cfets.fetch_many(("USD/CNY", "HKD/CNY"), now.date())
                except Exception as exc:
                    rates = None
                    common.log(f"China-internet CFETS unavailable: {exc}", error=True)
                next_fx = time.monotonic() + max(10.0, common.parse_duration(args.cfets_interval, DEFAULT_CFETS_INTERVAL))
            if not pcf_state or rates is None:
                common.STOP_EVENT.wait(0.2)
                continue
            try:
                quotes = hub.poll()
            except Exception as exc:
                common.log(f"China-internet market hub reconnecting: {exc}", error=True)
                hub.close()
                common.STOP_EVENT.wait(1.0)
                continue
            if quotes is None or monotonic < next_upload:
                common.STOP_EVENT.wait(0.1)
                continue
            items = [payload(pcf, rates, quotes, now, args.source) for pcf in pcf_state.values()]
            post_batch(args, items, now)
            common.log(f"China-internet private inputs uploaded funds={len(pcf_state)} unique_components={len(quotes)}")
            next_upload = time.monotonic() + max(0.5, common.parse_duration(args.upload_interval, DEFAULT_UPLOAD_INTERVAL))
            if args.once:
                return 0
    finally:
        hub.close()
    return 0


def main() -> int:
    common.load_env_file(".sina-uploader.env")
    args = parser().parse_args()
    if not str(args.token).strip():
        print("NNN_UPLOAD_TOKEN or --token is required", file=sys.stderr)
        return 2
    signal.signal(signal.SIGINT, common._request_stop)
    signal.signal(signal.SIGTERM, common._request_stop)
    try:
        return run(args)
    except (SourceUnavailableError, RuntimeError, urllib.error.URLError) as exc:
        common.log(str(exc), error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

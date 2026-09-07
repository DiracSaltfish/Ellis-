#!/usr/bin/env python3
"""Collect the three XOP private valuations through one audited TWS session.

Production ``smart`` mode sends SZ159518, SH513350 and SZ162411 through one
SMART subscription on one IB client connection.  ``dual_shadow`` remains an
explicit no-upload diagnostic that records SMART/OVERNIGHT equivalence;
``dual_route`` preserves the former mixed routing as a rollback aid.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_162411_valuation_uploader as lof162411  # noqa: E402
import private_513350_valuation_uploader as fund513350  # noqa: E402
import private_nasdaq_valuation_uploader as safe_common  # noqa: E402
import private_valuation_uploader as common  # noqa: E402


SMART = "smart"
OVERNIGHT = "overnight"
DUAL_ROUTE = "dual_route"
DUAL_SHADOW = "dual_shadow"
ROUTE_CONTRACT = {
    SMART: "XOP STK SMART/ARCA conId=413951498",
    OVERNIGHT: "XOP STK OVERNIGHT/ARCA conId=413951498",
}
ROUTE_SESSION = {
    SMART: "us_smart_live",
    OVERNIGHT: "us_overnight_live",
}
DEFAULT_SOURCE = "mac-home-private-xop-family-uploader"
MAX_BACKOFF = 60.0
MAX_STREAM_PACKET_AGE_SECONDS = 90.0


def audited_quote_payload(quote: common.IBQuote, route: str) -> dict[str, Any]:
    if route not in ROUTE_CONTRACT:
        raise common.InputValidationError(f"unknown XOP route {route!r}")
    value = quote.to_payload()
    value["contract"] = ROUTE_CONTRACT[route]
    value["quote_session"] = ROUTE_SESSION[route]
    return value


def sanitize_last(last: Any, bid: float, ask: float) -> float | None:
    """Drop obviously corrupt LAST values; never scale or rewrite a price."""
    value = common.raw_positive_price(last)
    if value is None:
        return None
    spread = max(ask - bid, 0.01)
    if value < bid - 20.0 * spread or value > ask + 20.0 * spread:
        return None
    return value


class XOPMarketHub:
    """One IB client connection with independently audited XOP subscriptions."""

    def __init__(self, host: str, port: int, client_id: int, timeout: float, mode: str) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.mode = mode
        self.ib: Any = None
        self.contracts: dict[str, Any] = {}
        self.tickers: dict[str, Any] = {}

    def required_routes(self) -> tuple[str, ...]:
        if self.mode in {SMART, OVERNIGHT}:
            return (self.mode,)
        return (SMART, OVERNIGHT)

    def connect(self) -> None:
        try:
            from ib_insync import Contract, IB, Stock
        except ImportError as exc:
            raise RuntimeError("ib_insync is required by the XOP family collector") from exc
        self.close()
        ib = IB()
        try:
            # Quote-only collectors must not trigger account/position sync.
            ib.wrapper.clientId = self.client_id
            ib.client.connect(self.host, self.port, self.client_id, timeout=self.timeout)
            if not ib.isConnected():
                raise RuntimeError("TWS API socket did not become connected")
            contracts: dict[str, Any] = {}
            for route in self.required_routes():
                candidate = (
                    Contract(
                        conId=common.XOP_CON_ID,
                        symbol=common.REFERENCE_SYMBOL,
                        secType=common.XOP_SEC_TYPE,
                        exchange=common.XOP_EXCHANGE,
                        primaryExchange=common.XOP_PRIMARY_EXCHANGE,
                        currency=common.XOP_CURRENCY,
                    )
                    if route == SMART
                    else Stock("XOP", "OVERNIGHT", "USD", primaryExchange="ARCA")
                )
                qualified = ib.qualifyContracts(candidate)
                if len(qualified) != 1:
                    raise RuntimeError(f"TWS could not uniquely qualify XOP / {route.upper()}")
                contract = qualified[0]
                if (
                    str(getattr(contract, "symbol", "") or "").upper() != "XOP"
                    or str(getattr(contract, "secType", "") or "") != "STK"
                    or str(getattr(contract, "currency", "") or "") != "USD"
                ):
                    raise RuntimeError(f"qualified {route} contract is not XOP STK USD")
                con_id = int(getattr(contract, "conId", 0) or 0)
                if con_id != common.XOP_CON_ID:
                    raise RuntimeError(f"qualified {route} XOP conId is {con_id}, expected {common.XOP_CON_ID}")
                contracts[route] = contract
            ib.reqMarketDataType(1)
            tickers = {
                route: ib.reqMktData(contract, "", False, False)
                for route, contract in contracts.items()
            }
        except Exception:
            if ib.isConnected():
                ib.disconnect()
            raise
        self.ib, self.contracts, self.tickers = ib, contracts, tickers

    def is_connected(self) -> bool:
        return bool(self.ib is not None and self.ib.isConnected())

    def poll(self, wait_seconds: float = 0.05) -> dict[str, common.IBQuote]:
        if not self.is_connected():
            raise common.SourceUnavailableError("XOP family TWS stream is disconnected")
        self.ib.sleep(max(0.0, wait_seconds))
        if not self.is_connected():
            raise common.SourceUnavailableError("XOP family TWS stream disconnected while polling")
        checked_at = datetime.now(common.SHANGHAI)
        quotes: dict[str, common.IBQuote] = {}
        for route, ticker in self.tickers.items():
            bid = common.raw_positive_price(getattr(ticker, "bid", None))
            ask = common.raw_positive_price(getattr(ticker, "ask", None))
            market_type = common.MARKET_DATA_TYPE_NAMES.get(
                int(getattr(ticker, "marketDataType", 0) or 0)
            )
            observed_at = common.ib_ticker_observed_at(getattr(ticker, "time", None))
            if bid is None or ask is None or ask < bid or market_type is None or observed_at is None:
                continue
            packet_age = (checked_at - observed_at).total_seconds()
            if packet_age > MAX_STREAM_PACKET_AGE_SECONDS:
                raise common.SourceUnavailableError(
                    f"XOP family {route} market-data stream is stale "
                    f"age={packet_age:.1f}s; reconnect required"
                )
            quote = common.IBQuote(
                bid=bid,
                ask=ask,
                last=sanitize_last(getattr(ticker, "last", None), bid, ask),
                market_data_type=market_type,
                observed_at=observed_at,
                stream_checked_at=checked_at,
            )
            common.validate_ib_quote(quote)
            quotes[route] = quote
        return quotes

    def regular_session_reference(self, pcf_day: date) -> fund513350.XOPRegularSessionReference:
        """Reuse the one connection for SH513350's once-daily RTH audit anchor."""
        if not self.is_connected():
            raise fund513350.SourceUnavailableError("IBKR is disconnected before RTH calibration")
        return regular_session_reference_from_ib(self.ib, pcf_day)

    def close(self) -> None:
        ib = self.ib
        if ib is not None and ib.isConnected():
            for contract in self.contracts.values():
                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass
            ib.disconnect()
        self.ib = None
        self.contracts = {}
        self.tickers = {}


def regular_session_reference_from_ib(ib: Any, pcf_day: date) -> fund513350.XOPRegularSessionReference:
    """Fetch the prior completed XOP RTH reference on an existing IB client."""
    if ib is None or not ib.isConnected():
        raise fund513350.SourceUnavailableError("IBKR is disconnected before RTH calibration")
    try:
        from ib_insync import Stock
    except ImportError as exc:
        raise RuntimeError("ib_insync is required by the XOP family collector") from exc
    contract = Stock("XOP", "SMART", "USD", primaryExchange="ARCA")
    qualified = ib.qualifyContracts(contract)
    if len(qualified) != 1:
        raise fund513350.SourceUnavailableError("IBKR could not qualify XOP SMART for calibration")
    cutoff = datetime(pcf_day.year, pcf_day.month, pcf_day.day, 8, 30, tzinfo=common.SHANGHAI)
    latest_day = cutoff.astimezone(fund513350.NEW_YORK).date()
    try:
        bids = ib.reqHistoricalData(
            qualified[0], cutoff, "10 D", "1 min", "BID", True, 2, False, [], 60
        )
        asks = ib.reqHistoricalData(
            qualified[0], cutoff, "10 D", "1 min", "ASK", True, 2, False, [], 60
        )
    except Exception as exc:
        raise fund513350.SourceUnavailableError(f"IBKR RTH BID/ASK unavailable: {exc}") from exc
    bid_days = fund513350.regular_session_prices(bids)
    ask_days = fund513350.regular_session_prices(asks)
    days = sorted(
        (day for day in set(bid_days) & set(ask_days) if day <= latest_day), reverse=True
    )
    if not days:
        raise fund513350.SourceUnavailableError("no completed XOP RTH session for calibration")
    session_day = days[0]
    minutes = sorted(set(bid_days[session_day]) & set(ask_days[session_day]))
    if not minutes:
        raise fund513350.SourceUnavailableError("no aligned XOP RTH BID/ASK bars")
    minute = minutes[-1]
    bid, ask = bid_days[session_day][minute], ask_days[session_day][minute]
    if ask < bid:
        raise fund513350.SourceUnavailableError("XOP RTH Ask is below Bid")
    observed_at = datetime.combine(
        session_day, datetime.min.time(), tzinfo=fund513350.NEW_YORK
    ).replace(hour=minute // 60, minute=minute % 60).astimezone(common.SHANGHAI)
    return fund513350.XOPRegularSessionReference(session_day, bid, ask, observed_at)


class XOPHistoryClient:
    """Short-lived history-only IB client; it never opens a market-data stream."""

    def __init__(self, host: str, port: int, client_id: int, timeout: float) -> None:
        self.host, self.port, self.client_id, self.timeout = host, port, client_id, timeout

    def fetch_regular_session_reference(self, pcf_day: date) -> fund513350.XOPRegularSessionReference:
        try:
            from ib_insync import IB
        except ImportError as exc:
            raise RuntimeError("ib_insync is required by the XOP family collector") from exc
        ib = IB()
        try:
            ib.wrapper.clientId = self.client_id
            ib.client.connect(self.host, self.port, self.client_id, timeout=self.timeout)
            if not ib.isConnected():
                raise fund513350.SourceUnavailableError("history-only TWS socket did not connect")
            return regular_session_reference_from_ib(ib, pcf_day)
        finally:
            if ib.isConnected():
                ib.disconnect()


class ShadowRecorder:
    def __init__(self, runtime_dir: str | os.PathLike[str]) -> None:
        self.root = Path(runtime_dir).expanduser().resolve() / "shadow"

    def record(self, smart: common.IBQuote, overnight: common.IBQuote) -> None:
        checked_at = max(
            smart.stream_checked_at or smart.observed_at,
            overnight.stream_checked_at or overnight.observed_at,
        )
        row = {
            "checked_at": common.iso_timestamp(checked_at),
            "smart": smart.to_payload(),
            "overnight": overnight.to_payload(),
            "observed_alignment_ms": abs((smart.observed_at - overnight.observed_at).total_seconds()) * 1000,
            "bid_delta": overnight.bid - smart.bid,
            "ask_delta": overnight.ask - smart.ask,
        }
        path = self.root / f"{checked_at.astimezone(common.SHANGHAI).date().isoformat()}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")

    def report(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        days: set[str] = set()
        for path in sorted(self.root.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                    days.add(path.stem)
        aligned = [row for row in rows if float(row.get("observed_alignment_ms", math.inf)) <= 500.0]
        exact = [
            row for row in aligned
            if abs(float(row.get("bid_delta", math.inf))) <= 1e-12
            and abs(float(row.get("ask_delta", math.inf))) <= 1e-12
        ]
        max_delta = max(
            (
                max(abs(float(row.get("bid_delta", math.inf))), abs(float(row.get("ask_delta", math.inf))))
                for row in aligned
            ),
            default=math.inf,
        )
        longest = 0.0
        divergence_start: datetime | None = None
        last_at: datetime | None = None
        for row in sorted(aligned, key=lambda item: str(item.get("checked_at") or "")):
            try:
                checked_at = common.parse_datetime(row.get("checked_at"), "checked_at")
                diverged = max(abs(float(row["bid_delta"])), abs(float(row["ask_delta"]))) > 0.01
            except (KeyError, TypeError, ValueError, common.InputValidationError):
                continue
            if diverged:
                divergence_start = divergence_start or checked_at
                last_at = checked_at
            elif divergence_start is not None:
                longest = max(longest, ((last_at or checked_at) - divergence_start).total_seconds())
                divergence_start = last_at = None
        if divergence_start is not None and last_at is not None:
            longest = max(longest, (last_at - divergence_start).total_seconds())
        equality = len(exact) / len(aligned) if aligned else 0.0
        result = {
            "days": len(days),
            "samples": len(rows),
            "aligned_samples": len(aligned),
            "alignment_rate": len(aligned) / len(rows) if rows else 0.0,
            "exact_equality_rate": equality,
            "max_bid_or_ask_delta": None if not math.isfinite(max_delta) else max_delta,
            "longest_delta_over_0_01_seconds": longest,
        }
        result["eligible_for_single_route"] = bool(
            result["days"] >= 5
            and rows
            and len(aligned) == len(rows)
            and equality >= 0.9999
            and math.isfinite(max_delta)
            and max_delta <= 0.01
            and longest <= 1.0
        )
        return result


@dataclass
class FamilyState:
    pcf159518: common.PCFInput | None = None
    pcf513350: fund513350.PCFInput | None = None
    cfets: common.CFETSSpotQuote | None = None
    seed162411: lof162411.PublicModelSeed | None = None
    base_safe: Any = None
    current_fx: common.CFETSSpotQuote | None = None
    current_safe: Any = None
    calibration513350: fund513350.DailyCalibration | None = None
    reference513350: fund513350.XOPRegularSessionReference | None = None


class XOPFamilyCollector:
    def __init__(self, args: argparse.Namespace, hub: XOPMarketHub) -> None:
        self.args = args
        self.hub = hub
        runtime = Path(args.runtime_dir).expanduser().resolve()
        pacer = common.PCFRequestPacer(
            common.parse_duration(args.pcf_symbol_interval, 10.0), args.pcf_pacer_state
        )
        self.pcf159518 = common.PCFClient(
            runtime / "159518",
            timeout=args.timeout,
            refresh_seconds=common.parse_duration(args.pcf_interval, 600.0),
            lookback_days=args.lookback_days,
            request_pacer=pacer,
        )
        self.pcf513350 = fund513350.PCFClient(
            runtime / "513350", timeout=args.timeout, request_pacer=pacer
        )
        self.cfets = common.PrivateCFETSSpotClient(
            args.server,
            args.token,
            timeout=args.timeout,
            origin_ip=args.origin_ip,
            origin_tls_insecure=args.origin_tls_insecure,
            origin_ca_file=args.origin_ca_file,
        )
        self.history = XOPHistoryClient(
            args.ib_host, args.ib_port, args.ib_history_client_id, args.ib_timeout
        )
        self.state = FamilyState()
        self.state_lock = threading.RLock()
        self.next_pcf = self.next_cfets = self.next_seed = self.next_safe = 0.0
        self.next_calibration = 0.0
        self.day: date | None = None

    def refresh(self, now: datetime) -> None:
        minute = common.shanghai_minute_of_day(now)
        if (
            not self.args.force
            and (
                not common.is_shanghai_weekday(now)
                or minute < self.args.pcf_start_minute
                or minute >= common.IB_COLLECTION_END_MINUTE
            )
        ):
            return
        monotonic = time.monotonic()
        today = now.astimezone(common.SHANGHAI).date()
        if self.day != today:
            with self.state_lock:
                self.day = today
                self.state.pcf159518 = None
                self.state.pcf513350 = None
                self.state.cfets = None
                self.state.current_fx = None
                self.state.current_safe = None
                self.state.calibration513350 = None
                self.state.reference513350 = None
            self.next_calibration = 0.0
            self.next_pcf = self.next_cfets = self.next_seed = self.next_safe = 0.0
        if monotonic >= self.next_pcf and common.pcf_collection_window(now, self.args.pcf_start_minute):
            pcf_success = True
            try:
                pcf159518 = self.pcf159518.fetch_latest(today, force_refresh=True)
                if pcf159518.trading_day != today:
                    raise common.SourceUnavailableError("SZ159518 PCF is not for the current Shanghai day")
                with self.state_lock:
                    if self.day == today:
                        self.state.pcf159518 = pcf159518
            except Exception as exc:
                pcf_success = False
                common.log(f"XOP family SZ159518 PCF unavailable: {exc}", error=True)
            try:
                pcf = self.pcf513350.fetch_today(today)
                with self.state_lock:
                    if self.day == today:
                        if self.state.pcf513350 is None or self.state.pcf513350.sha256 != pcf.sha256:
                            self.state.calibration513350 = fund513350.load_calibration(
                                str(Path(self.args.runtime_dir) / "513350"), pcf
                            )
                            self.state.reference513350 = None
                            self.next_calibration = 0.0
                        self.state.pcf513350 = pcf
            except Exception as exc:
                pcf_success = False
                common.log(f"XOP family SH513350 PCF unavailable: {exc}", error=True)
            self.next_pcf = time.monotonic() + (
                max(10.0, common.parse_duration(self.args.pcf_interval, 600.0))
                if pcf_success else 30.0
            )
        if monotonic >= self.next_cfets:
            try:
                cfets = self.cfets.fetch("USD/CNY", today)
                with self.state_lock:
                    if self.day == today:
                        self.state.cfets = cfets
                        self.state.current_fx = cfets
            except Exception as exc:
                common.log(f"XOP family shared CFETS spot unavailable: {exc}", error=True)
                self.next_cfets = time.monotonic() + 15.0
            else:
                self.next_cfets = time.monotonic() + max(10.0, self.args.fx_interval)
        # SZ162411 is the only SAFE-basis private fund. Its current FX must be
        # the SAFE daily central parity, not the shared CFETS spot. SAFE
        # publishes around 09:15, so skip the pre-open retry storm and gate the
        # first attempt on the post-09:15 window.
        if monotonic >= self.next_safe and common.shanghai_minute_of_day(now) >= 9 * 60 + 15:
            try:
                current_safe = safe_common.fetch_safe_central_parity(
                    today, self.args.timeout, safe_common.NASDAQ_FAMILY
                )
                with self.state_lock:
                    if self.day == today:
                        self.state.current_safe = current_safe
            except Exception as exc:
                common.log(f"XOP family SZ162411 current SAFE parity unavailable: {exc}", error=True)
                self.next_safe = time.monotonic() + 15.0
            else:
                self.next_safe = time.monotonic() + max(10.0, self.args.fx_interval)
        if monotonic >= self.next_seed:
            try:
                seed = lof162411.fetch_public_seed(self.args, now)
                base_safe = safe_common.fetch_safe_central_parity(
                    seed.base_nav_date, self.args.timeout, safe_common.NASDAQ_FAMILY
                )
                with self.state_lock:
                    self.state.seed162411, self.state.base_safe = seed, base_safe
            except Exception as exc:
                common.log(f"XOP family SZ162411 seed unavailable: {exc}", error=True)
                self.next_seed = time.monotonic() + 30.0
            else:
                self.next_seed = time.monotonic() + max(60.0, self.args.seed_interval)
        self.refresh_513350_calibration()

    def refresh_513350_calibration(self) -> None:
        if time.monotonic() < self.next_calibration:
            return
        with self.state_lock:
            pcf = self.state.pcf513350
            fx = self.state.cfets
            existing = self.state.calibration513350
        if pcf is None or fx is None or existing is not None or fx.trading_day != pcf.trading_day:
            return
        try:
            reference = self.history.fetch_regular_session_reference(pcf.trading_day)
            calibration = fund513350.calibrate(pcf, fx, reference)
            fund513350.save_calibration(
                str(Path(self.args.runtime_dir) / "513350"), calibration
            )
            with self.state_lock:
                if self.state.pcf513350 and self.state.pcf513350.sha256 == pcf.sha256:
                    self.state.reference513350 = reference
                    self.state.calibration513350 = calibration
            self.next_calibration = 0.0
            common.log(
                "XOP family SH513350 history-only calibration ready "
                f"day={pcf.trading_day} rth_day={reference.session_day}"
            )
        except Exception as exc:
            self.next_calibration = time.monotonic() + 60.0
            common.log(f"XOP family SH513350 calibration unavailable: {exc}", error=True)
    def route_for(self, symbol: str) -> str:
        if self.args.mode in {SMART, OVERNIGHT}:
            return self.args.mode
        return OVERNIGHT if symbol == fund513350.SYMBOL else SMART

    def build_inputs(
        self, quotes: dict[str, common.IBQuote], now: datetime
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        values: list[dict[str, Any]] = []
        failures: dict[str, str] = {}
        with self.state_lock:
            state = FamilyState(
                pcf159518=self.state.pcf159518,
                pcf513350=self.state.pcf513350,
                cfets=self.state.cfets,
                seed162411=self.state.seed162411,
                base_safe=self.state.base_safe,
                current_fx=self.state.current_fx,
                current_safe=self.state.current_safe,
                calibration513350=self.state.calibration513350,
                reference513350=self.state.reference513350,
            )
        route159 = self.route_for(common.SYMBOL)
        try:
            if state.pcf159518 is None or state.cfets is None or route159 not in quotes:
                raise common.SourceUnavailableError("missing PCF, CFETS or routed XOP quote")
            value = common.build_private_payload(
                state.pcf159518, state.cfets, quotes[route159], generated_at=now,
                source=self.args.source,
            )
            value["ib"] = audited_quote_payload(quotes[route159], route159)
            values.append(value)
        except Exception as exc:
            failures[common.SYMBOL] = str(exc)

        route513 = self.route_for(fund513350.SYMBOL)
        try:
            if state.pcf513350 is None or state.cfets is None or route513 not in quotes:
                raise fund513350.SourceUnavailableError("missing PCF, CFETS or routed XOP quote")
            if state.calibration513350 is None:
                raise fund513350.SourceUnavailableError("history-only daily calibration is not ready")
            value = fund513350.build_private_payload(
                state.pcf513350, state.cfets, quotes[route513], state.calibration513350,
                generated_at=now, source=self.args.source,
            )
            value["ib"] = audited_quote_payload(quotes[route513], route513)
            values.append(value)
        except Exception as exc:
            failures[fund513350.SYMBOL] = str(exc)

        route162 = self.route_for(lof162411.SYMBOL)
        try:
            if (
                state.seed162411 is None
                or state.base_safe is None
                or state.current_safe is None
                or route162 not in quotes
            ):
                raise lof162411.SourceUnavailableError("missing seed, base SAFE parity, current SAFE parity or routed XOP quote")
            values.append(lof162411.build_payload(
                state.seed162411,
                state.base_safe,
                state.current_safe,
                quotes[route162],
                now,
                self.args.source,
                contract=ROUTE_CONTRACT[route162],
                quote_session=ROUTE_SESSION[route162],
            ))
        except Exception as exc:
            failures[lof162411.SYMBOL] = str(exc)
        return values, failures


class SourceRefreshWorker:
    """Keep slow HTTP/PCF work away from the latency-sensitive TWS loop."""

    def __init__(self, collector: XOPFamilyCollector) -> None:
        self.collector = collector
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name="xop-family-source-refresh",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        # ib_insync resolves its asyncio loop from the calling thread even for
        # the low-level synchronous connect used by the short history client.
        # Python 3.11+ no longer creates a loop implicitly for worker threads.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not self.stop_event.is_set() and not common.STOP_EVENT.is_set():
                try:
                    self.collector.refresh(datetime.now(common.SHANGHAI))
                except Exception as exc:
                    common.log(f"XOP family source worker recovered from error: {exc}", error=True)
                self.stop_event.wait(0.5)
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=max(1.0, self.collector.args.timeout + 1.0))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--server", default=os.getenv("NNN_SERVER_URL", "https://1navs.com"))
    result.add_argument("--public-server", default=os.getenv("NNN_PRIVATE_162411_PUBLIC_SERVER", "https://1navs.com"))
    result.add_argument("--token", default=os.getenv("NNN_UPLOAD_TOKEN", ""))
    result.add_argument("--origin-ip", default=os.getenv("NNN_ORIGIN_IP", ""))
    result.add_argument("--origin-ca-file", default=os.getenv("NNN_ORIGIN_CA_FILE", ""))
    result.add_argument("--origin-tls-insecure", action="store_true", default=common.is_true(os.getenv("NNN_ORIGIN_TLS_INSECURE", "")))
    result.add_argument(
        "--timeout", type=float,
        default=float(os.getenv("NNN_UPLOAD_TIMEOUT", "12")),
        help="HTTP source/upload timeout",
    )
    result.add_argument(
        "--ib-timeout", type=float,
        default=float(os.getenv("NNN_XOP_FAMILY_IB_TIMEOUT", "40")),
        help="TWS socket handshake timeout",
    )
    result.add_argument("--ib-host", default=os.getenv("NNN_XOP_FAMILY_IB_HOST", os.getenv("NNN_IB_HOST", "192.168.1.111")))
    result.add_argument("--ib-port", type=int, default=int(os.getenv("NNN_XOP_FAMILY_IB_PORT", os.getenv("NNN_IB_PORT", "7496"))))
    result.add_argument("--ib-client-id", type=int, default=int(os.getenv("NNN_XOP_FAMILY_IB_CLIENT_ID", "159519")))
    result.add_argument(
        "--ib-history-client-id", type=int,
        default=int(os.getenv("NNN_XOP_FAMILY_IB_HISTORY_CLIENT_ID", "159521")),
    )
    result.add_argument(
        "--mode",
        choices=(SMART, DUAL_ROUTE, DUAL_SHADOW, OVERNIGHT),
        default=os.getenv("NNN_XOP_FAMILY_MODE", SMART),
    )
    result.add_argument("--source", default=os.getenv("NNN_XOP_FAMILY_SOURCE", DEFAULT_SOURCE))
    result.add_argument("--runtime-dir", default=os.getenv("NNN_XOP_FAMILY_RUNTIME_DIR", "scripts/.runtime/private_xop_family"))
    result.add_argument("--upload-interval", type=float, default=float(os.getenv("NNN_XOP_FAMILY_UPLOAD_INTERVAL", "3")))
    result.add_argument("--shadow-interval", type=float, default=float(os.getenv("NNN_XOP_FAMILY_SHADOW_INTERVAL", "1")))
    result.add_argument("--fx-interval", type=float, default=float(os.getenv("NNN_XOP_FAMILY_FX_INTERVAL", "60")))
    result.add_argument("--seed-interval", type=float, default=float(os.getenv("NNN_XOP_FAMILY_SEED_INTERVAL", "900")))
    result.add_argument("--pcf-interval", default=os.getenv("NNN_XOP_FAMILY_PCF_INTERVAL", "10m"))
    result.add_argument("--pcf-start-at", default=os.getenv("NNN_XOP_FAMILY_PCF_START_AT", "08:30"))
    result.add_argument("--pcf-symbol-interval", default=os.getenv("NNN_PRIVATE_PCF_SYMBOL_INTERVAL", "10s"))
    result.add_argument("--pcf-pacer-state", default=os.getenv("NNN_PRIVATE_PCF_PACER_STATE", "/tmp/newnavnav-private-pcf-pacer.json"))
    result.add_argument("--lookback-days", type=int, default=int(os.getenv("NNN_XOP_FAMILY_LOOKBACK_DAYS", "10")))
    result.add_argument("--once", action="store_true")
    result.add_argument("--require-complete-batch", action="store_true")
    result.add_argument("--once-deadline", type=float, default=180.0)
    result.add_argument("--force", action="store_true", help="diagnostic collection outside the Shanghai window")
    result.add_argument("--shadow-report", action="store_true")
    return result


def run(args: argparse.Namespace) -> int:
    recorder = ShadowRecorder(args.runtime_dir)
    if args.shadow_report:
        print(json.dumps(recorder.report(), ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    if args.mode != DUAL_SHADOW and not str(args.token or "").strip():
        raise common.InputValidationError("NNN_UPLOAD_TOKEN or --token is required")
    args.pcf_start_minute = common.parse_clock_minute(args.pcf_start_at, 8 * 60 + 30)
    hub = XOPMarketHub(args.ib_host, args.ib_port, args.ib_client_id, args.ib_timeout, args.mode)
    # A shadow preflight must test only the TWS handshake/subscriptions.  It
    # must not be held hostage by PCF, SAFE, CFETS or public API availability.
    collector = None if args.mode == DUAL_SHADOW else XOPFamilyCollector(args, hub)
    source_worker = SourceRefreshWorker(collector) if collector is not None else None
    if source_worker is not None:
        source_worker.start()
    next_connect = next_upload = next_missing_log = next_shadow = 0.0
    connect_backoff = 1.0
    started = time.monotonic()
    try:
        while not common.STOP_EVENT.is_set():
            now = datetime.now(common.SHANGHAI)
            if not args.force and not common.ib_collection_window(now):
                hub.close()
                if args.once:
                    return 1
                common.STOP_EVENT.wait(30.0)
                continue
            monotonic = time.monotonic()
            if not hub.is_connected() and monotonic >= next_connect:
                try:
                    hub.connect()
                    connect_backoff = 1.0
                    common.log(
                        f"XOP family IB connected client_id={args.ib_client_id} "
                        f"routes={','.join(hub.required_routes())} timeout={args.ib_timeout:.0f}s"
                    )
                except Exception as exc:
                    common.log(f"XOP family IB connect failed; retry in {connect_backoff:.0f}s: {exc}", error=True)
                    next_connect = time.monotonic() + connect_backoff
                    connect_backoff = min(MAX_BACKOFF, connect_backoff * 2.0)
            if not hub.is_connected():
                if args.once and time.monotonic() - started >= args.once_deadline:
                    return 1
                common.STOP_EVENT.wait(0.2)
                continue
            try:
                quotes = hub.poll(0.05)
            except Exception as exc:
                common.log(f"XOP family stream failed: {exc}", error=True)
                hub.close()
                next_connect = time.monotonic() + connect_backoff
                connect_backoff = min(MAX_BACKOFF, connect_backoff * 2.0)
                continue
            if (
                SMART in quotes
                and OVERNIGHT in quotes
                and time.monotonic() >= next_shadow
            ):
                recorder.record(quotes[SMART], quotes[OVERNIGHT])
                next_shadow = time.monotonic() + max(0.2, args.shadow_interval)
            if args.mode == DUAL_SHADOW:
                if args.once and SMART in quotes and OVERNIGHT in quotes:
                    return 0
                common.STOP_EVENT.wait(0.2)
                continue
            if time.monotonic() < next_upload:
                common.STOP_EVENT.wait(0.1)
                continue
            assert collector is not None
            inputs, failures = collector.build_inputs(quotes, now)
            required_symbols = {common.SYMBOL, fund513350.SYMBOL, lof162411.SYMBOL}
            input_symbol_list = [str(item.get("symbol") or "").upper() for item in inputs]
            input_symbols = set(input_symbol_list)
            duplicate_symbols = sorted(
                symbol for symbol in input_symbols if input_symbol_list.count(symbol) > 1
            )
            complete_batch = (
                len(input_symbol_list) == len(required_symbols)
                and input_symbols == required_symbols
                and not failures
                and not duplicate_symbols
            )
            if duplicate_symbols:
                common.log(
                    f"XOP family refused duplicate symbols before upload: {duplicate_symbols}",
                    error=True,
                )
                inputs = []
            if args.require_complete_batch and not complete_batch:
                if time.monotonic() >= next_missing_log:
                    common.log(
                        f"XOP family complete batch not ready symbols={sorted(input_symbols)} "
                        f"duplicates={duplicate_symbols} failures={failures}",
                        error=True,
                    )
                    next_missing_log = time.monotonic() + 10.0
                next_upload = time.monotonic() + max(0.5, args.upload_interval)
                if args.once and time.monotonic() - started >= args.once_deadline:
                    return 1
                continue
            if inputs:
                try:
                    reply = common.post_private_input_batch(
                        args.server,
                        args.token,
                        inputs,
                        args.timeout,
                        source=args.source,
                        generated_at=now,
                        origin_ip=args.origin_ip,
                        origin_tls_insecure=args.origin_tls_insecure,
                        origin_ca_file=args.origin_ca_file,
                    )
                    expected = {str(item["symbol"]).upper() for item in inputs}
                    accepted = {str(value).upper() for value in reply["accepted"]}
                    if reply["rejected"] or accepted != expected:
                        raise RuntimeError(f"partial XOP family batch acknowledgement: {reply}")
                    common.log(
                        f"XOP family batch uploaded symbols={','.join(sorted(accepted))} "
                        f"routes={args.mode}"
                    )
                    if args.once:
                        return 0
                except Exception as exc:
                    common.log(f"XOP family batch upload failed: {exc}", error=True)
            if failures and time.monotonic() >= next_missing_log:
                common.log(f"XOP family isolated source failures: {failures}", error=True)
                next_missing_log = time.monotonic() + 30.0
            next_upload = time.monotonic() + max(0.5, args.upload_interval)
            if args.once and time.monotonic() - started >= args.once_deadline:
                return 1
    finally:
        if source_worker is not None:
            source_worker.close()
        hub.close()
    return 0


def main() -> int:
    common.load_env_file(".sina-uploader.env")
    args = parser().parse_args()
    signal.signal(signal.SIGINT, common._request_stop)
    signal.signal(signal.SIGTERM, common._request_stop)
    try:
        return run(args)
    except (common.InputValidationError, common.SourceUnavailableError, RuntimeError) as exc:
        common.log(str(exc), error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

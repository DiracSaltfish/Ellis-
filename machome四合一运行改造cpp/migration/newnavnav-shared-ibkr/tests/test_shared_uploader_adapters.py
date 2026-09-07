#!/usr/bin/env python3
"""Regression tests for every uploader converted to the native quote bridge.

No test in this file opens TCP, Unix-socket, HTTP, or WebSocket connections.
The low-frequency contract-resolution path is faked independently from the
long-lived local quote path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
import types
import unittest


SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

import private_159605_valuation_uploader as basket  # noqa: E402
import private_164824_valuation_uploader as india  # noqa: E402
import private_513350_valuation_uploader as overnight  # noqa: E402
import private_china_internet_valuation_uploader as china  # noqa: E402
import private_nasdaq_valuation_uploader as futures  # noqa: E402
import private_valuation_uploader as primary  # noqa: E402


NOW = datetime(2026, 9, 4, 1, 2, 3, tzinfo=timezone.utc)


class FakeContractSubscription:
    @staticmethod
    def create(**values):
        subscription_id = values.get("subscription_id") or (
            f"DYN.{values.get('security_type')}.{values.get('symbol')}."
            f"{values.get('exchange')}.{values.get('con_id', 0)}"
        )
        return SimpleNamespace(subscription_id=subscription_id, values=values)


class FakeSingleQuoteStream:
    instances: list["FakeSingleQuoteStream"] = []

    def __init__(self, subscription, timeout):
        self.subscription, self.timeout = subscription, timeout
        self.connected = False
        self.closed = False
        self.__class__.instances.append(self)

    def connect(self):
        self.connected = True

    def is_connected(self):
        return self.connected and not self.closed

    def poll(self, _wait_seconds):
        return bridge_quote()

    def close(self):
        self.closed = True


class FakeMultiQuoteStream:
    instances: list["FakeMultiQuoteStream"] = []

    def __init__(self, subscriptions, timeout):
        self.subscriptions, self.timeout = tuple(subscriptions), timeout
        self.connected = False
        self.closed = False
        self.__class__.instances.append(self)

    def connect(self):
        self.connected = True

    def is_connected(self):
        return self.connected and not self.closed

    def poll(self, _wait_seconds):
        return {
            item.subscription_id: bridge_quote()
            for item in self.subscriptions
        }

    def close(self):
        self.closed = True


def bridge_quote():
    return SimpleNamespace(
        bid=100.0,
        ask=100.2,
        last=100.1,
        market_data_type="Live",
        observed_at=NOW,
    )


def fake_bridge_module():
    FakeSingleQuoteStream.instances.clear()
    FakeMultiQuoteStream.instances.clear()
    return types.SimpleNamespace(
        ContractSubscription=FakeContractSubscription,
        SharedSingleQuoteStream=FakeSingleQuoteStream,
        SharedMultiQuoteStream=FakeMultiQuoteStream,
    )


class SharedUploaderAdapterTest(unittest.TestCase):
    def test_primary_and_overnight_xop_use_pinned_native_routes(self) -> None:
        with mock.patch.dict(sys.modules, {"machome_ibkr_bridge_client": fake_bridge_module()}):
            smart = primary.IBQuoteStream("ignored", 7496, 1, 2.0)
            smart.connect()
            self.assertEqual(
                FakeSingleQuoteStream.instances[-1].subscription.subscription_id,
                "XOP.SMART",
            )
            self.assertEqual(
                FakeSingleQuoteStream.instances[-1].subscription.values["generic_ticks"],
                "236",
            )
            self.assertEqual(
                FakeSingleQuoteStream.instances[-1].subscription.values.get("con_id", 0),
                0,
            )
            self.assertEqual(smart.poll(0).bid, 100.0)

            night = overnight.OvernightXOPQuoteStream("ignored", 7496, 2, 2.0)
            night.connect()
            self.assertEqual(
                FakeSingleQuoteStream.instances[-1].subscription.subscription_id,
                "XOP.OVERNIGHT",
            )
            self.assertEqual(night.poll(0).ask, 100.2)

    def test_159605_batches_thirty_components_in_one_local_poll(self) -> None:
        components = tuple(
            basket.Component(
                symbol=(f"0{index:03d}" if index < 15 else f"US{index}"),
                name=f"component-{index}",
                market=("HK" if index < 15 else "US"),
                currency=("HKD" if index < 15 else "USD"),
                quantity=1.0,
            )
            for index in range(basket.EXPECTED_COMPONENT_COUNT)
        )
        with mock.patch.dict(sys.modules, {"machome_ibkr_bridge_client": fake_bridge_module()}):
            stream = basket.IBMultiQuoteStream("ignored", 7496, 3, 2.0)
            stream.connect(components)
            self.assertEqual(len(FakeMultiQuoteStream.instances), 1)
            self.assertEqual(
                len(FakeMultiQuoteStream.instances[0].subscriptions),
                basket.EXPECTED_COMPONENT_COUNT,
            )
            quotes = stream.poll(0)
            self.assertIsNotNone(quotes)
            self.assertEqual(len(quotes or ()), basket.EXPECTED_COMPONENT_COUNT)
            self.assertEqual(quotes[0].observed_at.tzinfo, basket.common.SHANGHAI)

    def test_china_hub_bridges_us_and_does_not_open_direct_tws(self) -> None:
        us = china.Component("BABA", "Alibaba", "US", "USD", 2.0)
        with mock.patch.dict(sys.modules, {"machome_ibkr_bridge_client": fake_bridge_module()}):
            hub = china.MarketHub("ignored", 7496, 4, 2.0)
            hub.sync({us.key: us})
            self.assertEqual(len(FakeMultiQuoteStream.instances), 1)
            subscription = FakeMultiQuoteStream.instances[0].subscriptions[0]
            self.assertEqual(subscription.values["exchange"], "OVERNIGHT")
            quote = hub.poll()[us.key]
            self.assertEqual((quote.bid, quote.ask), (100.0, 100.2))

    def test_inda_live_quote_never_constructs_ib_insync_client(self) -> None:
        with mock.patch.dict(sys.modules, {"machome_ibkr_bridge_client": fake_bridge_module()}):
            market = india.INDAMarket("ignored", 7496, 5, 2.0)
            with mock.patch.object(
                market, "connect", side_effect=AssertionError("direct TWS must stay unused")
            ):
                quote = market.inda_quote()
            self.assertIsNotNone(quote)
            self.assertIsNone(market.ib)
            subscription = FakeSingleQuoteStream.instances[-1].subscription
            self.assertEqual(subscription.subscription_id, "INDA.SMART")

    def test_inda_history_session_disconnects_after_bid_ask_pair(self) -> None:
        observed = datetime(2026, 9, 3, 15, 50, tzinfo=india.NEW_YORK)

        class FakeHistoryIB:
            def __init__(self):
                self.disconnect_count = 0

            def reqHistoricalData(self, _contract, **values):
                price = 99.9 if values["whatToShow"] == "BID" else 100.1
                return [SimpleNamespace(date=observed, close=price)]

            def disconnect(self):
                self.disconnect_count += 1

        direct = FakeHistoryIB()
        market = india.INDAMarket("ignored", 7496, 5, 2.0)

        def connect():
            market.ib = direct

        with mock.patch.object(market, "connect", side_effect=connect):
            quotes = market.historical_bid_ask_window(
                SimpleNamespace(localSymbol="INDA"),
                "INDA",
                observed - timedelta(minutes=1),
                observed + timedelta(minutes=1),
            )
        self.assertEqual(len(quotes), 1)
        self.assertEqual(direct.disconnect_count, 1)
        self.assertIsNone(market.ib)

    def test_nifty_resolution_failure_cannot_leak_direct_session(self) -> None:
        direct = SimpleNamespace(disconnect=mock.Mock())
        market = india.INDAMarket("ignored", 7496, 5, 2.0)
        market.ib = direct
        with mock.patch.object(
            market, "nifty_contract", side_effect=RuntimeError("resolution failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "resolution failed"):
                market.nifty_quote(NOW.astimezone(india.SHANGHAI))
        direct.disconnect.assert_called_once_with()
        self.assertIsNone(market.ib)

    def test_future_resolver_disconnects_before_native_live_stream(self) -> None:
        contract = SimpleNamespace(
            symbol="NQ",
            secType="FUT",
            exchange="CME",
            primaryExchange="",
            currency="USD",
            conId=123456,
            lastTradeDateOrContractMonth="20261218",
            multiplier="20",
            tradingClass="NQ",
            localSymbol="NQZ6",
        )

        class FakeIB:
            instances: list["FakeIB"] = []

            def __init__(self):
                self.connected = False
                self.disconnect_count = 0
                self.__class__.instances.append(self)

            def connect(self, *_args, **_kwargs):
                self.connected = True

            def isConnected(self):
                return self.connected

            def reqContractDetails(self, _search):
                return [SimpleNamespace(contract=contract)]

            def qualifyContracts(self, selected):
                return [selected]

            def disconnect(self):
                self.connected = False
                self.disconnect_count += 1

        fake_ib = types.SimpleNamespace(
            IB=FakeIB,
            Future=lambda **values: SimpleNamespace(**values),
        )
        modules = {
            "ib_insync": fake_ib,
            "machome_ibkr_bridge_client": fake_bridge_module(),
        }
        with mock.patch.dict(sys.modules, modules):
            market = futures.NQMarket("ignored", 7496, 6, 2.0)
            market.connect()
            self.assertEqual(len(FakeIB.instances), 1)
            self.assertEqual(FakeIB.instances[0].disconnect_count, 1)
            self.assertFalse(FakeIB.instances[0].connected)
            self.assertIsNone(market.ib)
            self.assertTrue(market.shared.is_connected())
            self.assertEqual(
                market.shared.subscription.values["con_id"],
                contract.conId,
            )
            self.assertEqual(market.quote().symbol, "NQ")


if __name__ == "__main__":
    unittest.main(verbosity=2)

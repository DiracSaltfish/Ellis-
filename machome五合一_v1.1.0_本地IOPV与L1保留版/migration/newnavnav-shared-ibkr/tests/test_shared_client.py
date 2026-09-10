#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest


TEST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TEST_ROOT / "scripts"))

from machome_ibkr_bridge_client import (  # noqa: E402
    BridgeError,
    BridgeQuote,
    ContractSubscription,
    SharedQuoteClient,
    PROTOCOL,
)


BRIDGE_BINARY = ""
if "--bridge-binary" in sys.argv:
    index = sys.argv.index("--bridge-binary")
    BRIDGE_BINARY = sys.argv[index + 1]
    del sys.argv[index:index + 2]


class ContractAndQuoteTest(unittest.TestCase):
    def test_identity_is_deterministic_and_contract_is_normalized(self) -> None:
        first = ContractSubscription.create(
            symbol=" aapl ", security_type="stk", exchange="smart",
            primary_exchange="nasdaq", currency="usd",
        )
        second = ContractSubscription.create(
            symbol="AAPL", security_type="STK", exchange="SMART",
            primary_exchange="NASDAQ", currency="USD",
        )
        self.assertEqual(first, second)
        self.assertTrue(first.subscription_id.startswith("DYN.STK.AAPL."))
        self.assertEqual(first.contract_payload()["symbol"], "AAPL")

    def test_quote_parser_preserves_bridge_receive_time(self) -> None:
        observed = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        quote = BridgeQuote.from_item({
            "contract": {"id": "XOP.SMART", "symbol": "XOP"},
            "sequence": 9,
            "market_data_type": 1,
            "bid": "123.45",
            "ask": "123.47",
            "last": "123.46",
            "close": None,
            "bid_size": "10",
            "ask_size": "20",
            "last_size": None,
            "received_at": observed,
            "exchange_timestamp": None,
            "age_ms": 3,
            "fresh": True,
        })
        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote.bid, 123.45)
        self.assertEqual(quote.market_data_type, "Live")
        self.assertEqual(quote.sequence, 9)

    def test_invalid_contract_and_crossed_quote_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ContractSubscription.create(
                symbol="BAD\nSYMBOL", security_type="STK", exchange="SMART", currency="USD"
            )
        with self.assertRaises(BridgeError):
            BridgeQuote.from_item({
                "contract": {"id": "BAD", "symbol": "BAD"},
                "sequence": 1,
                "market_data_type": 1,
                "bid": "11",
                "ask": "10",
                "received_at": datetime.now(timezone.utc).isoformat(),
                "exchange_timestamp": None,
                "age_ms": 1,
                "fresh": True,
            })

    def test_batch_keeps_fresh_symbol_when_another_symbol_is_stale(self) -> None:
        received_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

        def item(subscription_id: str, symbol: str, fresh: bool) -> dict:
            return {
                "contract": {"id": subscription_id, "symbol": symbol},
                "sequence": 9,
                "market_data_type": 1,
                "bid": "123.45",
                "ask": "123.47",
                "last": "123.46",
                "close": "122.00",
                "received_at": received_at,
                "exchange_timestamp": None,
                "age_ms": 3 if fresh else 120000,
                "fresh": fresh,
            }

        client = SharedQuoteClient("/unused", timeout=1)
        client._request = lambda payload: {
            "bridge_ready": True,
            "items": [
                item("XOP.SMART", "XOP", True),
                item("INDA.SMART", "INDA", False),
            ],
        }

        values = client.quotes(("XOP.SMART", "INDA.SMART"))

        self.assertIsNotNone(values["XOP.SMART"])
        self.assertIsNone(values["INDA.SMART"])


@unittest.skipUnless(BRIDGE_BINARY, "native bridge binary not supplied")
class NativeLeaseIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="machome-ibkr-lease-")
        root = Path(self.temp.name)
        self.socket_path = root / "bridge.sock"
        config_path = root / "config.json"
        config_path.write_text(json.dumps({
            "schema_version": 1,
            "socket_path": str(self.socket_path),
            "health_file": str(root / "health.json"),
            "tws": {
                "host": "127.0.0.1",
                "port": 1,
                "client_id": 1_909_041,
                "market_data_type": 1,
                "connect_timeout_ms": 1000,
                "reconnect_minimum_ms": 1000,
                "reconnect_maximum_ms": 1000,
                "heartbeat_interval_ms": 1000,
                "heartbeat_stale_ms": 3000,
            },
            "connection_schedule": {
                "enabled": False,
                "timezone": "Asia/Shanghai",
                "weekdays": [1, 2, 3, 4, 5],
                "start_time": "09:00",
                "stop_time": "15:06",
            },
            "limits": {
                "maximum_clients": 8,
                "maximum_request_bytes": 65536,
                "maximum_subscriptions": 8,
            },
            "subscriptions": [{
                "id": "XOP.SMART",
                "con_id": 413951498,
                "symbol": "XOP",
                "security_type": "STK",
                "exchange": "SMART",
                "primary_exchange": "ARCA",
                "currency": "USD",
                "generic_ticks": "236",
            }],
        }), encoding="utf-8")
        self.process = subprocess.Popen(
            [BRIDGE_BINARY, "--config", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while not self.socket_path.exists() and self.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self.socket_path.exists():
            stdout, stderr = self.process.communicate(timeout=2)
            self.fail(f"bridge did not create socket: {stdout}\n{stderr}")

    def tearDown(self) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()
        self.temp.cleanup()

    def test_two_clients_share_one_dynamic_subscription_lease(self) -> None:
        contract = ContractSubscription.create(
            symbol="AAPL", security_type="STK", exchange="SMART",
            primary_exchange="NASDAQ", currency="USD",
        )
        first = SharedQuoteClient(str(self.socket_path), timeout=2)
        second = SharedQuoteClient(str(self.socket_path), timeout=2)
        observer = SharedQuoteClient(str(self.socket_path), timeout=2)
        first.connect()
        second.connect()
        self.assertTrue(first.subscribe(contract))
        self.assertFalse(second.subscribe(contract))
        self.assertEqual(second.status()["subscription_count"], 2)

        different = ContractSubscription.create(
            subscription_id=contract.subscription_id,
            symbol="MSFT", security_type="STK", exchange="SMART",
            primary_exchange="NASDAQ", currency="USD",
        )
        with self.assertRaisesRegex(BridgeError, "subscription_conflict"):
            first.subscribe(different)

        first.close()
        self.assertEqual(second.status()["subscription_count"], 2)
        second.close()
        observer.connect()
        deadline = time.monotonic() + 2
        count = observer.status()["subscription_count"]
        while count != 1 and time.monotonic() < deadline:
            time.sleep(0.02)
            count = observer.status()["subscription_count"]
        self.assertEqual(count, 1)
        observer.close()

    def test_slow_client_does_not_delay_another_client(self) -> None:
        slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        slow.settimeout(2)
        slow.connect(str(self.socket_path))
        slow.sendall((json.dumps({
            "request_id": "hello",
            "type": "hello",
            "protocol": PROTOCOL,
        }) + "\n").encode())
        self.assertIn(b'"ok":true', slow.recv(65536))
        burst = b"".join(
            (json.dumps({"request_id": f"slow-{index}", "type": "status"}) + "\n").encode()
            for index in range(32)
        )
        slow.sendall(burst)

        fast = SharedQuoteClient(str(self.socket_path), timeout=2)
        started = time.monotonic()
        fast.connect()
        status = fast.status()
        elapsed = time.monotonic() - started
        self.assertIn(status["state"], {"connecting", "backoff", "handshaking", "ready"})
        self.assertLess(elapsed, 0.75)
        fast.close()
        slow.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

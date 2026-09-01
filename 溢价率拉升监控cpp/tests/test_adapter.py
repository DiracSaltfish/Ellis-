from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapter"))

from tgw_adapter import Adapter, strip_inline_comment
from proto_wire import BridgeFrame


class FakeSdk:
    def __init__(self) -> None:
        self.unsubscribed: list[object] = []
        self.subscribed_batches: list[list[object]] = []

        class Item:
            def __init__(self) -> None:
                self.code = ""

            def set_code(self, code: str) -> "Item":
                self.code = code
                return self

        self.SubscribeItem = Item
        self.MarketType = type("MarketType", (), {"kSSE": 101, "kSZSE": 102})
        self.SubscribeDataType = type("SubscribeDataType", (), {"kSnapshot": 1, "kHKTSnapshot": 12})

    def UnSubscribe(self, item: object) -> int:
        self.unsubscribed.append(item)
        return 0

    def Subscribe(self, items: list[object]) -> int:
        self.subscribed_batches.append(items)
        return 0


class AdapterTests(unittest.TestCase):
    def test_inline_comment_is_removed_from_fixed_width_config_field(self) -> None:
        self.assertEqual(strip_inline_comment("127.0.0.1  # preferred endpoint"), "127.0.0.1")

    def test_quote_schedule_toggle_unsubscribes_and_resubscribes_same_set(self) -> None:
        adapter = Adapter(Path("/tmp/not-used.sock"), Path("/tmp/not-used.json"), False, None)
        adapter.desired = {"159866.SZ"}
        adapter.sdk = FakeSdk()
        token = object()
        adapter.subscriptions = {"159866.SZ": token}
        adapter._status = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

        adapter.quotes_desired = False
        adapter.apply_desired({"159866.SZ"})
        self.assertEqual(adapter.sdk.unsubscribed, [])
        self.assertTrue(adapter._reconcile_live())
        self.assertEqual(adapter.sdk.unsubscribed, [token])
        self.assertEqual(adapter.subscriptions, {})

        adapter.quotes_desired = True
        adapter.apply_desired({"159866.SZ"})
        self.assertTrue(adapter._reconcile_live())
        self.assertEqual([item.code for item in adapter.sdk.subscribed_batches[0]], ["159866"])
        self.assertEqual(set(adapter.subscriptions), {"159866.SZ"})

    def test_subscribe_is_batched_and_single_remove_uses_single_item(self) -> None:
        adapter = Adapter(Path("/tmp/not-used.sock"), Path("/tmp/not-used.json"), False, None)
        adapter.sdk = FakeSdk()
        adapter.quotes_desired = True
        adapter.desired = {f"{index:06d}.SZ" for index in range(45)}
        adapter._status = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        self.assertTrue(adapter._reconcile_live())
        self.assertEqual([len(batch) for batch in adapter.sdk.subscribed_batches], [20, 20, 5])
        removed = sorted(adapter.subscriptions)[0]
        token = adapter.subscriptions[removed]
        adapter.apply_desired(set(adapter.desired) - {removed})
        self.assertTrue(adapter._reconcile_live())
        self.assertIs(adapter.sdk.unsubscribed[-1], token)

    def test_hkt_uses_exact_five_digit_deep_connect_route(self) -> None:
        adapter = Adapter(Path("/tmp/not-used.sock"), Path("/tmp/not-used.json"), False, None)
        adapter.sdk = FakeSdk()
        item = adapter._make_subscribe_item("02800.HK")
        self.assertEqual(item.code, "02800")
        self.assertEqual(item.market, 102)
        self.assertEqual(item.flag, 12)
        self.assertEqual(item.category_type, 0)
        with self.assertRaises(ValueError):
            adapter._make_subscribe_item("2800.HK")
        with self.assertRaises(ValueError):
            adapter._make_subscribe_item("002800.HK")

    def test_rejected_hkt_is_isolated_and_backed_off_without_blocking_domestic(self) -> None:
        class SelectiveSdk(FakeSdk):
            def __init__(self) -> None:
                super().__init__()
                self.reject_hkt = True

            def Subscribe(self, items: list[object]) -> int:
                self.subscribed_batches.append(items)
                if self.reject_hkt and any(getattr(item, "flag", 0) == 12 for item in items):
                    return -76
                return 0

        adapter = Adapter(Path("/tmp/not-used.sock"), Path("/tmp/not-used.json"), False, None)
        adapter.sdk = SelectiveSdk()
        adapter.quotes_desired = True
        adapter.desired = {"159866.SZ", "02800.HK"}
        statuses: list[tuple[str, dict[str, object]]] = []
        adapter._status = lambda message, detail=None: statuses.append((message, detail or {}))  # type: ignore[method-assign]

        self.assertTrue(adapter._reconcile_live())
        self.assertIn("159866.SZ", adapter.subscriptions)
        self.assertNotIn("02800.HK", adapter.subscriptions)
        self.assertIn("02800.HK", adapter.symbol_retries)
        first_calls = len(adapter.sdk.subscribed_batches)
        self.assertTrue(adapter._reconcile_live())
        self.assertEqual(len(adapter.sdk.subscribed_batches), first_calls)
        self.assertTrue(any(message == "subscribe_symbol_backoff" and detail["retry_sec"] == 1.0
                            for message, detail in statuses))

        _when, delay, failures, last = adapter.symbol_retries["02800.HK"]
        adapter.symbol_retries["02800.HK"] = (0.0, delay, failures, last)
        adapter.sdk.reject_hkt = False
        self.assertTrue(adapter._reconcile_live())
        self.assertIn("02800.HK", adapter.subscriptions)
        self.assertNotIn("02800.HK", adapter.symbol_retries)

    def test_systemic_rejection_has_bounded_sdk_calls_and_defers_every_symbol(self) -> None:
        class RejectAllSdk(FakeSdk):
            def Subscribe(self, items: list[object]) -> int:
                self.subscribed_batches.append(items)
                return -1000

        adapter = Adapter(Path("/tmp/not-used.sock"), Path("/tmp/not-used.json"), False, None)
        adapter.sdk = RejectAllSdk()
        adapter.quotes_desired = True
        adapter.desired = {f"{index:06d}.SZ" for index in range(100)}
        adapter._status = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
        self.assertTrue(adapter._reconcile_live())
        self.assertLessEqual(len(adapter.sdk.subscribed_batches), 64)
        self.assertEqual(set(adapter.symbol_retries), adapter.desired)
        calls = len(adapter.sdk.subscribed_batches)
        self.assertTrue(adapter._reconcile_live())
        self.assertEqual(len(adapter.sdk.subscribed_batches), calls)

    def test_market_frame_from_previous_core_bridge_epoch_is_not_sent(self) -> None:
        class RecordingSocket:
            def __init__(self) -> None:
                self.payloads: list[bytes] = []

            def sendall(self, payload: bytes) -> None:
                self.payloads.append(payload)

        adapter = Adapter(Path("/tmp/not-used.sock"), Path("/tmp/not-used.json"), False, None)
        socket = RecordingSocket()
        adapter.socket = socket  # type: ignore[assignment]
        adapter.bridge_epoch = 2
        frame = BridgeFrame(kind=1, payload_json=b"{}")
        self.assertFalse(adapter._send(frame, expected_bridge_epoch=1))
        self.assertEqual(socket.payloads, [])
        self.assertEqual(adapter.sequence, 0)


if __name__ == "__main__":
    unittest.main()

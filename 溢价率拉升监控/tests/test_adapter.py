from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapter"))

from tgw_adapter import Adapter, strip_inline_comment


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
        self.SubscribeDataType = type("SubscribeDataType", (), {"kSnapshot": 1})

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


if __name__ == "__main__":
    unittest.main()

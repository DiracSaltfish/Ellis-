#!/usr/bin/env python3
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import sina_ws_uploader as ws  # noqa: E402


class Args:
    source = "home-mac"

    def __init__(self, path: str):
        self.valuation_position_state_file = path


class ValuationPositionOverrideTests(unittest.TestCase):
    def test_apply_updates_persists_state_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "valuation_position_overrides.json")
            applied = ws.apply_valuation_position_updates(
                path,
                [{"symbol": "sh501018", "ratio": 0.8125}],
                "debug_ws",
            )

            self.assertEqual(len(applied), 1)
            self.assertEqual(applied[0]["symbol"], "SH501018")
            self.assertAlmostEqual(applied[0]["ratio"], 0.8125)

            rows = ws.valuation_position_state_rows(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "SH501018")
            self.assertAlmostEqual(rows[0]["ratio"], 0.8125)

    def test_handle_state_request_returns_current_rows(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "valuation_position_overrides.json")
            ws.apply_valuation_position_updates(path, [{"symbol": "SZ159518", "ratio": 0.995}], "debug_ws")
            args = Args(path)
            sent = []
            original = ws.send_json
            try:
                ws.send_json = lambda _conn, payload: sent.append(payload)
                ws.handle_valuation_position_state_request(object(), args, {"request_id": "req-1"})
            finally:
                ws.send_json = original

            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["type"], "valuation_position_state")
            self.assertEqual(sent[0]["request_id"], "req-1")
            self.assertEqual(sent[0]["valuation_position_states"][0]["symbol"], "SZ159518")

    def test_handle_set_request_rejects_empty_updates(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "valuation_position_overrides.json")
            args = Args(path)
            sent = []
            original = ws.send_json
            try:
                ws.send_json = lambda _conn, payload: sent.append(payload)
                ws.handle_valuation_position_set_request(object(), args, {"request_id": "req-2", "valuation_position_updates": []})
            finally:
                ws.send_json = original

            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["type"], "valuation_position_set_result")
            self.assertIn("required", sent[0]["error"])
            self.assertEqual(ws.valuation_position_state_rows(path), [])


if __name__ == "__main__":
    unittest.main()

"""Regression tests for codelist capture redaction controls."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ssl_write_capture_sanitizer",
    ROOT / "tools" / "oracle" / "analyze_ssl_write_capture.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SecuritiesInfoCaptureSanitizerTests(unittest.TestCase):
    def test_multi_item_security_keeps_only_safe_shape(self):
        control = MODULE.securities_info_security_control(
            "510300|101,159919|102"
        )
        self.assertEqual(control, {
            "format": "multi-code|market",
            "container": "str",
            "item_count": 2,
            "markets_in_order": [101, 102],
            "empty_code_flags": [False, False],
            "separator": ",",
        })
        serialized = json.dumps(control, sort_keys=True)
        self.assertNotIn("510300", serialized)
        self.assertNotIn("159919", serialized)

    def test_single_empty_code_and_invalid_values_are_explicit(self):
        self.assertEqual(
            MODULE.securities_info_security_control("|102")["format"],
            "empty-code|market",
        )
        self.assertEqual(
            MODULE.securities_info_security_control({"unsafe": "value"})["format"],
            "invalid",
        )


if __name__ == "__main__":
    unittest.main()

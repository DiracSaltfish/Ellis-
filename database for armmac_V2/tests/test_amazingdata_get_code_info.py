"""Offline contract tests for experimental ``BaseData.get_code_info``.

All rows are synthetic.  The tests protect the official Linux-observed output
shape while keeping the unverified macOS full-market transport explicitly
blocked.
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

try:
    import pandas as pd
except ImportError:  # pragma: no cover - exercised by the project no-pandas suite
    pd = None


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))
sys.path.insert(0, str(ROOT / "experimental" / "amazingdata_compat"))

from amazingdata_re.base_data import (  # noqa: E402
    BaseData,
    _CODE_INFO_COLUMNS,
    _normalise_extra_etf_frames,
)


def _raw_frame(rows):
    if pd is None:  # pragma: no cover - guarded by the class skip below
        raise RuntimeError("pandas is unavailable")
    return pd.DataFrame(rows, columns=[
        "security_code",
        "symbol",
        "security_status",
        "pre_close_price",
        "high_limited",
        "low_limited",
        "price_tick",
        "list_day",
    ])


class AmazingDataCodeInfoBoundaryTests(unittest.TestCase):
    def test_signature_preserves_official_default_but_only_extra_etf_is_in_scope(self):
        signature = inspect.signature(BaseData.get_code_info)
        self.assertEqual(list(signature.parameters), ["self", "security_type"])
        self.assertEqual(signature.parameters["security_type"].default, "EXTRA_STOCK_A")

        with self.assertRaisesRegex(NotImplementedError, "three-market"):
            BaseData().get_code_info("EXTRA_ETF")

    def test_unknown_or_non_string_security_type_fails_explicitly(self):
        base = BaseData()
        with self.assertRaisesRegex(NotImplementedError, "only security_type='EXTRA_ETF'"):
            base.get_code_info("EXTRA_STOCK_A")
        with self.assertRaisesRegex(NotImplementedError, "only security_type='EXTRA_ETF'"):
            base.get_code_info("UNKNOWN")
        with self.assertRaisesRegex(TypeError, "must be a string"):
            base.get_code_info(None)  # type: ignore[arg-type]

@unittest.skipUnless(pd is not None, "pandas is required for DataFrame contract tests")
class AmazingDataCodeInfoNormalisationTests(unittest.TestCase):
    def test_normalisation_matches_official_column_index_dtype_and_scaling_contract(self):
        result = _normalise_extra_etf_frames([
            (102, _raw_frame([
                ("159001", "SYN_SZ", "", 12_340_000, 13_000_000, 11_000_000, 10_000, 20240102),
                ("000001", "SYN_STOCK", "", 1, 2, 0, 1, 20240102),
            ])),
            (101, _raw_frame([
                ("510001", "SYN_SH", "", 23_450_000, 24_000_000, 22_000_000, 10_000, 20240103),
                ("600000", "SYN_STOCK", "", 1, 2, 0, 1, 20240103),
            ])),
            (2, _raw_frame([
                ("830001", "SYN_BJ", "", 1, 2, 0, 1, 20240104),
            ])),
        ])

        self.assertEqual(tuple(result.columns), _CODE_INFO_COLUMNS)
        self.assertEqual(result.index.name, "code_market")
        self.assertIn(str(result.index.dtype), {"object", "str"})
        self.assertTrue(result.index.is_unique)
        self.assertEqual(list(result.index), ["159001.SZ", "510001.SH"])
        self.assertEqual(
            {column: str(result[column].dtype) for column in (
                "pre_close", "high_limited", "low_limited", "price_tick"
            )},
            {
                "pre_close": "float64",
                "high_limited": "float64",
                "low_limited": "float64",
                "price_tick": "float64",
            },
        )
        self.assertEqual(str(result["list_day"].dtype), "int64")
        self.assertTrue((result["pre_close"] * 1_000_000).map(float.is_integer).all())
        self.assertTrue((result["high_limited"] * 1_000_000).map(float.is_integer).all())
        self.assertTrue((result["low_limited"] * 1_000_000).map(float.is_integer).all())
        self.assertTrue((result["price_tick"] * 1_000_000).map(float.is_integer).all())
        self.assertFalse(result.isna().any().any())

    def test_missing_or_invalid_raw_result_never_becomes_empty_success(self):
        with self.assertRaisesRegex(RuntimeError, "lacks required"):
            _normalise_extra_etf_frames([(101, pd.DataFrame({"security_code": []}))])
        with self.assertRaisesRegex(TypeError, "must be a pandas DataFrame"):
            _normalise_extra_etf_frames([(101, [])])
        with self.assertRaisesRegex(NotImplementedError, "no verified market"):
            _normalise_extra_etf_frames([(103, _raw_frame([]))])


if __name__ == "__main__":
    unittest.main()

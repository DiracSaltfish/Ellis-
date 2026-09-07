#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import private_513350_valuation_uploader as uploader  # noqa: E402
import private_valuation_uploader as common  # noqa: E402


def pcf_response() -> bytes:
    return json.dumps({
        "code": 0,
        "data": {
            "tradingDay": "2026-07-10",
            "ptradeDate": "2026-07-09",
            "minShdy": "1000000",
            "minYgcash": "-730.81",
            "minShnav": "1105079.94",
            "recordNumber": "51",
            "ifSh": "允许申购、赎回",
        },
    }).encode("utf-8")


class Private513350UploaderTests(unittest.TestCase):
    def test_regular_session_reference_excludes_opening_minute(self) -> None:
        class Bar:
            def __init__(self, timestamp: datetime, close: float) -> None:
                self.date = timestamp
                self.close = close

        session = date(2026, 7, 9)
        bars = [
            Bar(datetime(2026, 7, 9, 9, 30, tzinfo=uploader.NEW_YORK), 150.00),
            Bar(datetime(2026, 7, 9, 9, 31, tzinfo=uploader.NEW_YORK), 150.20),
            Bar(datetime(2026, 7, 9, 15, 59, tzinfo=uploader.NEW_YORK), 152.98),
            Bar(datetime(2026, 7, 9, 16, 0, tzinfo=uploader.NEW_YORK), 153.00),
        ]
        self.assertEqual(
            uploader.regular_session_prices(bars),
            {session: {9 * 60 + 31: 150.20, 15 * 60 + 59: 152.98}},
        )

    def test_parser_uses_official_pcf_nav_and_estimated_cash(self) -> None:
        day = date(2026, 7, 10)
        pcf = uploader.parse_pcf_response(pcf_response(), uploader.pcf_url_for_day(day), day)
        self.assertEqual(pcf.trading_day, day)
        self.assertEqual(pcf.estimate_cash_component_cny, -730.81)
        self.assertEqual(pcf.nav_per_cu, 1105079.94)
        self.assertEqual(pcf.component_count, 51)
        self.assertEqual(pcf.redemption, "Y")

    def test_calibration_uses_pcf_stock_value_and_midpoint_once(self) -> None:
        day = date(2026, 7, 10)
        pcf = uploader.parse_pcf_response(pcf_response(), uploader.pcf_url_for_day(day), day)
        now = datetime(2026, 7, 10, 10, 0, tzinfo=common.SHANGHAI)
        fx = common.CFETSQuote(6.8043, day, "10:00", now)
        reference = uploader.XOPRegularSessionReference(date(2026, 7, 9), 152.90, 153.06, now)
        calibration = uploader.calibrate(pcf, fx, reference, now)
        self.assertEqual(calibration.xop_equivalent_shares, uploader.FIXED_XOP_EQUIVALENT_SHARES)
        self.assertAlmostEqual(calibration.xop_mid, 152.98)

    def test_persisted_calibration_only_reloads_for_same_pcf(self) -> None:
        day = date(2026, 7, 10)
        pcf = uploader.parse_pcf_response(pcf_response(), uploader.pcf_url_for_day(day), day)
        now = datetime(2026, 7, 10, 10, 0, tzinfo=common.SHANGHAI)
        calibration = uploader.DailyCalibration(day, pcf.sha256, uploader.FIXED_XOP_EQUIVALENT_SHARES, date(2026, 7, 9), 152.98, 6.8043, now)
        with tempfile.TemporaryDirectory() as root:
            uploader.save_calibration(root, calibration)
            self.assertEqual(uploader.load_calibration(root, pcf), calibration)
            changed = uploader.PCFInput(**{**pcf.__dict__, "sha256": "a" * 64})
            self.assertIsNone(uploader.load_calibration(root, changed))

    def test_payload_matches_private_backend_contract(self) -> None:
        day = date(2026, 7, 10)
        pcf = uploader.parse_pcf_response(pcf_response(), uploader.pcf_url_for_day(day), day)
        now = datetime(2026, 7, 10, 10, 0, tzinfo=common.SHANGHAI)
        fx = common.CFETSQuote(6.8043, day, "10:00", now)
        ib = common.IBQuote(152.90, 153.06, 152.98, "Live", now)
        reference = uploader.XOPRegularSessionReference(date(2026, 7, 9), 152.90, 153.06, now)
        calibration = uploader.calibrate(pcf, fx, reference, now)
        payload = uploader.build_private_payload(pcf, fx, ib, calibration, generated_at=now)
        self.assertEqual(payload["symbol"], "SH513350")
        self.assertEqual(payload["model_version"], uploader.MODEL_VERSION)
        self.assertGreater(payload["pcf"]["xop_equivalent_shares"], 0)
        self.assertEqual(payload["pcf"]["trading_day"], "2026-07-10")


if __name__ == "__main__":
    unittest.main()

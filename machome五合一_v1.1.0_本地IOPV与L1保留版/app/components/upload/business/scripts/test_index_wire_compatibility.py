import copy
import unittest
from datetime import datetime
import private_nasdaq_valuation_uploader as uploader


class WireCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.fromisoformat("2026-09-07T11:00:00+08:00")
        self.values = [{"symbol": "SH513100", "fx": {
            "source": uploader.common.CFETS_SPOT_SOURCE, "trading_day": "2026-09-07",
            "pair": "USD/CNY", "rate": 6.85, "quote_time": "11:00:00",
            "fetched_at": self.now.isoformat(), "source_observed_at": self.now.isoformat()}}]

    def test_legacy_preserves_economic_fields_and_input(self):
        original = copy.deepcopy(self.values)
        result = uploader.wire_values(self.values, self.now, "legacy_realtime")
        self.assertEqual(self.values, original)
        expected = copy.deepcopy(original)
        del expected[0]["fx"]["source_observed_at"]
        self.assertEqual(result, expected)

    def test_current_preserves_metadata(self):
        self.assertEqual(uploader.wire_values(self.values, self.now, "current"), self.values)

    def test_legacy_refuses_fallback_or_old_day(self):
        for key, value in (("source", uploader.common.CFETS_PREOPEN_FALLBACK_SOURCE),
                           ("trading_day", "2026-09-04"), ("fallback_reason", "not published")):
            values = copy.deepcopy(self.values)
            values[0]["fx"][key] = value
            with self.assertRaises(uploader.SourceUnavailableError):
                uploader.wire_values(values, self.now, "legacy_realtime")

    def test_unknown_mode_refuses_upload(self):
        with self.assertRaises(uploader.SourceUnavailableError):
            uploader.wire_values(self.values, self.now, "typo")


if __name__ == "__main__":
    unittest.main()

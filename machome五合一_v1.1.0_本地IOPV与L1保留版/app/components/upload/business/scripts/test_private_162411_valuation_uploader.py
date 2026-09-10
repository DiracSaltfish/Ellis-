from __future__ import annotations

import copy
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_162411_valuation_uploader as uploader
import private_nasdaq_valuation_uploader as safe_common
import private_valuation_uploader as common


SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")


def public_snapshot() -> dict:
    return {
        "symbol": "SZ162411",
        "estimate": {
            "model_version": "v1.weighted_anchor.xop",
            "reference_symbol": "XOP",
            "effective_ratio": 0.955,
        },
        "valuation_inputs": [
            {
                "role": "净值基准",
                "symbol": "SZ162411",
                "base_date": "2026-08-07",
                "base_price": 0.8804,
                "base_source": "eastmoney",
            },
            {
                "role": "连续价格尺度",
                "symbol": "XOP",
                "base_date": "2026-08-07",
                "base_price": 166.41,
                "base_source": "sina_us",
            },
            {
                "role": "估值锚点",
                "symbol": "XOP",
                "name": "美国收盘",
                "base_date": "2026-08-07",
                "base_price": 166.41,
                "base_source": "sina_us",
                "target_at": "2026-08-07T20:00:00Z",
                "observed_at": "2026-08-07T20:00:02Z",
                "capture_status": "stored_probe_window",
            },
        ],
        "as_of": "2026-08-11T13:10:00.123456789+08:00",
    }


class PublicSeedTest(unittest.TestCase):
    def test_parse_timestamp_accepts_go_rfc3339_nano(self) -> None:
        parsed = uploader.parse_timestamp(
            "2026-08-11T13:10:00.123456789+08:00", "go timestamp"
        )
        self.assertEqual(parsed.microsecond, 123456)

    def test_parses_canonical_close_anchor_and_dst(self) -> None:
        seed = uploader.parse_public_seed(
            public_snapshot(), datetime(2026, 8, 11, 13, 11, tzinfo=SHANGHAI)
        )

        self.assertEqual(seed.base_nav_date, date(2026, 8, 7))
        self.assertEqual(seed.base_nav, 0.8804)
        self.assertEqual(seed.base_reference.price, 166.41)
        self.assertEqual(seed.base_reference.target_at.astimezone(uploader.NEW_YORK).hour, 16)
        self.assertEqual(
            seed.base_reference.target_at.astimezone(UTC).isoformat(),
            "2026-08-07T20:00:00+00:00",
        )
        self.assertEqual(seed.ratio_source, "weighted_anchor")

    def test_rejects_noncanonical_model_or_mismatched_anchor_day(self) -> None:
        wrong_model = public_snapshot()
        wrong_model["estimate"]["model_version"] = "something-else"
        with self.assertRaisesRegex(uploader.SourceUnavailableError, "model_version"):
            uploader.parse_public_seed(wrong_model, datetime.now(SHANGHAI))

        wrong_day = public_snapshot()
        wrong_day["valuation_inputs"][2]["base_date"] = "2026-08-06"
        with self.assertRaisesRegex(uploader.SourceUnavailableError, "anchor date"):
            uploader.parse_public_seed(wrong_day, datetime.now(SHANGHAI))

        missing_audit = public_snapshot()
        del missing_audit["valuation_inputs"][2]["observed_at"]
        with self.assertRaisesRegex(uploader.SourceUnavailableError, "observed_at"):
            uploader.parse_public_seed(missing_audit, datetime.now(SHANGHAI))

    def test_preserves_public_anchor_event_audit_when_exposed(self) -> None:
        payload = public_snapshot()
        anchor = payload["valuation_inputs"][2]
        anchor["target_at"] = "2026-08-07T20:00:00Z"
        anchor["observed_at"] = "2026-08-07T19:59:57Z"
        anchor["capture_status"] = "exact_close"
        seed = uploader.parse_public_seed(payload, datetime.now(SHANGHAI))
        self.assertEqual(
            seed.base_reference.observed_at.astimezone(UTC).isoformat(),
            "2026-08-07T19:59:57+00:00",
        )
        self.assertEqual(seed.base_reference.capture_status, "exact_close")

    def test_accepts_audited_us_holiday_close_carry_forward(self) -> None:
        payload = public_snapshot()
        payload["valuation_inputs"][0]["base_date"] = "2026-07-03"
        anchor = payload["valuation_inputs"][2]
        anchor["base_date"] = "2026-07-03"
        anchor["target_at"] = "2026-07-02T20:00:00Z"
        anchor["observed_at"] = "2026-07-02T19:59:58Z"
        anchor["capture_status"] = "stored_probe_window"
        seed = uploader.parse_public_seed(payload, datetime.now(SHANGHAI))
        self.assertEqual(seed.base_nav_date, date(2026, 7, 3))
        self.assertEqual(seed.base_reference.anchor_day, date(2026, 7, 2))

    def test_rejects_anchor_carry_older_than_seven_days(self) -> None:
        payload = public_snapshot()
        anchor = payload["valuation_inputs"][2]
        anchor["target_at"] = "2026-07-30T20:00:00Z"
        anchor["observed_at"] = "2026-07-30T20:00:00Z"
        with self.assertRaisesRegex(uploader.SourceUnavailableError, "prior seven days"):
            uploader.parse_public_seed(payload, datetime.now(SHANGHAI))

    def test_rejects_observation_outside_close_window(self) -> None:
        payload = public_snapshot()
        payload["valuation_inputs"][2]["observed_at"] = "2026-08-07T19:56:59Z"
        with self.assertRaisesRegex(uploader.SourceUnavailableError, "within 180 seconds"):
            uploader.parse_public_seed(payload, datetime.now(SHANGHAI))

    def test_nondefault_effective_ratio_without_override_audit_fails_closed(self) -> None:
        payload = public_snapshot()
        payload["estimate"]["effective_ratio"] = 0.975
        with self.assertRaisesRegex(uploader.SourceUnavailableError, "override audit"):
            uploader.parse_public_seed(payload, datetime.now(SHANGHAI))


class PayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.generated_at = datetime(2026, 8, 11, 13, 10, tzinfo=SHANGHAI)
        self.seed = uploader.parse_public_seed(public_snapshot(), self.generated_at)
        self.base_fx = safe_common.CentralParity(
            rate=6.7904,
            trading_day=date(2026, 8, 7),
            fetched_at=self.generated_at,
        )
        self.current_fx = safe_common.CentralParity(
            rate=6.79, trading_day=date(2026, 8, 11), fetched_at=self.generated_at,
        )
        self.quote = common.IBQuote(
            bid=174.19,
            ask=174.64,
            last=174.58,
            market_data_type="Live",
            observed_at=self.generated_at,
        )

    def test_payload_is_independent_lof_input(self) -> None:
        payload = uploader.build_payload(
            self.seed, self.base_fx, self.current_fx, self.quote, self.generated_at
        )

        self.assertEqual(payload["model_version"], uploader.MODEL_VERSION)
        self.assertEqual(payload["valuation_kind"], "lof_weighted_anchor")
        self.assertNotIn("pcf", payload)
        self.assertNotIn("fx", payload)
        self.assertEqual(payload["lof"]["base_reference"]["price_basis"], "regular_session_close")
        self.assertEqual(payload["lof"]["base_reference"]["price"], 166.41)
        self.assertEqual(payload["lof"]["base_fx"]["trading_day"], "2026-08-07")
        self.assertEqual(payload["lof"]["current_fx"]["trading_day"], "2026-08-11")
        self.assertEqual(payload["lof"]["current_fx"]["source"], "SAFE_CENTRAL_PARITY")
        self.assertEqual(payload["ib"]["bid"], 174.19)
        self.assertEqual(payload["ib"]["ask"], 174.64)
        self.assertEqual(payload["ib"]["quote_session"], "us_smart_live")

    def test_last_parity_reproduces_public_formula_and_bid_ask_envelope(self) -> None:
        ratio = self.seed.effective_ratio
        static = 1 - ratio
        fx_ratio = self.current_fx.rate / self.base_fx.rate

        def nav(xop: float) -> float:
            return self.seed.base_nav * (
                static + ratio * (xop / self.seed.base_reference.price) * fx_ratio
            )

        bid_nav = nav(self.quote.bid)
        last_nav = nav(self.quote.last or 0)
        ask_nav = nav(self.quote.ask)
        self.assertAlmostEqual(last_nav, 0.9216267436, places=10)
        self.assertLessEqual(bid_nav, last_nav)
        self.assertLessEqual(last_nav, ask_nav)

    def test_rejects_nonexact_current_safe_day(self) -> None:
        stale = copy.copy(self.current_fx)
        stale = safe_common.CentralParity(
            rate=stale.rate, trading_day=date(2026, 8, 10), fetched_at=stale.fetched_at,
        )
        with self.assertRaisesRegex(uploader.SourceUnavailableError, "exact"):
            uploader.build_payload(
                self.seed, self.base_fx, stale, self.quote, self.generated_at
            )


if __name__ == "__main__":
    unittest.main()

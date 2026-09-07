#!/usr/bin/env python3
"""Focused unit tests for the 164824 redemption-arbitrage backtest."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import backtest_private_164824_redemption_arbitrage as backtest


def quote(value: float) -> backtest.Quote:
    return backtest.Quote(value, value, "2026-08-03T15:50:00-04:00")


class RedemptionArbitrageBacktestTest(unittest.TestCase):
    def test_timing_matches_monday_buy_thursday_redeem_next_friday_available(self) -> None:
        nav_days = [date(2026, 8, 3 + offset) for offset in (0, 1, 2, 3, 4, 7, 8, 9, 10, 11)]
        buy_day = date(2026, 8, 3)
        self.assertEqual(backtest.get_official_redemption_day(buy_day, nav_days, 3), date(2026, 8, 6))
        self.assertEqual(backtest.get_official_redemption_day(buy_day, nav_days, 9), date(2026, 8, 14))

    def test_unchanged_market_and_fx_preserve_base_nav(self) -> None:
        estimate = backtest.estimate_nav(
            base_nav=1.25,
            base_fx=6.8,
            target_fx=6.8,
            base_inda_close=quote(50.0),
            bridge_inda=quote(50.0),
            bridge_nifty=quote(24_000.0),
            current_nifty=quote(24_000.0),
        )
        self.assertAlmostEqual(estimate, 1.25)

    def test_actual_strategy_fee_is_the_default(self) -> None:
        self.assertAlmostEqual(backtest.parser().parse_args([]).redemption_fee, 0.00464)

    def test_summary_reports_cash_and_hedge_overlap(self) -> None:
        common = {
            "total_pnl_rmb": 100.0,
            "fund_cost_rmb": 100_000.0,
            "actual_discount_vs_T_nav": 0.012,
            "estimated_discount": 0.015,
            "nifty_entry_notional_usd": 50_000.0,
        }
        summary = backtest.summarize([
            {**common, "entry_day": "2026-08-03", "redemption_day": "2026-08-06", "cash_available_day": "2026-08-14"},
            {**common, "entry_day": "2026-08-04", "redemption_day": "2026-08-07", "cash_available_day": "2026-08-17"},
        ])
        self.assertEqual(summary["max_concurrent_cash_tranches"], 2)
        self.assertEqual(summary["max_concurrent_hedge_tranches"], 2)
        self.assertAlmostEqual(summary["max_locked_fund_cash_rmb"], 200_000.0)
        self.assertAlmostEqual(summary["max_active_hedge_notional_usd"], 100_000.0)

    def test_roll_basis_aligns_new_contract_to_old_bridge_scale(self) -> None:
        roll_day = date(2026, 7, 28)
        observed_at = "2026-07-27T12:30:00+08:00"

        def row(contract: str, bid: float, ask: float) -> dict[str, object]:
            return {"contract": contract, "bid": bid, "ask": ask, "observed_at": observed_at}

        with TemporaryDirectory() as raw:
            cache_dir = Path(raw)
            backtest.atomic_json(backtest.roll_cache_path(cache_dir, roll_day), {
                "cache_version": backtest.ROLL_CACHE_VERSION,
                "roll_day": "2026-07-28",
                "window": "Monday 12:28-12:32 BJT before monthly last Tuesday",
                "old": [row("NIFTYN26", 24_000.0, 24_002.0)],
                "new": [row("NIFTYQ26", 24_100.0, 24_102.0)],
                "old_bridge": [row("NIFTYN26", 23_990.0, 23_992.0)],
            })
            reference, basis = backtest.bridge_for_estimation(cache_dir, roll_day, {
                "bridge": {"nifty": [row("NIFTYQ26", 24_090.0, 24_092.0)]},
            })
            self.assertEqual(reference.contract, "NIFTYN26")
            adjusted, roll = backtest.align_nifty_to_reference(
                cache_dir,
                backtest.Quote(24_150.0, 24_152.0, "2026-07-28T09:30:00+08:00", "NIFTYQ26"),
                reference,
                basis,
            )

        self.assertIsNotNone(roll)
        assert roll is not None
        self.assertEqual(roll.direction, "new_to_old")
        self.assertAlmostEqual(adjusted.bid, 24_150.0 * 24_000.0 / 24_102.0)
        self.assertAlmostEqual(adjusted.ask, 24_152.0 * 24_002.0 / 24_100.0)


if __name__ == "__main__":
    unittest.main()

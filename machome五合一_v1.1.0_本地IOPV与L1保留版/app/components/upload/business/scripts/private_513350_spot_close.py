#!/usr/bin/env python3
"""Validated USD/CNY 16:30 spot-close cache for SH513350 history replay."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from zoneinfo import ZoneInfo


CFETS_USD_CNY_SPOT_CLOSE_SOURCE = "CFETS_USD_CNY_SPOT_CLOSE_1630"
SPOT_CLOSE_TIME = "16:30"
SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_RATES_PATH = Path(__file__).resolve().parent / "data" / "cfets_usd_cny_spot_close_1630.csv"


class SpotCloseRateError(ValueError):
    """The immutable historical spot-close cache is missing or malformed."""


@dataclass(frozen=True)
class SpotCloseQuote:
    rate: float
    trading_day: date
    quote_time: str = SPOT_CLOSE_TIME
    source: str = CFETS_USD_CNY_SPOT_CLOSE_SOURCE

    @property
    def observed_at(self) -> datetime:
        return datetime.combine(self.trading_day, time(16, 30), SHANGHAI)


def load_rates(path: str | Path = DEFAULT_RATES_PATH) -> dict[date, float]:
    source = Path(path)
    try:
        with source.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise SpotCloseRateError(f"cannot read SH513350 spot-close cache {source}: {exc}") from exc
    rates: dict[date, float] = {}
    for row in rows:
        try:
            trading_day = datetime.strptime(str(row.get("trading_day") or ""), "%Y-%m-%d").date()
            rate = float(row.get("usd_cny_spot_close") or "")
        except (TypeError, ValueError) as exc:
            raise SpotCloseRateError(f"invalid spot-close row in {source}: {row}") from exc
        if not math.isfinite(rate) or rate <= 0 or trading_day in rates:
            raise SpotCloseRateError(f"invalid or duplicate spot-close row for {trading_day}")
        rates[trading_day] = rate
    if not rates:
        raise SpotCloseRateError(f"SH513350 spot-close cache {source} is empty")
    return rates


def quote_for_day(trading_day: date, path: str | Path = DEFAULT_RATES_PATH) -> SpotCloseQuote:
    rate = load_rates(path).get(trading_day)
    if rate is None:
        raise SpotCloseRateError(
            f"missing CFETS USD/CNY 16:30 spot close for {trading_day}; do not substitute an hourly reference rate"
        )
    return SpotCloseQuote(rate=rate, trading_day=trading_day)

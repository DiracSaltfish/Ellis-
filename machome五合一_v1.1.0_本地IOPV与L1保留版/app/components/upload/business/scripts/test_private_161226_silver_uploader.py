#!/usr/bin/env python3
from __future__ import annotations

import io
import sys
import unittest
import urllib.parse
from argparse import Namespace
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_161226_silver_uploader as silver


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type: str = "text/event-stream; charset=utf-8") -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class Private161226SilverUploaderTests(unittest.TestCase):
    def args(self) -> Namespace:
        return Namespace(
            source=silver.LIVE_SOURCE,
            timeout=5.0,
            sse_endpoint=silver.EASTMONEY_FUTURES_SSE_ENDPOINT,
        )

    def test_sse_url_uses_one_unnumbered_origin_and_selected_contract(self) -> None:
        url = silver.eastmoney_sse_url("AG2610")
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "futsseapi.eastmoney.com")
        self.assertEqual(parsed.path, "/sse/113_ag2610_qt")
        self.assertEqual(query["token"], [silver.EASTMONEY_FUTURES_TOKEN])
        self.assertIn("utime", query["field"][0].split(","))

    def test_collection_window_stops_exactly_at_1500(self) -> None:
        self.assertTrue(
            silver.in_collection_window(datetime(2026, 9, 2, 14, 59, 59, tzinfo=silver.SHANGHAI))
        )
        self.assertFalse(
            silver.in_collection_window(datetime(2026, 9, 2, 15, 0, 0, tzinfo=silver.SHANGHAI))
        )

    def test_sse_delta_events_are_merged_into_the_snapshot(self) -> None:
        response = FakeResponse(
            b'data:{"qt":{"sc":"113","dm":"ag2610","p":15628,"zjsj":16259,"utime":1788330099,"cje":127521656832,"vol":540620}}\n\n'
            b'data:{"qt":{"p":15632,"utime":1788330103,"cje":127534317568,"vol":540674}}\n\n'
        )
        with mock.patch.object(silver.urllib.request, "urlopen", return_value=response):
            stream = silver.eastmoney_quote_stream("AG2610", 5, silver.EASTMONEY_FUTURES_SSE_ENDPOINT)
            first = next(stream)
            second = next(stream)
            stream.close()
        self.assertEqual(first["p"], 15628)
        self.assertEqual(second["p"], 15632)
        self.assertEqual(second["zjsj"], 16259)
        self.assertEqual(second["dm"], "ag2610")

    def test_quote_builds_turnover_vwap_and_eastmoney_provenance(self) -> None:
        now = datetime(2026, 9, 2, 14, 30, 10, tzinfo=silver.SHANGHAI)
        observed = datetime(2026, 9, 2, 14, 30, 5, tzinfo=silver.SHANGHAI)
        volume = 1000
        expected_average = 15724.75
        quote = {
            "sc": "113",
            "dm": "ag2610",
            "p": 15632,
            "zjsj": 16259,
            "utime": int(observed.timestamp()),
            "cje": expected_average * volume * silver.AG_CONTRACT_MULTIPLIER,
            "vol": volume,
            "j": 15725,
        }
        payload = silver.payload_from_eastmoney_quote(
            self.args(), quote, [(date(2026, 9, 1), 1.2345)], now
        )
        self.assertAlmostEqual(payload["silver"]["intraday_average"], expected_average)
        self.assertEqual(payload["silver"]["intraday_average_basis"], silver.EASTMONEY_AVERAGE_BASIS)
        self.assertEqual(payload["silver"]["previous_settlement_source"], silver.EASTMONEY_SETTLEMENT_SOURCE)
        self.assertEqual(payload["silver"]["source"], silver.EASTMONEY_FUTURES_SOURCE)
        self.assertEqual(payload["silver"]["observed_at"], "2026-09-02T14:30:05.000+08:00")

    def test_stale_or_wrong_contract_quote_is_rejected(self) -> None:
        now = datetime(2026, 9, 2, 14, 30, 10, tzinfo=silver.SHANGHAI)
        quote = {
            "sc": "113",
            "dm": "ag2612",
            "p": 15632,
            "zjsj": 16259,
            "utime": int(datetime(2026, 9, 2, 14, 30, 5, tzinfo=silver.SHANGHAI).timestamp()),
            "cje": 235875000,
            "vol": 1000,
        }
        with self.assertRaisesRegex(silver.SourceUnavailableError, "contract mismatch"):
            silver.payload_from_eastmoney_quote(self.args(), quote, [(date(2026, 9, 1), 1.2)], now)
        quote["dm"] = "ag2610"
        quote["utime"] = int(datetime(2026, 9, 2, 14, 20, tzinfo=silver.SHANGHAI).timestamp())
        with self.assertRaisesRegex(silver.SourceUnavailableError, "stale"):
            silver.payload_from_eastmoney_quote(self.args(), quote, [(date(2026, 9, 1), 1.2)], now)

    def test_legacy_backfill_build_input_defaults_are_unchanged(self) -> None:
        observed = datetime(2026, 9, 2, 14, 30, tzinfo=silver.SHANGHAI)
        payload = silver.build_input(
            trading_day=observed.date(), observed_at=observed, contract="AG2610",
            base_nav_day=date(2026, 9, 1), base_nav=1.2,
            previous_settlement=16259, futures_price=15632, intraday_average=15725,
            average_basis=silver.MINLINE_AVERAGE_BASIS,
            source=silver.BACKFILL_SOURCE, generated_at=observed,
        )
        self.assertEqual(payload["silver"]["previous_settlement_source"], silver.PREVIOUS_SETTLEMENT_SOURCE)
        self.assertEqual(payload["silver"]["source"], "SINA_INNER_FUTURES_NEW_SERVICE")


if __name__ == "__main__":
    unittest.main()

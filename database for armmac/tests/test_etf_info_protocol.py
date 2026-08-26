"""Synthetic contract tests for the QueryETFInfo SSE sub-scope.

All fixtures are synthetic; no captured business values are embedded. The
wire shape mirrors the authorized official Linux SDK capture of 2026-08-26
(method ReqGetETFCodeTableList on /amd/dgw/push, string tag "111",
numeric-key record slots, ASCII-code char fields).
"""
from __future__ import annotations

import ctypes
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from tgw_macos import _protocol, interface  # noqa: E402
from tgw_macos._protocol import (  # noqa: E402
    ETF_CONSTITUENT_FIELDS,
    ETF_RECORD_FIELDS,
    TgwProtocolError,
    build_etf_codelist_complete_request,
    build_etf_info_request,
    decode_etf_record,
    parse_etf_info_packets,
)
from tgw_macos._structures import SubCodeTableItem  # noqa: E402


try:
    import pandas  # noqa: F401
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


def wire_record() -> dict:
    """One synthetic numeric-key wire record shaped like the capture."""
    return {
        "1": "SYNTH01",
        "2": 1000000,
        "3": 100000,
        "4": 89,     # publish -> 'Y'
        "5": 0,      # creation -> ''
        "6": 0,      # redemption -> ''
        "7": 49,     # creation_redemption_switch -> '1'
        "8": 30000,
        "9": 30000,
        "10": 123400,
        "11": 20260826,
        "12": 20260825,
        "13": 0,
        "14": 4000000000,
        "15": 40000,
        "16": 101,
        "17": "SYN_NAME",
        "18": "SYN_COMPANY",
        "19": "000998",
        "20": "SSE",
        "21": 0,
        "22": 0,
        "23": 0,
        "24": 0,
        "25": 0,
        "26": 0,
        "27": 0,
        "28": 0,
        "29": 0,
        "30": 0,
        "31": "",
        "32": "",
        "33": "",
        "34": 0,
        "35": "",
        "36": [wire_constituent()],
    }


def wire_constituent() -> dict:
    return {
        "1": "600000",
        "2": 101,
        "3": "SYN_COMP",
        "4": 10000,
        "5": 49,   # substitute_flag -> '1'
        "6": 1000,
        "7": 2000,
        "8": 0,
        "9": 0,
        "10": 300,
        "11": "",
        "12": 0,
        "13": "",
    }


def etf_packet(record=None, *, tag="111", status=0, request_id=7):
    return {
        "headers": {"id": request_id, "tag": tag},
        "status": status,
        "data": [wire_record() if record is None else record],
    }


class SubCodeTableItemTests(unittest.TestCase):
    def test_pack1_layout_matches_public_header(self):
        self.assertEqual(ctypes.sizeof(SubCodeTableItem), 36)
        self.assertEqual(SubCodeTableItem.market.offset, 0)
        self.assertEqual(SubCodeTableItem.security_code.offset, 4)

    def test_defaults_are_zero_and_set_code_truncates_at_nul(self):
        item = SubCodeTableItem()
        self.assertEqual(item.market, 0)
        self.assertEqual(item.security_code, b"")
        item.market = 101
        item.set_code("510300")
        self.assertEqual(item.market, 101)
        self.assertEqual(item.security_code.split(b"\0", 1)[0], b"510300")

    def test_market_field_is_signed_int32(self):
        item = SubCodeTableItem()
        item.market = -1
        self.assertEqual(item.market, -1)


class BuildEtfInfoRequestTests(unittest.TestCase):
    def test_envelope_matches_captured_contract(self):
        raw = build_etf_info_request(
            "user", "token", 7, [{"market": 101, "security_code": "510300"}]
        )
        self.assertNotIn(b" ", raw)
        value = json.loads(raw)
        self.assertEqual(list(value), ["headers", "method", "params"])
        self.assertEqual(value["method"], "ReqGetETFCodeTableList")
        # Captured codelist-channel header key order: id first.
        self.assertEqual(list(value["headers"]), ["id", "userName", "token"])
        self.assertEqual(value["headers"]["id"], 7)
        self.assertEqual(
            value["params"],
            {"Security": "510300|101"},
        )
        self.assertEqual(list(value["params"]), ["Security"])

    def test_rejects_multiple_items(self):
        items = [
            {"market": 101, "security_code": "510300"},
            {"market": 101, "security_code": "510050"},
        ]
        with self.assertRaisesRegex(NotImplementedError, "single-item"):
            build_etf_info_request("user", "token", 7, items)

    def test_rejects_unverified_markets(self):
        for market in (0, 102, 103, 201):
            with self.assertRaisesRegex(NotImplementedError, f"market {market}"):
                build_etf_info_request(
                    "user", "token", 7,
                    [{"market": market, "security_code": "510300"}],
                )

    def test_rejects_empty_or_oversized_code(self):
        with self.assertRaisesRegex(ValueError, "missing security_code"):
            build_etf_info_request(
                "user", "token", 7, [{"market": 101, "security_code": "  "}]
            )
        with self.assertRaisesRegex(ValueError, "exceeds 32"):
            build_etf_info_request(
                "user", "token", 7,
                [{"market": 101, "security_code": "5" * 33}],
            )


class BuildEtfCodelistCompleteTests(unittest.TestCase):
    def test_completion_has_no_params(self):
        raw = build_etf_codelist_complete_request("user", "token", 7)
        value = json.loads(raw)
        self.assertEqual(list(value), ["headers", "method"])
        self.assertEqual(value["method"], "ReqGetCodelistComplete")
        self.assertEqual(list(value["headers"]), ["id", "userName", "token"])
        self.assertEqual(value["headers"]["id"], 7)


class DecodeEtfRecordTests(unittest.TestCase):
    def test_record_decodes_to_official_named_shape(self):
        basic, constituents = decode_etf_record(wire_record())
        expected_names = [name for _, name, _ in ETF_RECORD_FIELDS]
        self.assertEqual(len(basic), 35)
        self.assertEqual(list(basic), expected_names)
        self.assertEqual(basic["security_code"], "SYNTH01")
        self.assertIsInstance(basic["creation_redemption_unit"], int)
        # Single-char fields arrive as ASCII integer codes on the wire.
        self.assertEqual(basic["publish"], "Y")
        self.assertEqual(basic["creation"], "")
        self.assertEqual(basic["redemption"], "")
        self.assertEqual(basic["creation_redemption_switch"], "1")
        self.assertEqual(basic["all_cash_flag"], "")
        self.assertEqual(basic["rtgs_flag"], "")
        self.assertEqual(basic["reserved"], "")
        self.assertEqual(basic["trading_day"], 20260826)
        self.assertEqual(basic["market_type"], 101)
        self.assertEqual(len(constituents), 1)
        self.assertEqual(
            list(constituents[0]),
            [name for _, name, _ in ETF_CONSTITUENT_FIELDS],
        )
        self.assertEqual(constituents[0]["substitute_flag"], "1")
        self.assertEqual(constituents[0]["component_share"], 10000)
        self.assertEqual(constituents[0]["buy_or_sell_to_open"], "")

    def test_empty_data_yields_no_rows(self):
        self.assertEqual(parse_etf_info_packets([{
            "headers": {"id": 7, "tag": "111"}, "status": 0, "data": [],
        }]), [])


class ParseEtfInfoPacketsTests(unittest.TestCase):
    def test_single_packet_roundtrip_with_request_id_echo(self):
        rows = parse_etf_info_packets([etf_packet()], expected_request_id=7)
        self.assertEqual(len(rows), 1)
        basic, constituents = rows[0]
        self.assertEqual(basic["security_code"], "SYNTH01")
        self.assertEqual(len(constituents), 1)

    def test_multi_frame_concatenates_in_arrival_order(self):
        second = wire_record()
        second["17"] = "SECOND"
        rows = parse_etf_info_packets([
            etf_packet(), etf_packet(record=second),
        ])
        self.assertEqual([basic["symbol"] for basic, _ in rows], ["SYN_NAME", "SECOND"])

    def test_rejects_wrong_tag_shape(self):
        # The captured tag is the *string* "111"; neither another string nor
        # the integer 111 may be silently accepted.
        with self.assertRaisesRegex(Exception, "tag"):
            parse_etf_info_packets([etf_packet(tag="112")])
        with self.assertRaisesRegex(Exception, "tag"):
            parse_etf_info_packets([etf_packet(tag=111)])

    def test_rejects_nonzero_status(self):
        with self.assertRaisesRegex(Exception, "status=-76"):
            parse_etf_info_packets([etf_packet(status=-76)])

    def test_rejects_missing_or_extra_slots(self):
        broken = wire_record()
        del broken["7"]
        with self.assertRaisesRegex(Exception, "slot mismatch"):
            parse_etf_info_packets([etf_packet(record=broken)])
        extended = wire_record()
        extended["37"] = 1
        with self.assertRaisesRegex(Exception, "slot mismatch"):
            parse_etf_info_packets([etf_packet(record=extended)])

    def test_rejects_wrong_value_kinds(self):
        cases = []
        as_str = wire_record()
        as_str["4"] = "Y"  # char slot delivered as string instead of ASCII code
        cases.append(as_str)
        as_bool = wire_record()
        as_bool["15"] = True  # bool must not pass as int
        cases.append(as_bool)
        out_of_range = wire_record()
        out_of_range["30"] = 300
        cases.append(out_of_range)
        as_int = wire_record()
        as_int["17"] = 42  # char-array slot delivered as int
        cases.append(as_int)
        for record in cases:
            with self.assertRaises(TgwProtocolError):
                parse_etf_info_packets([etf_packet(record=record)])

    def test_rejects_bad_containers_and_request_ids(self):
        with self.assertRaisesRegex(Exception, "not a list"):
            parse_etf_info_packets([{
                "headers": {"id": 7, "tag": "111"}, "status": 0, "data": {},
            }])
        with self.assertRaisesRegex(Exception, "not an object"):
            parse_etf_info_packets([{
                "headers": {"id": 7, "tag": "111"}, "status": 0, "data": [1],
            }])
        with self.assertRaisesRegex(Exception, "request id mismatch"):
            parse_etf_info_packets([etf_packet()], expected_request_id=8)

    def test_rejects_bad_constituent_shapes(self):
        broken_slot = wire_record()
        broken_slot["36"] = "nope"
        with self.assertRaisesRegex(Exception, "constituent slot"):
            parse_etf_info_packets([etf_packet(record=broken_slot)])
        broken_entry = wire_record()
        broken_entry["36"] = [7]
        with self.assertRaisesRegex(Exception, "constituent entry"):
            parse_etf_info_packets([etf_packet(record=broken_entry)])
        missing_key = wire_record()
        constituent = wire_constituent()
        del constituent["5"]
        missing_key["36"] = [constituent]
        with self.assertRaisesRegex(Exception, "constituent slot mismatch"):
            parse_etf_info_packets([etf_packet(record=missing_key)])


class FakeEtfBackend:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def query(self, kind, req):
        self.calls.append((kind, req))
        return self.result


class PublicContractTests(unittest.TestCase):
    def setUp(self):
        self._previous = interface._g_backend
        self.backend = FakeEtfBackend(result=[wire_record_named()])
        interface._g_backend = self.backend
        self.addCleanup(setattr, interface, "_g_backend", self._previous)

    def test_sync_tuple_contract_and_request_shape(self):
        item = SubCodeTableItem().set_code("510300")
        item.market = 101
        result, error = interface.QueryETFInfo(item, return_df_format=False)
        self.assertEqual(error, 0)
        self.assertEqual(result, [wire_record_named()])
        self.assertEqual(len(self.backend.calls), 1)
        kind, req = self.backend.calls[0]
        self.assertEqual(kind, "etf_info")
        self.assertEqual(req["items"], [{"market": 101, "security_code": "510300"}])

    def test_async_spi_fails_explicitly(self):
        item = SubCodeTableItem().set_code("510300")
        with self.assertRaisesRegex(NotImplementedError, "asynchronous query SPI"):
            interface.QueryETFInfo(item, query_spi=object())

    def test_multi_item_cfg_fails_explicitly(self):
        first = SubCodeTableItem().set_code("510300")
        second = SubCodeTableItem().set_code("510050")
        with self.assertRaisesRegex(NotImplementedError, "single-item"):
            interface.QueryETFInfo([first, second])

    @unittest.skipIf(HAS_PANDAS, "pandas is installed")
    def test_df_format_requires_pandas(self):
        item = SubCodeTableItem().set_code("510300")
        with self.assertRaisesRegex(RuntimeError, "pandas is required"):
            interface.QueryETFInfo(item, return_df_format=True)

    def test_df_format_wraps_basic_record_as_one_row(self):
        frames = []

        class FakePandas:
            @staticmethod
            def DataFrame(value):
                frames.append(value)
                return value

        item = SubCodeTableItem().set_code("510300")
        with patch.dict(sys.modules, {"pandas": FakePandas}):
            result, error = interface.QueryETFInfo(item, return_df_format=True)

        self.assertEqual(error, 0)
        basic, constituents = wire_record_named()
        self.assertEqual(result, [([basic], constituents)])
        self.assertEqual(frames, [[basic], constituents])


def wire_record_named():
    basic, constituents = decode_etf_record(wire_record())
    return basic, constituents


if __name__ == "__main__":
    unittest.main()

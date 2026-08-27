"""Synthetic contract tests for the QueryCodeTable full-market sub-scope.

All fixtures are synthetic; no captured business values are embedded. The wire
shape mirrors the authorized official Linux SDK capture of 2026-08-26 (method
ReqGetReduceCodeTable on the one-shot dgw1_query endpoint, integer tag 11103,
pack_num/all_pack_num paging, backtick-separated 6-field rows).
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
    CODE_TABLE_COLUMNS,
    CODE_TABLE_WIRE_TAG,
    TgwProtocolError,
    TgwTimeoutError,
    build_code_table_request,
    build_get_package_request,
    parse_code_table_packets,
)
from tgw_macos._structures import MDCodeTable  # noqa: E402


def code_table_row(security_code="600000", symbol="SYN", english_name="SYNTH",
                   market_type=101, security_type="EQ", currency="CNY"):
    return "\x60".join([
        security_code, symbol, english_name, str(market_type), security_type,
        currency,
    ])


def code_table_packet(rows, *, pack_num=1, all_pack_num=1, status=0,
                      request_id=1, tag=CODE_TABLE_WIRE_TAG):
    return {
        "headers": {"id": request_id, "tag": tag, "pack_num": pack_num,
                    "all_pack_num": all_pack_num},
        "status": status,
        "data": rows,
    }


def code_table_packets(count=1):
    return [
        code_table_packet([code_table_row(market_type=101)], pack_num=n,
                          all_pack_num=count)
        for n in range(1, count + 1)
    ]


class MDCodeTableTests(unittest.TestCase):
    def test_pack1_layout_matches_public_header(self):
        self.assertEqual(ctypes.sizeof(MDCodeTable), 191)
        self.assertEqual(MDCodeTable.security_code.offset, 0)
        self.assertEqual(MDCodeTable.symbol.offset, 16)
        self.assertEqual(MDCodeTable.english_name.offset, 48)
        self.assertEqual(MDCodeTable.market_type.offset, 176)
        self.assertEqual(MDCodeTable.security_type.offset, 177)
        self.assertEqual(MDCodeTable.currency.offset, 187)

    def test_defaults_are_zero(self):
        row = MDCodeTable()
        self.assertEqual(row.market_type, 0)
        self.assertEqual(row.security_code, b"")
        self.assertEqual(row.currency, b"")


class BuildCodeTableRequestTests(unittest.TestCase):
    def test_envelope_matches_captured_contract(self):
        raw = build_code_table_request("user", "token", 1)
        self.assertNotIn(b" ", raw)
        value = json.loads(raw)
        self.assertEqual(list(value), ["headers", "method", "params"])
        self.assertEqual(value["method"], "ReqGetReduceCodeTable")
        self.assertEqual(list(value["headers"]), ["id", "userName", "token"])
        self.assertEqual(value["headers"]["id"], 1)
        self.assertEqual(value["params"], {"QueryBandWidth": 0.0})

    def test_get_package_envelope(self):
        raw = build_get_package_request("user", "token", 1, 3)
        value = json.loads(raw)
        self.assertEqual(value["method"], "ReqGetPackage")
        self.assertEqual(value["params"], {"pack_num": "3,"})


class ParseCodeTablePacketsTests(unittest.TestCase):
    def test_single_packet_roundtrip(self):
        rows = parse_code_table_packets([
            code_table_packet([code_table_row()], all_pack_num=1),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0]), CODE_TABLE_COLUMNS)
        self.assertEqual(rows[0]["security_code"], "600000")
        self.assertEqual(rows[0]["symbol"], "SYN")
        self.assertEqual(rows[0]["english_name"], "SYNTH")
        self.assertEqual(rows[0]["market_type"], 101)
        self.assertEqual(rows[0]["security_type"], "EQ")
        self.assertEqual(rows[0]["currency"], "CNY")

    def test_market_type_is_int_and_others_are_str(self):
        rows = parse_code_table_packets([
            code_table_packet([code_table_row()], all_pack_num=1),
        ])
        self.assertIsInstance(rows[0]["market_type"], int)
        for column in ("security_code", "symbol", "english_name",
                       "security_type", "currency"):
            self.assertIsInstance(rows[0][column], str)

    def test_multi_packet_reorders_by_pack_num(self):
        row_a = code_table_row(security_code="600000")
        row_b = code_table_row(security_code="600001")
        packets = [
            code_table_packet([row_b], pack_num=2, all_pack_num=2),
            code_table_packet([row_a], pack_num=1, all_pack_num=2),
        ]
        rows = parse_code_table_packets(packets)
        self.assertEqual([row["security_code"] for row in rows],
                         ["600000", "600001"])

    def test_missing_packet_fails_explicitly(self):
        packets = code_table_packets(3)[:2]
        with self.assertRaisesRegex(TgwProtocolError, "incomplete"):
            parse_code_table_packets(packets)

    def test_duplicate_packet_fails_explicitly(self):
        packet = code_table_packet([code_table_row()], pack_num=1, all_pack_num=2)
        packets = [packet, packet]
        with self.assertRaisesRegex(TgwProtocolError, "duplicate"):
            parse_code_table_packets(packets)

    def test_wrong_tag_fails_explicitly(self):
        packets = [code_table_packet([code_table_row()], tag=11104)]
        with self.assertRaisesRegex(TgwProtocolError, "tag"):
            parse_code_table_packets(packets)

    def test_nonzero_status_fails_explicitly(self):
        packets = [code_table_packet([code_table_row()], status=-76)]
        with self.assertRaisesRegex(TgwProtocolError, "status"):
            parse_code_table_packets(packets)

    def test_wrong_data_container_fails(self):
        packets = [{
            "headers": {"id": 1, "tag": CODE_TABLE_WIRE_TAG,
                        "pack_num": 1, "all_pack_num": 1},
            "status": 0, "data": {},
        }]
        with self.assertRaisesRegex(TgwProtocolError, "string array"):
            parse_code_table_packets(packets)

    def test_wrong_field_count_fails(self):
        packets = [code_table_packet(["a\x60b\x60c"])]
        with self.assertRaisesRegex(TgwProtocolError, "6 fields"):
            parse_code_table_packets(packets)

    def test_non_integer_market_type_fails(self):
        packets = [code_table_packet([code_table_row(market_type="NOPE")])]
        with self.assertRaisesRegex(TgwProtocolError, "not an integer"):
            parse_code_table_packets(packets)


class FakeCodeTableBackend:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def query(self, kind, req):
        self.calls.append((kind, req))
        return self.result


class PublicContractTests(unittest.TestCase):
    def setUp(self):
        self._previous = interface._g_backend
        self.backend = FakeCodeTableBackend(result=[{
            "security_code": "600000", "symbol": "SYN", "english_name": "SYNTH",
            "market_type": 101, "security_type": "EQ", "currency": "CNY",
        }])
        interface._g_backend = self.backend
        self.addCleanup(setattr, interface, "_g_backend", self._previous)

    def test_sync_tuple_contract_and_kind(self):
        result, error = interface.QueryCodeTable(return_df_format=False)
        self.assertEqual(error, 0)
        self.assertEqual(len(self.backend.calls), 1)
        kind, req = self.backend.calls[0]
        self.assertEqual(kind, "code_table")
        self.assertIn("task_id", req)

    def test_async_spi_fails_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "asynchronous query SPI"):
            interface.QueryCodeTable(query_spi=object())

    def test_unimplemented_query_kind_fails_explicitly(self):
        from tgw_macos import _backend as _b
        base = _b.BaseBackend()
        with self.assertRaisesRegex(NotImplementedError, "not implemented"):
            base.query("unknown_kind", {"task_id": 1})


class ReexportTests(unittest.TestCase):
    def test_query_code_table_is_reexported(self):
        import tgw_macos
        self.assertIs(tgw_macos.QueryCodeTable, interface.QueryCodeTable)


if __name__ == "__main__":
    unittest.main()

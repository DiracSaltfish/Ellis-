"""Synthetic contract tests for the QueryExFactorTable single-code sub-scope.

All fixtures are synthetic; no captured business values are embedded. The wire
shape mirrors the authorized official Linux SDK capture of 2026-08-26 (SSE
000001): method ReqGetExFactor on the one-shot dgw*_query endpoint, headers
id -> userName -> token, params security_code then QueryBandWidth, integer tag
11102, pack_num/all_pack_num paging, 0x59+ZSTD frames, data as a list of
5-field CSV strings (inner_code, security_code, ex_date, ex_factor,
cum_factor) where the doubles travel as fixed-point decimals.
"""
from __future__ import annotations

import ctypes
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from tgw_macos import _protocol, interface  # noqa: E402
from tgw_macos._protocol import (  # noqa: E402
    EX_FACTOR_ROW_FIELD_COUNT,
    EX_FACTOR_WIRE_TAG,
    TgwProtocolError,
    TgwTimeoutError,
    build_ex_factor_request,
    parse_ex_factor_packets,
)
from tgw_macos._structures import MDExFactorTable  # noqa: E402


def ex_factor_row(*, inner_code="000001.SZ", security_code="000001",
                  ex_date=20260525, ex_factor="1.000000000000000000",
                  cum_factor="1.000000000000000000"):
    return ",".join([inner_code, security_code, str(ex_date), ex_factor, cum_factor])


def ex_factor_packet(rows, *, status=0, request_id=1, tag=EX_FACTOR_WIRE_TAG,
                     pack_num=1, all_pack_num=1):
    return {
        "headers": {
            "id": request_id,
            "tag": tag,
            "pack_num": pack_num,
            "all_pack_num": all_pack_num,
        },
        "status": status,
        "data": rows,
    }


class MDExFactorTableTests(unittest.TestCase):
    def test_pack1_layout_matches_public_header(self):
        self.assertEqual(ctypes.sizeof(MDExFactorTable), 52)
        self.assertEqual(MDExFactorTable.inner_code.offset, 0)
        self.assertEqual(MDExFactorTable.security_code.offset, 16)
        self.assertEqual(MDExFactorTable.ex_date.offset, 32)
        self.assertEqual(MDExFactorTable.ex_factor.offset, 36)
        self.assertEqual(MDExFactorTable.cum_factor.offset, 44)

    def test_field_widths(self):
        self.assertEqual(MDExFactorTable.inner_code.size, 16)
        self.assertEqual(MDExFactorTable.security_code.size, 16)
        self.assertEqual(MDExFactorTable.ex_date.size, 4)
        self.assertEqual(MDExFactorTable.ex_factor.size, 8)
        self.assertEqual(MDExFactorTable.cum_factor.size, 8)

    def test_defaults_are_zero(self):
        row = MDExFactorTable()
        self.assertEqual(row.inner_code, b"")
        self.assertEqual(row.security_code, b"")
        self.assertEqual(row.ex_date, 0)
        self.assertEqual(row.ex_factor, 0.0)
        self.assertEqual(row.cum_factor, 0.0)


class BuildExFactorRequestTests(unittest.TestCase):
    def test_envelope_matches_captured_contract(self):
        raw = build_ex_factor_request("user", "token", 1, "000001")
        self.assertNotIn(b" ", raw)
        value = json.loads(raw)
        self.assertEqual(list(value), ["headers", "method", "params"])
        self.assertEqual(value["method"], "ReqGetExFactor")
        self.assertEqual(list(value["headers"]), ["id", "userName", "token"])
        self.assertEqual(value["headers"]["id"], 1)
        self.assertEqual(list(value["params"]), ["security_code", "QueryBandWidth"])
        self.assertEqual(value["params"]["security_code"], "000001")
        self.assertEqual(value["params"]["QueryBandWidth"], 0.0)

    def test_empty_code_rejected(self):
        with self.assertRaises(ValueError):
            build_ex_factor_request("user", "token", 1, "   ")

    def test_oversized_code_rejected(self):
        with self.assertRaises(ValueError):
            build_ex_factor_request("user", "token", 1, "A" * 33)


class DecodeExFactorRowTests(unittest.TestCase):
    def test_parses_all_five_fields_with_correct_types(self):
        row = parse_ex_factor_packets([
            ex_factor_packet([ex_factor_row()], request_id=1),
        ])[0]
        self.assertEqual(len(row), 5)
        self.assertIsInstance(row["inner_code"], str)
        self.assertIsInstance(row["security_code"], str)
        self.assertIsInstance(row["ex_date"], int)
        self.assertIsInstance(row["ex_factor"], float)
        self.assertIsInstance(row["cum_factor"], float)
        self.assertEqual(row["inner_code"], "000001.SZ")
        self.assertEqual(row["security_code"], "000001")
        self.assertEqual(row["ex_date"], 20260525)
        self.assertEqual(row["ex_factor"], 1.0)
        self.assertEqual(row["cum_factor"], 1.0)

    def test_double_precision_roundtrip(self):
        # The fixed-point wire string must decode to the exact float64 value
        # that the official C++ double carries (N38(15) precision).
        factor = "2.610556000000000000"
        expected = 2.610556
        self.assertEqual(float(factor), expected)
        row = parse_ex_factor_packets([
            ex_factor_packet([
                ex_factor_row(ex_factor=factor, cum_factor="2.610556000000000000"),
            ], request_id=1),
        ])[0]
        self.assertEqual(row["ex_factor"], expected)
        self.assertEqual(row["cum_factor"], expected)

    def test_high_precision_double_keeps_full_float64(self):
        factor = "1.1234567890123456789012345"
        row = parse_ex_factor_packets([
            ex_factor_packet([ex_factor_row(ex_factor=factor)], request_id=1),
        ])[0]
        # Python float() and a C++ double both round to float64.
        self.assertEqual(row["ex_factor"], float(factor))

    def test_non_integer_ex_date_fails(self):
        row = ex_factor_row(ex_date="2026052A")
        with self.assertRaisesRegex(TgwProtocolError, "ex_date"):
            parse_ex_factor_packets([ex_factor_packet([row], request_id=1)])

    def test_non_numeric_double_fails(self):
        row = ex_factor_row(ex_factor="abc")
        with self.assertRaisesRegex(TgwProtocolError, "ex_factor"):
            parse_ex_factor_packets([ex_factor_packet([row], request_id=1)])

    def test_too_few_fields_fails(self):
        row = "000001.SZ,000001,20260525,1.0"
        with self.assertRaisesRegex(TgwProtocolError, "fields"):
            parse_ex_factor_packets([ex_factor_packet([row], request_id=1)])

    def test_too_many_fields_fails(self):
        row = "000001.SZ,000001,20260525,1.0,1.0,extra"
        with self.assertRaisesRegex(TgwProtocolError, "fields"):
            parse_ex_factor_packets([ex_factor_packet([row], request_id=1)])


class ParseExFactorPacketsTests(unittest.TestCase):
    def test_single_packet_roundtrip(self):
        rows = parse_ex_factor_packets([
            ex_factor_packet([ex_factor_row()], request_id=1),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 5)

    def test_empty_response_fails(self):
        with self.assertRaisesRegex(TgwProtocolError, "empty"):
            parse_ex_factor_packets([])

    def test_wrong_tag_fails(self):
        packets = [ex_factor_packet([ex_factor_row()], tag=11103)]
        with self.assertRaisesRegex(TgwProtocolError, "tag"):
            parse_ex_factor_packets(packets)

    def test_nonzero_status_fails(self):
        packets = [ex_factor_packet([ex_factor_row()], status=-76)]
        with self.assertRaisesRegex(TgwProtocolError, "status"):
            parse_ex_factor_packets(packets)

    def test_wrong_data_container_fails(self):
        packets = [{
            "headers": {
                "id": 1, "tag": EX_FACTOR_WIRE_TAG,
                "pack_num": 1, "all_pack_num": 1,
            },
            "status": 0, "data": "000001.SZ",
        }]
        with self.assertRaisesRegex(TgwProtocolError, "string array"):
            parse_ex_factor_packets(packets)

    def test_out_of_order_packets_are_reordered(self):
        rows = parse_ex_factor_packets([
            ex_factor_packet([ex_factor_row(ex_date=20260610)], pack_num=2,
                             all_pack_num=2),
            ex_factor_packet([ex_factor_row(ex_date=20260525)], pack_num=1,
                             all_pack_num=2),
        ])
        self.assertEqual([row["ex_date"] for row in rows], [20260525, 20260610])

    def test_missing_packet_fails(self):
        packets = [ex_factor_packet([ex_factor_row()], pack_num=1, all_pack_num=2)]
        with self.assertRaisesRegex(TgwProtocolError, "incomplete"):
            parse_ex_factor_packets(packets)

    def test_duplicate_packet_fails(self):
        packets = [
            ex_factor_packet([ex_factor_row()], pack_num=1, all_pack_num=1),
            ex_factor_packet([ex_factor_row()], pack_num=1, all_pack_num=1),
        ]
        with self.assertRaisesRegex(TgwProtocolError, "duplicate"):
            parse_ex_factor_packets(packets)

    def test_inconsistent_packet_count_fails(self):
        packets = [
            ex_factor_packet([ex_factor_row()], pack_num=1, all_pack_num=2),
            ex_factor_packet([ex_factor_row()], pack_num=1, all_pack_num=1),
        ]
        with self.assertRaisesRegex(TgwProtocolError, "inconsistent"):
            parse_ex_factor_packets(packets)

    def test_missing_packet_counters_fail(self):
        packets = [{
            "headers": {"id": 1, "tag": EX_FACTOR_WIRE_TAG},
            "status": 0,
            "data": [ex_factor_row()],
        }]
        with self.assertRaisesRegex(TgwProtocolError, "packet counters"):
            parse_ex_factor_packets(packets)


class FakeExFactorBackend:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def query(self, kind, req):
        self.calls.append((kind, req))
        return self.result


class PublicContractTests(unittest.TestCase):
    def setUp(self):
        self._previous = interface._g_backend
        self.backend = FakeExFactorBackend(result=[{
            "inner_code": "000001.SZ", "security_code": "000001",
            "ex_date": 20260525, "ex_factor": 1.0, "cum_factor": 1.0,
        }])
        interface._g_backend = self.backend
        self.addCleanup(setattr, interface, "_g_backend", self._previous)

    def test_sync_tuple_contract_and_kind(self):
        result, error = interface.QueryExFactorTable("000001", return_df_format=False)
        self.assertEqual(error, 0)
        self.assertEqual(len(self.backend.calls), 1)
        kind, req = self.backend.calls[0]
        self.assertEqual(kind, "ex_factor")
        self.assertIn("task_id", req)
        self.assertEqual(req["security_code"], "000001")

    def test_async_spi_fails_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "asynchronous query SPI"):
            interface.QueryExFactorTable("000001", query_spi=object())

    def test_bytes_code_decoded(self):
        interface.QueryExFactorTable(b"000001", return_df_format=False)
        kind, req = self.backend.calls[0]
        self.assertEqual(req["security_code"], "000001")


class ReexportTests(unittest.TestCase):
    def test_query_ex_factor_table_is_reexported(self):
        import tgw_macos
        self.assertIs(tgw_macos.QueryExFactorTable, interface.QueryExFactorTable)

    def test_md_ex_factor_table_is_reexported(self):
        import tgw_macos
        self.assertIs(tgw_macos.MDExFactorTable, MDExFactorTable)


if __name__ == "__main__":
    unittest.main()
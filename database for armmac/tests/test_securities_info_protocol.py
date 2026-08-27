"""Synthetic contract tests for the QuerySecuritiesInfo single-item sub-scope.

All fixtures are synthetic; no captured business values are embedded. The wire
shape mirrors the authorized official Linux SDK capture of 2026-08-26 (SSE
510300): method ReqGetCodeTableList on the persistent /amd/dgw/push connection,
headers id -> userName -> token, single "Security" "<code>|<market>" param,
string tag "109", code_num in headers, no pack_num/all_pack_num paging, data as
a list of 43-numeric-slot record objects.
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
    SECINFO_RECORD_FIELDS,
    SECINFO_WIRE_TAG,
    TgwProtocolError,
    TgwTimeoutError,
    build_secinfo_request,
    decode_secinfo_record,
    parse_secinfo_packets,
)
from tgw_macos._structures import MDCodeTableRecord  # noqa: E402


def secinfo_record(**overrides):
    record = {
        str(position): (
            f"FIELD{position}" if kind == "str" else position * 10
        )
        for position, name, kind in SECINFO_RECORD_FIELDS
    }
    record.update({str(k): v for k, v in overrides.items()})
    return record


def secinfo_packet(records, *, status=0, request_id=1, tag=SECINFO_WIRE_TAG,
                   code_num=None):
    return {
        "headers": {
            "id": request_id,
            "tag": tag,
            "code_num": len(records) if code_num is None else code_num,
        },
        "status": status,
        "data": records,
    }


class MDCodeTableRecordTests(unittest.TestCase):
    def test_pack1_layout_matches_public_header(self):
        self.assertEqual(ctypes.sizeof(MDCodeTableRecord), 555)
        self.assertEqual(MDCodeTableRecord.security_code.offset, 0)
        self.assertEqual(MDCodeTableRecord.market_type.offset, 32)
        self.assertEqual(MDCodeTableRecord.symbol.offset, 33)
        self.assertEqual(MDCodeTableRecord.english_name.offset, 161)
        self.assertEqual(MDCodeTableRecord.security_type.offset, 225)
        self.assertEqual(MDCodeTableRecord.currency.offset, 241)
        self.assertEqual(MDCodeTableRecord.variety_category.offset, 249)
        self.assertEqual(MDCodeTableRecord.pre_close_price.offset, 250)
        self.assertEqual(MDCodeTableRecord.security_status.offset, 318)
        self.assertEqual(MDCodeTableRecord.regular_share.offset, 474)
        self.assertEqual(MDCodeTableRecord.product_code.offset, 499)
        self.assertEqual(MDCodeTableRecord.position_type.offset, 551)

    def test_defaults_are_zero(self):
        row = MDCodeTableRecord()
        self.assertEqual(row.market_type, 0)
        self.assertEqual(row.variety_category, 0)
        self.assertEqual(row.security_code, b"")
        self.assertEqual(row.pre_close_price, 0)
        self.assertEqual(row.position_type, 0)


class BuildSecinfoRequestTests(unittest.TestCase):
    def test_envelope_matches_captured_contract(self):
        raw = build_secinfo_request("user", "token", 1,
                                    [{"market": 101, "security_code": "510300"}])
        self.assertNotIn(b" ", raw)
        value = json.loads(raw)
        self.assertEqual(list(value), ["headers", "method", "params"])
        self.assertEqual(value["method"], "ReqGetCodeTableList")
        self.assertEqual(list(value["headers"]), ["id", "userName", "token"])
        self.assertEqual(value["headers"]["id"], 1)
        self.assertEqual(value["params"], {"Security": "510300|101"})

    def test_multi_item_fails_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "single-item"):
            build_secinfo_request("user", "token", 1, [
                {"market": 101, "security_code": "510300"},
                {"market": 101, "security_code": "510500"},
            ])

    def test_unverified_market_fails_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "market 102"):
            build_secinfo_request("user", "token", 1,
                                  [{"market": 102, "security_code": "159518"}])

    def test_empty_and_oversized_code_rejected(self):
        with self.assertRaises(ValueError):
            build_secinfo_request("user", "token", 1,
                                  [{"market": 101, "security_code": ""}])
        with self.assertRaises(ValueError):
            build_secinfo_request("user", "token", 1,
                                  [{"market": 101, "security_code": "A" * 33}])


class DecodeSecinfoRecordTests(unittest.TestCase):
    def test_decodes_all_43_fields_with_correct_types(self):
        row = decode_secinfo_record(secinfo_record())
        self.assertEqual(len(row), 43)
        for position, name, kind in SECINFO_RECORD_FIELDS:
            self.assertIn(name, row)
            if kind == "int":
                self.assertIsInstance(row[name], int)
            else:
                self.assertIsInstance(row[name], str)

    def test_int_field_as_bool_fails(self):
        record = secinfo_record(**{"2": True})
        with self.assertRaisesRegex(TgwProtocolError, "not an integer"):
            decode_secinfo_record(record)

    def test_str_field_as_int_fails(self):
        record = secinfo_record(**{"1": 123})
        with self.assertRaisesRegex(TgwProtocolError, "not a string"):
            decode_secinfo_record(record)

    def test_missing_slot_fails(self):
        record = secinfo_record()
        del record["43"]
        with self.assertRaisesRegex(TgwProtocolError, "slot mismatch"):
            decode_secinfo_record(record)

    def test_extra_slot_fails(self):
        record = secinfo_record(**{"44": "x"})
        with self.assertRaisesRegex(TgwProtocolError, "slot mismatch"):
            decode_secinfo_record(record)

    def test_non_object_fails(self):
        with self.assertRaisesRegex(TgwProtocolError, "not an object"):
            decode_secinfo_record(["x"])


class ParseSecinfoPacketsTests(unittest.TestCase):
    def test_single_packet_roundtrip(self):
        rows = parse_secinfo_packets([
            secinfo_packet([secinfo_record()], request_id=1),
        ], expected_request_id=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 43)

    def test_request_id_mismatch_fails(self):
        packets = [secinfo_packet([secinfo_record()], request_id=2)]
        with self.assertRaisesRegex(TgwProtocolError, "request id mismatch"):
            parse_secinfo_packets(packets, expected_request_id=1)

    def test_wrong_tag_fails(self):
        packets = [secinfo_packet([secinfo_record()], tag="111")]
        with self.assertRaisesRegex(TgwProtocolError, "tag"):
            parse_secinfo_packets(packets)

    def test_nonzero_status_fails(self):
        packets = [secinfo_packet([secinfo_record()], status=-76)]
        with self.assertRaisesRegex(TgwProtocolError, "status"):
            parse_secinfo_packets(packets)

    def test_wrong_data_container_fails(self):
        packets = [{
            "headers": {"id": 1, "tag": SECINFO_WIRE_TAG, "code_num": 0},
            "status": 0, "data": {},
        }]
        with self.assertRaisesRegex(TgwProtocolError, "not a list"):
            parse_secinfo_packets(packets)

    def test_empty_response_fails(self):
        with self.assertRaisesRegex(TgwProtocolError, "empty"):
            parse_secinfo_packets([])

    def test_multiple_frames_concatenate(self):
        rows = parse_secinfo_packets([
            secinfo_packet([secinfo_record()], request_id=1),
            secinfo_packet([secinfo_record()], request_id=1),
        ])
        self.assertEqual(len(rows), 2)


class FakeSecinfoBackend:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def query(self, kind, req):
        self.calls.append((kind, req))
        return self.result


class PublicContractTests(unittest.TestCase):
    def setUp(self):
        self._previous = interface._g_backend
        self.backend = FakeSecinfoBackend(result=[{"security_code": "510300"}])
        interface._g_backend = self.backend
        self.addCleanup(setattr, interface, "_g_backend", self._previous)

    def test_sync_tuple_contract_and_kind(self):
        item = _protocol_placeholder()
        result, error = interface.QuerySecuritiesInfo(item, return_df_format=False)
        self.assertEqual(error, 0)
        self.assertEqual(len(self.backend.calls), 1)
        kind, req = self.backend.calls[0]
        self.assertEqual(kind, "securities_info")
        self.assertIn("task_id", req)
        self.assertEqual(req["items"], [{"market": 101, "security_code": "510300"}])

    def test_async_spi_fails_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "asynchronous query SPI"):
            interface.QuerySecuritiesInfo(_protocol_placeholder(), query_spi=object())

    def test_multi_item_fails_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "single-item"):
            interface.QuerySecuritiesInfo(
                [_protocol_placeholder(), _protocol_placeholder()],
                return_df_format=False,
            )


def _protocol_placeholder():
    class Item:
        market = 101
        security_code = b"510300"
    return Item()


class ReexportTests(unittest.TestCase):
    def test_query_securities_info_is_reexported(self):
        import tgw_macos
        self.assertIs(tgw_macos.QuerySecuritiesInfo, interface.QuerySecuritiesInfo)

    def test_md_code_table_record_is_reexported(self):
        import tgw_macos
        self.assertIs(tgw_macos.MDCodeTableRecord, MDCodeTableRecord)


if __name__ == "__main__":
    unittest.main()

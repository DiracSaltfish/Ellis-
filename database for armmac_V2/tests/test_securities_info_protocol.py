"""Synthetic contract tests for the narrow QuerySecuritiesInfo sub-scope.

All fixtures are synthetic; no captured business values are embedded. The wire
shape mirrors authorized official Linux SDK captures: method ReqGetCodeTableList
on the persistent /amd/dgw/push connection,
headers id -> userName -> token, single "Security" "<code>|<market>" param,
string tag "109", code_num in headers, no pack_num/all_pack_num paging, data as
a list of 43-numeric-slot record objects.
"""
from __future__ import annotations

import ctypes
import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from tgw_macos import _protocol, interface  # noqa: E402
from tgw_macos._backend import LiveBackend  # noqa: E402
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

    def test_szse_single_item_matches_captured_contract(self):
        raw = build_secinfo_request("user", "token", 2,
                                    [{"market": 102, "security_code": "159919"}])
        value = json.loads(raw)
        self.assertEqual(value["headers"]["id"], 2)
        self.assertEqual(value["params"], {"Security": "159919|102"})

    def test_ordered_sse_szse_pair_matches_captured_contract(self):
        raw = build_secinfo_request("user", "token", 2, [
            {"market": 101, "security_code": "510300"},
            {"market": 102, "security_code": "159919"},
        ])
        value = json.loads(raw)
        self.assertEqual(value["params"], {
            "Security": "510300|101,159919|102",
        })

    def test_unobserved_batch_or_order_fails_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "only one verified"):
            build_secinfo_request("user", "token", 1, [
                {"market": 101, "security_code": "510300"},
                {"market": 102, "security_code": "159919"},
                {"market": 101, "security_code": "510300"},
            ])
        with self.assertRaisesRegex(NotImplementedError, "ordered SSE 510300"):
            build_secinfo_request("user", "token", 1, [
                {"market": 102, "security_code": "159919"},
                {"market": 101, "security_code": "510300"},
            ])

    def test_unverified_single_item_fails_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "single item"):
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

    def test_code_num_mismatch_or_wrong_type_fails(self):
        with self.assertRaisesRegex(TgwProtocolError, "does not match"):
            parse_secinfo_packets([secinfo_packet([secinfo_record()], code_num=2)])
        with self.assertRaisesRegex(TgwProtocolError, "code_num is not an integer"):
            packet = secinfo_packet([secinfo_record()])
            packet["headers"]["code_num"] = "1"
            parse_secinfo_packets([packet])

    def test_empty_response_fails(self):
        with self.assertRaisesRegex(TgwProtocolError, "empty"):
            parse_secinfo_packets([])

    def test_multiple_frames_concatenate(self):
        rows = parse_secinfo_packets([
            secinfo_packet([secinfo_record()], request_id=1),
            secinfo_packet([secinfo_record()], request_id=1),
        ])
        self.assertEqual(len(rows), 2)

    def test_two_item_frame_preserves_server_response_order(self):
        rows = parse_secinfo_packets([
            secinfo_packet([
                secinfo_record(**{"1": "SYN_SZ", "2": 102}),
                secinfo_record(**{"1": "SYN_SH", "2": 101}),
            ], request_id=2),
        ], expected_request_id=2)
        self.assertEqual([row["market_type"] for row in rows], [102, 101])
        self.assertEqual([row["security_code"] for row in rows], ["SYN_SZ", "SYN_SH"])


class SecinfoCodelistRequestIdTests(unittest.TestCase):
    def test_backend_uses_independent_low_wire_sequence(self):
        class FakeCodelistClient:
            username = "user"
            token = "token"

            def __init__(self):
                self.next_id = 1
                self.request_ids = []
                self.payloads = []
                self.completions = []

            def next_codelist_request_id(self):
                value = self.next_id
                self.next_id += 1
                return value

            def request_many(self, request_id, payload, *, done, timeout):
                self.request_ids.append(request_id)
                self.payloads.append(json.loads(payload))
                packet = secinfo_packet([secinfo_record()], request_id=request_id)
                self.assert_done = done(packet)
                return [packet]

            def send(self, payload):
                self.completions.append(json.loads(payload))

        backend = LiveBackend()
        client = FakeCodelistClient()
        backend.client = client
        result = backend._query_securities_info(
            829232038000001,
            [{"market": 101, "security_code": "510300"}],
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(client.request_ids, [1])
        self.assertTrue(client.assert_done)
        self.assertEqual(client.payloads[0]["headers"]["id"], 1)
        self.assertEqual(client.completions[0]["headers"]["id"], 1)

    def test_backend_preserves_captured_two_item_wire_encoding(self):
        class FakeCodelistClient:
            username = "user"
            token = "token"

            def next_codelist_request_id(self):
                return 2

            def request_many(self, request_id, payload, *, done, timeout):
                self.request_id = request_id
                self.payload = json.loads(payload)
                packet = secinfo_packet(
                    [secinfo_record(), secinfo_record()], request_id=request_id
                )
                self.done = done(packet)
                return [packet]

            def send(self, payload):
                self.completion = json.loads(payload)

        backend = LiveBackend()
        client = FakeCodelistClient()
        backend.client = client
        result = backend._query_securities_info(829232038000001, [
            {"market": 101, "security_code": "510300"},
            {"market": 102, "security_code": "159919"},
        ])

        self.assertEqual(len(result), 2)
        self.assertEqual(client.request_id, 2)
        self.assertTrue(client.done)
        self.assertEqual(
            client.payload["params"]["Security"], "510300|101,159919|102"
        )
        self.assertEqual(client.completion["headers"]["id"], 2)


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

    def test_signature_matches_official_parameter_spelling_and_defaults(self):
        signature = inspect.signature(interface.QuerySecuritiesInfo)
        self.assertEqual(list(signature.parameters), [
            "req_security_info_cfg", "query_spi", "return_df_format",
        ])
        self.assertIsNone(signature.parameters["query_spi"].default)
        self.assertIs(signature.parameters["return_df_format"].default, True)

    def test_async_spi_fails_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "asynchronous query SPI"):
            interface.QuerySecuritiesInfo(_protocol_placeholder(), query_spi=object())

    def test_verified_two_item_pair_reaches_backend_in_public_order(self):
        szse_item = _protocol_placeholder(market=102, code=b"159919")
        result, error = interface.QuerySecuritiesInfo(
            [_protocol_placeholder(), szse_item], return_df_format=False
        )
        self.assertEqual(error, 0)
        self.assertEqual(result, [{"security_code": "510300"}])
        _kind, req = self.backend.calls[-1]
        self.assertEqual(req["items"], [
            {"market": 101, "security_code": "510300"},
            {"market": 102, "security_code": "159919"},
        ])

    def test_more_than_two_items_fails_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "two-item pair"):
            interface.QuerySecuritiesInfo(
                [_protocol_placeholder(), _protocol_placeholder(), _protocol_placeholder()],
                return_df_format=False,
            )


def _protocol_placeholder(market=101, code=b"510300"):
    class Item:
        pass
    Item.market = market
    Item.security_code = code
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

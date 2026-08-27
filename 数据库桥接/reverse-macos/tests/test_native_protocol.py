from __future__ import annotations

import ctypes
import json
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src_reconstructed" / "python"))

from tgw_macos import _backend  # noqa: E402
from tgw_macos._protocol import (  # noqa: E402
    CompressedMessage,
    SNAPSHOT_ROW_FIELD_COUNT,
    SNAPSHOT_WIRE_TAG,
    ZSTD_MAGIC,
    build_kline_request,
    build_logon_request,
    build_query_complete_request,
    build_snapshot_request,
    build_subscribe_request,
    build_third_info_request,
    decode_server_payload,
    parse_third_info_packets,
    parse_kline_packets,
    parse_snapshot_packets,
)
from tgw_macos._structures import (  # noqa: E402
    Cfg,
    ColocaCfg,
    LogonResponse,
    ReqDefault,
    ReqKline,
    SubscribeItem,
)
from tgw_macos._websocket import WebSocketStream, apply_mask, encode_frame  # noqa: E402


class StructureContractTests(unittest.TestCase):
    def test_public_pack1_sizes(self):
        self.assertEqual(ctypes.sizeof(ColocaCfg), 22)
        self.assertEqual(ctypes.sizeof(Cfg), 145)
        self.assertEqual(ctypes.sizeof(LogonResponse), 14)
        self.assertEqual(ctypes.sizeof(SubscribeItem), 42)
        self.assertEqual(ctypes.sizeof(ReqKline), 71)
        self.assertEqual(ctypes.sizeof(ReqDefault), 55)

    def test_cfg_offsets_match_public_header(self):
        self.assertEqual(Cfg.server_vip.offset, 0)
        self.assertEqual(Cfg.server_port.offset, 24)
        self.assertEqual(Cfg.username.offset, 26)
        self.assertEqual(Cfg.password.offset, 58)
        self.assertEqual(Cfg.force_logout.offset, 122)
        self.assertEqual(Cfg.coloca_cfg.offset, 123)

    def test_req_default_offsets_include_level_type_delta(self):
        # The C++ manual's ReqDefault table stops at data_type; the V1.0.8
        # headers add level_type:uint16_t=0. Local ABI follows the release.
        self.assertEqual(ReqDefault.security_code.offset, 0)
        self.assertEqual(ReqDefault.market_type.offset, 38)
        self.assertEqual(ReqDefault.date.offset, 39)
        self.assertEqual(ReqDefault.begin_time.offset, 43)
        self.assertEqual(ReqDefault.end_time.offset, 47)
        self.assertEqual(ReqDefault.data_type.offset, 51)
        self.assertEqual(ReqDefault.level_type.offset, 53)
        request = ReqDefault()
        self.assertEqual(request.data_type, 0)
        self.assertEqual(request.level_type, 0)


class WebSocketTests(unittest.TestCase):
    def test_extended_masked_frame_matches_captured_shape(self):
        payload = b"x" * 312
        encoded = encode_frame(payload, opcode=0x2, mask=True, mask_key=b"abcd")
        self.assertEqual(encoded[:4], b"\x82\xfe\x01\x38")
        self.assertEqual(encoded[4:8], b"abcd")
        self.assertEqual(apply_mask(encoded[8:], b"abcd"), payload)

    def test_stream_reads_unmasked_server_frame(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        right.sendall(encode_frame(b'{"status":0}', opcode=0x2, mask=False))
        frame = WebSocketStream(left).read_frame()
        self.assertTrue(frame.fin)
        self.assertEqual(frame.opcode, 0x2)
        self.assertEqual(frame.payload, b'{"status":0}')

    def test_close_frame_status_payload(self):
        payload = (1000).to_bytes(2, "big") + b"complete"
        encoded = encode_frame(payload, opcode=0x8, mask=False)
        self.assertEqual(encoded[:2], b"\x88\x0a")


class ProtocolEnvelopeTests(unittest.TestCase):
    def test_logon_envelope_matches_authorized_capture_contract(self):
        request_id, raw = build_logon_request(
            "test_user",
            "test_pass",
            force_logout=False,
            client_version="test-version",
            process_id=1234,
            mac_addresses=["00:11:22:33:44:55", "aa:bb:cc:dd:ee:ff"],
        )
        self.assertEqual(request_id, 0)
        self.assertNotIn(b" ", raw)
        value = json.loads(raw)
        self.assertEqual(list(value), ["headers", "method", "params"])
        self.assertEqual(value["method"], "ReqLogon")
        self.assertEqual(value["headers"], {"id": 0, "userName": "test_user"})
        self.assertEqual(
            list(value["params"]),
            [
                "Username", "Password", "MacAddress", "Version", "ProcessId",
                "ForceLogout", "PushBandWidth", "QueryBandWidth",
            ],
        )

    def test_subscribe_envelope(self):
        raw = build_subscribe_request(
            "test_user",
            "test_token",
            1_000_000,
            [{"market": 102, "category_type": 2, "flag": 22, "security_code": "159001"}],
        )
        value = json.loads(raw)
        self.assertEqual(value["method"], "ReqSubscribeBatch")
        self.assertEqual(value["params"]["marketType"], [102])
        self.assertEqual(value["params"]["subscribeDataType"], [22])

    def test_public_l1_subscription_maps_to_verified_wire_tag(self):
        raw = build_subscribe_request(
            "test_user",
            "test_token",
            1_000_000,
            [{"market": 102, "category_type": 0, "flag": 10,
              "security_code": "159518"}],
        )
        value = json.loads(raw)
        self.assertEqual(value["params"]["subscribeDataType"], [14])
        client = __import__(
            "tgw_macos._protocol", fromlist=["TgwWssClient"]
        ).TgwWssClient()
        self.assertEqual(client.next_request_id(), 1_000_000)

    def test_public_hkt_l1_subscription_maps_to_verified_wire_tag(self):
        raw = build_subscribe_request(
            "test_user",
            "test_token",
            1_000_000,
            [{"market": 101, "category_type": 0, "flag": 12,
              "security_code": "02800"}],
        )
        value = json.loads(raw)
        self.assertEqual(value["params"]["marketType"], [101])
        self.assertEqual(value["params"]["subscribeDataType"], [16])
        self.assertEqual(value["params"]["securityCode"], ["02800"])

    def test_third_info_envelope_and_response(self):
        raw = build_third_info_request(
            "test_user",
            "test_token",
            1_000_001,
            {
                "function_id": "A010061003",
                "start_date": "20260801",
                "end_date": "20260826",
                "market": "SSE",
            },
        )
        value = json.loads(raw)
        self.assertEqual(value["method"], "ReqGetThirdInfo")
        self.assertEqual(value["offset"], 0)
        self.assertEqual(value["count"], 1000)
        self.assertEqual(value["params"], {"QueryBandWidth": 0.0})
        self.assertEqual(
            [item["key"] for item in value["item"]],
            ["start_date", "end_date", "market"],
        )
        complete = json.loads(build_query_complete_request(
            "test_user", "test_token", 1_000_001
        ))
        self.assertEqual(complete["method"], "ReqGetComplete")

        packets = [{
            "headers": {"id": 1_000_001, "tag": 11101, "pack_num": 1, "all_pack_num": 1},
            "status": 0,
            "data": json.dumps({"body": {"data": [{"TRADE_DAYS": "20260803"}],
                                                 "page": {"offset": 0, "count": 1}}}),
        }]
        self.assertEqual(parse_third_info_packets(packets), [{"TRADE_DAYS": "20260803"}])

    def test_third_info_response_orders_packets_and_rejects_duplicates(self):
        def packet(number, value):
            return {
                "headers": {
                    "id": 1,
                    "tag": 11101,
                    "pack_num": number,
                    "all_pack_num": 2,
                },
                "status": 0,
                "data": json.dumps({"body": {"data": [{"value": value}], "page": {}}}),
            }

        self.assertEqual(
            parse_third_info_packets([packet(2, "second"), packet(1, "first")]),
            [{"value": "first"}, {"value": "second"}],
        )
        with self.assertRaisesRegex(Exception, "duplicate"):
            parse_third_info_packets([packet(1, "first"), packet(1, "again")])

    def test_kline_envelope_and_response(self):
        request = ReqKline().set_code("510300")
        request.market_type = 101
        request.cq_flag = 0
        request.cyc_type = 10008
        request.begin_date = 20260825
        request.end_date = 20260825
        raw = build_kline_request("user", "token", 1, request)
        value = json.loads(raw)
        self.assertEqual(value["method"], "ReqGetKline")
        self.assertEqual(value["params"]["period_type"], 10100)
        self.assertEqual(list(value["params"]), [
            "security_code", "market_type", "cq_flag", "auto_complete",
            "period_type", "begin_date", "end_date", "begin_time",
            "end_time", "QueryBandWidth",
        ])
        packets = [{
            "headers": {"id": 1, "tag": 10100, "pack_num": 1, "all_pack_num": 1},
            "status": 0,
            "data": ["510300,101,20260825,100,110,90,105,200,300"],
        }]
        self.assertEqual(parse_kline_packets(packets), [{
            "market_type": 101,
            "security_code": "510300",
            "orig_time": 0,
            "kline_time": 20260825,
            "open_price": 100,
            "high_price": 110,
            "low_price": 90,
            "close_price": 105,
            "volume_trade": 200,
            "value_trade": 300,
            "variety_category": 0,
        }])

    def test_snapshot_envelope_and_response(self):
        request = ReqDefault().set_code("159518")
        request.market_type = 102
        request.date = 20260825
        request.begin_time = 93000000
        request.end_time = 93030000
        raw = build_snapshot_request("user", "token", 1, request)
        value = json.loads(raw)
        self.assertEqual(value["method"], "ReqGetSnapshot")
        self.assertNotIn("level_type", value["params"])
        self.assertEqual(list(value["params"]), [
            "security_code", "market_type", "date", "begin_time",
            "end_time", "data_type", "QueryBandWidth",
        ])
        self.assertEqual(
            list(value["headers"]), ["userName", "token", "id"]
        )

        row = ",".join([
            "000001", "101", "20260801093000000", "T0",
            "1000000", "1100000", "1200000", "900000", "1150000", "0",
            "990000|980000|970000|960000|950000|940000|930000|920000|910000|900000",
            "100|200|300|400|500|600|700|800|900|1000",
            "1010000|1020000|1030000|1040000|1050000|1060000|1070000|1080000|1090000|1100000",
            "1100|1200|1300|1400|1500|1600|1700|1800|1900|2000",
            "5", "12345600", "23456789000", "1145000", "1210000", "890000",
            "111111111", "222222222", "333333", "444444",
            "", "", "", "", "",
            "1", "100000000", "0", "0", "0", "0",
            "",
        ])
        self.assertEqual(len(row.split(",")), SNAPSHOT_ROW_FIELD_COUNT)
        packets = [{
            "headers": {
                "id": 1, "tag": SNAPSHOT_WIRE_TAG, "pack_num": 1, "all_pack_num": 1,
            },
            "status": 0,
            "data": [row],
        }]
        rows = parse_snapshot_packets(packets)
        expected_keys = [
            "market_type", "security_code", "variety_category", "orig_time",
            "trading_phase_code", "pre_close_price", "open_price", "high_price",
            "low_price", "last_price", "close_price",
        ]
        for level in range(1, 11):
            expected_keys += [
                f"bid_price{level}", f"bid_volume{level}",
                f"offer_price{level}", f"offer_volume{level}",
            ]
        expected_keys += [
            "num_trades", "total_volume_trade", "total_value_trade",
            "IOPV", "high_limited", "low_limited",
        ]
        self.assertEqual(list(rows[0]), expected_keys)
        self.assertEqual(len(rows[0]), 57)
        self.assertEqual(rows[0]["security_code"], "000001")
        self.assertEqual(rows[0]["variety_category"], 0)
        self.assertEqual(rows[0]["orig_time"], 20260801093000000)
        self.assertEqual(rows[0]["bid_price10"], 900000)
        self.assertEqual(rows[0]["offer_volume3"], 1300)

    def test_snapshot_rejects_unverified_data_types(self):
        request = ReqDefault().set_code("00700")
        request.market_type = 103
        request.data_type = 1
        with self.assertRaises(NotImplementedError):
            build_snapshot_request("user", "token", 1, request)

    def test_snapshot_multi_packet_ordering_and_error_shapes(self):
        packed = "|".join(["1"] * 10)
        row_a = ",".join(["000001", "101"] + ["1"] * 8 + [packed] * 4 + ["1"] * 22)
        row_b = ",".join(["000002", "101"] + ["2"] * 8 + [packed] * 4 + ["2"] * 22)

        def packet(number, total, payload):
            return {
                "headers": {
                    "id": 7, "tag": SNAPSHOT_WIRE_TAG,
                    "pack_num": number, "all_pack_num": total,
                },
                "status": 0,
                "data": payload,
            }

        ordered = parse_snapshot_packets([
            packet(2, 2, [row_b]), packet(1, 2, [row_a]),
        ])
        self.assertEqual([item["security_code"] for item in ordered], ["000001", "000002"])

        cases = [
            ([packet(1, 2, [row_a])], "incomplete"),
            ([packet(1, 1, [row_b]), packet(1, 1, [row_a])], "duplicate"),
            ([{
                "headers": {"id": 7, "tag": 999, "pack_num": 1, "all_pack_num": 1},
                "status": 0, "data": [row_a],
            }], "tag"),
            ([{
                "headers": {"id": 7, "tag": SNAPSHOT_WIRE_TAG,
                            "pack_num": 1, "all_pack_num": 1},
                "status": -76, "data": [row_a],
            }], "status"),
            ([packet(1, 1, [",".join(["000001"] * 10)])], "36 fields"),
            ([packet(1, 1, [
                ",".join([
                    "000001", "101", "20260801093000000", "T0",
                    *["1"] * 6,
                    "1|2|3",  # malformed packed array
                    *["1"] * 25,
                ])
            ])], "packed array"),
            ([{
                "headers": {"id": 7, "tag": SNAPSHOT_WIRE_TAG,
                            "pack_num": 1, "all_pack_num": 1},
                "status": 0,
                "data": {"1": "oops"},
            }], "string array"),
        ]
        for packets, fragment in cases:
            with self.assertRaisesRegex(Exception, fragment):
                parse_snapshot_packets(packets)

    def test_plain_server_response(self):
        value = decode_server_payload(
            b'{"headers":{"id":0,"tag":"OnRspLogon","token":"test"},"status":0,"data":{}}'
        )
        self.assertEqual(value["headers"]["tag"], "OnRspLogon")

    def test_zstd_marker_when_decoder_available(self):
        # zstd-compressed {"status":0}; generated once with zstd 1.5.7.
        compressed = bytes.fromhex(
            "28b52ffd04586100007b22737461747573223a307db385eae7"
        )
        value = decode_server_payload(b"Y" + compressed)
        if isinstance(value, CompressedMessage):
            self.skipTest("no Python or native zstd decoder is available")
        self.assertNotIsInstance(value, CompressedMessage)
        self.assertEqual(value["status"], 0)
        self.assertEqual((b"Y" + compressed)[1:5], ZSTD_MAGIC)


class BackendSelectionTests(unittest.TestCase):
    def test_live_is_default_and_simulation_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            backend, source = _backend.get_backend()
            self.assertIsInstance(backend, _backend.LiveBackend)
            self.assertEqual(source, "live-wss")
            backend.close()
        with patch.dict(os.environ, {"TGW_BACKEND": "sim"}, clear=True):
            backend, source = _backend.get_backend()
            self.assertIsInstance(backend, _backend.SimBackend)
            self.assertEqual(source, "explicit-sim")


if __name__ == "__main__":
    unittest.main()

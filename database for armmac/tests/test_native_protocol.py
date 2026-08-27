from __future__ import annotations

import ctypes
import json
import os
import socket
import sys
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

import tgw_macos as tgw  # noqa: E402
from tgw_macos import _backend, _protocol, interface  # noqa: E402
from tgw_macos._kline_units import (  # noqa: E402
    normalize_verified_159691_szse_one_minute_kline_rows,
)
from tgw_macos._protocol import (  # noqa: E402
    CompressedMessage,
    DecodedMessageBatch,
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
    kline_wire_period,
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

    def test_subscribe_rejects_unverified_public_flag(self):
        with self.assertRaisesRegex(NotImplementedError, "flag 22"):
            build_subscribe_request(
                "test_user",
                "test_token",
                1_000_000,
                [{"market": 102, "category_type": 2, "flag": 22,
                  "security_code": "159001"}],
            )

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
        self.assertEqual(parse_kline_packets(packets, expected_tag=10100), [{
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

    def test_one_minute_kline_maps_to_verified_wire_period_and_tag(self):
        # Captured from an authorized official Linux SDK session on 2026-08-26:
        # public cyc_type=10000 remains period_type=10000 and the response tag
        # is 10000. Minute rows use an HHmm suffix in kline_time.
        self.assertEqual(kline_wire_period(10000), 10000)
        request = ReqKline().set_code("159691")
        request.market_type = 102
        request.cq_flag = 0
        request.cq_date = 0
        request.qj_flag = 0
        request.cyc_type = 10000
        request.cyc_def = 0
        request.auto_complete = 1
        request.begin_date = 20260826
        request.end_date = 20260826
        request.begin_time = 900
        request.end_time = 1500
        raw = build_kline_request("user", "token", 1, request)
        value = json.loads(raw)
        self.assertEqual(value["method"], "ReqGetKline")
        self.assertEqual(value["params"]["period_type"], 10000)
        self.assertEqual(value["params"]["begin_time"], 900)
        self.assertEqual(value["params"]["end_time"], 1500)
        self.assertEqual(
            list(value["params"]),
            [
                "security_code", "market_type", "cq_flag", "auto_complete",
                "period_type", "begin_date", "end_date", "begin_time",
                "end_time", "QueryBandWidth",
            ],
        )
        packets = [
            {
                "headers": {
                    "id": 1, "tag": 10000, "pack_num": 2, "all_pack_num": 2,
                },
                "status": 0,
                "data": ["159691,102,202608260931,101,111,91,106,201,301"],
            },
            {
                "headers": {
                    "id": 1, "tag": 10000, "pack_num": 1, "all_pack_num": 2,
                },
                "status": 0,
                "data": ["159691,102,202608260930,100,110,90,105,200,300"],
            },
        ]
        rows = parse_kline_packets(packets, expected_tag=10000)
        self.assertEqual(
            [row["kline_time"] for row in rows], [202608260930, 202608260931]
        )
        for row in rows:
            self.assertEqual(row["orig_time"], 0)
            self.assertEqual(row["variety_category"], 0)
            self.assertEqual(row["market_type"], 102)
            self.assertEqual(row["security_code"], "159691")
            self.assertEqual(len(row), 11)
            for key, value in row.items():
                if key != "security_code":
                    self.assertIsInstance(value, int)

    def test_week_kline_maps_to_verified_wire_period_and_tag(self):
        # Captured from an authorized official Linux SDK session on 2026-08-26:
        # public cyc_type=10009 is transmitted as period_type=10101 and the
        # single response packet carries tag=10101 with the daily CSV contract.
        self.assertEqual(kline_wire_period(10008), 10100)
        self.assertEqual(kline_wire_period(10009), 10101)
        request = ReqKline().set_code("510300")
        request.market_type = 101
        request.cq_flag = 0
        request.cyc_type = 10009
        request.begin_date = 20260817
        request.end_date = 20260821
        raw = build_kline_request("user", "token", 1, request)
        value = json.loads(raw)
        self.assertEqual(value["method"], "ReqGetKline")
        self.assertEqual(value["params"]["period_type"], 10101)
        self.assertEqual(
            list(value["params"]),
            [
                "security_code", "market_type", "cq_flag", "auto_complete",
                "period_type", "begin_date", "end_date", "begin_time",
                "end_time", "QueryBandWidth",
            ],
        )
        packets = [
            {
                "headers": {
                    "id": 1, "tag": 10101, "pack_num": 2, "all_pack_num": 2,
                },
                "status": 0,
                "data": ["510300,101,20260821,100,110,90,105,200,300"],
            },
            {
                "headers": {
                    "id": 1, "tag": 10101, "pack_num": 1, "all_pack_num": 2,
                },
                "status": 0,
                "data": ["510300,101,20260814,99,111,89,104,201,301"],
            },
        ]
        rows = parse_kline_packets(packets, expected_tag=10101)
        self.assertEqual([row["kline_time"] for row in rows], [20260814, 20260821])
        for row in rows:
            self.assertEqual(row["orig_time"], 0)
            self.assertEqual(row["variety_category"], 0)
            self.assertEqual(row["market_type"], 101)

    def test_month_kline_maps_to_verified_wire_period_and_tag(self):
        # Captured from an authorized official Linux SDK session on 2026-08-26:
        # public cyc_type=10010 is transmitted as period_type=10102 and the
        # response packets carry tag=10102 with the daily CSV contract.
        self.assertEqual(kline_wire_period(10010), 10102)
        request = ReqKline().set_code("510300")
        request.market_type = 101
        request.cq_flag = 0
        request.cyc_type = 10010
        request.begin_date = 20260701
        request.end_date = 20260731
        raw = build_kline_request("user", "token", 1, request)
        value = json.loads(raw)
        self.assertEqual(value["method"], "ReqGetKline")
        self.assertEqual(value["params"]["period_type"], 10102)
        self.assertEqual(
            list(value["params"]),
            [
                "security_code", "market_type", "cq_flag", "auto_complete",
                "period_type", "begin_date", "end_date", "begin_time",
                "end_time", "QueryBandWidth",
            ],
        )
        packets = [
            {
                "headers": {
                    "id": 1, "tag": 10102, "pack_num": 3, "all_pack_num": 3,
                },
                "status": 0,
                "data": ["510300,101,20260731,100,110,90,105,200,300"],
            },
            {
                "headers": {
                    "id": 1, "tag": 10102, "pack_num": 1, "all_pack_num": 3,
                },
                "status": 0,
                "data": ["510300,101,20260529,98,109,88,103,199,299"],
            },
            {
                "headers": {
                    "id": 1, "tag": 10102, "pack_num": 2, "all_pack_num": 3,
                },
                "status": 0,
                "data": ["510300,101,20260630,97,108,87,102,198,298"],
            },
        ]
        rows = parse_kline_packets(packets, expected_tag=10102)
        self.assertEqual(
            [row["kline_time"] for row in rows],
            [20260529, 20260630, 20260731],
        )
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(row["orig_time"], 0)
            self.assertEqual(row["variety_category"], 0)
            self.assertEqual(row["market_type"], 101)
            self.assertEqual(row["security_code"], "510300")
            self.assertEqual(len(row), 11)
            self.assertEqual(sorted(row), sorted([
                "market_type", "security_code", "orig_time", "kline_time",
                "open_price", "high_price", "low_price", "close_price",
                "volume_trade", "value_trade", "variety_category",
            ]))
            for key, value in row.items():
                if key != "security_code":
                    self.assertIsInstance(value, int)

    def test_season_kline_maps_to_verified_wire_period_and_tag(self):
        # Captured from an authorized official Linux SDK session on 2026-08-26:
        # public cyc_type=10011 is transmitted as period_type=10103 and the
        # response packets carry tag=10103 with the daily CSV contract.
        self.assertEqual(kline_wire_period(10011), 10103)
        request = ReqKline().set_code("510300")
        request.market_type = 101
        request.cq_flag = 0
        request.cq_date = 0
        request.qj_flag = 0
        request.cyc_type = 10011
        request.cyc_def = 0
        request.auto_complete = 1
        request.begin_date = 20260401
        request.end_date = 20260630
        raw = build_kline_request("user", "token", 1, request)
        value = json.loads(raw)
        self.assertEqual(value["method"], "ReqGetKline")
        self.assertEqual(value["params"]["period_type"], 10103)
        self.assertEqual(
            list(value["params"]),
            [
                "security_code", "market_type", "cq_flag", "auto_complete",
                "period_type", "begin_date", "end_date", "begin_time",
                "end_time", "QueryBandWidth",
            ],
        )
        packets = [
            {
                "headers": {
                    "id": 1, "tag": 10103, "pack_num": 2, "all_pack_num": 2,
                },
                "status": 0,
                "data": ["510300,101,20260630,100,110,90,105,200,300"],
            },
            {
                "headers": {
                    "id": 1, "tag": 10103, "pack_num": 1, "all_pack_num": 2,
                },
                "status": 0,
                "data": ["510300,101,20260331,99,111,89,104,201,301"],
            },
        ]
        rows = parse_kline_packets(packets, expected_tag=10103)
        self.assertEqual(
            [row["kline_time"] for row in rows], [20260331, 20260630]
        )
        for row in rows:
            self.assertEqual(row["orig_time"], 0)
            self.assertEqual(row["variety_category"], 0)
            self.assertEqual(row["market_type"], 101)
            self.assertEqual(row["security_code"], "510300")
            self.assertEqual(len(row), 11)
            self.assertEqual(sorted(row), sorted([
                "market_type", "security_code", "orig_time", "kline_time",
                "open_price", "high_price", "low_price", "close_price",
                "volume_trade", "value_trade", "variety_category",
            ]))
            for key, value in row.items():
                if key != "security_code":
                    self.assertIsInstance(value, int)

    def test_year_kline_maps_to_verified_wire_period_and_tag(self):
        # Captured from an authorized official Linux SDK session on 2026-08-26:
        # public cyc_type=10012 is transmitted as period_type=10104 and the
        # response packets carry tag=10104 with the daily CSV contract.
        self.assertEqual(kline_wire_period(10012), 10104)
        request = ReqKline().set_code("510300")
        request.market_type = 101
        request.cq_flag = 0
        request.cq_date = 0
        request.qj_flag = 0
        request.cyc_type = 10012
        request.cyc_def = 0
        request.auto_complete = 1
        request.begin_date = 20250101
        request.end_date = 20251231
        raw = build_kline_request("user", "token", 1, request)
        value = json.loads(raw)
        self.assertEqual(value["method"], "ReqGetKline")
        self.assertEqual(value["params"]["period_type"], 10104)
        self.assertEqual(
            list(value["params"]),
            [
                "security_code", "market_type", "cq_flag", "auto_complete",
                "period_type", "begin_date", "end_date", "begin_time",
                "end_time", "QueryBandWidth",
            ],
        )
        packets = [
            {
                "headers": {
                    "id": 1, "tag": 10104, "pack_num": 2, "all_pack_num": 2,
                },
                "status": 0,
                "data": ["510300,101,20251231,100,110,90,105,200,300"],
            },
            {
                "headers": {
                    "id": 1, "tag": 10104, "pack_num": 1, "all_pack_num": 2,
                },
                "status": 0,
                "data": ["510300,101,20250102,99,111,89,104,201,301"],
            },
        ]
        rows = parse_kline_packets(packets, expected_tag=10104)
        self.assertEqual(
            [row["kline_time"] for row in rows], [20250102, 20251231]
        )
        for row in rows:
            self.assertEqual(row["orig_time"], 0)
            self.assertEqual(row["variety_category"], 0)
            self.assertEqual(row["market_type"], 101)
            self.assertEqual(row["security_code"], "510300")
            self.assertEqual(len(row), 11)
            self.assertEqual(sorted(row), sorted([
                "market_type", "security_code", "orig_time", "kline_time",
                "open_price", "high_price", "low_price", "close_price",
                "volume_trade", "value_trade", "variety_category",
            ]))
            for key, value in row.items():
                if key != "security_code":
                    self.assertIsInstance(value, int)

    def test_kline_rejects_unverified_cycles(self):
        # One-minute 10000 plus daily 10008, weekly 10009, monthly 10010,
        # seasonal 10011 and yearly 10012 are wire-proven. Every other public
        # cycle must fail loudly instead of reusing a verified wire enum.
        for cyc_type in (10001, 10007, 9999):
            request = ReqKline().set_code("510300")
            request.cyc_type = cyc_type
            with self.assertRaisesRegex(
                NotImplementedError, f"cyc_type={cyc_type}"
            ):
                build_kline_request("user", "token", 1, request)
        self.assertEqual(
            sorted(_protocol.VERIFIED_KLINE_WIRE_TYPES),
            [10000, 10008, 10009, 10010, 10011, 10012],
        )

    def test_kline_response_rejects_wrong_period_tag(self):
        request_packets = [{
            "headers": {"id": 1, "tag": 99999, "pack_num": 1, "all_pack_num": 1},
            "status": 0,
            "data": ["510300,101,20260825,100,110,90,105,200,300"],
        }]
        with self.assertRaisesRegex(Exception, "tag"):
            parse_kline_packets(request_packets, expected_tag=10101)

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
        rows, error_code = parse_snapshot_packets(packets)
        self.assertIsNone(error_code)
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

    def test_snapshot_rejects_unverified_level_market_or_code(self):
        request = ReqDefault().set_code("159518")
        request.market_type = 102
        request.level_type = 1
        with self.assertRaisesRegex(NotImplementedError, "level_type"):
            build_snapshot_request("user", "token", 1, request)

        request.level_type = 0
        request.market_type = 101
        with self.assertRaisesRegex(NotImplementedError, "SZSE ETF 159518"):
            build_snapshot_request("user", "token", 1, request)

        request.market_type = 102
        request.set_code("159001")
        with self.assertRaisesRegex(NotImplementedError, "SZSE ETF 159518"):
            build_snapshot_request("user", "token", 1, request)

    def test_snapshot_error_frame_maps_to_public_data_empty(self):
        # Captured empty-query wire frame (2026-08-26, authorized Linux SDK):
        # string tag "DataEmpty", wire-generic status=-100, pack counters 0 and
        # an empty string data payload. The official SDK surfaces -76.
        packets = [{
            "headers": {"id": 1, "tag": "DataEmpty", "pack_num": 0, "all_pack_num": 0},
            "status": -100,
            "data": "",
        }]
        rows, error_code = parse_snapshot_packets(packets)
        self.assertEqual(rows, [])
        self.assertEqual(error_code, -76)

    def test_snapshot_rejects_unobserved_error_shapes(self):
        unknown_tag = [{
            "headers": {"id": 1, "tag": "SomeError", "pack_num": 0, "all_pack_num": 0},
            "status": -100,
            "data": "",
        }]
        with self.assertRaisesRegex(Exception, "error tag"):
            parse_snapshot_packets(unknown_tag)

        packed = "|".join(["1"] * 10)
        data_frame = {
            "headers": {"id": 1, "tag": SNAPSHOT_WIRE_TAG,
                        "pack_num": 1, "all_pack_num": 1},
            "status": 0,
            "data": [",".join(["000001", "101"] + ["1"] * 8 + [packed] * 4 + ["1"] * 22)],
        }
        error_frame = {
            "headers": {"id": 1, "tag": "DataEmpty", "pack_num": 0, "all_pack_num": 0},
            "status": -100,
            "data": "",
        }
        with self.assertRaisesRegex(Exception, "mixes"):
            parse_snapshot_packets([data_frame, error_frame])

        # Defensive branch: two *mapped* error frames must agree on one code.
        # A second real wire tag has not been captured yet, so the mapping is
        # extended synthetically for this unit test only.
        with patch.dict(_protocol.SNAPSHOT_ERROR_TAGS, {"SyntheticError": -88}):
            conflicting = [
                {
                    "headers": {"id": 1, "tag": "DataEmpty",
                                "pack_num": 0, "all_pack_num": 0},
                    "status": -100, "data": "",
                },
                {
                    "headers": {"id": 1, "tag": "SyntheticError",
                                "pack_num": 0, "all_pack_num": 0},
                    "status": -100, "data": "",
                },
            ]
            with self.assertRaisesRegex(Exception, "distinct error"):
                parse_snapshot_packets(conflicting)
            agreeing = [
                {
                    "headers": {"id": 1, "tag": "DataEmpty",
                                "pack_num": 0, "all_pack_num": 0},
                    "status": -100, "data": "",
                },
                {
                    "headers": {"id": 1, "tag": "DataEmpty",
                                "pack_num": 0, "all_pack_num": 0},
                    "status": -100, "data": "",
                },
            ]
            rows, error_code = parse_snapshot_packets(agreeing)
            self.assertEqual(rows, [])
            self.assertEqual(error_code, -76)

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

        rows, error_code = parse_snapshot_packets([
            packet(2, 2, [row_b]), packet(1, 2, [row_a]),
        ])
        self.assertIsNone(error_code)
        self.assertEqual([item["security_code"] for item in rows], ["000001", "000002"])

        cases = [
            ([packet(1, 2, [row_a])], "incomplete"),
            ([packet(1, 1, [row_b]), packet(1, 1, [row_a])], "duplicate"),
            ([{
                "headers": {"id": 7, "tag": 999, "pack_num": 1, "all_pack_num": 1},
                "status": 0, "data": [row_a],
            }], "tag"),
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

    def test_zstd_bulk_push_decodes_backtick_delimited_object_stream(self):
        decoded = (
            b'{"headers":{"tag":"14"},"status":0,"is_delta":0,'
            b'"data":{"1":102,"2":"159866"}}`'
            b'{"headers":{"tag":"14"},"status":0,"is_delta":1,'
            b'"data":{"2":"164824","10":1312000}}\x00'
        )
        with patch.object(_protocol, "_decompress_zstd", return_value=decoded):
            value = decode_server_payload(b"Y" + ZSTD_MAGIC + b"fixture")
        self.assertIsInstance(value, DecodedMessageBatch)
        self.assertEqual(len(value.messages), 2)
        self.assertEqual(value.messages[0]["data"]["2"], "159866")
        self.assertEqual(value.messages[1]["data"]["2"], "164824")

    def test_bulk_push_dispatches_each_object_as_an_individual_raw_event(self):
        decoded = (
            b'{"headers":{"tag":"14"},"status":0,"is_delta":0,'
            b'"data":{"2":"159866"}}`'
            b'{"headers":{"tag":"14"},"status":0,"is_delta":0,'
            b'"data":{"2":"164824"}}\x00'
        )
        client = _protocol.TgwWssClient()
        with patch.object(_protocol, "_decompress_zstd", return_value=decoded):
            client._dispatch_payload(b"Y" + ZSTD_MAGIC + b"fixture")
        self.assertEqual(client.recv_event(timeout=0.01)["data"]["2"], "159866")
        self.assertEqual(client.recv_event(timeout=0.01)["data"]["2"], "164824")

    def test_object_stream_rejects_unobserved_separator(self):
        with self.assertRaisesRegex(_protocol.TgwProtocolError, "separated"):
            decode_server_payload(b'{"status":0}|{"status":0}')


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


class PublicContractTests(unittest.TestCase):
    def test_unimplemented_spi_surfaces_fail_explicitly(self):
        with self.assertRaisesRegex(NotImplementedError, "typed push SPI"):
            interface.Subscribe(SubscribeItem(), object())
        with self.assertRaisesRegex(NotImplementedError, "asynchronous query SPI"):
            interface.QueryKline(ReqKline(), object(), return_df_format=False)

    def test_error_code_table_matches_official_values(self):
        # Values cross-checked against the official V1.0.9.2 Python wheel.
        self.assertEqual(tgw.ErrorCode.kSuccess, 0)
        self.assertEqual(tgw.ErrorCode.kFailure, -100)
        self.assertEqual(tgw.ErrorCode.kDataEmpty, -76)
        self.assertEqual(tgw.ErrorCode.kNonQueryTimePeriod, -88)
        self.assertEqual(tgw.ErrorCode.kTimeout, -83)
        self.assertEqual(tgw.ErrorCode.kOverMaxQueryLimit, -78)
        self.assertEqual(interface.GetErrorMsg(0), "成功")
        self.assertEqual(interface.GetErrorMsg(-76), "数据为空")
        self.assertEqual(interface.GetErrorMsg(-88), "非查询时间段(非查询时间段不支持查询)")
        self.assertEqual(interface.GetErrorMsg(-12345), "unknown error code")

    @staticmethod
    def _verified_one_minute_row(**overrides):
        row = {
            "market_type": 102,
            "security_code": "159691",
            "orig_time": 0,
            "kline_time": 202608260930,
            "open_price": 2_500_000,
            "high_price": 2_750_000,
            "low_price": 2_250_000,
            "close_price": 2_625_000,
            "volume_trade": 123_400,
            "value_trade": 250_012_345,
            "variety_category": 0,
        }
        row.update(overrides)
        return row

    @staticmethod
    def _verified_one_minute_request():
        request = ReqKline().set_code("159691")
        request.market_type = 102
        request.cq_flag = 0
        request.cq_date = 0
        request.qj_flag = 0
        request.cyc_type = 10000
        request.cyc_def = 0
        request.auto_complete = 1
        request.begin_date = 20260826
        request.end_date = 20260826
        request.begin_time = 900
        request.end_time = 1500
        return request

    def test_verified_one_minute_normalizer_emits_explicit_exact_units(self):
        raw_row = self._verified_one_minute_row()
        normalized = normalize_verified_159691_szse_one_minute_kline_rows([raw_row])
        self.assertEqual(normalized, [{
            "market_type": 102,
            "security_code": "159691",
            "orig_time": 0,
            "kline_time": 202608260930,
            "open_price_yuan": Decimal("2.5"),
            "high_price_yuan": Decimal("2.75"),
            "low_price_yuan": Decimal("2.25"),
            "close_price_yuan": Decimal("2.625"),
            "volume_shares": 1234,
            "value_trade_yuan": Decimal("2500.12345"),
            "variety_category": 0,
            "raw_open_price": 2_500_000,
            "raw_high_price": 2_750_000,
            "raw_low_price": 2_250_000,
            "raw_close_price": 2_625_000,
            "raw_volume_trade": 123_400,
            "raw_value_trade": 250_012_345,
        }])
        # The public top-level re-export has the same exact, non-float result.
        self.assertEqual(
            tgw.NormalizeVerified159691SzseOneMinuteKlineRows([raw_row]),
            normalized,
        )

    def test_verified_one_minute_normalizer_rejects_out_of_scope_or_invalid_rows(self):
        for override, error in (
            ({"security_code": "000001"}, "security_code"),
            ({"kline_time": 202608270930}, "2026-08-26"),
            ({"volume_trade": 123_401}, "not divisible"),
            ({"high_price": 2_400_000}, "OHLC"),
        ):
            with self.subTest(override=override), self.assertRaisesRegex(
                (NotImplementedError, ValueError), error
            ):
                normalize_verified_159691_szse_one_minute_kline_rows([
                    self._verified_one_minute_row(**override)
                ])

    def test_query_kline_normalized_is_opt_in_and_prevalidates_scope(self):
        calls = []

        def query(kind, request_payload):
            calls.append((kind, request_payload))
            return [self._verified_one_minute_row()]

        self._install_fake_backend(query=query)
        request = self._verified_one_minute_request()
        normalized, error = interface.QueryKline(
            request, return_df_format=False, normalized=True
        )
        self.assertEqual(error, 0)
        self.assertEqual(normalized[0]["close_price_yuan"], Decimal("2.625"))
        self.assertEqual(normalized[0]["volume_shares"], 1234)
        self.assertEqual(calls[0][0], "kline")

        request.market_type = 101
        with self.assertRaisesRegex(NotImplementedError, "market_type=101"):
            interface.QueryKline(request, return_df_format=False, normalized=True)
        self.assertEqual(len(calls), 1)

    def _install_fake_backend(self, **methods):
        class FakeBackend:
            pass

        backend = FakeBackend()
        for name, method in methods.items():
            setattr(backend, name, method)
        previous = interface._g_backend
        interface._g_backend = backend
        self.addCleanup(setattr, interface, "_g_backend", previous)
        return backend

    def test_snapshot_sync_empty_returns_official_data_empty(self):
        calls = {}

        def query(kind, req):
            calls["kind"] = kind
            return [], -76

        self._install_fake_backend(query=query)
        request = ReqDefault().set_code("159518")
        rows, error = interface.QuerySnapshot(request, return_df_format=False)
        self.assertIsNone(rows)
        self.assertEqual(error, -76)
        self.assertEqual(calls["kind"], "snapshot")
        # DataFrame mode keeps the official (None, -76) shape on empty data.
        rows, error = interface.QuerySnapshot(request, return_df_format=True)
        self.assertIsNone(rows)
        self.assertEqual(error, -76)

    def test_snapshot_async_delivers_result_through_spi(self):
        prepared = ("prepared",)

        def build_query(kind, req):
            return prepared

        def run_query(value):
            assert value is prepared
            return [{"security_code": "159518"}], None

        deliveries = []
        done = threading.Event()

        class Collector:
            def __call__(self, result, err_code):
                deliveries.append((result, err_code))
                done.set()

        self._install_fake_backend(build_query=build_query, run_query=run_query)
        request = ReqDefault().set_code("159518")
        submitted, submit_err = interface.QuerySnapshot(
            request, query_spi=Collector(), return_df_format=False
        )
        self.assertIs(submitted, True)
        self.assertIsNone(submit_err)
        self.assertTrue(done.wait(timeout=5.0))
        self.assertEqual(deliveries, [([{"security_code": "159518"}], None)])

    def test_snapshot_async_maps_timeout_and_errors(self):
        import threading as threading_module

        from tgw_macos._protocol import TgwTimeoutError as ProtocolTimeout

        cases = [
            (ProtocolTimeout("slow"), -83),
            (RuntimeError("boom"), "boom"),
        ]
        for raised, expected_err in cases:
            with self.subTest(expected=expected_err):
                done = threading_module.Event()
                deliveries = []

                class Collector:
                    def __call__(self, result, err_code):
                        deliveries.append((result, err_code))
                        done.set()

                def run_query(_value):
                    raise raised

                self._install_fake_backend(
                    build_query=lambda kind, req: (), run_query=run_query
                )
                submitted, submit_err = interface.QuerySnapshot(
                    ReqDefault().set_code("159518"),
                    query_spi=Collector(),
                    return_df_format=False,
                )
                self.assertIs(submitted, True)
                self.assertIsNone(submit_err)
                self.assertTrue(done.wait(timeout=5.0))
                self.assertEqual(len(deliveries), 1)
                result, err = deliveries[0]
                self.assertIsNone(result)
                self.assertEqual(err, expected_err)

    def test_snapshot_async_empty_maps_to_data_empty_callback(self):
        done = threading.Event()
        deliveries = []

        class Collector:
            def __call__(self, result, err_code):
                deliveries.append((result, err_code))
                done.set()

        self._install_fake_backend(
            build_query=lambda kind, req: (),
            run_query=lambda _value: ([], -76),
        )
        submitted, _submit_err = interface.QuerySnapshot(
            ReqDefault().set_code("159518"),
            query_spi=Collector(),
            return_df_format=False,
        )
        self.assertIs(submitted, True)
        self.assertTrue(done.wait(timeout=5.0))
        self.assertEqual(deliveries, [(None, -76)])

    def test_packaged_vendor_ca_is_discoverable(self):
        ca_file = _backend._find_ca_file()
        self.assertIsNotNone(ca_file)
        self.assertEqual(Path(ca_file).name, "vendor-dgw-ca.crt")
        self.assertTrue(Path(ca_file).is_file())

    def test_raw_event_reader_surfaces_background_failure(self):
        class Client:
            @staticmethod
            def recv_event(timeout=None):
                return RuntimeError("reader stopped")

        class Backend:
            client = Client()

        previous = interface._g_backend
        interface._g_backend = Backend()
        self.addCleanup(setattr, interface, "_g_backend", previous)
        with self.assertRaisesRegex(RuntimeError, "push reader failed"):
            interface.ReceiveRawEvent(timeout=0.01)


if __name__ == "__main__":
    unittest.main()

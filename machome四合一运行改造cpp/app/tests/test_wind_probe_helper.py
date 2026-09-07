#!/usr/bin/env python3
import ctypes
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def receive(process):
    line = process.stdout.readline()
    assert line, process.stderr.read()
    return json.loads(line)


def raw_capture():
    fields = bytearray(struct.pack(">H", 4))
    names = ["etfbuynumber", "etfbuyamount", "etfsellnumber", "etfsellamount"]
    for index, name in enumerate(names):
        encoded = name.encode()
        fields += struct.pack(">H", len(encoded)) + encoded + b"\0"
        fields += bytes([0x27]) + struct.pack(">HQQ", 0, 8, 0)
        fields += struct.pack("<I", 0) + struct.pack(">HHH", index * 8, 0, index + 1)
    row = struct.pack("<qqqq", 1, 1_000_000, 2, 2_000_000)
    data = struct.pack(">III", 1, len(row), 0) + row
    return {
        "requested_windcode": "159518.SZ",
        "callback_epoch_ms": 1_788_488_660_000,
        "callback_seq": 9,
        "sub_id": 7,
        "error_code": 0,
        "field_info": {"hex": fields.hex()},
        "buffer_58": {"hex": data.hex()},
    }


def main():
    binary = sys.argv[1]
    probe_path = Path(binary).with_name("libmachome_wind_tbapi_probe.dylib")
    assert probe_path.is_file()
    probe = ctypes.CDLL(str(probe_path))
    probe.wind_tbapi_set_output_dir.argtypes = [ctypes.c_char_p]
    probe.wind_tbapi_set_output_dir.restype = ctypes.c_longlong
    probe.wind_tbapi_subscribe.argtypes = [ctypes.c_char_p, ctypes.c_int]
    probe.wind_tbapi_subscribe.restype = ctypes.c_longlong
    assert probe.wind_tbapi_set_output_dir(b"relative") == -21001
    assert probe.wind_tbapi_subscribe(b"bad", 500) == -20001
    with tempfile.TemporaryDirectory() as root:
        captures = Path(root) / "captures"
        captures.mkdir()
        (captures / "one.json").write_text(json.dumps({
            "windcode": "159518.SZ",
            "observed_at": "2026-09-04T09:31:00+08:00",
            "values": {"etfbuynumber": 1, "etfbuyamount": 1000000,
                       "etfsellnumber": 0, "etfsellamount": 0},
        }), encoding="utf-8")
        (captures / "two.json").write_text(json.dumps(raw_capture()), encoding="utf-8")
        process = subprocess.Popen(
            [binary, "--stdio", "--mode", "fixture", "--data-root", root],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        hello = receive(process)
        assert hello["type"] == "hello" and hello["protocol"] == 1
        assert hello["poll_interval_ms"] == 1000
        status = receive(process)
        assert status["type"] == "status" and status["abi_verified"] is True
        process.stdin.write(json.dumps({
            "action": "warmup", "request_id": "warmup-1",
            "symbols": ["159518.SZ"],
        }) + "\n")
        process.stdin.flush()
        warmed = receive(process)
        assert warmed["type"] == "command_result" and warmed["ok"] is True
        assert warmed["request_id"] == "warmup-1"
        assert warmed["details"]["warmup_completed"] is True
        assert warmed["details"]["unsubscribe_confirmed"] is True
        process.stdin.write('{"action":"subscribe","symbols":["159518.SZ"]}\n')
        process.stdin.flush()
        subscribed = receive(process)
        assert subscribed["state"] == "subscribed"
        capture = receive(process)
        assert capture["type"] == "capture"
        assert capture["payload"]["windcode"] == "159518.SZ"
        decoded = receive(process)
        assert decoded["type"] == "capture"
        assert decoded["payload"]["values"]["etfsellamount"] == 2_000_000
        assert decoded["payload"]["callback_seq"] == 9
        process.stdin.write('{"action":"unsubscribe","request_id":"stop-1"}\n')
        process.stdin.flush()
        stopped = receive(process)
        assert stopped["type"] == "status" and stopped["unsubscribed"] is True
        assert stopped["request_id"] == "stop-1"
        stop_result = receive(process)
        assert stop_result["type"] == "command_result" and stop_result["ok"] is True
        assert stop_result["details"]["unsubscribe_confirmed"] is True
        process.stdin.write('{"action":"shutdown_wind"}\n')
        process.stdin.flush()
        failure = receive(process)
        assert failure["code"] == "wind_control_disabled"
        process.stdin.write('{"action":"quit"}\n')
        process.stdin.flush()
        assert process.wait(timeout=3) == 0

        blocked = subprocess.Popen(
            [binary, "--stdio", "--mode", "live", "--data-root", root],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        receive(blocked)  # hello
        status = receive(blocked)
        assert status["state"] == "blocked" and status["abi_verified"] is False
        blocked.stdin.write(json.dumps({
            "action": "warmup", "request_id": "blocked-warmup",
            "symbols": ["159518.SZ"],
        }) + "\n")
        blocked.stdin.flush()
        warmup_rejection = receive(blocked)
        assert warmup_rejection["type"] == "error"
        assert warmup_rejection["action"] == "warmup"
        assert warmup_rejection["request_id"] == "blocked-warmup"
        assert warmup_rejection["fail_closed"] is True
        blocked.stdin.write('{"action":"subscribe","symbols":["159518.SZ"]}\n')
        blocked.stdin.flush()
        rejection = receive(blocked)
        assert rejection["type"] == "error" and rejection["fail_closed"] is True
        blocked.stdin.write('{"action":"quit"}\n')
        blocked.stdin.flush()
        assert blocked.wait(timeout=3) == 0


if __name__ == "__main__":
    main()

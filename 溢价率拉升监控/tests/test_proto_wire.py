from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapter"))

from proto_wire import BridgeFrame, decode, encode, encode_framed, take_frames


class ProtoWireTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        source = BridgeFrame(kind=1, sequence=99, session_id="abc", receive_wall_ns=123,
                             receive_monotonic_ns=456, is_delta=True, tag="14",
                             payload_json=b'{"data":{"1":"159518"}}', sdk_queue_depth=7)
        result = decode(encode(source))
        self.assertEqual(result, source)

    def test_partial_length_prefix(self) -> None:
        payload = encode_framed(BridgeFrame(kind=3, message="set_symbols"))
        buffer = bytearray(payload[:3])
        self.assertEqual(list(take_frames(buffer)), [])
        buffer.extend(payload[3:])
        frames = list(take_frames(buffer))
        self.assertEqual(frames[0].message, "set_symbols")
        self.assertEqual(buffer, bytearray())

    def test_rejects_oversize(self) -> None:
        with self.assertRaises(ValueError):
            list(take_frames(bytearray(struct.pack(">I", 99)), maximum=10))


if __name__ == "__main__":
    unittest.main()

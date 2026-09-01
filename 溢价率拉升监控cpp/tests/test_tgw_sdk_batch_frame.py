from __future__ import annotations

import unittest
from unittest.mock import patch

import tgw_macos
from tgw_macos import _protocol


class TgwSdkBatchFrameTests(unittest.TestCase):
    def test_project_uses_sdk_with_native_bulk_frame_support(self) -> None:
        self.assertEqual(tgw_macos.__version__, "1.0.9.2.macos.re6")
        decoded = (
            b'{"headers":{"tag":"14"},"status":0,"is_delta":0,'
            b'"data":{"2":"159866"}}`'
            b'{"headers":{"tag":"14"},"status":0,"is_delta":0,'
            b'"data":{"2":"164824"}}\x00'
        )
        client = _protocol.TgwWssClient()
        with patch.object(_protocol, "_decompress_zstd", return_value=decoded):
            client._dispatch_payload(b"Y" + _protocol.ZSTD_MAGIC + b"fixture")
        self.assertEqual(client.recv_event(timeout=0.01)["data"]["2"], "159866")
        self.assertEqual(client.recv_event(timeout=0.01)["data"]["2"], "164824")


if __name__ == "__main__":
    unittest.main()

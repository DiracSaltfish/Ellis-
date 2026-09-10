from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HELPER = Path(sys.argv[1]).resolve()
del sys.argv[1]


class HelperProtocolTest(unittest.TestCase):
    def test_fixture_transport_and_shutdown_do_not_need_browser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [str(HELPER), "--profile", directory,
                 "--ticker-id", "sanitized", "--symbol", "XOP"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            )
            assert process.stdin and process.stdout
            hello = json.loads(process.stdout.readline())
            self.assertEqual(hello["protocol"], "machome.webull.raw.v1")
            event = {"type": "raw_http", "captured_at": "2026-09-04T01:31:00Z",
                     "body": {"depth": {"ntvAggBidList": [{"price":"1","volume":"2"}],
                                         "ntvAggAskList": [{"price":"2","volume":"3"}]}}}
            process.stdin.write(json.dumps({"command":"fixture_event","arguments":{"event":event}})+"\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline()), event)
            self.assertTrue(json.loads(process.stdout.readline())["ok"])
            process.stdin.write('{"command":"shutdown","arguments":{}}\n')
            process.stdin.flush()
            lines = [json.loads(process.stdout.readline()), json.loads(process.stdout.readline())]
            self.assertEqual(lines[-1]["command"], "shutdown")
            self.assertEqual(process.wait(timeout=3), 0)
            process.stdin.close()
            process.stdout.close()

    def test_is_native_executable(self) -> None:
        self.assertTrue(HELPER.is_file())
        result = subprocess.run(["/usr/bin/file", str(HELPER)], capture_output=True, text=True, check=True)
        self.assertIn("Mach-O", result.stdout)

    def test_helper_failure_is_confined_to_helper_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [str(HELPER), "--profile", directory,
                 "--ticker-id", "sanitized", "--symbol", "XOP"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            )
            assert process.stdin and process.stdout
            self.assertEqual(json.loads(process.stdout.readline())["type"], "hello")
            process.stdin.write('{"command":"crash_for_test","arguments":{}}\n')
            process.stdin.flush()
            acknowledgment = json.loads(process.stdout.readline())
            self.assertTrue(acknowledgment["ok"])
            self.assertEqual(acknowledgment["command"], "crash_for_test")
            self.assertEqual(process.wait(timeout=3), 86)
            process.stdin.close()
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from concurrent.futures import Future
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

import webull_control_runner as runner


TOKEN = b"test-control-token-0123456789-abcdef"


class _ImmediateSignal:
    def __init__(self) -> None:
        self.calls: list[runner.ControlInvocation] = []

    def emit(self, invocation: runner.ControlInvocation) -> None:
        self.calls.append(invocation)
        invocation.future.set_result(
            {
                "ok": True,
                "request_id": invocation.request_id,
                "action": invocation.action,
                "schedule_mode": invocation.arguments.get("mode", "auto"),
                "collector_running": invocation.action != "collector_stop",
                "status": {"data": "flowing"},
            }
        )


class _ImmediateDispatcher:
    def __init__(self) -> None:
        self.requested = _ImmediateSignal()


class _NeverSignal:
    def emit(self, _invocation: runner.ControlInvocation) -> None:
        return


class _NeverDispatcher:
    def __init__(self) -> None:
        self.requested = _NeverSignal()


class ControlHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = _ImmediateDispatcher()
        broker = runner.ControlBroker(self.dispatcher, timeout_seconds=1.0)
        self.server = runner.LoopbackControlServer(("127.0.0.1", 0), TOKEN, broker)
        self.server_thread = runner.ControlServerThread(self.server)
        self.server_thread.start()
        self.port = int(self.server.server_address[1])

    def tearDown(self) -> None:
        self.server_thread.stop()

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        token: bytes | None = TOKEN,
    ) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token.decode("ascii")
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response_body = json.loads(response.read())
        status = response.status
        connection.close()
        return status, response_body

    def test_auth_health_and_capabilities(self) -> None:
        status, body = self.request("GET", "/v1/health", token=None)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "AUTH_REJECTED")

        status, body = self.request("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["service"], "webull-control-runner")

        status, body = self.request("GET", "/v1/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(set(body["actions"]), set(runner.ALLOWED_ACTIONS))

    def test_all_actions_and_idempotency(self) -> None:
        actions = (
            ("set_schedule_mode", {"mode": "force_running"}),
            ("collector_start", {}),
            ("collector_stop", {}),
            ("open_login", {}),
            ("restart_browser", {}),
        )
        first_payload: dict[str, object] | None = None
        first_result: dict[str, object] | None = None
        for index, (action, arguments) in enumerate(actions):
            payload = {
                "request_id": f"smoke-{index:04d}",
                "action": action,
                "arguments": arguments,
            }
            status, result = self.request("POST", "/v1/commands", payload)
            self.assertEqual(status, 200)
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], action)
            if index == 0:
                first_payload = payload
                first_result = result

        self.assertEqual(len(self.dispatcher.requested.calls), len(actions))
        assert first_payload is not None and first_result is not None
        status, duplicate = self.request("POST", "/v1/commands", first_payload)
        self.assertEqual(status, 200)
        self.assertEqual(duplicate, first_result)
        self.assertEqual(len(self.dispatcher.requested.calls), len(actions))

        conflict = dict(first_payload)
        conflict["action"] = "collector_stop"
        conflict["arguments"] = {}
        status, body = self.request("POST", "/v1/commands", conflict)
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "IDEMPOTENCY_CONFLICT")

    def test_rejects_invalid_envelopes(self) -> None:
        status, body = self.request(
            "POST",
            "/v1/commands",
            {"request_id": "short", "action": "collector_start"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "INVALID_REQUEST_ID")

        status, body = self.request(
            "POST",
            "/v1/commands",
            {"request_id": "smoke-9999", "action": "shell", "arguments": {}},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "UNSUPPORTED_ACTION")

        status, body = self.request(
            "POST",
            "/v1/commands",
            {
                "request_id": "smoke-9998",
                "action": "set_schedule_mode",
                "arguments": {"mode": "sometimes"},
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "INVALID_ARGUMENT")

        status, body = self.request(
            "POST",
            "/v1/commands",
            {
                "request_id": "smoke-9997",
                "action": "restart_browser",
                "arguments": {"shell": "ignored-no-more"},
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "INVALID_ARGUMENTS")


class TokenFileTest(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "geteuid"), "Unix permission contract")
    def test_secure_token_file_contract(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            os.chmod(directory, 0o700)
            path = directory / "control.token"
            token = runner._safe_token_file(path, create=True)
            self.assertGreaterEqual(len(token), 32)
            self.assertEqual(path.stat().st_mode & 0o7777, 0o600)

            os.chmod(path, 0o644)
            with self.assertRaisesRegex(ValueError, "0600"):
                runner._safe_token_file(path, create=False)


class ControlBrokerTest(unittest.TestCase):
    def test_dispatch_timeout_is_explicitly_uncertain(self) -> None:
        broker = runner.ControlBroker(_NeverDispatcher(), timeout_seconds=0.01)
        status, result = broker.submit("timeout-test-0001", "restart_browser", {})
        self.assertEqual(status, 504)
        self.assertEqual(result["error"], "CONTROL_TIMEOUT")

    def test_internal_control_failure_uses_server_error(self) -> None:
        class FailedSignal:
            def emit(self, invocation: runner.ControlInvocation) -> None:
                invocation.future.set_result(
                    {
                        "ok": False,
                        "request_id": invocation.request_id,
                        "action": invocation.action,
                        "error": "CONTROL_FAILED",
                        "message": "mock failure",
                        "outcome_uncertain": True,
                    }
                )

        class FailedDispatcher:
            requested = FailedSignal()

        broker = runner.ControlBroker(FailedDispatcher(), timeout_seconds=0.1)
        status, result = broker.submit("failure-test-0001", "restart_browser", {})
        self.assertEqual(status, 500)
        self.assertTrue(result["outcome_uncertain"])


class QtDispatcherTest(unittest.TestCase):
    def test_worker_signal_executes_runtime_on_qt_owner_thread(self) -> None:
        try:
            from PyQt6.QtCore import QCoreApplication
        except ImportError:
            self.skipTest("PyQt6 is not installed in this interpreter")

        application = QCoreApplication.instance() or QCoreApplication([])
        owner_thread = threading.get_ident()

        class Collector:
            running = False

            def __init__(self) -> None:
                self.show_calls = 0

            def show_browser(self) -> None:
                self.show_calls += 1

        class Runtime:
            VALID_MODES = ("auto", "force_running", "force_stopped")

            def __init__(self) -> None:
                self.mode = "auto"
                self.collector = Collector()
                self.called_on: int | None = None

            def set_mode(self, mode: str) -> None:
                self.called_on = threading.get_ident()
                self.mode = mode

            def start_collector_now(self) -> None:
                self.called_on = threading.get_ident()

            def stop_collector_now(self) -> None:
                self.called_on = threading.get_ident()

            def open_login_window(self) -> None:
                self.called_on = threading.get_ident()
                self.mode = "force_running"
                self.collector.running = True

            def restart_browser(self) -> None:
                self.called_on = threading.get_ident()

        class Status:
            def to_dict(self) -> dict[str, object]:
                return {"data": "flowing"}

        class Store:
            def status(self) -> Status:
                return Status()

        runtime = Runtime()
        dispatcher = runner.build_dispatcher(runtime, Store())
        future: Future[dict[str, object]] = Future()
        invocation = runner.ControlInvocation(
            "thread-check-0001",
            "set_schedule_mode",
            {"mode": "force_running"},
            future,
        )
        sender = threading.Thread(target=dispatcher.requested.emit, args=(invocation,))
        sender.start()
        sender.join(timeout=1)
        deadline = time.monotonic() + 2
        while not future.done() and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.001)

        self.assertTrue(future.done())
        self.assertEqual(runtime.called_on, owner_thread)
        self.assertEqual(future.result()["schedule_mode"], "force_running")

        login_future: Future[dict[str, object]] = Future()
        login_invocation = runner.ControlInvocation(
            "thread-check-0002",
            "open_login",
            {},
            login_future,
        )
        login_sender = threading.Thread(
            target=dispatcher.requested.emit,
            args=(login_invocation,),
        )
        login_sender.start()
        login_sender.join(timeout=1)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and (
            not login_future.done() or runtime.collector.show_calls == 0
        ):
            application.processEvents()
            time.sleep(0.005)
        self.assertTrue(login_future.done())
        self.assertGreaterEqual(runtime.collector.show_calls, 1)
        self.assertEqual(runtime.called_on, owner_thread)


if __name__ == "__main__":
    unittest.main()

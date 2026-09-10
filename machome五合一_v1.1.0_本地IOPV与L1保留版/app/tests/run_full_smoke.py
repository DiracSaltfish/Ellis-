#!/usr/bin/env python3
"""Start all mock services, the Hub Agent and the Qt UI, then verify contracts."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


APP = Path(__file__).resolve().parents[1]
BUILD = Path(os.environ.get("MACHOME_HUB_BUILD_DIR", APP / "build")).resolve()
AGENT_BINARY = Path(
    os.environ.get("MACHOME_HUB_AGENT_BINARY", BUILD / "machome-hub-agent")
).resolve()
UI_BINARY = Path(
    os.environ.get(
        "MACHOME_HUB_UI_BINARY",
        BUILD
        / "Machome 四合一运行中心.app"
        / "Contents"
        / "MacOS"
        / "Machome 四合一运行中心",
    )
).resolve()
ROOT = Path("/private/tmp/machome-hub-local-test")
CONFIG = APP.parent / "config" / "modules.local-test.json"
SOCKET = ROOT / "agent.sock"
CONFIG_SHA = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
AGENT_INSTANCE = ""
APPROVAL_ACTIONS = {
    "acknowledge_uncertain", "webull_set_mode", "webull_collector_start",
    "webull_collector_stop", "webull_restart_browser", "webull_show_login",
    "redemption_monitor_stop", "redemption_wind_shutdown_cleanup",
    "redemption_qmt_disconnect", "redemption_qmt_order",
}


def frame(value: dict) -> bytes:
    payload = json.dumps(value, separators=(",", ":")).encode()
    return struct.pack("!I", len(payload)) + payload


def receive(sock: socket.socket, deadline: float) -> dict:
    sock.settimeout(max(0.1, deadline - time.monotonic()))
    header = b""
    while len(header) < 4:
        header += sock.recv(4 - len(header))
    length = struct.unpack("!I", header)[0]
    if not 0 < length <= 1024 * 1024:
        raise RuntimeError(f"invalid frame length {length}")
    payload = b""
    while len(payload) < length:
        payload += sock.recv(length - len(payload))
    return json.loads(payload)


def wait_for(sock, predicate, seconds=8):
    deadline = time.monotonic() + seconds
    observed = []
    while time.monotonic() < deadline:
        try:
            message = receive(sock, deadline)
        except TimeoutError:
            break
        observed.append(message)
        if predicate(message):
            return message, observed
    compact = [
        item if item.get("module_id") == "webull" else
        {key: item.get(key) for key in ("type", "module_id", "event", "work_state", "headline", "message") if key in item}
        for item in observed[-30:]
    ]
    raise TimeoutError(f"expected Hub message was not observed; observed={compact}")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def command(sock, revisions: dict[str, int], module: str, action: str, arguments=None, seconds=8):
    global AGENT_INSTANCE
    command_id = f"smoke-{module}-{action}-{time.time_ns()}"
    request = {
        "schema_version": 1, "protocol": "module.control.v1", "type": "command", "command_id": command_id,
        "module_id": module, "action": action, "arguments": arguments or {},
        "expected_revision": revisions.get(module, -1), "deadline_ms": int(seconds * 1000),
        "requested_at": now(), "requested_by": "automated-smoke", "reason": "local_acceptance_test",
        "agent_instance_id": AGENT_INSTANCE, "config_sha256": CONFIG_SHA,
    }
    if action in APPROVAL_ACTIONS:
        approval_id = f"approval-{time.time_ns()}"
        approval = dict(request)
        approval.pop("command_id")
        approval["type"] = "approval_request"
        approval["request_id"] = approval_id
        sock.sendall(frame(approval))
        ticket, _ = wait_for(sock, lambda m: m.get("type") == "approval_ticket"
                             and m.get("request_id") == approval_id, 3)
        request["approval_token"] = ticket["approval_token"]
    sock.sendall(frame(request))
    final, observed = wait_for(sock, lambda m: m.get("type") == "command_result"
                               and m.get("command_id") == command_id
                               and m.get("state") in {"succeeded", "failed", "timed_out"}, seconds + 2)
    if final["state"] != "succeeded":
        compact = [
            item if item.get("module_id") == module else
            {key: item.get(key) for key in ("type", "module_id", "event", "state", "message") if key in item}
            for item in observed[-20:]
        ]
        raise AssertionError(f"{module}/{action} failed: {final}; observed={compact}")
    assert final.get("schema_version") == 1 and final.get("protocol") == "module.control.v1", final
    assert final.get("instance_id") and final.get("message") and final.get("timestamp"), final
    if isinstance(final.get("control_revision"), int):
        revisions[module] = final["control_revision"]
    return final


def terminate(process: subprocess.Popen | None):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def main() -> int:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True, mode=0o700)
    mock = agent = ui = None
    try:
        mock = subprocess.Popen(
            [sys.executable, str(APP / "tests" / "mock_services.py")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if mock.stdout.readline().strip() != "READY":
            raise RuntimeError(f"mock services did not start: {mock.stderr.read()}")

        agent = subprocess.Popen(
            [str(AGENT_BINARY), "--config", str(CONFIG)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 5
        while not SOCKET.exists() and time.monotonic() < deadline:
            if agent.poll() is not None:
                raise RuntimeError(f"agent exited: {agent.stderr.read()}")
            time.sleep(0.05)
        if not SOCKET.exists():
            raise TimeoutError("agent socket was not created")

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(SOCKET))
        client.sendall(frame({"schema_version": 1, "protocol": "module.control.v1",
                              "type": "hello", "client": "smoke",
                              "client_instance_id": f"smoke-client-{time.time_ns()}",
                              "config_sha256": CONFIG_SHA, "timestamp": now()}))
        hello, _ = wait_for(client, lambda m: m.get("type") == "hello")
        global AGENT_INSTANCE
        AGENT_INSTANCE = hello["instance_id"]
        assert hello.get("version") == "1.1.0", hello
        assert hello.get("schema_version") == 1 and hello.get("protocol") == "module.control.v1", hello
        assert hello.get("client_config_bound") is True and hello.get("control_ready") is True, hello

        modules = set()
        revisions: dict[str, int] = {}
        deadline = time.monotonic() + 10
        while modules != {"upload", "premium", "webull", "redemption"} and time.monotonic() < deadline:
            message = receive(client, deadline)
            if message.get("type") == "snapshot":
                modules.add(message.get("module_id"))
                revisions[message["module_id"]] = message["control_revision"]
                assert message.get("schema_version") == 1
                assert message.get("protocol") == "module.status.v1"
                assert message.get("instance_id") == hello.get("instance_id")
        assert modules == {"upload", "premium", "webull", "redemption"}, modules

        # Prove that the Webull adapter has accepted an authoritative v2 book,
        # rather than merely proving that its worker thread exists.
        webull_ready, webull_observed = wait_for(
            client,
            lambda m: m.get("type") == "snapshot"
            and m.get("module_id") == "webull"
            and bool(m.get("payload", {}).get("telemetry", {}).get("book")),
            6,
        )
        assert webull_ready["payload"]["telemetry"]["book"]["sequence"] > 0, webull_observed[-20:]
        webull_status = webull_ready["payload"]["telemetry"]["status"]
        assert webull_status["service"] == "webull-lv2-gateway", webull_status
        assert webull_status["api_running"] is True, webull_status
        assert "webull_set_mode" in webull_ready["payload"]["capabilities"]["commands"], webull_ready

        upload_ready, upload_observed = wait_for(
            client,
            lambda m: m.get("type") == "snapshot"
            and m.get("module_id") == "upload"
            and m.get("payload", {}).get("telemetry", {}).get("workers_expected") is True
            and m.get("payload", {}).get("telemetry", {}).get("worker_count") == 25
            and m.get("payload", {}).get("telemetry", {}).get("engine", {}).get("engine") == "native"
            and m.get("payload", {}).get("telemetry", {}).get("engine", {}).get("record_only") is True,
            6,
        )
        assert upload_ready["payload"]["work_state"] == "active", upload_observed[-20:]

        redemption_ready, redemption_observed = wait_for(
            client,
            lambda m: m.get("type") == "snapshot"
            and m.get("module_id") == "redemption"
            and bool(m.get("payload", {}).get("telemetry", {}).get("snapshot", {}).get("items"))
            and m.get("payload", {}).get("telemetry", {}).get("watchlist") == ["159513"],
            6,
        )
        realtime_item = redemption_ready["payload"]["telemetry"]["snapshot"]["items"][0]
        assert realtime_item["values"]["etfbuyamount"] == 1_200_000, redemption_observed[-20:]
        assert realtime_item["pcf"]["creation_redemption_unit"] == 600_000, realtime_item
        assert realtime_item["opportunity"]["label"] == "盘中申购机会", realtime_item

        stale_id = f"smoke-stale-{time.time_ns()}"
        client.sendall(frame({
            "schema_version": 1, "protocol": "module.control.v1", "type": "command", "command_id": stale_id,
            "module_id": "redemption", "action": "redemption_monitor_start",
            "arguments": {}, "expected_revision": 999_999, "deadline_ms": 5000,
            "requested_at": now(), "requested_by": "automated-smoke", "reason": "stale_revision_negative_test",
            "agent_instance_id": AGENT_INSTANCE, "config_sha256": CONFIG_SHA,
        }))
        stale, _ = wait_for(
            client,
            lambda m: m.get("type") == "command_result" and m.get("command_id") == stale_id,
            3,
        )
        assert stale.get("state") == "failed" and stale.get("code") == "stale_revision", stale

        # Lifecycle takeover is independently denied by the per-action allowlist
        # when local test config deliberately has no ownership.
        lifecycle_id = f"smoke-lifecycle-{time.time_ns()}"
        client.sendall(frame({
            "schema_version": 1, "protocol": "module.control.v1", "type": "command",
            "command_id": lifecycle_id, "module_id": "upload", "action": "start_service",
            "arguments": {}, "expected_revision": revisions["upload"], "deadline_ms": 5000,
            "requested_at": now(), "requested_by": "automated-smoke", "reason": "lifecycle_envelope_negative_test",
            "agent_instance_id": AGENT_INSTANCE, "config_sha256": CONFIG_SHA,
        }))
        lifecycle, _ = wait_for(
            client,
            lambda m: m.get("type") == "error" and m.get("command_id") == lifecycle_id,
            3,
        )
        assert lifecycle.get("code") == "action_not_allowed" and lifecycle.get("schema_version") == 1, lifecycle

        # A per-unit lifecycle request accepts only an Upload launchd unit id.
        # The local-test profile deliberately has no units, so a label/path-like
        # value must be rejected by the argument contract before the allowlist.
        invalid_target_id = f"smoke-invalid-target-{time.time_ns()}"
        client.sendall(frame({
            "schema_version": 1, "protocol": "module.control.v1", "type": "command",
            "command_id": invalid_target_id, "module_id": "upload", "action": "stop_service",
            "arguments": {"target_unit": "com.newnavnav.web"},
            "expected_revision": revisions["upload"], "deadline_ms": 5000,
            "requested_at": now(), "requested_by": "automated-smoke",
            "reason": "strict_unit_id_negative_test",
            "agent_instance_id": AGENT_INSTANCE, "config_sha256": CONFIG_SHA,
        }))
        invalid_target, _ = wait_for(
            client,
            lambda m: m.get("type") == "error" and m.get("command_id") == invalid_target_id,
            3,
        )
        assert invalid_target.get("code") == "invalid_arguments", invalid_target

        # Exact replays return the cached terminal result; a reused ID with
        # different parameters is rejected instead of executing a second action.
        replay_id = f"smoke-replay-{time.time_ns()}"
        replay_request = {
            "schema_version": 1, "protocol": "module.control.v1", "type": "command",
            "command_id": replay_id, "module_id": "upload", "action": "refresh", "arguments": {},
            "expected_revision": -1, "deadline_ms": 5000, "requested_at": now(),
            "requested_by": "automated-smoke", "reason": "idempotency_test",
            "agent_instance_id": AGENT_INSTANCE, "config_sha256": CONFIG_SHA,
        }
        client.sendall(frame(replay_request))
        replay_first, _ = wait_for(
            client,
            lambda m: m.get("type") == "command_result" and m.get("command_id") == replay_id
            and m.get("state") == "succeeded",
            3,
        )
        client.sendall(frame(replay_request))
        replay_second, _ = wait_for(
            client,
            lambda m: m.get("type") == "command_result" and m.get("command_id") == replay_id,
            3,
        )
        assert replay_second["state"] == replay_first["state"], (replay_first, replay_second)
        assert replay_second.get("details", {}).get("replayed") is True, replay_second
        conflict = dict(replay_request)
        conflict["action"] = "premium_sync"
        client.sendall(frame(conflict))
        conflict_result, _ = wait_for(
            client,
            lambda m: m.get("type") == "error" and m.get("command_id") == replay_id,
            3,
        )
        assert conflict_result.get("code") == "command_id_conflict", conflict_result

        # Allow the remaining adapter handshake to settle before mutations.
        time.sleep(0.2)
        command(client, revisions, "premium", "premium_sync")
        command(client, revisions, "webull", "webull_get_book")
        webull_mode = command(
            client, revisions, "webull", "webull_set_mode", {"mode": "force_running"}
        )
        mode_status = webull_mode["details"]["status"]
        assert mode_status["schedule_mode"] == "force_running", webull_mode
        assert mode_status["collector_running"] is True, webull_mode
        assert mode_status["browser_state"] == "running", webull_mode
        command(client, revisions, "webull", "webull_restart_browser")
        command(client, revisions, "redemption", "redemption_get_pcf", {"symbol": "159513.SZ"})
        command(client, revisions, "redemption", "redemption_set_watchlist", {"symbols": ["159513"]})
        command(client, revisions, "redemption", "redemption_set_symbol_name",
                {"symbol": "159513", "name": "测试标的"})
        command(client, revisions, "redemption", "redemption_monitor_start")
        command(client, revisions, "redemption", "redemption_qmt_connect", {"backend": "QMT1"}, seconds=20)
        command(client, revisions, "redemption", "redemption_qmt_connect", {"backend": "QMT2"}, seconds=20)
        command(client, revisions, "redemption", "redemption_qmt_order",
                {"backend": "QMT1", "symbol": "159513.SZ", "side": "PURCHASE"}, seconds=20)

        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = os.environ.get(
            "MACHOME_HUB_QPA_PLATFORM", "offscreen"
        )
        ui = subprocess.Popen([str(UI_BINARY), "--config", str(CONFIG), "--agent", str(AGENT_BINARY),
                               "--exit-after-agent"],
                              env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ui_exit = ui.wait(timeout=7)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Qt UI did not complete the Agent handshake in time")
        if ui_exit != 0:
            raise RuntimeError(f"Qt UI Agent handshake failed ({ui_exit}): {ui.stderr.read()}")
        ui = None

        assert (ROOT / "audit.db").exists(), "audit database was not created"
        client.close()
        # Exercise the same SIGTERM path launchd uses. The POSIX self-pipe must
        # enter Qt shutdown, drain module critical mailboxes and close SQLite
        # cooperatively rather than relying on QThread::terminate().
        agent.terminate()
        try:
            agent_exit = agent.wait(timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Agent did not complete graceful SIGTERM shutdown") from exc
        if agent_exit != 0:
            raise RuntimeError(f"Agent SIGTERM shutdown failed ({agent_exit}): {agent.stderr.read()}")
        agent = None
        print("FULL_SMOKE_OK modules=4 commands=11 stale_revision=ok lifecycle_envelope=ok "
              "idempotency=ok command_id_conflict=ok qmt_backends=2 "
              "webull_control=ok nested_realtime=ok upload_schedule_input=ok "
              "ui_agent_handshake=ok audit=present sigterm_shutdown=ok")
        return 0
    finally:
        terminate(ui)
        terminate(agent)
        terminate(mock)


if __name__ == "__main__":
    raise SystemExit(main())

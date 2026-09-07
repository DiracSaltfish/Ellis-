#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import upload_health_monitor as monitor
import upload_monitor_status as status
import private_161226_silver_uploader as silver


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send(self, title: str, content: str) -> None:
        self.messages.append((title, content))


class FakeState:
    def __init__(self) -> None:
        self.scopes: list[str] = []
        self.paused_scopes: dict[str, tuple[str, ...]] = {}
        self.reports: list[tuple[str, str, str]] = []

    def reconcile(self, scope: str, problems: list[monitor.Problem], now: datetime, paused_scopes=()) -> None:
        self.scopes.append(scope)
        self.paused_scopes[scope] = tuple(paused_scopes)

    def pause(self, scope: str, now: datetime) -> None:
        pass

    def send_once(self, key: str, title: str, content: str, now: datetime) -> bool:
        self.reports.append((key, title, content))
        return True


class UploadMonitorStatusTests(unittest.TestCase):
    def test_disabled_monitor_exits_once_without_token(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_MONITOR_ENABLED": "false",
                "PUSHPLUS_TOKEN": "",
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            self.assertFalse(instance.enabled)
            instance.run(once=True)

    def test_enabled_monitor_waits_for_startup_grace(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_MONITOR_ENABLED": "true",
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_MONITOR_STARTUP_GRACE": "60s",
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            with (
                mock.patch.object(monitor.time, "sleep") as sleep,
                mock.patch.object(instance, "tick", side_effect=SystemExit),
                self.assertRaises(SystemExit),
            ):
                instance.run()
            sleep.assert_called_once_with(60.0)

    def test_monitor_defaults_to_two_minute_startup_grace(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "NNN_UPLOAD_MONITOR_STARTUP_GRACE": "",
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            self.assertEqual(monitor.Monitor().startup_grace, 120.0)

    def test_once_check_bypasses_startup_grace(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_MONITOR_ENABLED": "true",
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_MONITOR_STARTUP_GRACE": "60s",
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            with (
                mock.patch.object(monitor.time, "sleep") as sleep,
                mock.patch.object(instance, "tick") as tick,
            ):
                instance.run(once=True)
            sleep.assert_not_called()
            tick.assert_called_once()

    def test_status_writer_records_success_and_failure_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(os.environ, {"NNN_UPLOAD_HEALTH_DIR": raw}, clear=False):
            status.record_success("source-a", stage="ack", accepted=2, symbols=["SZ2", "SZ1"])
            path = next(Path(raw).glob("*.json"))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "ok")
            self.assertEqual(payload["symbols"], ["SZ1", "SZ2"])
            self.assertNotIn("token", payload)

            status.record_failure("source-a", stage="upload", error="boom")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "error")
            self.assertEqual(payload["last_error"], "boom")

    def test_incident_repeat_is_six_minutes_and_new_incident_is_immediate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            sender = FakeSender()
            state = monitor.State(Path(raw) / "state.json", sender, 360)
            now = datetime(2026, 9, 1, 9, 0, tzinfo=monitor.SHANGHAI)
            # Deadline-based PCF incidents retain immediate notification.
            problem = monitor.Problem("upload.pcf.missing", "missing PCF", "source-a")
            state.reconcile("upload.pcf.", [problem], now)
            state.reconcile("upload.pcf.", [problem], now + timedelta(minutes=5, seconds=59))
            self.assertEqual(len(sender.messages), 1)
            state.reconcile("upload.pcf.", [problem], now + timedelta(minutes=6))
            self.assertEqual(len(sender.messages), 2)
            for offset in (5, 10, 15):
                state.reconcile("upload.pcf.", [], now + timedelta(minutes=6, seconds=offset))
            self.assertEqual(len(sender.messages), 3)
            state.reconcile("upload.pcf.", [problem], now + timedelta(minutes=6, seconds=16))
            self.assertEqual(len(sender.messages), 4)

    def test_source_freshness_uses_last_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_HEALTH_DIR": raw,
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
                "NNN_UPLOAD_MONITOR_PREOPEN_SOURCES": "source-a",
                "NNN_UPLOAD_MONITOR_REALTIME_SOURCES": "source-a",
            },
            clear=False,
        ):
            now = datetime.now(monitor.SHANGHAI)
            Path(raw, "source-a.json").write_text(
                json.dumps({"source": "source-a", "state": "ok", "last_success_at": now.isoformat()}),
                encoding="utf-8",
            )
            instance = monitor.Monitor()
            problems, healthy, expected, _ = instance.source_problems(now, ("source-a",))
            self.assertEqual(problems, [])
            self.assertEqual((healthy, expected), (1, 1))

    def test_fresh_success_suppresses_single_transient_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_HEALTH_DIR": raw,
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            now = datetime.now(monitor.SHANGHAI)
            path = Path(raw, "source-a.json")
            path.write_text(
                json.dumps(
                    {
                        "source": "source-a",
                        "state": "error",
                        "last_success_at": (now - timedelta(seconds=10)).isoformat(),
                        "last_error": "temporary EOF",
                    }
                ),
                encoding="utf-8",
            )
            instance = monitor.Monitor()
            problems, healthy, _, _ = instance.source_problems(now, ("source-a",))
            self.assertEqual(problems, [])
            self.assertEqual(healthy, 1)

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["last_success_at"] = (now - timedelta(seconds=36)).isoformat()
            path.write_text(json.dumps(payload), encoding="utf-8")
            problems, healthy, _, _ = instance.source_problems(now, ("source-a",))
            self.assertEqual(healthy, 0)
            self.assertEqual([problem.key for problem in problems], ["upload.source.source-a.error"])

    def test_private_source_requires_full_expected_symbol_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_HEALTH_DIR": raw,
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            now = datetime(2026, 9, 2, 10, 0, tzinfo=monitor.SHANGHAI)
            Path(raw, "nikkei.json").write_text(
                json.dumps(
                    {
                        "source": monitor.NIKKEI_SOURCE,
                        "state": "ok",
                        "last_success_at": now.isoformat(),
                        "accepted": 3,
                        "symbols": ["SH513000", "SH513520", "SZ159866"],
                    }
                ),
                encoding="utf-8",
            )
            instance = monitor.Monitor()
            problems, healthy, expected, _ = instance.source_problems(
                now, (monitor.NIKKEI_SOURCE,), "upload.private"
            )
            self.assertEqual((healthy, expected), (0, 1))
            self.assertEqual(
                [problem.key for problem in problems],
                ["upload.private.mac-home-private-nikkei225-pcf-n225m-uploader.coverage"],
            )
            self.assertIn("SH513880", problems[0].detail)

    def test_pcf_readiness_uses_todays_files_and_validates_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_RUNTIME_DIR": raw,
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            now = datetime(2026, 9, 1, 9, 14, 30, tzinfo=monitor.SHANGHAI)
            for _, template in monitor.DEFAULT_PCF_PATHS:
                relative = template.format(date_dash="2026-09-01", date_compact="20260901")
                path = Path(raw, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}" if path.suffix == ".json" else "<PCF />", encoding="utf-8")

            instance = monitor.Monitor()
            problems, healthy, expected = instance.pcf_problems(now)
            self.assertEqual(problems, [])
            self.assertEqual((healthy, expected), (8, 8))

            missing_path = Path(raw, monitor.DEFAULT_PCF_PATHS[0][1].format(date_dash="2026-09-01", date_compact="20260901"))
            missing_path.unlink()
            invalid_path = Path(raw, monitor.DEFAULT_PCF_PATHS[1][1].format(date_dash="2026-09-01", date_compact="20260901"))
            invalid_path.write_text("not json", encoding="utf-8")
            problems, healthy, expected = instance.pcf_problems(now)
            self.assertEqual((healthy, expected), (6, 8))
            self.assertTrue(any(problem.key == "upload.pcf.missing" for problem in problems))
            self.assertTrue(any(problem.key == "upload.pcf.invalid" for problem in problems))

    def test_launchd_managed_check_allows_scheduled_process_before_private_start(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
                "NNN_UPLOAD_MONITOR_EXPECTED_LABELS": "example.job",
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            result = subprocess_result(stdout="state = spawn scheduled\n")
            with mock.patch.object(monitor.subprocess, "run", return_value=result):
                self.assertEqual(instance.process_problems(strict=False), [])
                strict = instance.process_problems(strict=True)
            self.assertEqual([problem.key for problem in strict], ["upload.process.not_running"])

    def test_launchd_scheduled_retry_is_healthy_while_source_success_is_fresh(self) -> None:
        india_label = "com.newnavnav.private-164824-valuation-uploader"
        india_source = "mac-home-private-164824-t2-inda-uploader"
        now = datetime(2026, 9, 1, 14, 0, 0, tzinfo=monitor.SHANGHAI)
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_HEALTH_DIR": raw,
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
                "NNN_UPLOAD_MONITOR_EXPECTED_LABELS": india_label,
            },
            clear=False,
        ):
            Path(raw, "india.json").write_text(
                json.dumps({"source": india_source, "last_success_at": (now - timedelta(seconds=10)).isoformat()}),
                encoding="utf-8",
            )
            instance = monitor.Monitor()
            result = subprocess_result(stdout="state = spawn scheduled\n")
            with mock.patch.object(monitor.subprocess, "run", return_value=result):
                self.assertEqual(instance.process_problems(strict=True, now=now), [])
                stale = instance.process_problems(strict=True, now=now + timedelta(seconds=36))
            self.assertEqual([problem.key for problem in stale], ["upload.process.not_running"])

    def test_tick_defers_private_checks_until_0937(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            fake_state = FakeState()
            instance.state = fake_state
            with (
                mock.patch.object(instance, "process_problems", return_value=[]),
                mock.patch.object(instance, "tws_problems", return_value=[]),
                mock.patch.object(instance, "pcf_problems", return_value=([], 8, 8)),
                mock.patch.object(instance, "source_problems", return_value=([], 1, 1, 2.0)),
            ):
                instance.tick(datetime(2026, 9, 1, 9, 36, 59, tzinfo=monitor.SHANGHAI))
                self.assertNotIn("upload.process.", fake_state.scopes)
                self.assertNotIn("upload.private.", fake_state.scopes)

                fake_state.scopes.clear()
                instance.tick(datetime(2026, 9, 1, 9, 37, 0, tzinfo=monitor.SHANGHAI))
                self.assertIn("upload.process.", fake_state.scopes)
                self.assertIn("upload.private.", fake_state.scopes)

    def test_0916_report_checks_public_source_not_private_batches(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            fake_state = FakeState()
            instance.state = fake_state
            with (
                mock.patch.object(instance, "process_problems", return_value=[]),
                mock.patch.object(instance, "tws_problems", return_value=[]),
                mock.patch.object(instance, "pcf_problems", return_value=([], 8, 8)),
                mock.patch.object(instance, "source_problems", return_value=([], 1, 1, 2.0)) as source,
            ):
                instance.tick(datetime(2026, 9, 1, 9, 16, 30, tzinfo=monitor.SHANGHAI))
            source.assert_called_once_with(mock.ANY, instance.public_sources, "upload.public")
            self.assertNotIn("upload.private.", fake_state.scopes)
            self.assertEqual(len(fake_state.reports), 2)
            self.assertIn("公共 upload 数据源：1/1", fake_state.reports[-1][2])

    def test_tick_pauses_public_source_during_domestic_lunch(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            fake_state = FakeState()
            instance.state = fake_state
            with (
                mock.patch.object(instance, "process_problems", return_value=[]),
                mock.patch.object(instance, "tws_problems", return_value=[]),
                mock.patch.object(instance, "pcf_problems", return_value=([], 8, 8)),
                mock.patch.object(instance, "source_problems", return_value=([], 1, 1, 2.0)),
            ):
                instance.tick(datetime(2026, 9, 1, 12, 0, 0, tzinfo=monitor.SHANGHAI))
                self.assertNotIn("upload.public.", fake_state.scopes)
                self.assertIn("upload.private.", fake_state.scopes)

                fake_state.scopes.clear()
                instance.tick(datetime(2026, 9, 1, 13, 0, 59, tzinfo=monitor.SHANGHAI))
                self.assertNotIn("upload.public.", fake_state.scopes)

                fake_state.scopes.clear()
                instance.tick(datetime(2026, 9, 1, 13, 1, 0, tzinfo=monitor.SHANGHAI))
            self.assertIn("upload.public.", fake_state.scopes)

    def test_tick_applies_public_and_nikkei_close_cutoffs(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            fake_state = FakeState()
            instance.state = fake_state
            with (
                mock.patch.object(instance, "process_problems", return_value=[]) as process,
                mock.patch.object(instance, "tws_problems", return_value=[]),
                mock.patch.object(instance, "pcf_problems", return_value=([], 8, 8)),
                mock.patch.object(instance, "source_problems", return_value=([], 1, 1, 2.0)) as source,
            ):
                instance.tick(datetime(2026, 9, 1, 14, 39, 59, tzinfo=monitor.SHANGHAI))
                labels = process.call_args.kwargs["labels"]
                self.assertIn(monitor.PUBLIC_LABEL, labels)
                self.assertIn(monitor.NIKKEI_LABEL, labels)
                private_sources = next(
                    call.args[1] for call in source.call_args_list if call.args[2] == "upload.private"
                )
                self.assertIn(monitor.NIKKEI_SOURCE, private_sources)
                self.assertNotIn("upload.private." + monitor.NIKKEI_SOURCE.lower() + ".", fake_state.paused_scopes["upload.private."])

                fake_state.scopes.clear()
                process.reset_mock()
                source.reset_mock()
                instance.tick(datetime(2026, 9, 1, 14, 40, 0, tzinfo=monitor.SHANGHAI))
                labels = process.call_args.kwargs["labels"]
                self.assertIn(monitor.PUBLIC_LABEL, labels)
                self.assertNotIn(monitor.NIKKEI_LABEL, labels)
                private_sources = next(
                    call.args[1] for call in source.call_args_list if call.args[2] == "upload.private"
                )
                self.assertNotIn(monitor.NIKKEI_SOURCE, private_sources)
                self.assertIn("upload.private." + monitor.NIKKEI_SOURCE.lower() + ".", fake_state.paused_scopes["upload.private."])
                self.assertIn("upload.public.", fake_state.scopes)

                fake_state.scopes.clear()
                process.reset_mock()
                source.reset_mock()
                instance.tick(datetime(2026, 9, 1, 14, 57, 0, tzinfo=monitor.SHANGHAI))
                labels = process.call_args.kwargs["labels"]
                self.assertNotIn(monitor.PUBLIC_LABEL, labels)
                self.assertNotIn(monitor.NIKKEI_LABEL, labels)
                self.assertNotIn("upload.public.", fake_state.scopes)
                self.assertFalse(any(call.args[2] == "upload.public" for call in source.call_args_list))

    def test_tick_stops_all_realtime_checks_at_1500(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            fake_state = FakeState()
            instance.state = fake_state
            with (
                mock.patch.object(instance, "process_problems", return_value=[]) as process,
                mock.patch.object(instance, "tws_problems", return_value=[]) as tws,
                mock.patch.object(instance, "pcf_problems", return_value=([], 8, 8)) as pcf,
                mock.patch.object(instance, "source_problems", return_value=([], 1, 1, 2.0)) as source,
            ):
                instance.tick(datetime(2026, 9, 1, 15, 0, 0, tzinfo=monitor.SHANGHAI))
            process.assert_not_called()
            tws.assert_not_called()
            pcf.assert_not_called()
            source.assert_not_called()
            self.assertEqual(fake_state.scopes, [])

    def test_default_policy_excludes_china_internet_nq_and_es_monitoring(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_RUNTIME_DIR": raw,
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            self.assertNotIn("com.newnavnav.private-china-internet-valuation-uploader", instance.labels)
            self.assertNotIn("com.newnavnav.private-nasdaq-valuation-uploader", instance.labels)
            self.assertNotIn("com.newnavnav.private-sp500-valuation-uploader", instance.labels)
            self.assertNotIn("mac-home-private-china-internet-uploader", instance.private_sources)
            self.assertNotIn("mac-home-private-nasdaq-pcf-nq-uploader", instance.private_sources)
            self.assertNotIn("mac-home-private-sp500-pcf-es-uploader", instance.private_sources)
            self.assertEqual(len(instance.labels), 6)
            self.assertEqual(len(instance.private_sources), 5)
            _, _, expected = instance.pcf_problems(datetime(2026, 9, 1, 9, 14, 30, tzinfo=monitor.SHANGHAI))
            self.assertEqual(expected, 8)

    def test_silver_break_is_not_monitored_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as raw, mock.patch.dict(
            os.environ,
            {
                "PUSHPLUS_TOKEN": "test",
                "NNN_UPLOAD_HEALTH_DIR": raw,
                "NNN_UPLOAD_MONITOR_STATE_FILE": str(Path(raw) / "state.json"),
            },
            clear=False,
        ):
            instance = monitor.Monitor()
            before_break = datetime(2026, 9, 1, 10, 14, 59, tzinfo=monitor.SHANGHAI)
            problems, _, expected, _ = instance.source_problems(before_break, (monitor.SILVER_SOURCE,), "upload.private")
            self.assertEqual(expected, 1)
            self.assertEqual(
                [problem.key for problem in problems],
                ["upload.private.mac-home-private-161226-silver-uploader.missing"],
            )

            during_break = datetime(2026, 9, 1, 10, 20, 0, tzinfo=monitor.SHANGHAI)
            problems, healthy, expected, _ = instance.source_problems(during_break, (monitor.SILVER_SOURCE,), "upload.private")
            self.assertEqual(problems, [])
            self.assertEqual((healthy, expected), (0, 0))

            reopen_grace = datetime(2026, 9, 1, 10, 30, 30, tzinfo=monitor.SHANGHAI)
            _, _, expected, _ = instance.source_problems(reopen_grace, (monitor.SILVER_SOURCE,), "upload.private")
            self.assertEqual(expected, 0)

            after_grace = datetime(2026, 9, 1, 10, 31, 0, tzinfo=monitor.SHANGHAI)
            _, _, expected, _ = instance.source_problems(after_grace, (monitor.SILVER_SOURCE,), "upload.private")
            self.assertEqual(expected, 1)

            lunch_start = datetime(2026, 9, 1, 11, 30, 0, tzinfo=monitor.SHANGHAI)
            _, _, expected, _ = instance.source_problems(lunch_start, (monitor.SILVER_SOURCE,), "upload.private")
            self.assertEqual(expected, 0)

            afternoon_grace = datetime(2026, 9, 1, 13, 30, 59, tzinfo=monitor.SHANGHAI)
            _, _, expected, _ = instance.source_problems(afternoon_grace, (monitor.SILVER_SOURCE,), "upload.private")
            self.assertEqual(expected, 0)

            afternoon_active = datetime(2026, 9, 1, 13, 31, 0, tzinfo=monitor.SHANGHAI)
            _, _, expected, _ = instance.source_problems(afternoon_active, (monitor.SILVER_SOURCE,), "upload.private")
            self.assertEqual(expected, 1)

    def test_silver_uploader_collection_window_excludes_exchange_break(self) -> None:
        tests = (
            (datetime(2026, 9, 1, 10, 14, tzinfo=monitor.SHANGHAI), True),
            (datetime(2026, 9, 1, 10, 15, tzinfo=monitor.SHANGHAI), False),
            (datetime(2026, 9, 1, 10, 29, tzinfo=monitor.SHANGHAI), False),
            (datetime(2026, 9, 1, 10, 30, tzinfo=monitor.SHANGHAI), True),
            (datetime(2026, 9, 1, 11, 29, tzinfo=monitor.SHANGHAI), True),
            (datetime(2026, 9, 1, 11, 30, tzinfo=monitor.SHANGHAI), False),
            (datetime(2026, 9, 1, 13, 29, tzinfo=monitor.SHANGHAI), False),
            (datetime(2026, 9, 1, 13, 30, tzinfo=monitor.SHANGHAI), True),
            (datetime(2026, 9, 1, 14, 59, 59, tzinfo=monitor.SHANGHAI), True),
            (datetime(2026, 9, 1, 15, 0, 0, tzinfo=monitor.SHANGHAI), False),
        )
        for value, expected in tests:
            with self.subTest(value=value):
                self.assertEqual(silver.in_collection_window(value), expected)


def subprocess_result(stdout: str, returncode: int = 0) -> object:
    return type("Result", (), {"stdout": stdout, "returncode": returncode})()


if __name__ == "__main__":
    unittest.main()

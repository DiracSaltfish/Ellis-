#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import upload_health_monitor as monitor


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.attempts = 0
        self.fail = False

    def send(self, title: str, content: str) -> None:
        self.attempts += 1
        if self.fail:
            raise RuntimeError("simulated failure")
        self.messages.append((title, content))


class UploadAlertDebounceTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.json"
        self.sender = RecordingSender()
        self.state = monitor.State(self.path, self.sender, 360)
        self.now = datetime(2026, 9, 4, 10, 0, 0, 999999, tzinfo=monitor.SHANGHAI)
        self.problem = monitor.Problem("upload.tws.connection", "TWS 断线", "unreachable")

    def observe(self, seconds: float, *problems: monitor.Problem) -> None:
        self.state.reconcile("upload.", list(problems), self.now + timedelta(seconds=seconds))

    def test_policy_only_delays_realtime_checks(self) -> None:
        for key in (
            "upload.process.not_running", "upload.tws.connection", "upload.public.home-mac.stale",
            "upload.private.germany.error", "upload.source.nikkei.missing", "upload.private.silver.coverage",
        ):
            self.assertEqual(monitor.incident_confirmation_seconds(key), 60, key)
        for key in ("upload.pcf.missing", "upload.pcf.invalid"):
            self.assertEqual(monitor.incident_confirmation_seconds(key), 0, key)

    def test_first_notification_requires_full_minute_and_repeats_after_six(self) -> None:
        for seconds in (0, 5, 59, 59.999999):
            self.observe(seconds, self.problem)
        self.assertEqual(self.sender.messages, [])
        self.observe(60, self.problem)
        self.assertEqual(len(self.sender.messages), 1)
        self.observe(419, self.problem)
        self.assertEqual(len(self.sender.messages), 1)
        self.observe(420, self.problem)
        self.assertEqual(len(self.sender.messages), 2)
        for seconds in (425, 430):
            self.observe(seconds)
            self.assertEqual(len(self.sender.messages), 2)
        self.observe(435)
        self.assertEqual(len(self.sender.messages), 3)
        self.assertIn("恢复", self.sender.messages[-1][0])
        self.observe(436, self.problem)
        self.observe(495, self.problem)
        self.assertEqual(len(self.sender.messages), 3)
        self.observe(496, self.problem)
        self.assertEqual(len(self.sender.messages), 4)

    def test_first_healthy_sample_cancels_silently_and_recurrence_restarts_timer(self) -> None:
        self.observe(0, self.problem)
        self.observe(59)
        self.assertEqual(self.state.data["incidents"], {})
        self.observe(60, self.problem)
        self.observe(119, self.problem)
        self.assertEqual(self.sender.messages, [])
        self.observe(120, self.problem)
        self.assertEqual(len(self.sender.messages), 1)

    def test_independent_sources_do_not_share_timer(self) -> None:
        germany = monitor.Problem("upload.private.germany.error", "德国", "bad")
        nikkei = monitor.Problem("upload.private.nikkei.error", "日经", "bad")
        self.observe(0, germany)
        self.observe(30, germany, nikkei)
        self.observe(60, germany, nikkei)
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("德国", self.sender.messages[0][0])
        self.observe(90, germany, nikkei)
        self.assertEqual(len(self.sender.messages), 2)

    def test_source_reason_changes_preserve_timer_and_notification_cooldown(self) -> None:
        def problem(reason: str) -> monitor.Problem:
            return monitor.Problem("upload.private.germany." + reason, reason, reason)
        self.observe(0, problem("missing"))
        self.observe(20, problem("error"))
        self.observe(40, problem("stale"))
        self.observe(60, problem("coverage"))
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("coverage", self.sender.messages[0][0])
        self.observe(70, problem("error"))
        self.observe(419, problem("stale"))
        self.assertEqual(len(self.sender.messages), 1)
        self.observe(420, problem("error"))
        self.assertEqual(len(self.sender.messages), 2)
        self.assertEqual(list(self.state.data["incidents"]), ["upload.private.germany.health"])

    def test_pause_discards_pending_but_not_confirmed(self) -> None:
        pending = monitor.Problem("upload.public.home-mac.error", "public", "bad")
        self.observe(0, self.problem)
        self.observe(60, self.problem, pending)
        self.state.pause("upload.", self.now + timedelta(seconds=65))
        self.assertEqual(list(self.state.data["incidents"]), [self.problem.key])
        self.assertEqual(len(self.sender.messages), 1)
        self.observe(3600, self.problem, pending)
        self.assertEqual(len(self.sender.messages), 2)  # Only the old confirmed incident repeats.
        self.observe(3660, self.problem, pending)
        self.assertEqual(len(self.sender.messages), 3)

    def test_restart_discards_pending_but_preserves_repeat_and_daily_receipts(self) -> None:
        pending = monitor.Problem("upload.public.home-mac.error", "public", "bad")
        self.observe(0, self.problem)
        self.observe(60, self.problem, pending)
        self.state.send_once("scheduled", "report", "status", self.now)
        self.state = monitor.State(self.path, self.sender, 360)
        self.assertEqual(list(self.state.data["incidents"]), [self.problem.key])
        self.observe(180, self.problem, pending)
        self.state.send_once("scheduled", "report", "status", self.now)
        self.assertEqual(len(self.sender.messages), 2)
        self.observe(240, self.problem, pending)
        self.assertEqual(len(self.sender.messages), 3)

    def test_upgrade_of_per_reason_incidents_preserves_latest_notification(self) -> None:
        old_incidents = {}
        for reason, last in (("missing", -600), ("error", -60)):
            old_incidents["upload.private.germany." + reason] = {
                "opened_at": (self.now - timedelta(seconds=700)).isoformat(),
                "last_notified_at": (self.now + timedelta(seconds=last)).isoformat(),
            }
        self.path.write_text(json.dumps({"incidents": old_incidents, "sent": {}}), encoding="utf-8")
        self.state = monitor.State(self.path, self.sender, 360)
        current = monitor.Problem("upload.private.germany.stale", "stale", "bad")
        self.observe(0, current)
        self.assertEqual(self.sender.messages, [])
        self.observe(300, current)
        self.assertEqual(len(self.sender.messages), 1)

    def test_failed_notification_retry_and_silent_recovery(self) -> None:
        self.sender.fail = True
        self.observe(0, self.problem)
        self.observe(60, self.problem)
        self.observe(89, self.problem)
        self.assertEqual(self.sender.attempts, 1)
        self.observe(90, self.problem)
        self.assertEqual(self.sender.attempts, 2)
        for seconds in (95, 100, 105):
            self.observe(seconds)
        self.assertEqual(self.sender.attempts, 2)
        self.assertEqual(self.state.data["incidents"], {})

    def test_pcf_and_scheduled_reports_still_notify_immediately(self) -> None:
        self.observe(0, monitor.Problem("upload.pcf.missing", "PCF", "missing"))
        self.state.send_once("morning", "report", "status", self.now)
        self.assertEqual(len(self.sender.messages), 2)

    def test_tick_discards_pending_outside_business_windows(self) -> None:
        instance = monitor.Monitor.__new__(monitor.Monitor)
        instance.state = self.state
        instance.labels = ()
        instance.public_sources = ()
        instance.private_sources = ()
        instance.ignored_symbols = set()
        instance.skip_dates = set()
        instance.private_start = 9 * 3600 + 37 * 60
        instance.public_start = 9 * 3600 + 20 * 60
        instance.preopen_at = 9 * 3600 + 14 * 60 + 30
        instance.realtime_at = 9 * 3600 + 16 * 60 + 30
        instance.process_problems = mock.Mock(return_value=[])
        instance.tws_problems = mock.Mock(return_value=[])
        instance.pcf_problems = mock.Mock(return_value=([], 0, 0))
        instance.source_problems = mock.Mock(return_value=([], 0, 0, 0))
        for day, hour, minute, key in (
            (4, 11, 30, "upload.public.home-mac.error"),
            (4, 14, 57, "upload.public.home-mac.error"),
            (4, 15, 0, "upload.private.germany.error"),
            (4, 15, 0, "upload.tws.connection"),
            (4, 15, 0, "upload.process.not_running"),
            (5, 10, 0, "upload.tws.connection"),
        ):
            with self.subTest(day=day, hour=hour, minute=minute, key=key):
                at = self.now.replace(day=day, hour=hour, minute=minute)
                self.state.reconcile("upload.", [monitor.Problem(key, key, "bad")], at - timedelta(seconds=30))
                instance.tick(at)
                self.assertEqual(self.state.data["incidents"], {})
                self.assertEqual(self.sender.messages, [])



class SessionBoundaryRegressionTests(unittest.TestCase):
    setUp = UploadAlertDebounceTests.setUp
    observe = UploadAlertDebounceTests.observe
    def test_new_business_day_requires_a_fresh_confirmation(self):
        self.observe(0, self.problem)
        self.observe(60, self.problem)
        self.assertEqual(len(self.sender.messages), 1)
        monday = self.now + timedelta(days=3)
        self.state.reconcile('upload.', [self.problem], monday)
        self.state.reconcile('upload.', [self.problem], monday + timedelta(seconds=59))
        self.assertEqual(len(self.sender.messages), 1)
        self.state.reconcile('upload.', [self.problem], monday + timedelta(seconds=60))
        self.assertEqual(len(self.sender.messages), 2)

    def test_exchange_break_is_not_a_recovery(self):
        problem = monitor.Problem('upload.private.silver.error', 'silver', 'HTTP 400')
        self.observe(0, problem)
        self.observe(60, problem)
        for seconds in (900, 905, 910):
            self.state.reconcile('upload.', [], self.now + timedelta(seconds=seconds), ['upload.private.silver.'])
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn('upload.private.silver.health', self.state.data['incidents'])
        self.observe(1800, problem)
        self.assertEqual(len(self.sender.messages), 2)
        self.assertNotIn('恢复', self.sender.messages[-1][0])


if __name__ == "__main__":
    unittest.main()

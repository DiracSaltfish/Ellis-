"""回归：重复扫描不能在 QThread 回收时触发 PySide 原生崩溃。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from archive_hash_checker.core import discover_files
from archive_hash_checker.ui import MainWindow


class ScanLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_rescan_finishes_twice_and_reclaims_threads_in_main_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / "backup.part01.rar"
            file_path.write_bytes(b"hash me" * 1024 * 256)
            window = MainWindow()
            window.records["left"] = discover_files([file_path])
            window.rebuild_table("left")

            rounds_started = 1
            rounds_finished = 0
            timed_out = False

            def poll() -> None:
                nonlocal rounds_started, rounds_finished, timed_out
                if window.scan_workers or window._threads:
                    QTimer.singleShot(10, poll)
                    return
                rounds_finished += 1
                if rounds_started < 2:
                    rounds_started += 1
                    window.rescan_side("left")
                    QTimer.singleShot(10, poll)
                    return
                self.app.quit()

            def timeout() -> None:
                nonlocal timed_out
                timed_out = True
                self.app.quit()

            window.start_scan("left", window.records["left"])
            QTimer.singleShot(10, poll)
            QTimer.singleShot(8000, timeout)
            self.app.exec()

            self.assertFalse(timed_out, "重复扫描没有在 8 秒内结束")
            self.assertEqual(rounds_started, 2)
            self.assertEqual(rounds_finished, 2)
            self.assertFalse(window.scan_workers)
            self.assertFalse(window._threads)
            self.assertTrue(window.records["left"][0].is_hashed)
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()

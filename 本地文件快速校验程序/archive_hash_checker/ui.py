"""PySide6 桌面界面。所有文件读取都在后台线程完成，界面线程只更新表格。"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .core import (
    DEFAULT_ALGORITHM,
    ComparisonResult,
    FileRecord,
    ScanMode,
    compare_records,
    discover_files,
    load_manifest,
    save_manifest,
    scan_records,
)


STATUS_COLORS = {
    "match": QColor("#C6EFCE"),
    "different": QColor("#FFEB9C"),
    "unmatched": QColor("#E7E9EC"),
}
STATUS_TEXT = {
    "match": "一致",
    "different": "存在差异",
    "unmatched": "未配对 / 待扫描",
}


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1024 or unit == "PB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


class FileTable(QTableWidget):
    """接收本机文件/目录 URL 的列表控件。"""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 4, parent)
        self.setAcceptDrops(True)
        self.setHorizontalHeaderLabels(["文件（相对路径）", "大小", "哈希", "核验状态"])
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(False)
        self.setSortingEnabled(False)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.setColumnWidth(0, 270)
        self.setColumnWidth(1, 100)
        self.setColumnWidth(2, 235)
        self.setMinimumWidth(560)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class FilePane(QFrame):
    """左或右一侧文件源的操作区。"""

    load_folder_requested = Signal(str)
    load_files_requested = Signal(str)
    load_manifest_requested = Signal(str)
    save_manifest_requested = Signal(str)
    scan_requested = Signal(str)
    clear_requested = Signal(str)
    paths_dropped = Signal(str, list)

    def __init__(self, side: str, title: str, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.side = side
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName(f"{side}Pane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        title_label = QLabel(title)
        title_label.setObjectName("paneTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("paneSubtitle")
        subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)

        first_row = QHBoxLayout()
        folder_button = QPushButton("加载文件夹")
        files_button = QPushButton("添加文件")
        scan_button = QPushButton("重新扫描")
        first_row.addWidget(folder_button)
        first_row.addWidget(files_button)
        first_row.addStretch()
        first_row.addWidget(scan_button)
        layout.addLayout(first_row)

        second_row = QHBoxLayout()
        load_button = QPushButton("加载清单")
        save_button = QPushButton("保存清单")
        clear_button = QPushButton("清空")
        self.count_label = QLabel("0 个文件")
        second_row.addWidget(load_button)
        second_row.addWidget(save_button)
        second_row.addWidget(clear_button)
        second_row.addStretch()
        second_row.addWidget(self.count_label)
        layout.addLayout(second_row)

        self.table = FileTable(self)
        layout.addWidget(self.table, 1)

        folder_button.clicked.connect(lambda: self.load_folder_requested.emit(self.side))
        files_button.clicked.connect(lambda: self.load_files_requested.emit(self.side))
        load_button.clicked.connect(lambda: self.load_manifest_requested.emit(self.side))
        save_button.clicked.connect(lambda: self.save_manifest_requested.emit(self.side))
        scan_button.clicked.connect(lambda: self.scan_requested.emit(self.side))
        clear_button.clicked.connect(lambda: self.clear_requested.emit(self.side))
        self.table.files_dropped.connect(lambda paths: self.paths_dropped.emit(self.side, paths))

    def set_count(self, count: int) -> None:
        self.count_label.setText(f"{count:,} 个文件")


class DiscoverWorker(QThread):
    """在后台展开拖入的文件夹，避免大目录枚举卡住窗口。

    直接继承 QThread，避免把 Python QObject 移入线程后由该线程 deleteLater 的
    生命周期问题；后者在部分 macOS/PySide6 组合下会触发原生段错误。
    """

    completed = Signal(str, object)
    failed = Signal(str, str)
    def __init__(self, side: str, paths: list[str]) -> None:
        super().__init__()
        self.side = side
        self.paths = paths
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            self.completed.emit(self.side, discover_files(self.paths, self.cancel_event))
        except Exception as exc:
            self.failed.emit(self.side, str(exc))


class ScanWorker(QThread):
    """在后台协调 SSD 线程池或 HDD 顺序扫描。

    不再采用 ``moveToThread + worker.deleteLater``：QThread 的结束信号会在主线程
    回收其 Python 包装对象，避免重扫时的跨线程 QObject 析构崩溃。
    """

    progress = Signal(str, int, int, object)
    failed = Signal(str, str)
    scan_completed = Signal(str, bool)

    def __init__(
        self,
        side: str,
        records: list[FileRecord],
        algorithm: str,
        mode: ScanMode,
        workers: int,
    ) -> None:
        super().__init__()
        self.side = side
        self.records = records
        self.algorithm = algorithm
        self.mode = mode
        self.workers = workers
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            scan_records(
                self.records,
                algorithm=self.algorithm,
                mode=self.mode,
                max_workers=self.workers,
                cancel_event=self.cancel_event,
                progress=lambda done, total, record: self.progress.emit(self.side, done, total, record),
            )
        except Exception as exc:
            self.failed.emit(self.side, str(exc))
        finally:
            self.scan_completed.emit(self.side, self.cancel_event.is_set())


class MainWindow(QMainWindow):
    """主窗口与左右清单状态管理。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("本地文件快速校验")
        self.resize(1380, 780)
        self.records: dict[str, list[FileRecord]] = {"left": [], "right": []}
        self.row_for_record: dict[str, dict[int, int]] = {"left": {}, "right": {}}
        self.scan_workers: dict[str, ScanWorker] = {}
        self.discover_workers: list[DiscoverWorker] = []
        self._threads: list[QThread] = []
        self._active_total = 0
        self._active_done = 0

        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 14, 16, 12)

        header = QHBoxLayout()
        header_text = QVBoxLayout()
        heading = QLabel("归档文件完整性核验")
        heading.setObjectName("appHeading")
        detail = QLabel("左侧放上传前原文件，右侧放重新下载的文件。支持拖放、文件夹递归加载和可携带 JSON 清单。")
        detail.setObjectName("appDetail")
        header_text.addWidget(heading)
        header_text.addWidget(detail)
        header.addLayout(header_text, 1)
        self.scan_both_button = QPushButton("扫描两侧未扫描文件")
        self.cancel_button = QPushButton("取消扫描")
        self.cancel_button.setEnabled(False)
        header.addWidget(self.scan_both_button)
        header.addWidget(self.cancel_button)
        layout.addLayout(header)

        options = QGroupBox("扫描设置")
        options_layout = QGridLayout(options)
        options_layout.addWidget(QLabel("读取策略："), 0, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("SSD 模式：有限并发读取（推荐 SSD / NVMe）", ScanMode.SSD)
        self.mode_combo.addItem("HDD 模式：逐文件顺序读取（推荐机械硬盘）", ScanMode.HDD)
        options_layout.addWidget(self.mode_combo, 0, 1)
        options_layout.addWidget(QLabel("哈希算法："), 0, 2)
        self.algorithm_combo = QComboBox()
        self.algorithm_combo.addItem("BLAKE2b-256（默认，快速）", DEFAULT_ALGORITHM)
        self.algorithm_combo.addItem("SHA-256（兼容模式）", "SHA-256")
        options_layout.addWidget(self.algorithm_combo, 0, 3)
        options_layout.addWidget(QLabel("SSD 并发数："), 0, 4)
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 32)
        self.workers_spin.setValue(min(8, max(2, os.cpu_count() or 4)))
        self.workers_spin.setToolTip("仅 SSD 模式生效；过高并发可能降低同一块磁盘的吞吐。")
        options_layout.addWidget(self.workers_spin, 0, 5)
        options_layout.setColumnStretch(1, 1)
        options_layout.setColumnStretch(3, 1)
        layout.addWidget(options)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.panes = {
            "left": FilePane("left", "左侧：上传前原始文件", "扫描后保存清单；作为可信基准。"),
            "right": FilePane("right", "右侧：网盘重新下载的文件", "加载下载目录并扫描，与左侧基准逐项比对。"),
        }
        splitter.addWidget(self.panes["left"])
        splitter.addWidget(self.panes["right"])
        splitter.setSizes([690, 690])
        layout.addWidget(splitter, 1)

        legend = QHBoxLayout()
        for status, text in (("match", "绿色：一致"), ("different", "黄色：不一致"), ("unmatched", "灰色：未配对或待扫描")):
            label = QLabel(text)
            label.setStyleSheet(f"background: {STATUS_COLORS[status].name()}; padding: 4px 8px; border-radius: 4px;")
            legend.addWidget(label)
        legend.addStretch()
        layout.addLayout(legend)

        self.progress = QProgressBar(self)
        self.progress.setFixedWidth(230)
        self.progress.setVisible(False)
        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.status.addPermanentWidget(self.progress)
        self.status.showMessage("将文件或文件夹拖入任意一侧，即会自动建立清单并开始扫描。")

        self.scan_both_button.clicked.connect(self.scan_unhashed_both)
        self.cancel_button.clicked.connect(self.cancel_scans)
        for pane in self.panes.values():
            pane.load_folder_requested.connect(self.choose_folder)
            pane.load_files_requested.connect(self.choose_files)
            pane.load_manifest_requested.connect(self.choose_manifest)
            pane.save_manifest_requested.connect(self.choose_save_manifest)
            pane.scan_requested.connect(self.rescan_side)
            pane.clear_requested.connect(self.clear_side)
            pane.paths_dropped.connect(self.add_paths)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: #F7F8FA; }
            #appHeading { font-size: 22px; font-weight: 700; color: #20242A; }
            #appDetail, #paneSubtitle { color: #626973; }
            #paneTitle { font-size: 16px; font-weight: 700; color: #2A3441; }
            QFrame#leftPane, QFrame#rightPane, QGroupBox {
                background: white; border: 1px solid #DDE1E6; border-radius: 8px;
            }
            QGroupBox { font-weight: 600; padding-top: 11px; }
            QTableWidget { border: 1px solid #DDE1E6; border-radius: 5px; gridline-color: #E7EAEE; }
            QHeaderView::section { background: #F2F4F6; border: 0; border-bottom: 1px solid #DDE1E6; padding: 6px; font-weight: 600; }
            QPushButton { padding: 6px 10px; }
            """
        )

    @property
    def selected_algorithm(self) -> str:
        return str(self.algorithm_combo.currentData())

    @property
    def selected_mode(self) -> ScanMode:
        return self.mode_combo.currentData()

    def choose_folder(self, side: str) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择要扫描的文件夹")
        if path:
            self.add_paths(side, [path])

    def choose_files(self, side: str) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择一个或多个文件")
        if paths:
            self.add_paths(side, paths)

    def choose_manifest(self, side: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "加载哈希清单", filter="哈希清单 (*.json);;所有文件 (*)")
        if not path:
            return
        if self.records[side] and not self._confirm_replace(side, "加载清单会替换当前列表"):
            return
        try:
            self.records[side] = load_manifest(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "无法加载清单", str(exc))
            return
        self.rebuild_table(side)
        self.refresh_comparison()
        self.status.showMessage(f"已加载 {len(self.records[side]):,} 条清单记录。", 5000)

    def choose_save_manifest(self, side: str) -> None:
        if not self.records[side]:
            QMessageBox.information(self, "没有可保存的记录", "请先加载并扫描文件。")
            return
        unhashed = sum(1 for record in self.records[side] if not record.is_hashed)
        if unhashed:
            answer = QMessageBox.warning(
                self,
                "存在未完成哈希的文件",
                f"当前有 {unhashed} 个文件没有有效哈希。保存的清单将不能完整校验。仍要保存吗？",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Save:
                return
        path, _ = QFileDialog.getSaveFileName(self, "保存哈希清单", "hash-manifest.json", "哈希清单 (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            save_manifest(path, self.records[side], "原始文件" if side == "left" else "下载文件")
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.status.showMessage(f"清单已保存：{Path(path).name}", 5000)

    def _confirm_replace(self, side: str, action: str) -> bool:
        label = "左侧" if side == "left" else "右侧"
        answer = QMessageBox.question(
            self,
            "替换当前列表？",
            f"{label}{action}，当前记录会被移除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def add_paths(self, side: str, paths: list[str]) -> None:
        if not paths:
            return
        worker = DiscoverWorker(side, paths)
        self.discover_workers.append(worker)
        worker.completed.connect(self.on_paths_discovered)
        worker.failed.connect(lambda _, message: QMessageBox.critical(self, "读取文件列表失败", message))
        self._start_thread(worker)
        self.status.showMessage("正在读取文件列表…")

    @Slot(str, object)
    def on_paths_discovered(self, side: str, new_records: object) -> None:
        if not isinstance(new_records, list):
            return
        existing = {str(Path(record.source_path).resolve()) for record in self.records[side] if record.source_path}
        added = [record for record in new_records if record.source_path and str(Path(record.source_path).resolve()) not in existing]
        self.records[side].extend(added)
        self.records[side].sort(key=lambda record: record.relative_path.casefold())
        self.rebuild_table(side)
        self.refresh_comparison()
        if added:
            self.status.showMessage(f"已加入 {len(added):,} 个文件，开始扫描…")
            self.start_scan(side, added)
        else:
            self.status.showMessage("没有发现可新增的本地文件。", 4000)

    def rescan_side(self, side: str) -> None:
        local_records = [record for record in self.records[side] if record.has_local_file]
        if not local_records:
            QMessageBox.information(self, "没有本地文件", "当前侧只有清单记录或为空，无法重新扫描。")
            return
        self.start_scan(side, local_records)

    def scan_unhashed_both(self) -> None:
        for side in ("left", "right"):
            pending = [record for record in self.records[side] if record.has_local_file and not record.is_hashed]
            if pending:
                self.start_scan(side, pending)
        if not self.scan_workers:
            self.status.showMessage("没有需要扫描的本地文件。", 4000)

    def start_scan(self, side: str, targets: list[FileRecord]) -> None:
        if side in self.scan_workers:
            QMessageBox.information(self, "该侧正在扫描", "请等待当前扫描完成，或先取消扫描。")
            return
        if not targets:
            return
        for record in targets:
            record.digest = None
            record.algorithm = None
            record.error = None
        self.rebuild_table(side)
        self.refresh_comparison()
        worker = ScanWorker(side, targets, self.selected_algorithm, self.selected_mode, self.workers_spin.value())
        self.scan_workers[side] = worker
        self._active_total += len(targets)
        worker.progress.connect(self.on_scan_progress)
        worker.failed.connect(lambda _, message: QMessageBox.warning(self, "扫描任务异常", message))
        worker.scan_completed.connect(self.on_scan_finished)
        self._start_thread(worker)
        self._set_scanning_controls()

    @Slot(str, int, int, object)
    def on_scan_progress(self, side: str, done: int, total: int, record: object) -> None:
        if not isinstance(record, FileRecord):
            return
        self._active_done += 1
        self.update_record_row(side, record)
        self.progress.setVisible(True)
        self.progress.setMaximum(max(1, self._active_total))
        self.progress.setValue(min(self._active_done, self._active_total))
        self.status.showMessage(f"正在扫描 {side == 'left' and '左侧' or '右侧'}：{done:,}/{total:,} · {record.display_name}")

    @Slot(str, bool)
    def on_scan_finished(self, side: str, cancelled: bool) -> None:
        self.scan_workers.pop(side, None)
        self.rebuild_table(side)
        self.refresh_comparison()
        if not self.scan_workers:
            self.progress.setVisible(False)
            self._active_total = 0
            self._active_done = 0
            self.status.showMessage("扫描已取消。" if cancelled else "扫描完成，已刷新两侧比对结果。", 6000)
        self._set_scanning_controls()

    def cancel_scans(self) -> None:
        for worker in self.scan_workers.values():
            worker.cancel_event.set()
        self.status.showMessage("正在请求取消扫描；当前读块结束后停止。")

    def _set_scanning_controls(self) -> None:
        scanning = bool(self.scan_workers)
        self.cancel_button.setEnabled(scanning)
        self.mode_combo.setEnabled(not scanning)
        self.algorithm_combo.setEnabled(not scanning)
        self.workers_spin.setEnabled(not scanning)

    def clear_side(self, side: str) -> None:
        if side in self.scan_workers:
            QMessageBox.warning(self, "无法清空", "请先取消并等待该侧扫描结束。")
            return
        if self.records[side] and not self._confirm_replace(side, "清空会替换当前列表"):
            return
        self.records[side] = []
        self.rebuild_table(side)
        self.refresh_comparison()

    def rebuild_table(self, side: str) -> None:
        table = self.panes[side].table
        table.setUpdatesEnabled(False)
        table.setRowCount(len(self.records[side]))
        self.row_for_record[side] = {}
        for row, record in enumerate(self.records[side]):
            self.row_for_record[side][id(record)] = row
            self._write_row(table, row, record, None)
        table.setUpdatesEnabled(True)
        self.panes[side].set_count(len(self.records[side]))

    def update_record_row(self, side: str, record: FileRecord) -> None:
        row = self.row_for_record[side].get(id(record))
        if row is not None:
            self._write_row(self.panes[side].table, row, record, None)

    def refresh_comparison(self) -> None:
        left_status, right_status = compare_records(self.records["left"], self.records["right"])
        for side, statuses in (("left", left_status), ("right", right_status)):
            table = self.panes[side].table
            for record in self.records[side]:
                row = self.row_for_record[side].get(id(record))
                if row is not None:
                    self._write_row(table, row, record, statuses.get(id(record)))

    def _write_row(
        self, table: FileTable, row: int, record: FileRecord, comparison: ComparisonResult | None
    ) -> None:
        if record.error:
            hash_text = record.error
        elif record.digest:
            hash_text = f"{record.algorithm or ''}  {record.digest}"
        else:
            hash_text = "等待扫描" if record.has_local_file else "来自清单：无有效哈希"
        status_text = STATUS_TEXT[comparison.status] if comparison else "等待比对"
        items = [
            QTableWidgetItem(record.relative_path),
            QTableWidgetItem(format_size(record.size)),
            QTableWidgetItem(hash_text),
            QTableWidgetItem(status_text),
        ]
        for column, item in enumerate(items):
            item.setToolTip(comparison.message if comparison else hash_text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if column == 1 else Qt.AlignmentFlag.AlignLeft))
            table.setItem(row, column, item)
        if comparison:
            color = STATUS_COLORS[comparison.status]
            for item in items:
                item.setBackground(color)

    def _start_thread(self, thread: QThread) -> None:
        """启动 QThread，并在主线程内于结束后释放它。

        不在工作线程中删除 PySide QObject；这正是旧版“重新扫描”崩溃的根因。
        """

        thread.finished.connect(self.on_background_thread_finished)
        self._threads.append(thread)
        thread.start()

    @Slot()
    def on_background_thread_finished(self) -> None:
        thread = self.sender()
        if not isinstance(thread, QThread):
            return
        if isinstance(thread, DiscoverWorker) and thread in self.discover_workers:
            self.discover_workers.remove(thread)
        if thread in self._threads:
            self._threads.remove(thread)
        # 此槽运行在 MainWindow 所在线程；QThread 已停止，deleteLater 是安全的。
        thread.deleteLater()

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        if self._threads:
            answer = QMessageBox.question(
                self,
                "后台任务仍在进行",
                "退出将取消正在进行的文件扫描或目录读取。确定退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.cancel_scans()
            for worker in self.discover_workers:
                worker.cancel_event.set()
            # 避免窗口销毁仍在运行的 QThread。正常情况下最多等待当前 4 MiB 读块结束。
            for thread in tuple(self._threads):
                thread.wait()
        event.accept()


def main() -> None:
    app = QApplication([])
    app.setApplicationName("本地文件快速校验")
    window = MainWindow()
    window.show()
    app.exec()

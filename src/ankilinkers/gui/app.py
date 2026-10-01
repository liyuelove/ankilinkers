"""PySide6 图形界面：主窗口与程序入口。"""
from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from ..anki_client import AnkiClient
from ..config import Config, db_path, load
from ..siyuan_client import SiYuanClient
from ..storage import Storage
from .page_connect import ConnectPage
from .page_mapping import MappingPage
from .page_sync import SyncPage
from .widget_log import LogWidget


class MainWindow(QMainWindow):
    """主窗口：左侧导航（连接/映射/同步/日志），右侧页面堆栈。"""

    def __init__(self, cfg: Config | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("ankiLinkers · 思源闪卡 → Anki 同步")
        self.resize(1024, 640)

        self.cfg = cfg or load()
        self.storage = Storage(db_path())
        self.connection_ok = False

        self.page_connect = ConnectPage(self)
        self.page_mapping = MappingPage(self)
        self.page_sync = SyncPage(self)
        self.page_log = LogWidget(self)

        self._stack = QStackedWidget()
        for page in (self.page_connect, self.page_mapping, self.page_sync, self.page_log):
            self._stack.addWidget(page)

        self._nav = QListWidget()
        self._nav.setFixedWidth(140)
        for name in ("连接检测", "映射配置", "同步", "日志"):
            QListWidgetItem(name, self._nav)
        self._nav.setCurrentRow(0)
        self._nav.currentRowChanged.connect(self._switch_page)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._nav)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        QTimer.singleShot(0, self.page_connect.start_check)

    def make_clients(self) -> tuple[SiYuanClient, AnkiClient, Storage]:
        """新建一组后端客户端（工作线程专用；httpx/sqlite 连接不可跨线程共享）。"""
        return (
            SiYuanClient(self.cfg.siyuan_base_url, self.cfg.siyuan_token),
            AnkiClient(self.cfg.anki_base_url),
            Storage(db_path()),
        )

    def set_connection_state(self, siyuan_ok: bool, anki_ok: bool) -> None:
        """连接检测结果变化：未全部通过时锁定映射预演与同步操作。"""
        self.connection_ok = siyuan_ok and anki_ok
        self.page_mapping.set_connection_ok(self.connection_ok)
        self.page_sync.set_connection_ok(self.connection_ok)

    def refresh_logs(self) -> None:
        self.page_log.refresh()

    def _switch_page(self, row: int) -> None:
        self._stack.setCurrentIndex(row)
        if self._stack.currentWidget() is self.page_log:
            self.page_log.refresh()

    def closeEvent(self, event: QCloseEvent) -> None:
        running = [
            w for w in (
                self.page_connect.active_worker(),
                self.page_mapping.active_worker(),
                self.page_sync.active_worker(),
            )
            if w is not None and w.isRunning()
        ]
        if running:
            ret = QMessageBox.question(
                self, "退出确认",
                "有任务正在执行，退出将请求取消并等待其结束。确定退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for w in running:
                w.request_cancel()
            for w in running:
                w.wait()
        self.storage.close()
        super().closeEvent(event)


def main() -> int:
    """GUI 入口：创建应用并显示主窗口。"""
    app = QApplication(sys.argv)
    app.setApplicationName("ankiLinkers")
    win = MainWindow()
    win.show()
    return app.exec()

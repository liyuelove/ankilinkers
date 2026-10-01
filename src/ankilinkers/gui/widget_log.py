"""日志页：按批次（batch_id）分组展示同步日志，默认全部折叠，可展开查看明细。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .app import MainWindow


class LogWidget(QWidget):
    """同步日志页：batch_id 分组，默认折叠，提供刷新。"""

    def __init__(self, host: "MainWindow", parent: QWidget | None = None):
        super().__init__(parent)
        self._host = host

        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.clicked.connect(self.refresh)
        hint = QLabel("按批次分组，默认全部折叠；点击批次左侧箭头展开明细。")

        top = QHBoxLayout()
        top.addWidget(self._btn_refresh)
        top.addWidget(hint, 1)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["同步日志"])
        self._tree.setRootIsDecorated(True)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._tree, 1)

        self.refresh()

    def refresh(self) -> None:
        """重新读取日志并重建分组树（保持全部折叠）。"""
        rows = self._host.storage.logs(500)
        self._tree.clear()
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r["batch_id"], []).append(r)
        if not groups:
            self._tree.addTopLevelItem(QTreeWidgetItem(["（暂无日志）"]))
            return
        for batch_id, items in groups.items():
            top = QTreeWidgetItem([f"批次 {batch_id}（{len(items)} 条）"])
            self._tree.addTopLevelItem(top)
            for r in items:
                text = f"{r['created_at']}  [{r['action']}]  {r['detail']}".rstrip()
                QTreeWidgetItem(top, [text])
        # 不调用 expandAll：默认全部折叠

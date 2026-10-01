"""映射配置页：默认卡组、映射规则（笔记本/文档 → 卡组）与保存前预演。"""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import save
from ..sync_engine import SyncEngine
from .worker import Worker

if TYPE_CHECKING:
    from .app import MainWindow


class MappingPage(QWidget):
    """映射配置页：管理默认卡组与卡组映射规则，支持保存前预演影响。"""

    def __init__(self, host: "MainWindow", parent: QWidget | None = None):
        super().__init__(parent)
        self._host = host
        self._worker: Worker | None = None
        self._conn_ok = False
        self._notebooks: list[dict] = []

        # ---- 默认卡组 ----
        self._deck_edit = QLineEdit(host.cfg.default_deck)
        self._btn_save_deck = QPushButton("保存默认卡组")
        self._btn_save_deck.clicked.connect(self._save_default_deck)
        deck_row = QHBoxLayout()
        deck_row.addWidget(QLabel("默认卡组（未命中任何规则的闪卡去向）："))
        deck_row.addWidget(self._deck_edit, 1)
        deck_row.addWidget(self._btn_save_deck)

        # ---- 规则表格 ----
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["类型", "源", "目标卡组", "操作"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        # ---- 新增规则表单 ----
        self._type_combo = QComboBox()
        self._type_combo.addItems(["笔记本", "文档"])
        self._nb_combo = QComboBox()
        self._doc_edit = QLineEdit()
        self._doc_edit.setPlaceholderText("文档 rootID（如 20200812220555-lj3enxa）")
        self._src_stack = QStackedWidget()
        self._src_stack.addWidget(self._nb_combo)
        self._src_stack.addWidget(self._doc_edit)
        self._type_combo.currentIndexChanged.connect(self._src_stack.setCurrentIndex)
        self._deck_combo = QComboBox()
        self._deck_combo.setEditable(True)
        self._btn_fetch = QPushButton("刷新笔记本/卡组列表")
        self._btn_fetch.clicked.connect(self._fetch_lists)
        self._btn_add = QPushButton("添加规则")
        self._btn_add.clicked.connect(self._add_rule)

        form = QFormLayout()
        form.addRow("类型", self._type_combo)
        form.addRow("源", self._src_stack)
        form.addRow("目标卡组", self._deck_combo)
        form_btns = QHBoxLayout()
        form_btns.addWidget(self._btn_fetch)
        form_btns.addWidget(self._btn_add)
        form_btns.addStretch(1)
        form.addRow(form_btns)
        form_box = QGroupBox("新增规则")
        form_box.setLayout(form)

        # ---- 预演影响 ----
        self._btn_rehearse = QPushButton("预演影响")
        self._btn_rehearse.setEnabled(False)
        self._btn_rehearse.clicked.connect(self._rehearse)
        self._rehearse_hint = QLabel("需先通过连接检测。预演只统计各卡组落点数量，不执行任何写入。")
        self._rehearse_hint.setWordWrap(True)
        self._rehearse_out = QPlainTextEdit()
        self._rehearse_out.setReadOnly(True)
        self._rehearse_out.setPlaceholderText("点击「预演影响」查看统计结果。")
        self._rehearse_out.setMaximumBlockCount(1000)
        rehearse_row = QHBoxLayout()
        rehearse_row.addWidget(self._btn_rehearse)
        rehearse_row.addWidget(self._rehearse_hint, 1)
        rehearse_box = QGroupBox("预演影响")
        rehearse_layout = QVBoxLayout(rehearse_box)
        rehearse_layout.addLayout(rehearse_row)
        rehearse_layout.addWidget(self._rehearse_out)

        rules_box = QGroupBox("映射规则（文档级规则优先于笔记本级）")
        rules_layout = QVBoxLayout(rules_box)
        rules_layout.addWidget(self._table)

        layout = QVBoxLayout(self)
        layout.addLayout(deck_row)
        layout.addWidget(rules_box, 1)
        layout.addWidget(form_box)
        layout.addWidget(rehearse_box, 1)

        self._reload_rules()

    # ---- 默认卡组 ----
    def _save_default_deck(self) -> None:
        deck = self._deck_edit.text().strip()
        if not deck:
            QMessageBox.warning(self, "默认卡组", "卡组名不能为空。")
            return
        self._host.cfg.default_deck = deck
        save(self._host.cfg)
        QMessageBox.information(self, "默认卡组", f"已保存默认卡组：{deck}")

    # ---- 规则表 ----
    def _reload_rules(self) -> None:
        rules = self._host.storage.rules()
        nb_names = {nb["id"]: nb.get("name", "") for nb in self._notebooks}
        self._table.setRowCount(len(rules))
        for row, r in enumerate(rules):
            src_type = "笔记本" if r["src_type"] == "notebook" else "文档"
            src = r["src_key"]
            if r["src_type"] == "notebook" and nb_names.get(src):
                src = f"{nb_names[src]}（{src}）"
            self._table.setItem(row, 0, QTableWidgetItem(src_type))
            self._table.setItem(row, 1, QTableWidgetItem(src))
            self._table.setItem(row, 2, QTableWidgetItem(r["deck_name"]))
            btn = QPushButton("删除")
            btn.clicked.connect(lambda _checked=False, rid=r["id"]: self._remove_rule(rid))
            self._table.setCellWidget(row, 3, btn)

    def _remove_rule(self, rule_id: int) -> None:
        self._host.storage.remove_rule(rule_id)
        self._reload_rules()

    def _add_rule(self) -> None:
        if self._type_combo.currentIndex() == 0:
            src_type, src_key = "notebook", self._nb_combo.currentData()
            if not src_key:
                QMessageBox.warning(self, "添加规则", "请先点击「刷新笔记本/卡组列表」并选择一个笔记本。")
                return
        else:
            src_type, src_key = "doc", self._doc_edit.text().strip()
            if not src_key:
                QMessageBox.warning(self, "添加规则", "请输入文档 rootID。")
                return
        deck = self._deck_combo.currentText().strip()
        if not deck:
            QMessageBox.warning(self, "添加规则", "请输入或选择目标卡组。")
            return
        self._host.storage.add_rule(src_type, src_key, deck)
        self._reload_rules()

    # ---- 列表拉取 ----
    def _fetch_lists(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._btn_fetch.setEnabled(False)
        self._worker = Worker(self._do_fetch_lists, self)
        self._worker.succeeded.connect(self._fill_lists)
        self._worker.failed.connect(
            lambda msg: QMessageBox.warning(self, "刷新列表失败", msg)
        )
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _do_fetch_lists(self, progress, is_cancelled) -> dict:
        siyuan, anki, storage = self._host.make_clients()
        try:
            return {"notebooks": siyuan.ls_notebooks(), "decks": anki.deck_names()}
        finally:
            siyuan.close()
            anki.close()
            storage.close()

    def _fill_lists(self, result: dict) -> None:
        self._notebooks = result["notebooks"]
        self._nb_combo.clear()
        for nb in self._notebooks:
            label = f"{nb.get('name', '')}（{nb['id']}）"
            if nb.get("closed"):
                label += "（已关闭）"
            self._nb_combo.addItem(label, nb["id"])
        self._deck_combo.clear()
        self._deck_combo.addItems(result["decks"])
        self._reload_rules()

    # ---- 预演 ----
    def _rehearse(self) -> None:
        if not self._conn_ok:
            QMessageBox.information(self, "预演影响", "连接检测未全部通过，请先在「连接检测」页完成检测。")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._btn_rehearse.setEnabled(False)
        self._rehearse_hint.setText("预演中（只读统计，不会写入）…")
        self._worker = Worker(self._do_rehearse, self)
        self._worker.progressed.connect(
            lambda stage, done, total, msg: self._rehearse_hint.setText(f"预演中：{msg}")
        )
        self._worker.succeeded.connect(self._show_rehearsal)
        self._worker.failed.connect(
            lambda msg: QMessageBox.warning(self, "预演失败", msg)
        )
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _do_rehearse(self, progress, is_cancelled) -> dict:
        siyuan, anki, storage = self._host.make_clients()
        try:
            engine = SyncEngine(self._host.cfg, siyuan, anki, storage)
            plan = engine.build_plan(progress=progress)
        finally:
            siyuan.close()
            anki.close()
            storage.close()
        targets = Counter(c.deck for c in (*plan.adds, *plan.updates, *plan.moves))
        delete_from = Counter(c.old_deck for c in plan.deletes)
        return {
            "adds": len(plan.adds),
            "updates": len(plan.updates),
            "moves": len(plan.moves),
            "deletes": len(plan.deletes),
            "skips": len(plan.skips),
            "targets": dict(targets),
            "delete_from": dict(delete_from),
        }

    def _show_rehearsal(self, result: dict) -> None:
        lines = [
            "预演结果（未执行任何写入）：",
            f"  新增 {result['adds']}，更新 {result['updates']}，"
            f"移动 {result['moves']}，删除 {result['deletes']}，跳过 {result['skips']}",
            "",
            "各卡组落点（新增/更新/移动的目标卡组）：",
        ]
        if result["targets"]:
            lines += [f"  {deck}：{n} 张" for deck, n in sorted(result["targets"].items())]
        else:
            lines.append("  （无）")
        lines.append("删除来源卡组：")
        if result["delete_from"]:
            lines += [f"  {deck}：{n} 张" for deck, n in sorted(result["delete_from"].items())]
        else:
            lines.append("  （无）")
        self._rehearse_out.setPlainText("\n".join(lines))

    # ---- 状态 ----
    def set_connection_ok(self, ok: bool) -> None:
        self._conn_ok = ok
        if self._worker is None or not self._worker.isRunning():
            self._btn_rehearse.setEnabled(ok)

    def _on_finished(self) -> None:
        self._btn_fetch.setEnabled(True)
        self._btn_rehearse.setEnabled(self._conn_ok)
        self._rehearse_hint.setText("预演只统计各卡组落点数量，不执行任何写入。")
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def active_worker(self) -> Worker | None:
        return self._worker

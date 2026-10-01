"""同步页：生成预览 → 确认（必要时自动备份）→ 执行（进度/取消）→ 报告。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..backup import create_backup
from ..models import Change, Plan
from ..sync_engine import SyncEngine
from .worker import Worker

if TYPE_CHECKING:
    from .app import MainWindow

_STAGE_NAMES = {
    "enumerate": "枚举闪卡",
    "parse": "解析闪卡",
    "backup": "备份",
    "add": "新增",
    "update": "更新",
    "move": "移动",
    "delete": "删除",
}

_PREVIEW_ITEM_LIMIT = 500  # 每组最多展示的子项数，超出以汇总节点代替


class SyncPage(QWidget):
    """同步页：预览分组展示，执行前按需备份，执行中可取消。"""

    def __init__(self, host: "MainWindow", parent: QWidget | None = None):
        super().__init__(parent)
        self._host = host
        self._worker: Worker | None = None
        self._plan: Plan | None = None
        self._conn_ok = False

        self._lock_hint = QLabel("连接检测未全部通过，同步功能已锁定。请先在「连接检测」页完成检测。")
        self._lock_hint.setStyleSheet("color: #c0392b;")
        self._lock_hint.setWordWrap(True)

        self._btn_preview = QPushButton("生成预览")
        self._btn_preview.setEnabled(False)
        self._btn_preview.clicked.connect(self._preview)
        self._btn_sync = QPushButton("开始同步")
        self._btn_sync.setEnabled(False)
        self._btn_sync.clicked.connect(self._start_sync)
        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._cancel)

        btns = QHBoxLayout()
        btns.addWidget(self._btn_preview)
        btns.addWidget(self._btn_sync)
        btns.addWidget(self._btn_cancel)
        btns.addStretch(1)

        self._hint = QLabel("提示：同步期间请关闭 Anki 的「浏览/编辑」窗口，否则正在被查看的笔记可能更新不生效。")
        self._hint.setStyleSheet("color: #7f8c8d;")
        self._hint.setWordWrap(True)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._status = QLabel("就绪。")
        self._status.setWordWrap(True)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["同步计划预览"])
        self._tree.setRootIsDecorated(True)

        self._report = QPlainTextEdit()
        self._report.setReadOnly(True)
        self._report.setPlaceholderText("同步完成后在此显示结果报告。")
        self._report.setMaximumBlockCount(1000)

        layout = QVBoxLayout(self)
        layout.addWidget(self._lock_hint)
        layout.addLayout(btns)
        layout.addWidget(self._hint)
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        layout.addWidget(self._tree, 2)
        layout.addWidget(self._report, 1)

    # ---- 预览 ----
    def _preview(self) -> None:
        if not self._conn_ok or (self._worker is not None and self._worker.isRunning()):
            return
        self._btn_preview.setEnabled(False)
        self._btn_sync.setEnabled(False)
        self._report.clear()
        self._status.setText("正在生成预览…")
        self._worker = Worker(self._do_build_plan, self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.succeeded.connect(self._show_plan)
        self._worker.failed.connect(lambda msg: self._on_failed("预览失败", msg))
        self._worker.finished.connect(self._on_preview_finished)
        self._worker.start()

    def _do_build_plan(self, progress, is_cancelled) -> Plan:
        siyuan, anki, storage = self._host.make_clients()
        try:
            engine = SyncEngine(self._host.cfg, siyuan, anki, storage)
            return engine.build_plan(progress=progress)
        finally:
            siyuan.close()
            anki.close()
            storage.close()

    @staticmethod
    def _fmt_change(c: Change) -> str:
        if c.action == "move":
            return f"{c.summary}    {c.old_deck} → {c.deck}"
        if c.action == "delete":
            return f"{c.summary}（将删除 Anki 笔记）"
        return f"{c.summary}    [{c.deck}]"

    def _show_plan(self, plan: Plan) -> None:
        self._plan = plan
        self._tree.clear()
        for title, items in (
            ("新增", plan.adds),
            ("更新", plan.updates),
            ("移动", plan.moves),
            ("删除", plan.deletes),
        ):
            top = QTreeWidgetItem([f"{title}（{len(items)}）"])
            self._tree.addTopLevelItem(top)
            for c in items[:_PREVIEW_ITEM_LIMIT]:
                QTreeWidgetItem(top, [self._fmt_change(c)])
            if len(items) > _PREVIEW_ITEM_LIMIT:
                QTreeWidgetItem(top, [f"… 共 {len(items)} 条，仅显示前 {_PREVIEW_ITEM_LIMIT} 条"])
        skip_top = QTreeWidgetItem([f"跳过（{len(plan.skips)}）"])
        self._tree.addTopLevelItem(skip_top)
        for cid, reason in plan.skips[:_PREVIEW_ITEM_LIMIT]:
            QTreeWidgetItem(skip_top, [f"{cid}：{reason}"])
        if len(plan.skips) > _PREVIEW_ITEM_LIMIT:
            QTreeWidgetItem(skip_top, [f"… 共 {len(plan.skips)} 条"])
        self._tree.expandAll()
        if plan.is_empty():
            self._status.setText("预览完成：无变更。")
        else:
            self._status.setText(
                f"预览完成：新增 {len(plan.adds)}，更新 {len(plan.updates)}，"
                f"移动 {len(plan.moves)}，删除 {len(plan.deletes)}，跳过 {len(plan.skips)}。"
            )
        self._update_sync_button()

    # ---- 执行 ----
    def _start_sync(self) -> None:
        plan = self._plan
        if plan is None or plan.is_empty():
            return
        if self._worker is not None and self._worker.isRunning():
            return
        cfg = self._host.cfg
        need_backup = bool(plan.deletes) or plan.change_count >= cfg.backup_threshold
        lines = [
            f"本次同步计划：新增 {len(plan.adds)}，更新 {len(plan.updates)}，"
            f"移动 {len(plan.moves)}，删除 {len(plan.deletes)}。"
        ]
        if need_backup:
            reasons = []
            if plan.deletes:
                reasons.append(f"包含 {len(plan.deletes)} 项删除")
            if plan.change_count >= cfg.backup_threshold:
                reasons.append(f"变更数 {plan.change_count} 达到备份阈值 {cfg.backup_threshold}")
            lines.append(
                "注意：" + "；".join(reasons) + "。执行前将自动备份受影响的 Anki 笔记与本地映射库。"
            )
        lines.append("确认开始同步？")
        ret = QMessageBox.question(
            self, "同步确认", "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._btn_preview.setEnabled(False)
        self._btn_sync.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._report.clear()
        self._worker = Worker(lambda p, c: self._do_sync(plan, need_backup, p, c), self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_report)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.failed.connect(lambda msg: self._on_failed("同步失败", msg))
        self._worker.finished.connect(self._on_sync_finished)
        self._worker.start()

    def _do_sync(self, plan: Plan, need_backup: bool, progress, is_cancelled) -> dict:
        siyuan, anki, storage = self._host.make_clients()
        try:
            backup_path = ""
            if need_backup:
                progress("backup", 0, 1, "正在备份受影响的笔记…")
                affected = [
                    c.note_id
                    for c in (*plan.updates, *plan.deletes, *plan.moves)
                    if c.note_id is not None
                ]
                backup_path = str(create_backup(self._host.cfg, anki, affected, "sync"))
                progress("backup", 1, 1, f"备份完成：{backup_path}")
            engine = SyncEngine(self._host.cfg, siyuan, anki, storage)
            report = engine.execute(plan, progress=progress, cancel=is_cancelled)
            report["backup_path"] = backup_path
            return report
        finally:
            siyuan.close()
            anki.close()
            storage.close()

    def _on_report(self, report: dict) -> None:
        lines: list[str] = []
        if report.get("backup_path"):
            lines.append(f"备份路径：{report['backup_path']}")
        lines.append(str(report.get("summary", "")))
        for w in report.get("warnings", []):
            lines.append(f"! {w}")
        for kind, label in (("add", "新增"), ("update", "更新"), ("move", "移动"), ("delete", "删除")):
            for cid, err in report[kind]["fail"]:
                lines.append(f"✗ {label} {cid}：{err}")
        self._report.setPlainText("\n".join(lines))
        self._status.setText("同步完成。")
        self._host.refresh_logs()

    def _on_cancelled(self) -> None:
        self._status.setText("已取消（部分批次可能已执行）。")
        self._report.setPlainText("同步被用户取消，已执行的批次不会回滚。建议重新生成预览确认当前状态。")
        self._host.refresh_logs()

    def _on_failed(self, title: str, msg: str) -> None:
        self._status.setText(f"{title}：{msg}")
        QMessageBox.critical(self, title, msg)

    def _cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self._btn_cancel.setEnabled(False)
            self._status.setText("正在取消（将在当前批次结束后停止）…")

    # ---- 状态 ----
    def _on_progress(self, stage: str, done: int, total: int, msg: str) -> None:
        name = _STAGE_NAMES.get(stage, stage)
        self._status.setText(f"[{name}] {done}/{total} {msg}")
        if total > 0:
            self._progress.setMaximum(total)
            self._progress.setValue(min(done, total))

    def _update_sync_button(self) -> None:
        running = self._worker is not None and self._worker.isRunning()
        self._btn_sync.setEnabled(
            self._conn_ok and not running
            and self._plan is not None and not self._plan.is_empty()
        )

    def set_connection_ok(self, ok: bool) -> None:
        self._conn_ok = ok
        self._lock_hint.setVisible(not ok)
        running = self._worker is not None and self._worker.isRunning()
        self._btn_preview.setEnabled(ok and not running)
        self._update_sync_button()

    def _on_preview_finished(self) -> None:
        self._btn_preview.setEnabled(self._conn_ok)
        self._dispose_worker()
        self._update_sync_button()

    def _on_sync_finished(self) -> None:
        # 执行/取消后本地映射已变化，旧预览不再可靠，需重新生成
        self._plan = None
        self._btn_cancel.setEnabled(False)
        self._btn_preview.setEnabled(self._conn_ok)
        self._dispose_worker()
        self._update_sync_button()

    def _dispose_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def active_worker(self) -> Worker | None:
        return self._worker

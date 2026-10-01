"""连接检测页：分别检测思源内核与 AnkiConnect，逐项给出结论与修复指引。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..anki_client import AnkiClient, AnkiConnectError
from ..siyuan_client import SiYuanClient, SiYuanError
from .worker import Worker

if TYPE_CHECKING:
    from .app import MainWindow

CheckItem = tuple[bool, str, str]  # (是否通过, 检测项, 说明/修复指引)


class ConnectPage(QWidget):
    """连接检测页：思源（内核/笔记本/闪卡接口）与 Anki（连接/模板字段）。"""

    def __init__(self, host: "MainWindow", parent: QWidget | None = None):
        super().__init__(parent)
        self._host = host
        self._worker: Worker | None = None

        self._btn_check = QPushButton("开始检测")
        self._btn_check.clicked.connect(self.start_check)
        self._summary = QLabel("尚未检测。检测全部通过后才会解锁同步功能。")
        self._summary.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(self._btn_check)
        top.addWidget(self._summary, 1)

        self._siyuan_box, self._siyuan_layout = self._make_group("思源笔记")
        self._anki_box, self._anki_layout = self._make_group("Anki")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._siyuan_box)
        layout.addWidget(self._anki_box)
        layout.addStretch(1)

    @staticmethod
    def _make_group(title: str) -> tuple[QGroupBox, QVBoxLayout]:
        box = QGroupBox(title)
        return box, QVBoxLayout(box)

    def start_check(self) -> None:
        """启动一次检测（工作线程执行，不阻塞界面）。"""
        if self._worker is not None and self._worker.isRunning():
            return
        self._btn_check.setEnabled(False)
        self._summary.setText("检测中…")
        self._worker = Worker(self._run_checks, self)
        self._worker.succeeded.connect(self._render)
        self._worker.failed.connect(self._render_error)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _run_checks(self, progress, is_cancelled) -> dict:
        cfg = self._host.cfg
        siyuan_items: list[CheckItem] = []
        anki_items: list[CheckItem] = []

        siyuan_ok = False
        if not cfg.siyuan_token:
            siyuan_items.append((
                False, "API token",
                f"未找到思源 API token。请确认思源笔记已启动，且工作区路径配置正确"
                f"（当前：{cfg.siyuan_workspace}，将自动读取 conf/conf.json）；"
                "也可在思源 设置 → 关于 中查看 API token 后写入配置文件。",
            ))
        else:
            try:
                with SiYuanClient(cfg.siyuan_base_url, cfg.siyuan_token) as s:
                    siyuan_items.append((
                        True, "内核连接", f"版本 {s.version()}（{cfg.siyuan_base_url}）"
                    ))
                    notebooks = s.ls_notebooks()
                    siyuan_items.append((True, "笔记本", f"共 {len(notebooks)} 个"))
                    data = s.get_riff_cards(deck_id="", page=1, page_size=1)
                    siyuan_items.append((
                        True, "Riff 闪卡接口", f"可用，现有闪卡 {data.get('total', 0)} 张"
                    ))
                    siyuan_ok = True
            except SiYuanError as e:
                siyuan_items.append((
                    False, "内核连接",
                    f"{e}\n修复指引：1) 确认思源笔记已启动；"
                    f"2) 内核地址 {cfg.siyuan_base_url} 可访问；"
                    "3) API token 正确（思源 设置 → 关于 → API token）。",
                ))

        anki_ok = True
        try:
            with AnkiClient(cfg.anki_base_url) as a:
                anki_items.append((
                    True, "AnkiConnect 连接", f"协议版本 {a.version()}（{cfg.anki_base_url}）"
                ))
                decks = a.deck_names()
                anki_items.append((True, "卡组", f"共 {len(decks)} 个"))
                models = a.model_names()
                for model, required, label in (
                    (cfg.model_qa, {cfg.field_qa_front, cfg.field_qa_back}, "问答模板"),
                    (cfg.model_cloze, {cfg.field_cloze_text, cfg.field_cloze_extra}, "挖空模板"),
                ):
                    if model not in models:
                        anki_items.append((
                            False, f"{label}「{model}」",
                            "模板不存在。请在 Anki 中创建该模板（工具 → 管理笔记模板），"
                            "或修改配置文件中的模板名（model_qa / model_cloze）。",
                        ))
                        anki_ok = False
                        continue
                    have = set(a.model_field_names(model))
                    missing = required - have
                    if missing:
                        anki_items.append((
                            False, f"{label}「{model}」",
                            f"缺少字段 {sorted(missing)}（现有字段 {sorted(have)}）。"
                            "请在 Anki 模板字段管理中补齐，或修改配置文件中的字段名。",
                        ))
                        anki_ok = False
                    else:
                        anki_items.append((
                            True, f"{label}「{model}」", f"字段齐备 {sorted(have)}"
                        ))
        except AnkiConnectError as e:
            anki_items.append((
                False, "AnkiConnect 连接",
                f"{e}\n修复指引：1) 确认 Anki 正在运行；2) 已安装 AnkiConnect 插件"
                "（Anki → 工具 → 插件 → 获取插件，代码 2055492159）并重启 Anki；"
                f"3) 地址 {cfg.anki_base_url} 可访问。",
            ))
            anki_ok = False

        return {
            "siyuan": siyuan_items,
            "anki": anki_items,
            "siyuan_ok": siyuan_ok,
            "anki_ok": anki_ok,
        }

    def _render(self, result: dict) -> None:
        self._fill(self._siyuan_layout, result["siyuan"])
        self._fill(self._anki_layout, result["anki"])
        siyuan_ok, anki_ok = result["siyuan_ok"], result["anki_ok"]
        if siyuan_ok and anki_ok:
            self._summary.setText("✓ 连接检测全部通过，同步功能已解锁。")
        else:
            failed = [name for name, ok in (("思源笔记", siyuan_ok), ("Anki", anki_ok)) if not ok]
            self._summary.setText(
                f"✗ {'、'.join(failed)} 检测未通过，同步功能保持锁定。请按下方指引修复后重新检测。"
            )
        self._host.set_connection_state(siyuan_ok, anki_ok)

    def _render_error(self, msg: str) -> None:
        self._summary.setText(f"✗ 检测过程出错：{msg}")
        self._host.set_connection_state(False, False)

    @staticmethod
    def _fill(layout: QVBoxLayout, items: list[CheckItem]) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for ok, title, detail in items:
            mark = "✓" if ok else "✗"
            label = QLabel(f"{mark} {title}：{detail}")
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {'#1e8449' if ok else '#c0392b'};")
            layout.addWidget(label)

    def _on_finished(self) -> None:
        self._btn_check.setEnabled(True)
        self._btn_check.setText("重新检测")
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def active_worker(self) -> Worker | None:
        return self._worker

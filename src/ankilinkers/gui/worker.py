"""GUI 通用工作线程：耗时后端调用在后台执行，结果经信号回主线程。"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from ..sync_engine import SyncCancelled

# fn(progress_cb, is_cancelled_cb) -> 结果对象
WorkFn = Callable[[Callable[[str, int, int, str], None], Callable[[], bool]], Any]


class Worker(QThread):
    """执行一个工作函数的 QThread。

    信号：progressed(阶段, 已完成, 总数, 消息)、succeeded(结果)、
    failed(错误文本)、cancelled()（工作函数抛出 SyncCancelled 时触发）。
    """

    progressed = Signal(str, int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn: WorkFn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel_requested = False

    def request_cancel(self) -> None:
        """请求取消；工作函数通过 is_cancelled 回调在合适时机响应。"""
        self._cancel_requested = True

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested

    def run(self) -> None:
        try:
            result = self._fn(self.progressed.emit, self.is_cancel_requested)
        except SyncCancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - 工作线程内任何异常都转为失败信号
            self.failed.emit(str(e))
        else:
            self.succeeded.emit(result)

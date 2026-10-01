"""同步过程使用的数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .parser import ParsedCard


@dataclass
class SourceCard:
    """思源侧枚举到的一张闪卡。"""

    card_id: str  # riffCardID（缺失时退化为 block_id）
    block_id: str
    box: str  # 笔记本 ID
    root_id: str  # 所属文档 ID
    hpath: str  # 人类可读路径
    updated: str
    block_type: str = ""  # NodeHeading / NodeSuperBlock / NodeList / NodeParagraph ...


@dataclass
class Change:
    """执行计划中的一个操作。"""

    action: str  # add | update | delete | move
    card_id: str
    deck: str = ""  # 目标卡组（move 时为新卡组）
    note_id: int | None = None  # update/delete/move 时的 Anki note id
    parsed: ParsedCard | None = None  # add/update 的解析结果
    old_deck: str = ""  # move 时的原卡组
    summary: str = ""  # 预览用摘要
    block_id: str = ""
    block_updated: str = ""
    box: str = ""  # 所属笔记本 ID


@dataclass
class Plan:
    adds: list[Change] = field(default_factory=list)
    updates: list[Change] = field(default_factory=list)
    deletes: list[Change] = field(default_factory=list)
    moves: list[Change] = field(default_factory=list)
    skips: list[tuple[str, str]] = field(default_factory=list)  # (card_id, 原因)
    notices: list[str] = field(default_factory=list)  # 非阻塞提示（如接管已有笔记）

    @property
    def change_count(self) -> int:
        """计入备份阈值的变更数（纯移动无内容风险，不计入）。"""
        return len(self.adds) + len(self.updates) + len(self.deletes)

    @property
    def total_ops(self) -> int:
        return self.change_count + len(self.moves)

    def is_empty(self) -> bool:
        return self.total_ops == 0 and not self.skips

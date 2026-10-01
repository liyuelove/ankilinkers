"""同步前备份：思源 riff 数据 + Anki 受影响笔记快照 + 本地映射库。"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .anki_client import AnkiClient, AnkiConnectError
from .config import Config, backup_root, db_path


def create_backup(
    cfg: Config, anki: AnkiClient, note_ids: list[int], label: str = "sync"
) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_root() / f"{ts}-{label}"
    dest.mkdir(parents=True, exist_ok=True)

    # 1. 思源闪卡调度数据（riff 目录）
    riff = cfg.workspace_path / "data" / "storage" / "riff"
    if riff.exists():
        shutil.copytree(riff, dest / "riff")

    # 2. Anki 受影响笔记快照（JSON，可人工核对/恢复内容）
    if note_ids:
        try:
            snapshot = anki.notes_info(note_ids)
            (dest / "anki_notes.json").write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except AnkiConnectError:
            pass  # 快照失败不阻断备份

    # 3. 本地映射库
    if db_path().exists():
        shutil.copy2(db_path(), dest / "sync.db")

    return dest

"""本地 SQLite 存储：卡片映射、卡组映射规则、同步日志。"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_map(
    siyuan_card_id TEXT PRIMARY KEY,
    block_id       TEXT NOT NULL,
    anki_note_id   INTEGER NOT NULL,
    card_type      TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    deck_name      TEXT NOT NULL,
    box            TEXT,
    block_updated  TEXT,
    synced_at      TEXT
);
CREATE TABLE IF NOT EXISTS deck_map(
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    src_type  TEXT NOT NULL,   -- 'notebook' | 'doc'
    src_key   TEXT NOT NULL,   -- 笔记本 ID 或文档 rootID
    deck_name TEXT NOT NULL,
    priority  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sync_log(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id   TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path):
        self._c = sqlite3.connect(str(path))
        self._c.row_factory = sqlite3.Row
        self._c.executescript(_SCHEMA)
        self._c.commit()

    def close(self) -> None:
        self._c.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- card_map ----
    def all_cards(self) -> dict[str, sqlite3.Row]:
        rows = self._c.execute("SELECT * FROM card_map").fetchall()
        return {r["siyuan_card_id"]: r for r in rows}

    def upsert_card(
        self,
        card_id: str,
        block_id: str,
        note_id: int,
        card_type: str,
        content_hash: str,
        deck_name: str,
        block_updated: str,
        box: str = "",
    ) -> None:
        self._c.execute(
            "REPLACE INTO card_map VALUES (?,?,?,?,?,?,?,?,?)",
            (
                card_id,
                block_id,
                note_id,
                card_type,
                content_hash,
                deck_name,
                box,
                block_updated,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self._c.commit()

    def update_deck(self, card_id: str, deck_name: str) -> None:
        self._c.execute(
            "UPDATE card_map SET deck_name=? WHERE siyuan_card_id=?",
            (deck_name, card_id),
        )
        self._c.commit()

    def delete_card(self, card_id: str) -> None:
        self._c.execute(
            "DELETE FROM card_map WHERE siyuan_card_id=?", (card_id,)
        )
        self._c.commit()

    # ---- deck_map ----
    def rules(self) -> list[sqlite3.Row]:
        return self._c.execute(
            "SELECT * FROM deck_map ORDER BY priority DESC, id"
        ).fetchall()

    def replace_rules(self, rules: list[tuple[str, str, str, int]]) -> None:
        """rules: (src_type, src_key, deck_name, priority)"""
        self._c.execute("DELETE FROM deck_map")
        self._c.executemany(
            "INSERT INTO deck_map(src_type, src_key, deck_name, priority)"
            " VALUES (?,?,?,?)",
            rules,
        )
        self._c.commit()

    def add_rule(self, src_type: str, src_key: str, deck_name: str) -> None:
        priority = 2 if src_type == "doc" else 1
        self._c.execute(
            "INSERT INTO deck_map(src_type, src_key, deck_name, priority)"
            " VALUES (?,?,?,?)",
            (src_type, src_key, deck_name, priority),
        )
        self._c.commit()

    def remove_rule(self, rule_id: int) -> None:
        self._c.execute("DELETE FROM deck_map WHERE id=?", (rule_id,))
        self._c.commit()

    def clear_rules(self) -> None:
        self._c.execute("DELETE FROM deck_map")
        self._c.commit()

    # ---- sync_log ----
    def add_log(self, batch_id: str, action: str, detail: str = "") -> None:
        self._c.execute(
            "INSERT INTO sync_log(batch_id, action, detail, created_at)"
            " VALUES (?,?,?,?)",
            (batch_id, action, detail, datetime.now().isoformat(timespec="seconds")),
        )
        self._c.commit()

    def logs(self, limit: int = 200) -> list[sqlite3.Row]:
        return self._c.execute(
            "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

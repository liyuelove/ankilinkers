"""AnkiConnect 客户端（默认 127.0.0.1:8765，协议 version 6）。"""
from __future__ import annotations

from typing import Any

import httpx


class AnkiConnectError(Exception):
    pass


class AnkiClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self._c = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self._c.close()

    def __enter__(self) -> "AnkiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def invoke(self, action: str, params: dict | None = None) -> Any:
        payload: dict[str, Any] = {"action": action, "version": 6}
        if params is not None:
            payload["params"] = params
        try:
            r = self._c.post("/", json=payload)
        except httpx.HTTPError as e:
            raise AnkiConnectError(
                f"无法连接 AnkiConnect（{self._c.base_url}）：{e}。"
                "请确认 Anki 正在运行，且已安装 AnkiConnect 插件（代码 2055492159）"
            ) from e
        body = r.json()
        if body.get("error") is not None:
            raise AnkiConnectError(f"{action}：{body['error']}")
        return body.get("result")

    # ---- 连接/权限 ----
    def version(self) -> int:
        return self.invoke("version")

    def request_permission(self) -> dict:
        return self.invoke("requestPermission")

    # ---- 卡组 ----
    def deck_names(self) -> list[str]:
        return self.invoke("deckNames")

    def create_deck(self, deck: str) -> int:
        return self.invoke("createDeck", {"deck": deck})

    def ensure_deck(self, deck: str) -> None:
        if deck not in self.deck_names():
            self.create_deck(deck)

    # ---- 模板 ----
    def model_names(self) -> list[str]:
        return self.invoke("modelNames")

    def model_field_names(self, model_name: str) -> list[str]:
        return self.invoke("modelFieldNames", {"modelName": model_name})

    # ---- 笔记 ----
    def add_notes(self, notes: list[dict]) -> list[int | None]:
        return self.invoke("addNotes", {"notes": notes})

    def can_add_notes_with_error_detail(self, notes: list[dict]) -> list[dict]:
        return self.invoke("canAddNotesWithErrorDetail", {"notes": notes})

    def update_note(
        self,
        note_id: int,
        fields: dict[str, str] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        note: dict[str, Any] = {"id": note_id}
        if fields is not None:
            note["fields"] = fields
        if tags is not None:
            note["tags"] = tags
        self.invoke("updateNote", {"note": note})

    def delete_notes(self, note_ids: list[int]) -> None:
        self.invoke("deleteNotes", {"notes": note_ids})

    def notes_info(self, note_ids: list[int]) -> list[dict]:
        return self.invoke("notesInfo", {"notes": note_ids})

    def find_notes(self, query: str) -> list[int]:
        return self.invoke("findNotes", {"query": query})

    # ---- 卡片 ----
    def change_deck(self, card_ids: list[int], deck: str) -> None:
        self.invoke("changeDeck", {"cards": card_ids, "deck": deck})

    # ---- 媒体 ----
    def store_media_file(self, filename: str, data_b64: str) -> str:
        return self.invoke(
            "storeMediaFile", {"filename": filename, "data": data_b64}
        )

    # ---- 批量/同步 ----
    def multi(self, actions: list[dict]) -> list[dict]:
        """一次 HTTP 请求执行多个 action；返回各 action 的 {result, error}。"""
        return self.invoke(
            "multi", {"actions": [{"version": 6, **a} for a in actions]}
        )

    def sync(self) -> None:
        self.invoke("sync")

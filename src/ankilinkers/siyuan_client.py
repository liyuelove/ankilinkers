"""思源笔记内核 HTTP API 客户端（默认 127.0.0.1:6806）。"""
from __future__ import annotations

from typing import Any, Iterator

import httpx


class SiYuanError(Exception):
    def __init__(self, msg: str, code: int | None = None):
        super().__init__(msg)
        self.code = code


class SiYuanClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        self._c = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"Authorization": f"Token {token}"},
        )

    def close(self) -> None:
        self._c.close()

    def __enter__(self) -> "SiYuanClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _post(self, path: str, payload: dict | None = None) -> Any:
        try:
            r = self._c.post(path, json=payload or {})
        except httpx.HTTPError as e:
            raise SiYuanError(f"无法连接思源内核（{self._c.base_url}）：{e}") from e
        if r.status_code != 200:
            raise SiYuanError(f"{path} HTTP {r.status_code}：{r.text[:200]}")
        body = r.json()
        if body.get("code") != 0:
            raise SiYuanError(f"{path} 返回错误：{body.get('msg')}", code=body.get("code"))
        return body["data"]

    # ---- 基础 ----
    def version(self) -> str:
        return self._post("/api/system/version")

    def ls_notebooks(self) -> list[dict]:
        data = self._post("/api/notebook/lsNotebooks")
        return data.get("notebooks", []) if isinstance(data, dict) else data

    # ---- 闪卡 ----
    def get_riff_cards(
        self, deck_id: str = "", page: int = 1, page_size: int = 100
    ) -> dict:
        """分页获取闪卡；deck_id 传空字符串遍历所有卡包（含内置卡包）。"""
        return self._post(
            "/api/riff/getRiffCards",
            {"id": deck_id, "page": page, "pageSize": page_size},
        )

    def iter_riff_cards(self, page_size: int = 100) -> Iterator[dict]:
        """遍历全部闪卡块（含 riffCardID / riffCard 调度信息）。"""
        page = 1
        while True:
            data = self.get_riff_cards(deck_id="", page=page, page_size=page_size)
            blocks = data.get("blocks") or []
            yield from blocks
            if page >= data.get("pageCount", 1) or not blocks:
                return
            page += 1

    # ---- 内容 ----
    def get_block_kramdown(self, block_id: str) -> str:
        data = self._post("/api/block/getBlockKramdown", {"id": block_id})
        return data.get("kramdown", "")

    def get_file(self, path: str) -> bytes:
        """读取工作区内文件（如 assets/xx.png），返回原始字节。"""
        try:
            r = self._c.post("/api/file/getFile", json={"path": path})
        except httpx.HTTPError as e:
            raise SiYuanError(f"无法连接思源内核：{e}") from e
        if r.status_code != 200:
            raise SiYuanError(f"getFile {path} HTTP {r.status_code}")
        return r.content

    # ---- SQL 兜底 ----
    def sql(self, stmt: str) -> list[dict]:
        return self._post("/api/query/sql", {"stmt": stmt})

    def doc_titles(self, doc_ids: list[str]) -> dict[str, str]:
        """批量解析文档 rootID → 文档名。"""
        if not doc_ids:
            return {}
        quoted = ",".join("'%s'" % i.replace("'", "''") for i in doc_ids)
        rows = self.sql(
            f"SELECT id, content FROM blocks WHERE type='d' AND id IN ({quoted})"
        )
        return {r["id"]: r["content"] for r in rows}

    def search_docs(self, keyword: str, limit: int = 20) -> list[dict]:
        """按标题关键字搜索文档（最近更新优先）。"""
        kw = keyword.replace("'", "''")
        return self.sql(
            "SELECT id, content, hpath, box FROM blocks WHERE type='d' "
            f"AND content LIKE '%{kw}%' ORDER BY updated DESC LIMIT {int(limit)}"
        )

    def find_flashcard_blocks(self) -> list[dict]:
        """SQL 兜底枚举：按块属性查闪卡块（含旧版属性名）。"""
        return self.sql(
            "SELECT id, root_id, box, hpath, type, subtype, ial, updated "
            "FROM blocks WHERE ial LIKE '%custom-riff-decks%' "
            "OR ial LIKE '%custom-fsrs-flashcard%'"
        )

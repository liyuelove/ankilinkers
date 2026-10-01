"""同步引擎：枚举 → 解析 → hash diff → 路由 → 计划 → 执行。"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Iterable, TypeVar

from .anki_client import AnkiClient, AnkiConnectError
from .config import Config
from .models import Change, Plan, SourceCard
from .parser import ParsedCard, parse_card
from .siyuan_client import SiYuanClient, SiYuanError
from .storage import Storage

ProgressCb = Callable[[str, int, int, str], None]  # (阶段, 已完成, 总数, 消息)
CancelCb = Callable[[], bool]

IDENT_TAG = "siyuan-sync"

_T = TypeVar("_T")


class SyncCancelled(Exception):
    pass


def _noop(stage: str, done: int, total: int, msg: str) -> None:
    pass


def _chunked(seq: list[_T], n: int) -> Iterable[list[_T]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def content_hash(p: ParsedCard) -> str:
    payload = json.dumps(
        {
            "t": p.card_type,
            "front": p.front,
            "back": p.back,
            "text": p.text,
            "extra": p.extra,
        },
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def summarize(p: ParsedCard, limit: int = 50) -> str:
    text = (p.front or p.text).replace("\n", " ").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


class SyncEngine:
    def __init__(
        self,
        cfg: Config,
        siyuan: SiYuanClient,
        anki: AnkiClient,
        storage: Storage,
    ):
        self.cfg = cfg
        self.siyuan = siyuan
        self.anki = anki
        self.storage = storage

    # ---- 路由 ----
    def route(self, sc: SourceCard, rules: list) -> str:
        """文档级规则 > 笔记本级规则 > 默认卡组。"""
        for r in rules:
            if r["src_type"] == "doc" and r["src_key"] == sc.root_id:
                return r["deck_name"]
        for r in rules:
            if r["src_type"] == "notebook" and r["src_key"] == sc.box:
                return r["deck_name"]
        return self.cfg.default_deck

    # ---- 计划 ----
    def _full_kramdown(self, block_id: str, depth: int = 0) -> str:
        """取块的完整 kramdown；标题/段落等容器块的子块不在返回值里，需递归补拉。

        超级块/列表的子块 IAL 已含在父块 kramdown 中，用 id 出现与否去重。
        """
        text = self.siyuan.get_block_kramdown(block_id)
        if depth >= 4:
            return text
        try:
            children = self.siyuan.sql(
                f"SELECT id FROM blocks WHERE parent_id='{block_id}' ORDER BY sort"
            )
        except SiYuanError:
            return text
        for ch in children:
            if ch["id"] in text:
                continue
            text += "\n\n" + self._full_kramdown(ch["id"], depth + 1)
        return text

    def _fetch_parse(self, sc: SourceCard) -> ParsedCard:
        return parse_card(self._full_kramdown(sc.block_id), sc.block_type)

    def build_plan(self, progress: ProgressCb | None = None,
                   notebook: str | None = None) -> Plan:
        progress = progress or _noop
        plan = Plan()
        rules = self.storage.rules()

        progress("enumerate", 0, 1, "枚举思源闪卡…")
        src: dict[str, SourceCard] = {}
        for b in self.siyuan.iter_riff_cards(self.cfg.page_size):
            cid = b.get("riffCardID") or b["id"]
            src[cid] = SourceCard(
                card_id=cid,
                block_id=b["id"],
                box=b.get("box", ""),
                root_id=b.get("rootID") or b.get("root_id", ""),
                hpath=b.get("hPath") or b.get("hpath", ""),
                updated=b.get("updated", ""),
                block_type=b.get("type", ""),
            )
        if notebook:
            src = {cid: sc for cid, sc in src.items() if sc.box == notebook}

        known = self.storage.all_cards()

        # 删除：本地已接管、思源侧已不存在
        for cid, row in known.items():
            if notebook and (row["box"] or "") != notebook:
                continue  # 限定范围同步时不处理范围外的删除
            if cid not in src:
                plan.deletes.append(
                    Change(
                        "delete",
                        cid,
                        note_id=row["anki_note_id"],
                        old_deck=row["deck_name"],
                        summary=f"{row['deck_name']} / note {row['anki_note_id']}",
                        block_id=row["block_id"],
                        block_updated=row["block_updated"] or "",
                        box=row["box"] or "",
                    )
                )

        # 全量解析（以内容 hash 为准；容器块的子块编辑不改父块 updated，updated 粗筛不可靠）
        items = list(src.items())
        parsed_map: dict[str, ParsedCard] = {}
        failed: dict[str, str] = {}
        total = len(items)
        done = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {cid: pool.submit(self._fetch_parse, sc) for cid, sc in items}
            for cid, fut in futs.items():
                try:
                    parsed_map[cid] = fut.result()
                except Exception as e:  # noqa: BLE001 - 任何单卡失败都不阻断整体
                    failed[cid] = str(e)
                done += 1
                if done % 20 == 0 or done == total:
                    progress("parse", done, total, f"解析闪卡 {done}/{total}")

        for cid, sc in items:
            if cid in failed:
                plan.skips.append((cid, f"获取/解析失败：{failed[cid]}"))
                continue
            parsed = parsed_map[cid]
            if parsed.card_type == "skip":
                plan.skips.append((cid, "；".join(parsed.warnings)))
                continue
            deck = self.route(sc, rules)
            h = content_hash(parsed)
            row = known.get(cid)
            summary = summarize(parsed)
            if row is None:
                plan.adds.append(
                    Change("add", cid, deck=deck, parsed=parsed, summary=summary,
                           block_id=sc.block_id, block_updated=sc.updated, box=sc.box)
                )
                continue
            if row["content_hash"] != h or row["card_type"] != parsed.card_type:
                plan.updates.append(
                    Change("update", cid, deck=deck, note_id=row["anki_note_id"],
                           parsed=parsed, summary=summary,
                           block_id=sc.block_id, block_updated=sc.updated, box=sc.box)
                )
            if row["deck_name"] != deck:
                plan.moves.append(
                    Change("move", cid, deck=deck, note_id=row["anki_note_id"],
                           old_deck=row["deck_name"], summary=summary,
                           block_id=sc.block_id, block_updated=sc.updated, box=sc.box)
                )
        return plan

    # ---- 执行 ----
    def _fields_for(self, parsed: ParsedCard) -> dict[str, str]:
        cfg = self.cfg
        if parsed.card_type == "cloze":
            return {cfg.field_cloze_text: parsed.text, cfg.field_cloze_extra: parsed.extra}
        return {cfg.field_qa_front: parsed.front, cfg.field_qa_back: parsed.back}

    def _model_for(self, parsed: ParsedCard) -> str:
        return self.cfg.model_cloze if parsed.card_type == "cloze" else self.cfg.model_qa

    def _upload_media(self, parsed: ParsedCard, cache: dict[str, str]) -> None:
        for path in parsed.media:
            if path in cache:
                continue
            data = None
            for ws_path in (f"data/{path}", f"/data/{path}"):
                try:
                    data = self.siyuan.get_file(ws_path)
                    break
                except SiYuanError:
                    continue
            if data is None:
                raise SiYuanError(f"读取媒体文件失败：{path}")
            filename = path.rsplit("/", 1)[-1]
            cache[path] = self.anki.store_media_file(
                filename, base64.b64encode(data).decode()
            )

    def _record(self, c: Change, note_id: int) -> None:
        self.storage.upsert_card(
            c.card_id, c.block_id, note_id, c.parsed.card_type,
            content_hash(c.parsed), c.deck, c.block_updated, c.box,
        )

    def execute(
        self, plan: Plan, progress: ProgressCb | None = None, cancel: CancelCb | None = None
    ) -> dict:
        progress = progress or _noop
        cancel = cancel or (lambda: False)
        batch_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        report: dict = {
            "batch_id": batch_id,
            "add": {"ok": 0, "fail": []},
            "update": {"ok": 0, "fail": []},
            "move": {"ok": 0, "fail": []},
            "delete": {"ok": 0, "fail": []},
            "warnings": [],
        }

        # 预校验：note 在 Anki 侧是否仍存在（被手动删除的降级处理）
        referenced = [c for c in (*plan.updates, *plan.deletes, *plan.moves)]
        if referenced:
            infos = self.anki.notes_info([c.note_id for c in referenced])
            # 已不存在的 note 返回空对象，需过滤
            info_map = {n["noteId"]: n for n in infos if n and "noteId" in n}
        else:
            info_map = {}
        alive = set(info_map)

        updates: list[Change] = []
        for c in plan.updates:
            if c.note_id in alive:
                updates.append(c)
            else:
                plan.adds.append(
                    Change("add", c.card_id, deck=c.deck, parsed=c.parsed,
                           summary=c.summary, block_id=c.block_id,
                           block_updated=c.block_updated, box=c.box)
                )
                report["warnings"].append(
                    f"note {c.note_id} 在 Anki 中已不存在，将重新创建：{c.summary}"
                )
        moves = [c for c in plan.moves if c.note_id in alive]
        for c in plan.deletes:
            if c.note_id not in alive:
                self.storage.delete_card(c.card_id)
                report["warnings"].append(
                    f"note {c.note_id} 在 Anki 中已不存在，仅清理映射：{c.summary}"
                )
        deletes = [c for c in plan.deletes if c.note_id in alive]

        # 确保目标卡组存在
        for d in {c.deck for c in (*plan.adds, *updates, *moves)}:
            self.anki.ensure_deck(d)

        media_cache: dict[str, str] = {}
        steps = len(plan.adds) + len(updates) + len(moves) + len(deletes)
        done = 0

        def tick(stage: str, c: Change) -> None:
            nonlocal done
            done += 1
            progress(stage, done, steps, c.summary)

        # -- 新增 --
        for chunk in _chunked(plan.adds, self.cfg.add_batch_size):
            if cancel():
                raise SyncCancelled
            notes: list[tuple[Change, dict]] = []
            for c in chunk:
                try:
                    self._upload_media(c.parsed, media_cache)
                except (SiYuanError, AnkiConnectError) as e:
                    report["add"]["fail"].append((c.card_id, str(e)))
                    tick("add", c)
                    continue
                notes.append((c, {
                    "deckName": c.deck,
                    "modelName": self._model_for(c.parsed),
                    "fields": self._fields_for(c.parsed),
                    "tags": [IDENT_TAG, f"syid-{c.card_id}"],
                    "options": {"allowDuplicate": False},
                }))
            if not notes:
                continue
            try:
                checks = self.anki.can_add_notes_with_error_detail([n for _, n in notes])
            except AnkiConnectError as e:
                for c, _ in notes:
                    report["add"]["fail"].append((c.card_id, str(e)))
                    tick("add", c)
                continue
            ok_pairs = [(c, n) for (c, n), chk in zip(notes, checks) if chk.get("canAdd")]
            for (c, _), chk in zip(notes, checks):
                if not chk.get("canAdd"):
                    report["add"]["fail"].append((c.card_id, chk.get("error", "预检未通过")))
                    tick("add", c)
            try:
                ids = self.anki.add_notes([n for _, n in ok_pairs])
                for (c, _), nid in zip(ok_pairs, ids):
                    if nid:
                        self._record(c, nid)
                        report["add"]["ok"] += 1
                    else:
                        report["add"]["fail"].append((c.card_id, "addNotes 返回空 id"))
                    tick("add", c)
            except AnkiConnectError:
                # 批量失败降级为逐条，隔离坏卡
                for c, n in ok_pairs:
                    if cancel():
                        raise SyncCancelled
                    try:
                        nid = self.anki.add_notes([n])[0]
                        self._record(c, nid)
                        report["add"]["ok"] += 1
                    except AnkiConnectError as e:
                        report["add"]["fail"].append((c.card_id, str(e)))
                    tick("add", c)

        # -- 更新 --
        actions: list[tuple[Change, dict]] = []
        for c in updates:
            try:
                self._upload_media(c.parsed, media_cache)
            except (SiYuanError, AnkiConnectError) as e:
                report["update"]["fail"].append((c.card_id, str(e)))
                tick("update", c)
                continue
            old_tags = info_map.get(c.note_id, {}).get("tags", [])
            tags = sorted(set(old_tags) | {IDENT_TAG, f"syid-{c.card_id}"})
            actions.append((c, {
                "action": "updateNote",
                "params": {"note": {"id": c.note_id,
                                    "fields": self._fields_for(c.parsed),
                                    "tags": tags}},
            }))
        for chunk in _chunked(actions, self.cfg.update_batch_size):
            if cancel():
                raise SyncCancelled
            results = self.anki.multi([a for _, a in chunk])
            for (c, _), r in zip(chunk, results):
                if r.get("error"):
                    report["update"]["fail"].append((c.card_id, r["error"]))
                else:
                    self._record(c, c.note_id)
                    report["update"]["ok"] += 1
                tick("update", c)

        # 挖空减编号空卡检测
        for c in updates:
            if c.parsed and c.parsed.card_type == "cloze" and c.note_id in info_map:
                expected = len(set(re.findall(r"\{\{c(\d+)::", c.parsed.text)))
                actual = len(info_map[c.note_id].get("cards", []))
                if actual > expected:
                    report["warnings"].append(
                        f"「{c.summary}」挖空数减少（{actual}→{expected}），"
                        "请在 Anki 中执行 工具 → 空卡片 清理残留"
                    )

        # -- 移动 --
        move_actions: list[tuple[Change, dict]] = []
        for c in moves:
            cards = info_map.get(c.note_id, {}).get("cards", [])
            if not cards:
                report["move"]["fail"].append((c.card_id, "未找到卡片"))
                tick("move", c)
                continue
            move_actions.append((c, {"action": "changeDeck",
                                     "params": {"cards": cards, "deck": c.deck}}))
        for chunk in _chunked(move_actions, self.cfg.update_batch_size):
            if cancel():
                raise SyncCancelled
            results = self.anki.multi([a for _, a in chunk])
            for (c, _), r in zip(chunk, results):
                if r.get("error"):
                    report["move"]["fail"].append((c.card_id, r["error"]))
                else:
                    self.storage.update_deck(c.card_id, c.deck)
                    report["move"]["ok"] += 1
                tick("move", c)

        # -- 删除 --
        for chunk in _chunked(deletes, self.cfg.add_batch_size):
            if cancel():
                raise SyncCancelled
            try:
                self.anki.delete_notes([c.note_id for c in chunk])
                for c in chunk:
                    self.storage.delete_card(c.card_id)
                    report["delete"]["ok"] += 1
                    tick("delete", c)
            except AnkiConnectError as e:
                for c in chunk:
                    report["delete"]["fail"].append((c.card_id, str(e)))
                    tick("delete", c)

        fail_total = sum(len(report[k]["fail"]) for k in ("add", "update", "move", "delete"))
        summary = (
            f"新增 {report['add']['ok']}，更新 {report['update']['ok']}，"
            f"移动 {report['move']['ok']}，删除 {report['delete']['ok']}；失败 {fail_total}"
        )
        self.storage.add_log(batch_id, "sync", summary)
        report["summary"] = summary
        return report

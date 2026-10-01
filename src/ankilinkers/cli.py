"""命令行入口：check / notebooks / rules / sync / gui。"""
from __future__ import annotations

import argparse
import sys

from .anki_client import AnkiClient, AnkiConnectError
from .backup import create_backup
from .config import Config, db_path, load
from .storage import Storage
from .siyuan_client import SiYuanClient, SiYuanError
from .sync_engine import SyncCancelled, SyncEngine

OK, FAIL, WARN = "✓", "✗", "!"


def _clients(cfg: Config):
    siyuan = SiYuanClient(cfg.siyuan_base_url, cfg.siyuan_token)
    anki = AnkiClient(cfg.anki_base_url)
    storage = Storage(db_path())
    return siyuan, anki, storage


def _cli_progress(stage: str, done: int, total: int, msg: str) -> None:
    print(f"  [{stage}] {done}/{total} {msg}")


def _check_siyuan(cfg: Config) -> bool:
    print("== 思源笔记 ==")
    if not cfg.siyuan_token:
        print(f"  [{FAIL}] 未找到 API token（检查 siyuan_workspace 配置：{cfg.siyuan_workspace}）")
        return False
    try:
        with SiYuanClient(cfg.siyuan_base_url, cfg.siyuan_token) as s:
            print(f"  [{OK}] 内核连接正常，版本 {s.version()}")
            notebooks = s.ls_notebooks()
            print(f"  [{OK}] 笔记本 {len(notebooks)} 个")
            data = s.get_riff_cards(deck_id="", page=1, page_size=1)
            print(f"  [{OK}] Riff 闪卡接口可用，现有闪卡 {data.get('total', 0)} 张")
            return True
    except SiYuanError as e:
        print(f"  [{FAIL}] {e}")
        print("       请确认思源笔记已启动，且 API token 正确（设置 → 关于 → API token）")
        return False


def _check_anki(cfg: Config) -> bool:
    print("== Anki ==")
    ok = True
    try:
        with AnkiClient(cfg.anki_base_url) as a:
            print(f"  [{OK}] AnkiConnect 连接正常，协议版本 {a.version()}")
            decks = a.deck_names()
            print(f"  [{OK}] 卡组 {len(decks)} 个")
            if cfg.default_deck in decks:
                print(f"  [{OK}] 默认卡组「{cfg.default_deck}」已存在")
            else:
                print(f"  [{WARN}] 默认卡组「{cfg.default_deck}」不存在，将在首次同步时自动创建")
            for model, required in (
                (cfg.model_qa, {cfg.field_qa_front, cfg.field_qa_back}),
                (cfg.model_cloze, {cfg.field_cloze_text, cfg.field_cloze_extra}),
            ):
                if model not in a.model_names():
                    print(f"  [{FAIL}] 缺少笔记模板「{model}」")
                    ok = False
                    continue
                have = set(a.model_field_names(model))
                missing = required - have
                if missing:
                    print(f"  [{FAIL}] 模板「{model}」缺少字段 {sorted(missing)}（现有 {sorted(have)}）")
                    ok = False
                else:
                    print(f"  [{OK}] 模板「{model}」字段齐备 {sorted(have)}")
    except AnkiConnectError as e:
        print(f"  [{FAIL}] {e}")
        return False
    return ok


def cmd_check(_args: argparse.Namespace) -> int:
    cfg = load()
    ok = _check_siyuan(cfg)
    ok = _check_anki(cfg) and ok
    print("== 结论 ==")
    if ok:
        print(f"  [{OK}] 连接检测全部通过，可以同步")
        return 0
    print(f"  [{FAIL}] 存在未通过的检测项，请先解决上述问题")
    return 1


def cmd_notebooks(_args: argparse.Namespace) -> int:
    cfg = load()
    with SiYuanClient(cfg.siyuan_base_url, cfg.siyuan_token) as s:
        for nb in s.ls_notebooks():
            print(f"{nb['id']}  {nb['name']}{'（已关闭）' if nb.get('closed') else ''}")
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    storage = Storage(db_path())
    try:
        if args.rules_command == "list":
            rules = storage.rules()
            if not rules:
                print("（无映射规则，全部闪卡将进入默认卡组）")
            for r in rules:
                print(f"#{r['id']}  {r['src_type']:8s}  {r['src_key']}  →  {r['deck_name']}")
        elif args.rules_command == "add-notebook":
            storage.add_rule("notebook", args.box, args.deck)
            print(f"已添加：笔记本 {args.box} → {args.deck}")
        elif args.rules_command == "add-doc":
            storage.add_rule("doc", args.root_id, args.deck)
            print(f"已添加：文档 {args.root_id} → {args.deck}")
        elif args.rules_command == "remove":
            storage.remove_rule(args.id)
            print(f"已删除规则 #{args.id}")
        elif args.rules_command == "clear":
            storage.clear_rules()
            print("已清空全部映射规则")
    finally:
        storage.close()
    return 0


def _print_plan(plan) -> None:
    print("== 同步计划 ==")
    print(f"  新增 {len(plan.adds)}，更新 {len(plan.updates)}，"
          f"移动 {len(plan.moves)}，删除 {len(plan.deletes)}，跳过 {len(plan.skips)}")
    for title, items in (("新增", plan.adds), ("更新", plan.updates),
                         ("移动", plan.moves), ("删除", plan.deletes)):
        if not items:
            continue
        print(f"  -- {title} --")
        for c in items[:10]:
            extra = f"  {c.old_deck} → {c.deck}" if c.action == "move" else f"  [{c.deck}]"
            print(f"    {c.summary}{extra}")
        if len(items) > 10:
            print(f"    … 共 {len(items)} 条")
    for cid, reason in plan.skips[:10]:
        print(f"  [{WARN}] 跳过 {cid}：{reason}")


def cmd_sync(args: argparse.Namespace) -> int:
    cfg = load()
    siyuan, anki, storage = _clients(cfg)
    try:
        engine = SyncEngine(cfg, siyuan, anki, storage)
        plan = engine.build_plan(progress=None if args.quiet else _cli_progress,
                                 notebook=args.notebook)
        _print_plan(plan)
        if plan.is_empty():
            print("无变更。")
            return 0
        if args.dry_run:
            print("（dry-run，未执行）")
            return 0

        affected = [c.note_id for c in (*plan.updates, *plan.deletes, *plan.moves)]
        if plan.deletes or plan.change_count >= cfg.backup_threshold:
            path = create_backup(cfg, anki, affected, "sync")
            print(f"已备份：{path}")

        if not args.yes:
            if input("确认执行？[y/N] ").strip().lower() != "y":
                print("已取消。")
                return 1

        report = engine.execute(plan, progress=None if args.quiet else _cli_progress)
        print("== 同步结果 ==")
        print(f"  {report['summary']}")
        for w in report["warnings"]:
            print(f"  [{WARN}] {w}")
        for kind in ("add", "update", "move", "delete"):
            for cid, err in report[kind]["fail"][:10]:
                print(f"  [{FAIL}] {kind} {cid}：{err}")
        return 0
    except SyncCancelled:
        print("已取消（部分批次可能已执行）。")
        return 130
    except (SiYuanError, AnkiConnectError) as e:
        print(f"[{FAIL}] {e}")
        return 1
    finally:
        siyuan.close()
        anki.close()
        storage.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ankilinkers", description="思源笔记闪卡 → Anki 单向增量同步"
    )
    sub = p.add_subparsers(dest="command")
    sub.add_parser("check", help="连接检测（思源内核 / AnkiConnect / 模板字段）")
    sub.add_parser("notebooks", help="列出思源笔记本（ID + 名称）")

    pr = sub.add_parser("rules", help="卡组映射规则管理")
    rsub = pr.add_subparsers(dest="rules_command")
    rsub.add_parser("list", help="列出规则")
    pa = rsub.add_parser("add-notebook", help="笔记本 → 卡组")
    pa.add_argument("box")
    pa.add_argument("deck")
    pa = rsub.add_parser("add-doc", help="文档(rootID) → 卡组")
    pa.add_argument("root_id")
    pa.add_argument("deck")
    pa = rsub.add_parser("remove", help="删除规则")
    pa.add_argument("id", type=int)
    rsub.add_parser("clear", help="清空规则")

    ps = sub.add_parser("sync", help="同步闪卡到 Anki")
    ps.add_argument("--dry-run", action="store_true", help="只输出计划，不执行")
    ps.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    ps.add_argument("-q", "--quiet", action="store_true", help="不打印进度")
    ps.add_argument("--notebook", help="只同步指定笔记本（box ID）")

    sub.add_parser("gui", help="启动图形界面")
    return p


def main() -> None:
    args = build_parser().parse_args()
    handlers = {
        "check": cmd_check,
        "notebooks": cmd_notebooks,
        "rules": cmd_rules,
        "sync": cmd_sync,
    }
    if args.command in handlers:
        sys.exit(handlers[args.command](args))
    # gui 子命令或无参数：启动图形界面（延迟导入，避免 CLI 环境加载 Qt）
    from .gui.app import main as gui_main

    sys.exit(gui_main())


if __name__ == "__main__":
    main()

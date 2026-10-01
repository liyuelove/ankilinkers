"""应用配置：加载/保存用户配置，自动发现思源 API token。

数据目录解析优先级：
1. 环境变量 ANKILINKERS_HOME
2. 可执行文件同级（源码运行时为项目根）的 data/ 目录——便携模式
3. platformdirs 用户目录（同级目录不可写时兜底）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, fields
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "ankiLinkers"


@dataclass
class Config:
    siyuan_base_url: str = "http://127.0.0.1:6806"
    # 留空则从思源工作区 conf.json 自动读取，不落盘保存
    siyuan_token: str = ""
    siyuan_workspace: str = "~/SiYuan"
    anki_base_url: str = "http://127.0.0.1:8765"
    model_qa: str = "KaTeX and Markdown Basic"
    field_qa_front: str = "Front"
    field_qa_back: str = "Back"
    model_cloze: str = "KaTeX and Markdown Cloze"
    field_cloze_text: str = "Text"
    field_cloze_extra: str = "Back Extra"
    default_deck: str = "SiYuan"
    backup_threshold: int = 50
    add_batch_size: int = 100
    update_batch_size: int = 50
    page_size: int = 100

    @property
    def workspace_path(self) -> Path:
        return Path(self.siyuan_workspace).expanduser()


def _anchor_dir() -> Path:
    """可执行文件目录（PyInstaller）；源码运行时为项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _is_writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_probe"
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def _migrate_legacy(dest: Path) -> None:
    """从旧的 platformdirs 位置迁移一次数据（目标已存在则不动）。"""
    legacy_data = Path(user_data_dir(APP_NAME))
    legacy_conf = Path(user_config_dir(APP_NAME)) / "config.json"
    try:
        if legacy_data.resolve() == dest.resolve():
            return
        if not (dest / "sync.db").exists() and (legacy_data / "sync.db").exists():
            shutil.copy2(legacy_data / "sync.db", dest / "sync.db")
        if not (dest / "config.json").exists() and legacy_conf.exists():
            shutil.copy2(legacy_conf, dest / "config.json")
        # 旧备份体积可能较大，留在原处不搬移
    except OSError:
        pass


def data_dir() -> Path:
    env = os.environ.get("ANKILINKERS_HOME")
    if env:
        p = Path(env).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    portable = _anchor_dir() / "data"
    if _is_writable(portable):
        _migrate_legacy(portable)
        return portable
    p = Path(user_data_dir(APP_NAME))
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return data_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "sync.db"


def backup_root() -> Path:
    return data_dir() / "backups"


def _read_siyuan_token(workspace: Path) -> str:
    conf = workspace / "conf" / "conf.json"
    try:
        data = json.loads(conf.read_text(encoding="utf-8"))
        return data.get("api", {}).get("token", "") or ""
    except (OSError, json.JSONDecodeError):
        return ""


def load() -> Config:
    cfg = Config()
    path = config_path()
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            saved = {}
        known = {f.name for f in fields(cfg)}
        for k, v in saved.items():
            if k in known:
                setattr(cfg, k, v)
    if not cfg.siyuan_token:
        cfg.siyuan_token = _read_siyuan_token(cfg.workspace_path)
    return cfg


def save(cfg: Config) -> None:
    data = {f.name: getattr(cfg, f.name) for f in fields(cfg)}
    # token 若为自动读取则不落盘，避免密钥多副本
    if data["siyuan_token"] and data["siyuan_token"] == _read_siyuan_token(cfg.workspace_path):
        data["siyuan_token"] = ""
    config_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

"""思源 Kramdown 结构解析：IAL 剥离、超级块/标题/列表识别、代码区掩码。"""
from __future__ import annotations

import re

# 独立成行的 IAL（可能缩进）：  {: id="..." updated="..."}
_IAL_LINE = re.compile(r'^\s*\{:(?:\s+[A-Za-z0-9_-]+="[^"]*")+\s*\}\s*$')
# 列表项内联 IAL：- {: id="..."}内容
_LIST_ITEM_IAL = re.compile(
    r'^(\s*(?:[-*+]|\d+\.)\s+)\{:(?:\s+[A-Za-z0-9_-]+="[^"]*")+\s*\}'
)
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TOP_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+\.)\s+")
_FENCE_OPEN = re.compile(r"^\{\{\{(?:row|col)\s*$")
_FENCE_CLOSE = re.compile(r"^\}\}\}\s*$")

ZERO_WIDTH = "​‌‍⁠﻿"

_CODE_FENCED = re.compile(r"^\s*(```|~~~)[^\n]*\n(.*?)^\s*\1[ \t]*$", re.M | re.S)
_CODE_INLINE = re.compile(r"(`+)(.+?)\1", re.S)


def strip_zero_width(text: str) -> str:
    return text.translate({ord(c): None for c in ZERO_WIDTH})


def strip_ial(text: str) -> str:
    """移除独立 IAL 行与列表项内联 IAL。"""
    lines = []
    for line in text.split("\n"):
        if _IAL_LINE.match(line):
            continue
        line = _LIST_ITEM_IAL.sub(r"\1", line)
        lines.append(line)
    return "\n".join(lines)


def unwrap_superblock(text: str) -> tuple[bool, str]:
    """若整体是超级块（{{{row/col ... }}}），返回 (True, 去掉围栏后的内部文本)。"""
    lines = text.split("\n")
    if not lines or not _FENCE_OPEN.match(lines[0]):
        return False, text
    body = [
        line
        for line in lines[1:]
        if not _FENCE_OPEN.match(line) and not _FENCE_CLOSE.match(line)
    ]
    return True, "\n".join(body).strip("\n")


def split_blocks(text: str) -> list[str]:
    """按空行切分块（代码围栏内的空行不切）。"""
    blocks: list[str] = []
    cur: list[str] = []
    in_code = False
    for line in text.split("\n"):
        if re.match(r"^\s*(```|~~~)", line):
            in_code = not in_code
        if not in_code and not line.strip():
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def heading_text(block: str) -> str | None:
    m = _HEADING.match(block)
    return m.group(2).strip() if m else None


def is_list(block: str) -> bool:
    return bool(_TOP_LIST_ITEM.match(block))


def split_list(block: str) -> tuple[str, str]:
    """把列表块拆成 (首个顶层项的自身文本, 其余部分)。其余部分可能为空。"""
    lines = block.split("\n")
    front = _TOP_LIST_ITEM.sub("", lines[0]).strip()
    rest = "\n".join(lines[1:]).strip("\n")
    return front, rest


def mask_code(text: str) -> tuple[str, list[str]]:
    """把围栏代码块与行内代码替换为占位符，返回 (掩码后文本, 被掩码区域列表)。"""
    regions: list[str] = []

    def stash(m: re.Match) -> str:
        regions.append(m.group(0))
        return f"\x00{len(regions) - 1}\x00"

    masked = _CODE_FENCED.sub(stash, text)
    masked = _CODE_INLINE.sub(stash, masked)
    return masked, regions


def unmask_code(text: str, regions: list[str]) -> str:
    return re.sub("\x00(\\d+)\x00", lambda m: regions[int(m.group(1))], text)

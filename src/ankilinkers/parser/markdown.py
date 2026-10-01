"""思源行级语法 → 标准 Markdown 的转换（输入应先做代码区掩码）。"""
from __future__ import annotations

import re
from collections.abc import Callable

# ==挖空==（不成对出现在 ==== 中）
MARK = re.compile(r"(?<![=])==(?!=)(.+?)(?<![=])==(?![=])", re.S)
# 块引用 ((20240101120000-abcdefg "锚文本")) 或 ((id))
_BLOCK_REF = re.compile(r'\(\(\d{14}-[A-Za-z0-9]+(?:\s+"([^"]*)")?\)\)')
# 思源协议链接 [文本](siyuan://blocks/...)
_SIYUAN_LINK = re.compile(r"\[([^\]]*)\]\(siyuan://[^)]*\)")
# 资源引用 ![alt](assets/xx.png "title") 或 [名](assets/xx)
_ASSET = re.compile(r'(!?)\[([^\]]*)\]\((assets/[^)\s]+)(?:\s+"[^"]*")?\)')
# 嵌入块（独立成段）：{{id}} 或 {{select ...}}；不含 Anki cloze
_EMBED = re.compile(r"^\{\{(?!c\d+::)[^}]*\}\}$", re.M)
# 字面 Anki cloze 语法（危险内容检测）
LITERAL_CLOZE = re.compile(r"\{\{c\d+::")

EMBED_PLACEHOLDER = "*[嵌入块已忽略]*"


def marks_to_cloze(text: str) -> str:
    """==x== → {{cN::x}}，N 按出现顺序递增。"""
    counter = 0

    def repl(m: re.Match) -> str:
        nonlocal counter
        counter += 1
        return "{{c%d::%s}}" % (counter, m.group(1))

    return MARK.sub(repl, text)


def marks_to_html(text: str) -> str:
    """QA 卡中保留高亮：==x== → <mark>x</mark>。"""
    return MARK.sub(r"<mark>\1</mark>", text)


def convert(
    text: str,
    mark_mode: str,  # "cloze" | "html"
    media_collector: Callable[[str], str] | None = None,
) -> str:
    """行级语法统一转换。media_collector 接收 assets 路径、返回替换后的文件名。"""
    if mark_mode == "cloze":
        text = marks_to_cloze(text)
    else:
        text = marks_to_html(text)

    text = _BLOCK_REF.sub(lambda m: m.group(1) or "", text)
    text = _SIYUAN_LINK.sub(r"\1", text)
    text = _EMBED.sub(EMBED_PLACEHOLDER, text)

    def asset_repl(m: re.Match) -> str:
        bang, alt, path = m.group(1), m.group(2), m.group(3)
        filename = path.rsplit("/", 1)[-1]
        if media_collector is not None:
            filename = media_collector(path)
        return f"{bang}[{alt}]({filename})"

    return _ASSET.sub(asset_repl, text)

"""闪卡解析：思源 Kramdown → 标准 Markdown 卡片内容。

规则（与思源闪卡设置一致）：
- 含 ==标记==（代码区外）→ 挖空卡，==x== 展开为 {{cN::x}}
- 超级块：首子块=正面，其余=背面
- 标题块：标题=正面，下方块=背面
- 列表：首个顶层项=正面，其余=背面
- 段落：自身=正面，子块=背面
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import kramdown as kd
from . import markdown as md


@dataclass
class ParsedCard:
    card_type: str  # "qa" | "cloze" | "skip"
    front: str = ""  # qa
    back: str = ""  # qa
    text: str = ""  # cloze（含 {{cN::}}）
    extra: str = ""  # cloze 附加信息
    media: list[str] = field(default_factory=list)  # 引用到的 assets/ 路径
    warnings: list[str] = field(default_factory=list)


def parse_card(kramdown_text: str, block_type: str = "") -> ParsedCard:
    """block_type 为思源块类型（NodeHeading/NodeSuperBlock/...）。

    标题、超级块是结构性问答卡：即使内容含 ==标记== 也按问答处理（标记转 <mark>），
    与思源自身行为一致；其余块（段落/列表等）含标记则为挖空卡。
    """
    structural_qa = block_type in ("NodeHeading", "NodeSuperBlock")
    media: list[str] = []

    def collect(path: str) -> str:
        media.append(path)
        return path.rsplit("/", 1)[-1]

    text = kd.strip_zero_width(kramdown_text)

    # 源内容含字面 {{cN::}}：Anki 会误判为挖空，跳过并告警
    if md.LITERAL_CLOZE.search(text):
        return ParsedCard(
            card_type="skip",
            warnings=["内容含字面 {{cN::}} 语法（疑似代码示例），已跳过以避免生成错误挖空"],
        )

    text = kd.strip_ial(text)
    is_sb, text = kd.unwrap_superblock(text)
    blocks = kd.split_blocks(text)
    if not blocks:
        return ParsedCard(card_type="skip", warnings=["卡片内容为空"])

    joined = "\n\n".join(blocks)
    masked, regions = kd.mask_code(joined)

    if not structural_qa and md.MARK.search(masked):
        # 挖空卡：全部内容进 Text
        converted = md.convert(masked, mark_mode="cloze", media_collector=collect)
        return ParsedCard(
            card_type="cloze",
            text=kd.unmask_code(converted, regions).strip(),
            media=media,
        )

    # 问答卡：按结构拆分正反面
    front_raw: str
    back_raw: str
    if is_sb:
        front_raw = blocks[0]
        back_raw = "\n\n".join(blocks[1:])
    elif (h := kd.heading_text(blocks[0])) is not None:
        front_raw = h
        back_raw = "\n\n".join(blocks[1:])
    elif kd.is_list(blocks[0]):
        item_front, item_rest = kd.split_list(blocks[0])
        front_raw = item_front
        back_raw = "\n\n".join(x for x in (item_rest, *blocks[1:]) if x)
    else:
        front_raw = blocks[0]
        back_raw = "\n\n".join(blocks[1:])

    def conv(raw: str) -> str:
        if not raw.strip():
            return ""
        m, regs = kd.mask_code(raw)
        return kd.unmask_code(md.convert(m, mark_mode="html", media_collector=collect), regs).strip()

    front, back = conv(front_raw), conv(back_raw)
    if not front:
        return ParsedCard(card_type="skip", warnings=["卡片正面为空"])
    return ParsedCard(card_type="qa", front=front, back=back, media=media)

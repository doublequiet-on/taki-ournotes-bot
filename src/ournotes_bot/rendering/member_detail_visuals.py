# L3
# Input: 已取得详情的成员 Card 与 locale。
# Output: render 返回成员详情图编码 bytes。
# Pos: Rendering / Card 的成员卡详情展示；见 L2-2-Card.md。
# Effects/Dependencies: 经共享工具读取字体、下载素材及写素材缓存，并复用列表布局／卡牌 helpers；不执行详情查询。

"""Member ID detail: full artwork, rarity frame, facts, stats and complete skills."""
from datetime import datetime, timezone, timedelta
import math

from ..query.card_catalog import TYPES, rarity_name
from .card_visuals import _lines, _paste, _text
from ..data import localized_text
from .member_list_visuals import _frame
from ..visuals import PAPER, INK, MUTED, ACCENT, BORDER, SURFACE, STAT_COLORS, RENDER_SCALE, _canvas, _font
from ..sources.yatta import BASE


def _section(title, paragraphs):
    lines = []
    for text, size, color in paragraphs:
        lines.extend((line, size, color) for line in _lines(text, 868, size))
    return title, lines, 82 + sum(size + 10 for _, size, _ in lines)


def _panel(draw, top, height, title):
    # Opaque white panels lift the information off the patterned page. Skill
    # headings use related blue/purple tones, with words remaining the main cue.
    accent = ("#65578F" if title.startswith(("激奏", "队长")) else
              "#486890" if title.startswith("演出") else ACCENT)
    draw.rounded_rectangle((36, top + 5, 972, top + height + 5), radius=20, fill="#D9DFEE")
    draw.rounded_rectangle((32, top, 968, top + height), radius=20, fill="#FFFFFF", outline=BORDER, width=2)
    draw.rounded_rectangle((45, top + 10, 955, top + 55), radius=12, fill=accent)
    draw.text((62, top + 14), title, font=_font(28), fill="white")


def _time(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            return "时间未确认"
        return date.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M（日本时间）")
    except (ValueError, TypeError, AttributeError):
        return "时间未获取"


def render(card, locale="zh"):
    from ..visuals import _asset, _bytes
    # The full image is contained once; nothing is cropped or painted over it.
    picture = _asset(card.full_url, (1520, 2040), contain=True)
    art_w, art_h = (max(1, picture.width // RENDER_SCALE), max(1, picture.height // RENDER_SCALE)) if picture else (760, 1014)
    heading = _section(localized_text(card, "character", locale) or "角色未确认", [
        (localized_text(card, "band", locale) or "乐队未获取", 24, ACCENT),
        (localized_text(card, "title", locale) or "卡名未获取", 32, INK),
    ])
    facts = [(f"ID  {card.id}       稀有度  {rarity_name(card)}", 27, INK),
             ("类型：" + TYPES.get(card.card_type, "未确认"), 25, INK)]
    tags = card.catalog.get("tags") or {}
    facts.append(("得意乐曲：" + (" / ".join(n.get(locale) or n.get("ja") or str(k) for k, n in tags.items()) if tags else "未获取"), 25, INK))
    blocks = [_section("基本资料", facts)]
    raw_stats = card.catalog.get("stats_level1")
    stats = raw_stats if (isinstance(raw_stats, (list, tuple)) and len(raw_stats) == 3 and
                         all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in raw_stats)) else None
    blocks.append(("属性", [], 336) if stats is not None else _section("属性", [("数值状态未核实，暂不展示培养数值。", 25, MUTED)]))
    names = {"leaderSkill": "队长技能", "liveSkill": "演出技能", "gekisouSkill": "激奏技能"}
    # Preserve every skill and its full text, including multiple same-kind entries.
    for kind, name in names.items():
        selected = [s for s in card.skills if s.kind == kind]
        if not selected:
            blocks.append(_section(name, [("详情未获取，不代表无技能。", 25, MUTED)]))
        for skill in selected:
            language = card.catalog.get("skill_languages", {}).get(kind, "未标注")
            language = {"zh": "中文", "ja": "日文", "en": "英文", "ja/en": "日文／英文"}.get(language, language)
            blocks.append(_section(name + " · Lv.5", [
                (localized_text(skill, "name", locale) or "名称未获取", 27, INK),
                (localized_text(skill, "description", locale) or "技能效果未获取", 25, INK),
                ("资料语言：" + language, 20, MUTED),
            ]))
    for skill in card.skills:
        if skill.kind not in names:
            blocks.append(_section("其他技能 · " + skill.kind, [
                (localized_text(skill, "name", locale), 27, INK),
                (localized_text(skill, "description", locale), 25, INK),
            ]))
    titles = card.localized.get("title", {})
    alternate = [(label + "：" + titles[lang], 24, INK) for lang, label in (("ja", "日文"), ("en", "英文"))
                 if titles.get(lang) and titles[lang] != localized_text(card, "title", locale)]
    alternate.append(("上游收录时间：" + _time(card.start_at), 23, MUTED))
    blocks.append(_section("名称与时间", alternate))
    note = "资料与完整卡面：Project Yume"
    if card.catalog.get("detail_stale"):
        note += "\n详情使用上次有效缓存。"
    notes = _lines(note, 896, 22)
    art_top = 148 + heading[2] + 24
    start = art_top + art_h + 24 + 28
    height = start + sum(block[2] + 20 for block in blocks) + len(notes) * 32 + 48
    if height > 12000:
        raise ValueError("detail exceeds image limit; use complete text fallback")
    image, draw = _canvas(1000, height, "角色卡档案")
    _panel(draw, 148, heading[2], heading[0])
    y = 214
    for line, size, color in heading[1]:
        draw.text((62, y), line, font=_font(size), fill=color)
        y += size + 10
    art_x = (1000 - art_w) // 2
    draw.rounded_rectangle((art_x - 12, art_top, art_x + art_w + 12, art_top + art_h + 24), radius=10, fill=PAPER)
    _paste(image, picture, (art_x, art_top + 12, art_w, art_h))
    _frame(image, draw, art_x - 12, art_top, card.rarity, art_w + 24, art_h + 24)
    y = start
    for title, lines, block_h in blocks:
        _panel(draw, y, block_h, title)
        if title == "属性" and stats is not None:
            draw.text((62, y + 64), f"综合力  {sum(stats):g}", font=_font(30), fill=INK)
            draw.text((62, y + 109), "Lv.1 / Rank 1 / 觉醒0次 · 资料状态", font=_font(23), fill=MUTED)
            maximum = max(stats) or 1
            for index, (name, value, color) in enumerate(zip(("表演", "技巧", "表现"), stats, STAT_COLORS)):
                by = y + 151 + index * 55
                draw.text((62, by), f"{name}  {value:g}", font=_font(24), fill=INK)
                draw.rounded_rectangle((240, by + 7, 932, by + 28), radius=10, fill=SURFACE)
                if value > 0:
                    draw.rounded_rectangle((240, by + 7, 240 + max(1, int(692 * value / maximum)), by + 28), radius=10, fill=color)
        else:
            ly = y + 66
            for line, size, color in lines:
                draw.text((62, ly), line, font=_font(size), fill=color)
                ly += size + 10
        if title == "基本资料" and card.card_type in TYPES:
            icon = _asset(f"{BASE}/images/CardType{card.card_type}.webp", (100, 100), contain=True)
            if icon:
                _paste(image, icon, (888, y + 14, 50, 50))
        y += block_h + 20
    _text(draw, notes, 52, y + 4, 22, MUTED)
    return _bytes(image)

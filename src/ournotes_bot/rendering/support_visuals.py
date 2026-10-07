# L3
# Input: 当前页 SNAP 列表或已取得详情的 SupportCard、条件文本、locale 与页脚。
# Output: render_list／render_detail 返回编码 bytes，保留未知／不适用说明。
# Pos: Rendering / Card 的 SNAP 列表与详情展示；见 L2-2-Card.md。
# Effects/Dependencies: 经共享工具读字体、下载素材并写缓存，列表消费 support_summary 的保守摘要；不重新筛选卡牌。

"""SNAP-only list and detail presentation; querying remains in card_catalog."""
import math
from concurrent.futures import ThreadPoolExecutor

from ..query.card_catalog import labels, rarity_name, TYPES
from .card_visuals import _lines, _paste, _text
from ..data import localized_text
from .member_list_visuals import _frame, tag_layout
from .member_detail_visuals import _section, _panel, _time
from ..visuals import PAPER, INK, MUTED, ACCENT, BORDER, SURFACE, _canvas, _font
from ..sources.yatta import BASE


def _stats(card):
    values = card.catalog.get("stats_level1")
    return values if (isinstance(values, (list, tuple)) and len(values) == 3 and
                      all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values)) else None


def _border(image, draw, x, y, card, width, height):
    _frame(image, draw, x, y, card.rarity, width, height, support=True)
    # Keep the existing EX label; its mint/cyan border follows the reference.
    if card.rarity not in (2, 3, 4):
        draw.rounded_rectangle((x + width - 60, y + 8, x + width - 8, y + 40), radius=8, fill=ACCENT)
        draw.text((x + width - 53, y + 9), "EX" if card.rarity == 10 else "?", font=_font(23), fill="white")


def render_list(cards, query, locale="zh", footer=""):
    from ..visuals import _asset, _bytes
    from .support_summary import entries
    tags, top = tag_layout(query)
    top += 12
    layouts = []
    for card in cards:
        groups = []
        for title, primary, qualifier in entries(card):
            lines = _lines(primary, 254, 21)
            more = _lines(qualifier, 254, 18) if qualifier else []
            groups.append((title, lines, more, 29 + len(lines) * 28 + len(more) * 24 + 10))
        layouts.append(groups)
    row_heights = [max(242 + sum(g[3] for g in groups) for groups in layouts[i:i+3]) for i in range(0, len(cards), 3)]
    bottom = top + sum(h + 20 for h in row_heights)
    source = "Haneoka JP" if cards and all(c.catalog.get("source") == "haneoka" for c in cards) else "Project Yume"
    notes = _lines(footer, 912, 23) + ["技能 Lv.5 · 本页仅展示基础效果，成员条件加成见 ID 详情", "资料与卡面：" + source]
    image, draw = _canvas(1000, bottom + len(notes) * 33 + 40, "SNAP · 支援卡列表")
    _text(draw, [footer.splitlines()[0] if footer else f"本页 {len(cards)} 张"], 40, 148, 25)
    for text, x, y, w in tags:
        draw.rounded_rectangle((x, y, x + w, y + 32), radius=10, fill=SURFACE)
        draw.text((x + 12, y + 1), text, font=_font(23), fill=INK)
    urls = list(dict.fromkeys(c.thumbnail_url for c in cards))
    types = list(dict.fromkeys(c.card_type for c in cards if c.card_type in TYPES))
    with ThreadPoolExecutor(max_workers=6) as pool:
        arts = dict(zip(urls, pool.map(lambda u: _asset(u, (508, 292), contain=True), urls)))
        icons = dict(zip(types, pool.map(lambda t: _asset(f"{BASE}/images/CardType{t}.webp", (84, 84), contain=True), types)))
    for index, card in enumerate(cards):
        row = index // 3
        x, y = 35 + index % 3 * 318, top + sum(h + 20 for h in row_heights[:row])
        height = row_heights[row]
        draw.rounded_rectangle((x, y, x + 294, y + height), radius=16, fill="#FFFFFF", outline=ACCENT, width=2)
        # Only the art has a rarity border; the entire card uses the theme frame.
        _paste(image, arts[card.thumbnail_url], (x + 20, y + 20, 254, 146))
        _border(image, draw, x + 12, y + 12, card, 270, 162)
        if icons.get(card.card_type):
            _paste(image, icons[card.card_type], (x + 12, y + 12, 42, 42))
        else:
            draw.text((x + 20, y + 20), TYPES.get(card.card_type, "类型未知").split("（")[0], font=_font(20), fill=INK)
        draw.rounded_rectangle((x + 10, y + 186, x + 284, y + height - 44), radius=10, fill="#E5EBF7")
        sy = y + 194
        for title, lines, more, block_h in layouts[index]:
            draw.text((x + 20, sy), title + "支援 · 基础", font=_font(18), fill=ACCENT)
            _text(draw, lines, x + 20, sy + 27, 21)
            _text(draw, more, x + 20, sy + 27 + len(lines) * 28, 18, MUTED)
            sy += block_h
        draw.text((x + 20, y + height - 36), f"{index + 1:02d} · ID {card.id}", font=_font(25), fill=INK)
    _text(draw, notes, 40, bottom + 8, 23, MUTED)
    return _bytes(image)


def render_detail(card, locale="zh"):
    from ..visuals import _asset, _bytes
    picture = _asset(card.full_url, (1800, 1800), contain=True)
    aw, ah = (max(1, picture.width // 2), max(1, picture.height // 2)) if picture else (900, 506)
    heading = _section("SNAP · " + str(card.id), [
        (localized_text(card, "title", locale) or "卡名未获取", 30, INK),
        (localized_text(card, "character", locale) or "角色关联未获取", 24, MUTED)])
    bands = card.catalog.get("bands") or []
    blocks = [_section("基本资料", [
        (f"ID  {card.id}     稀有度  {rarity_name(card)}", 27, INK),
        ("类型：" + TYPES.get(card.card_type, "未确认"), 25, INK),
        ("关联乐队：" + (" / ".join(bands) or "未获取"), 25, INK)])]
    values = _stats(card)
    blocks.append(_section("支援属性", [("Lv.1 / Rank 1 · 资料状态", 23, MUTED)] + (
        [(f"{name}  {value:g}%", 28, INK) for name, value in zip(("表演", "技巧", "表现"), values)] if values is not None
        else [("数值状态未核实，暂不展示培养数值。", 25, MUTED)])))
    names = {"supportSkill": "演出支援", "gekisouSupportSkill": "激奏支援"}
    for kind, name in names.items():
        selected = [s for s in card.skills if s.kind == kind]
        if not selected:
            na = kind == "gekisouSupportSkill" and card.catalog.get("categories", {}).get("gekisou") == ["not_applicable"]
            blocks.append(_section(name, [("不适用" if na else "详情未获取，不代表无技能。", 25, MUTED)]))
        for skill in selected:
            language = card.catalog.get("skill_languages", {}).get(kind, "未标注")
            language = {"zh": "中文", "en": "英文", "ja": "日文", "ja/en": "日文／英文"}.get(language, language)
            blocks.append(_section(name + " · Lv.5", [
                (localized_text(skill, "name", locale), 27, INK),
                (localized_text(skill, "description", locale) or "效果未获取", 25, INK),
                ("资料语言：" + language, 20, MUTED)]))
    for skill in card.skills:
        if skill.kind not in names:
            blocks.append(_section("其他支援技能", [(localized_text(skill, "name", locale), 27, INK),
                                                     (localized_text(skill, "description", locale), 25, INK)]))
    titles = card.localized.get("title", {})
    blocks.append(_section("名称与时间", [(label + "：" + titles[lang], 24, INK)
        for lang, label in (("ja", "日文"), ("en", "英文")) if titles.get(lang) and titles[lang] != localized_text(card, "title", locale)] +
        [("上游收录时间：" + _time(card.start_at), 23, MUTED)]))
    art_top = 148 + heading[2] + 24
    start = art_top + ah + 52
    source = "Haneoka JP" if card.catalog.get("source") == "haneoka" else "Project Yume"
    note = ["资料与完整卡面：" + source] + (["详情使用上次有效缓存。"] if card.catalog.get("detail_stale") else [])
    height = start + sum(b[2] + 20 for b in blocks) + len(note) * 32 + 48
    if height > 12000:
        raise ValueError("detail exceeds image limit; use complete text fallback")
    image, draw = _canvas(1000, height, "SNAP · 支援档案")
    for top, block in [(148, heading)]:
        _panel(draw, top, block[2], block[0])
        yy = top + 66
        for line, size, color in block[1]:
            draw.text((62, yy), line, font=_font(size), fill=color)
            yy += size + 10
    x = (1000 - aw) // 2
    _paste(image, picture, (x, art_top + 12, aw, ah))
    _border(image, draw, x - 12, art_top, card, aw + 24, ah + 24)
    y = start
    for title, lines, block_h in blocks:
        _panel(draw, y, block_h, title)
        yy = y + 66
        for line, size, color in lines:
            draw.text((62, yy), line, font=_font(size), fill=color)
            yy += size + 10
        y += block_h + 20
    _text(draw, note, 52, y + 4, 22, MUTED)
    return _bytes(image)

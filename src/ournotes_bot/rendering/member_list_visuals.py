# L3
# Input: 当前页成员卡列表、条件文本、locale 与页脚；Haneoka Snapshot 由内部取得。
# Output: render 返回成员列表图编码 bytes，保留摘要缺失／未确认状态。
# Pos: Rendering / Card 的成员卡列表展示；见 L2-2-Card.md。
# Effects/Dependencies: 调用 get_snapshot 可联网刷新并读写 Haneoka 独立缓存；经根 visuals 读字体、下载素材及写缓存，并进行 CPU／内存绘图。

"""Member condition-list presentation only; selection and pagination stay upstream."""
import math
import re
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw, ImageColor

from ..query.card_catalog import TYPES
from ..sources.haneoka.haneoka_members import get_snapshot
from .card_visuals import _lines, _paste, _text
from ..visuals import PAPER, INK, BORDER, SURFACE, RENDER_SCALE, _canvas, _font
from ..sources.yatta import BASE

# A designed border treatment, not a claim to reproduce an official frame asset.
RARITY_BORDER = {3: "#F5C52B", 2: "#528CDD"}
WIDTH, CARD_W, CARD_H, STEP_X, STEP_Y = 1000, 294, 472, 318, 522


def condition_tags(query):
    """Display the validated query verbatim; never re-interpret its logic here."""
    if not query.strip():
        return ["条件：不限"]
    # Keep multiword names/values intact. Split only at explicit field boundaries.
    parts = re.split(r"\s+(?=[^\s=：:]+\s*[=：:])", query.strip())
    return [("稀有度：" + " / ".join(part.split())
             if re.fullmatch(r"(?:BD|SSR|SR|R)(?:\s+(?:BD|SSR|SR|R))*", part)
             else part.replace("=", "：", 1)) for part in parts]


def tag_layout(query):
    result, x, y = [], 40, 187
    for tag in condition_tags(query):
        for line in _lines(tag, 882, 23):
            width = math.ceil(_font(23).getlength(line)) + 24
            if x + width > 960:
                x, y = 40, y + 38
            result.append((line, x, y, width))
            x += width + 10
    return result, y + 48


def _frame(image, draw, x, y, rarity, width=CARD_W, height=CARD_H, *, support=False):
    scale = RENDER_SCALE
    w, h = width * scale, height * scale
    mask = Image.new("L", (w + 1, h + 1))
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w, h), radius=8 * scale, outline=255, width=6 * scale)
    if rarity == 4 or (support and rarity == 10) or (not support and rarity == 20):
        # Approximate the unobscured border colours in user screenshots
        # IMG_0479 (member) / IMG_0481 (SNAP), not official frame assets.
        # Two independent edge profiles retain their different hue directions.
        if not support and rarity == 20:
            # BD member frame: pink/lilac at the top, coral-red at the bottom.
            # Reference: user-provided birthday card screenshot (2026-10-04).
            left = ((0, "#DEA9DB"), (.5, "#EF9CCF"), (1, "#EF5B79"))
            right = ((0, "#E8A6D9"), (.5, "#F396C7"), (1, "#F15D78"))
        elif support and rarity == 10:
            left = ((0, "#91FFC7"), (1, "#9DFDDF"))
            right = ((0, "#9FFDE3"), (1, "#A9FBFB"))
        elif support:
            left = ((0, "#FF808D"), (.25, "#FF80C3"), (.5, "#FF81D4"),
                    (.75, "#FF98A4"), (1, "#FFCE11"))
            right = ((0, "#FFD200"), (.08, "#6DEA91"), (.4, "#40D8EB"),
                     (.5, "#3ED0FA"), (.65, "#52C0FB"), (.75, "#7EC4FF"), (1, "#87AAFF"))
        else:
            upper = ((0, "#FF6666"), (.08, "#FD7BDF"), (.2, "#F9D505"), (.4, "#00FFF7"))
            left = upper + ((.8, "#00FFF7"), (.95, "#7200FF"), (1, "#7200FF"))
            right = upper + ((.8, "#7200FF"), (1, "#7200FF"))

        def color_at(stops, position):
            for (p, a), (q, b) in zip(stops, stops[1:]):
                if position <= q:
                    mix = (position - p) / (q - p)
                    return tuple(round(v + (z - v) * mix) for v, z in zip(ImageColor.getrgb(a), ImageColor.getrgb(b)))
            return ImageColor.getrgb(stops[-1][1])

        # Only the rounded border mask is pasted. The card art stays untouched.
        strip = Image.new("RGB", (2, h + 1))
        strip.putdata([color_at(stops, yy / h) for yy in range(h + 1) for stops in (left, right)])
        layer = strip.transform(mask.size, Image.Transform.EXTENT,
                                (.5, 0, 1.5, h + 1), Image.Resampling.BILINEAR)
    else:
        layer = Image.new("RGB", mask.size, RARITY_BORDER.get(rarity, BORDER))
    image.paste(layer, (x * scale, y * scale), mask)
    draw.rounded_rectangle((x + 6, y + 6, x + width - 6, y + height - 6), radius=3, outline="#ECF2FF", width=1)


def _skill_panel(image, draw, x, y, summaries):
    # One integrated gradient over the artwork, not two opaque sticker backgrounds.
    scale = RENDER_SCALE
    extra = max(0, len(summaries[2][1].splitlines()) - 1) * 23
    width, height = CARD_W - 16, 188 + extra
    layer = Image.new("RGBA", (width * scale, height * scale))
    ld = ImageDraw.Draw(layer)
    for yy in range(height * scale):
        alpha = round(205 * min(1, yy / (20 * scale)))
        ld.line((0, yy, layer.width, yy), fill=(24, 34, 61, alpha))
    image.paste(layer, ((x + 8) * scale, (y + CARD_H - height - 8) * scale), layer)
    start = y + CARD_H - 180 - extra
    for index, title in enumerate(("队长", "演出", "激奏")):
        primary, qualifier = summaries[index]
        # Keep type/value and conditions separate; never shrink into illegibility.
        py = start + (0, 74, 123)[index]
        draw.text((x + 13, py + 1), title, font=_font(19), fill="#C9D8FF")
        size = 21
        while size > 18 and _font(size).getlength(primary) > CARD_W - 66:
            size -= 1
        if _font(size).getlength(primary) > CARD_W - 66 or any(_font(18).getlength(line) > CARD_W - 26 for line in qualifier.splitlines()):
            primary, qualifier = "摘要较长", "请按ID查看完整技能"
        draw.text((x + 57, py), primary, font=_font(size), fill="white")
        for line_index, line in enumerate(qualifier.splitlines()):
            draw.text((x + 13, py + 25 + line_index * 23), line, font=_font(18), fill="#ECF2FF")
    for offset in (72, 121):
        draw.line((x + 13, start + offset, x + CARD_W - 13, start + offset), fill="#8E9EBE", width=1)


def render(cards, query, locale="zh", footer=""):
    from ..visuals import _asset, _bytes
    snapshot = get_snapshot(cards)
    tags, top = tag_layout(query)
    top += 12
    footer_lines = _lines(footer, 912, 23) if footer else []
    footer_lines += ["技能 Lv.5 · 箭头后值仅在所列条件成立时适用"]
    footer_lines += ["技能来源：Haneoka 日服" + (" · 使用旧缓存" if snapshot and snapshot.stale else
                                            "" if snapshot else " · 数据暂不可用")]
    rows = math.ceil(len(cards) / 3)
    bottom = top + rows * STEP_Y
    height = bottom + len(footer_lines) * 33 + 44
    image, draw = _canvas(WIDTH, height, "角色卡列表")
    draw.rounded_rectangle((24, 138, 976, top - 12), radius=18, fill=PAPER)
    summary = footer.splitlines()[0] if footer else f"本页 {len(cards)} 张"
    _text(draw, [summary], 40, 148, 25)
    for text, x, y, width in tags:
        draw.rounded_rectangle((x, y, x + width, y + 32), radius=10, fill=SURFACE)
        draw.text((x + 12, y + 1), text, font=_font(23), fill=INK)
    urls = list(dict.fromkeys(c.thumbnail_url for c in cards))
    types = list(dict.fromkeys(c.card_type for c in cards if c.card_type in TYPES))
    with ThreadPoolExecutor(max_workers=6) as pool:
        arts = dict(zip(urls, pool.map(lambda url: _asset(url, ((CARD_W - 16) * RENDER_SCALE, 376 * RENDER_SCALE), contain=True), urls)))
        icons = dict(zip(types, pool.map(lambda t: _asset(f"{BASE}/images/CardType{t}.webp", (92, 92), contain=True), types)))
    for i, card in enumerate(cards):
        x, y = 35 + i % 3 * STEP_X, top + i // 3 * STEP_Y
        # ID is a frame attachment outside the art, aligned consistently per cell.
        draw.rounded_rectangle((x, y, x + CARD_W, y + CARD_H + 38), radius=12, fill=PAPER,
                               outline=BORDER, width=1)
        draw.rounded_rectangle((x + 6, y + 6, x + CARD_W - 6, y + CARD_H - 6), radius=6, fill=INK)
        _paste(image, arts[card.thumbnail_url], (x + 8, y + 8, CARD_W - 16, 376))
        _frame(image, draw, x, y, card.rarity)
        icon = icons.get(card.card_type)
        if icon:
            _paste(image, icon, (x + 4, y + 4, 46, 46))
        else:
            value = TYPES.get(card.card_type, "类型未确认").split("（")[0]
            draw.rounded_rectangle((x + 4, y + 4, x + 114, y + 32), radius=6, fill=PAPER)
            _text(draw, [value], x + 8, y + 6, 18)
        summaries = snapshot.for_card(card) if snapshot else (("数据暂不可用", "稍后重试技能摘要"),) * 3
        _skill_panel(image, draw, x, y, summaries)
        draw.text((x + 12, y + CARD_H + 5), f"{i + 1:02d} · ID", font=_font(21), fill="#45516F")
        draw.text((x + 100, y + CARD_H + 2), str(card.id), font=_font(25), fill=INK)
    if footer_lines:
        draw.rounded_rectangle((24, bottom, 976, height - 16), radius=16, fill=PAPER)
        _text(draw, footer_lines, 40, bottom + 8, 23)
    return _bytes(image)

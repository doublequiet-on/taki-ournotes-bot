# L3
# Input: 已选单卡或当前页卡牌列表、条件文本、locale 与页脚。
# Output: grid／detail 返回编码 bytes；art 返回保留比例的卡面 bytes，素材不可用时为 None。
# Pos: Rendering / Card 的成员／SNAP 分派与卡牌共享辅助；见 L2-2-Card.md。
# Effects/Dependencies: 经根 visuals 读字体、下载素材并写缓存；成员列表分支经绘制器取得 Haneoka 快照，可能发生来源网络／缓存 I/O。

"""ON catalog layouts. Neutral frame fallback; never claim a hand-drawn native frame."""
from PIL import Image, ImageOps

from ..data import SupportCard

from ..visuals import INK, SURFACE, _ScaledDraw


def _lines(text, width, size):
    from ..visuals import _font
    font = _font(size)
    lines = []
    for paragraph in text.splitlines():
        current = ""
        for char in paragraph:
            if current and font.getlength(current + char) > width:
                lines.append(current)
                current = ""
            current += char
        lines.append(current)
    return lines


def _text(draw, lines, x, y, size=24, fill=INK):
    from ..visuals import _font
    for line in lines:
        draw.text((x, y), line, font=_font(size), fill=fill)
        y += size + 10
    return y


def _paste(image, asset, box, missing="卡面暂不可用"):
    from ..visuals import _font
    x, y, w, h = box
    scale = image.info.get("render_scale", 1)
    draw = _ScaledDraw(image, scale)
    if asset is None:
        draw.rounded_rectangle((x, y, x + w, y + h), radius=12, fill=SURFACE)
        draw.text((x + 12, y + h // 3), missing, font=_font(22), fill=INK)
    else:
        asset = ImageOps.contain(asset, (w * scale, h * scale), Image.Resampling.LANCZOS)
        image.paste(asset, (x * scale + (w * scale - asset.width) // 2,
                           y * scale + (h * scale - asset.height) // 2),
                    asset if asset.mode == "RGBA" else None)


def grid(cards, query, locale="zh", footer="", support=False):
    from ..visuals import _asset, _bytes
    if not cards or len(cards) > 16:
        raise ValueError("catalog grid expects one page (1-16 cards)")
    if not support:
        from .member_list_visuals import render
        return render(cards, query, locale, footer)
    from .support_visuals import render_list
    return render_list(cards, query, locale, footer)


def detail(card, locale="zh"):
    if isinstance(card, SupportCard):
        from .support_visuals import render_detail
    else:
        from .member_detail_visuals import render as render_detail
    return render_detail(card, locale)


def art(card):
    from ..visuals import _asset, _bytes
    asset = _asset(card.full_url, (1800, 2400), contain=True)
    if asset is None:
        return None
    image = Image.new("RGB", asset.size, "white")
    image.paste(asset, (0, 0), asset)
    return _bytes(image)

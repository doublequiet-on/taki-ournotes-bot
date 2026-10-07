# L3
# Input: 已选歌曲／单卡／当前页列表、Song／Chart 与可选谱面 score dict、MetaAnswer、条件／locale／页脚；render_catalog 消费 CardAnswer。
# Output: render 返回编码 bytes；分数表串行绘图，双榜逐表合成后统一编码，不再次求值。
# Pos: Rendering 的共享主题／素材、歌曲绘图与卡牌委托入口；见 rendering/L2-2-Core.md、rendering/L2-2-Song.md、rendering/L2-2-Card.md。
# Effects/Dependencies: 字体／素材读取、允许来源下载；素材完整解码后原子缓存，旧坏缓存有限重取；委托 rendering 绘图器，成员列表可间接刷新 Haneoka；不调用模型或上传 QQ。

"""Rounded, high-contrast image replies using the existing Our Notes assets."""

from __future__ import annotations

import io
import re
import asyncio
import hashlib
import math
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from urllib.request import Request, urlopen

import aiohttp

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import runtime_data_dir
from .data import Card, Chart, Skill, Song, SupportCard, localized_text
from .query.efficiency_query import MetaAnswer
from .sources.yatta import ASSETS, BASE


PAPER = "#F1F2F8"
INK = "#202B49"
MUTED = "#45516F"
ACCENT = "#475B96"
STAT_COLORS = ("#697DA6", "#8887B2", "#A0A6BF")
BORDER = "#AAB5D0"
SURFACE = "#E6EAF4"
MOTIF_INK = "#A0A8C7"
MOTIF_MUTED = "#BBC1D7"
FABRIC_TILE_SIZE = 640
RENDER_SCALE = 2
_META_RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="taki-meta-render")
DIFFICULTY_COLORS = {
    "EASY": ("#D4E2F5", "#799CC8"),
    "NORMAL": ("#D3E8DF", "#78A793"),
    "HARD": ("#F3E4BC", "#B59A57"),
    "EXPERT": ("#EED3E0", "#B77D9B"),
}
MISSION_ICON_URLS = {
    kind: ("https://haneoka.org/assets/jp/Assets/AddressableResources/"
           f"UI/Texture/Tmp1/BattleLive/Icon_gekisou_{kind.lower()}.png")
    for kind in ("JUST", "COMBO", "LUCK")
}
FONT_PATHS = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]
IMAGE_TEXT = {
    "zh": {"songs": "曲目检索", "song_list": "歌曲列表", "chart": "谱面资料", "card": "成员卡档案", "cards": "成员卡检索", "card_list": "成员卡列表", "support_card": "支援卡档案", "support_cards": "支援卡检索", "support_card_list": "支援卡列表", "composer": "作曲", "lyricist": "作词", "preview": "音符谱面暂不可用；当前展示等级与 Note 数。", "score_title": "音符谱面 · {difficulty}", "score_note": "按音符节点绘制的静态预览；长条轨迹为节点连线。", "image_missing": "图片暂不可用", "power": "综合力", "support_bonus": "支援加成", "performance": "演出", "technic": "技巧", "visual": "表现", "skill": "技能", "type": "属性", "level5": "Lv.5 效果"},
    "en": {"songs": "Song search", "song_list": "Songs", "chart": "Chart details", "card": "Member card", "cards": "Member cards", "card_list": "Member cards", "support_card": "Support card", "support_cards": "Support cards", "support_card_list": "Support cards", "composer": "Composer", "lyricist": "Lyrics", "preview": "Project Yume data: levels and note counts only.", "score_title": "Note chart · {difficulty}", "score_note": "Static preview from note nodes; holds use straight node connections.", "image_missing": "Image unavailable", "power": "Total power", "support_bonus": "Support bonus", "performance": "Performance", "technic": "Technique", "visual": "Visual", "skill": "Skill", "type": "Type", "level5": "Lv.5 effect"},
    "ja": {"songs": "楽曲検索", "song_list": "楽曲一覧", "chart": "譜面情報", "card": "メンバーカード", "cards": "メンバーカード検索", "card_list": "メンバーカード一覧", "support_card": "サポートカード", "support_cards": "サポートカード検索", "support_card_list": "サポートカード一覧", "composer": "作曲", "lyricist": "作詞", "preview": "Project Yume のレベルとノーツ数を表示します。", "score_title": "ノーツ譜面 · {difficulty}", "score_note": "ノーツ座標による静的プレビュー。ロングは節点を直線で結びます。", "image_missing": "画像を取得できません", "power": "総合力", "support_bonus": "サポート効果", "performance": "パフォーマンス", "technic": "テクニック", "visual": "ビジュアル", "skill": "スキル", "type": "属性", "level5": "Lv.5 効果"},
}


def _label(locale: str, key: str) -> str:
    return IMAGE_TEXT.get(locale, IMAGE_TEXT["zh"])[key]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return _cached_font(size, tuple(FONT_PATHS))


@lru_cache(maxsize=64)
def _cached_font(size: int, paths: tuple[str, ...]) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in paths:
        try:
            if Path(path).is_absolute() and Path(path).exists():
                return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise RuntimeError("未找到可用的中日韩字体；请安装 Noto Sans CJK 或微软雅黑，图片回复将退回文字。")


class _ScaledDraw:
    """Keep layout in logical pixels while drawing shapes and glyphs at full resolution."""

    def __init__(self, image: Image.Image, scale: int):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.scale = scale

    def _coordinates(self, xy):
        return [tuple(value * self.scale for value in point) if isinstance(point, (tuple, list))
                else point * self.scale for point in xy]

    def textlength(self, text, **kwargs):
        return self.draw.textlength(text, **kwargs)

    def textbbox(self, xy, text, **kwargs):
        return self.draw.textbbox(xy, text, **kwargs)

    def text(self, xy, text, **kwargs):
        kwargs["font"] = _font(kwargs["font"].size * self.scale)
        return self.draw.text(self._coordinates(xy), text, **kwargs)

    def _shape(self, name, xy, **kwargs):
        kwargs["width"] = kwargs.get("width", 1) * self.scale
        coordinates = self._coordinates(xy)
        points = coordinates if isinstance(coordinates[0], (tuple, list)) else list(zip(coordinates[::2], coordinates[1::2]))
        xs, ys = zip(*points)
        if name == "rectangle" or (name == "line" and (len(set(xs)) == 1 or len(set(ys)) == 1)):
            return getattr(self.draw, name)(coordinates, **kwargs)
        if name == "rounded_rectangle":
            # Straight edges stay crisp; only corners need supersampling. Saving their
            # original backgrounds avoids blending on top of the aliased first pass.
            left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
            radius = min(kwargs.get("radius", 0), (right - left) / 2, (bottom - top) / 2)
            span = math.ceil(radius) + 4
            corners = [(left - 4, top - 4, left + span, top + span),
                       (right - span, top - 4, right + 4, top + span),
                       (left - 4, bottom - span, left + span, bottom + 4),
                       (right - span, bottom - span, right + 4, bottom + 4)]
            saved = [(region, self.image.crop(region)) for box in corners
                     if (region := self._clip(box)) is not None]
            self.draw.rounded_rectangle(coordinates, **kwargs)
            for region, base in saved:
                self._smooth_region(name, points, kwargs, region, base)
            return
        padding = kwargs["width"] + 4
        bounds = self._clip((min(xs) - padding, min(ys) - padding,
                             max(xs) + padding + 1, max(ys) + padding + 1))
        if bounds is None:
            return
        left, top, right, bottom = bounds
        # Limit temporary raster size even for holds spanning a long note chart.
        for y in range(top, bottom, 128):
            region = (left, y, right, min(y + 128, bottom))
            self._smooth_region(name, points, kwargs, region, self.image.crop(region))

    def _clip(self, box):
        left, top = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
        right, bottom = min(self.image.width, math.ceil(box[2])), min(self.image.height, math.ceil(box[3]))
        return (left, top, right, bottom) if left < right and top < bottom else None

    def _smooth_region(self, name, points, kwargs, region, base):
        sampling, pad = 3, 4
        left, top, right, bottom = region
        width, height = right - left, bottom - top
        layer = Image.new("RGBA", ((width + 2 * pad) * sampling, (height + 2 * pad) * sampling))
        local = [((x - left + pad) * sampling, (y - top + pad) * sampling) for x, y in points]
        options = dict(kwargs, width=kwargs["width"] * sampling)
        if "radius" in options:
            options["radius"] *= sampling
        getattr(ImageDraw.Draw(layer), name)(local, **options)
        # Area coverage avoids the light/dark ringing of a sharpening resampler.
        layer = layer.resize((width + 2 * pad, height + 2 * pad), Image.Resampling.BOX)
        layer = layer.crop((pad, pad, width + pad, height + pad))
        base.paste(layer, (0, 0), layer)
        self.image.paste(base, (left, top))

    def line(self, xy, **kwargs):
        return self._shape("line", xy, **kwargs)

    def rectangle(self, xy, **kwargs):
        return self._shape("rectangle", xy, **kwargs)

    def polygon(self, xy, **kwargs):
        return self._shape("polygon", xy, **kwargs)

    def rounded_rectangle(self, xy, radius=0, **kwargs):
        return self._shape("rounded_rectangle", xy, radius=radius * self.scale, **kwargs)


def _motif(draw: ImageDraw.ImageDraw, kind: str, x: int, y: int, size: int,
           color: str = MOTIF_INK) -> None:
    """Small deterministic line art; never baked into a downloaded game asset."""
    def points(values):
        return [(x + a * size, y + b * size) for a, b in values]

    stroke = max(2, size // 18)
    if kind == "meteor":
        # A directional four-point star echoes the official compass motif.
        star = [(0.25 + (0.24 if i % 2 == 0 else 0.08) * math.sin(i * math.pi / 4),
                 0.70 - (0.24 if i % 2 == 0 else 0.08) * math.cos(i * math.pi / 4)) for i in range(8)]
        draw.line(points(star + star[:1]), fill=color, width=stroke, joint="curve")
        draw.line(points([(0.45, 0.43), (0.95, 0.05)]), fill=color, width=stroke)
        draw.line(points([(0.56, 0.54), (1.06, 0.16)]), fill=color, width=stroke)
    elif kind == "moon":
        curve = []
        for controls in (((.35, .05), (1.10, .27), (1.02, .90), (.20, .96)),
                         ((.20, .96), (.75, .63), (.71, .35), (.35, .05))):
            for step in range(21):
                t = step / 20
                weights = ((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3)
                curve.append(tuple(sum(weight * point[axis] for weight, point in zip(weights, controls)) for axis in (0, 1)))
        draw.line(points(curve), fill=color, width=stroke, joint="curve")
    elif kind == "stone":
        curve = [(0.13, 0.68), (0.19, 0.42), (0.41, 0.19), (0.63, 0.17),
                 (0.88, 0.37), (0.93, 0.56), (0.78, 0.78), (0.45, 0.88),
                 (0.24, 0.83), (0.13, 0.68)]
        draw.line(points(curve), fill=color, width=stroke, joint="curve")
        draw.line(points([(0.28, 0.76), (0.44, 0.48), (0.65, 0.26)]), fill=color, width=stroke)
    else:
        # Rounded fourfold petals borrow the rhythm, not the official SVG artwork.
        petals = []
        for step in range(97):
            angle = step * math.tau / 96
            radius = .30 + .12 * math.cos(4 * angle)
            petals.append((.5 + radius * math.cos(angle), .5 + radius * math.sin(angle)))
        draw.line(points(petals), fill=color, width=stroke, joint="curve")
        draw.line(points([(.5, .41), (.59, .5), (.5, .59), (.41, .5), (.5, .41)]),
                  fill=color, width=stroke, joint="curve")


def _decoration_layout(width: int, height: int) -> list[tuple[str, int, int, int, str]]:
    """One fixed fabric pattern: canvas dimensions only crop the repeated tile."""
    tile = (("meteor", 48, 25, 38), ("flower", 225, 70, 36),
            ("moon", 425, 20, 38), ("stone", 598, 114, 36),
            ("moon", -12, 192, 38), ("stone", 164, 228, 36),
            ("meteor", 354, 180, 38), ("flower", 520, 278, 36),
            ("stone", 33, 405, 36), ("moon", 237, 360, 38),
            ("flower", 419, 439, 36), ("meteor", 610, 370, 38),
            ("flower", -12, 550, 36), ("meteor", 166, 579, 38),
            ("stone", 348, 539, 36), ("moon", 547, 576, 38))
    return [(kind, ox + x, oy + y, size, MOTIF_MUTED)
            for oy in range(-FABRIC_TILE_SIZE, height, FABRIC_TILE_SIZE)
            for ox in range(-FABRIC_TILE_SIZE, width + FABRIC_TILE_SIZE, FABRIC_TILE_SIZE)
            for kind, x, y, size in tile
            if ox + x < width and ox + x + size * 1.06 >= 0
            and oy + y < height and oy + y + size >= 0]


@lru_cache(maxsize=2)
def _fabric_tile(scale: int = 1) -> Image.Image:
    # Supersample only the reusable tile, keeping long-reply memory use bounded.
    sampling, pad = 3 * scale, 4
    extent = FABRIC_TILE_SIZE + 2 * pad
    tile = Image.new("RGB", (extent * sampling, extent * sampling), PAPER)
    draw = ImageDraw.Draw(tile)
    for kind, x, y, size, color in _decoration_layout(extent, extent):
        _motif(draw, kind, (x + pad) * sampling, (y + pad) * sampling, size * sampling, color)
    tile = tile.resize((extent * scale, extent * scale), Image.Resampling.LANCZOS)
    return tile.crop((pad * scale, pad * scale, (extent - pad) * scale, (extent - pad) * scale))


def _background(width: int, height: int, scale: int = 1) -> tuple[Image.Image, ImageDraw.ImageDraw | _ScaledDraw]:
    image = Image.new("RGB", (width * scale, height * scale), PAPER)
    image.info["render_scale"] = scale
    tile = _fabric_tile(scale)
    for y in range(0, image.height, tile.height):
        for x in range(0, image.width, tile.width):
            image.paste(tile, (x, y))
    return image, _ScaledDraw(image, scale) if scale != 1 else ImageDraw.Draw(image)


def _canvas(width: int, height: int, label: str) -> tuple[Image.Image, ImageDraw.ImageDraw | _ScaledDraw]:
    image, draw = _background(width, height, RENDER_SCALE)
    title_width = min(width - 100, draw.textlength(label, font=_font(36)))
    draw.rounded_rectangle((34, 24, 70 + title_width, 104), radius=22, fill=PAPER)
    draw.rounded_rectangle((36, 132, width - 36, min(186, height - 16)), radius=18, fill=PAPER)
    _write(draw, label, 50, 40, width - 100, 36)
    draw.line((50, 126, width - 50, 126), fill=BORDER, width=2)
    return image, draw


def _write(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, max_width: int, size: int, color: str = INK) -> None:
    font = _font(size)
    if max_width <= 0:
        return
    if draw.textlength(text, font=font) > max_width:
        while text and draw.textlength(text + "…", font=font) > max_width:
            text = text[:-1]
        text += "…" if draw.textlength("…", font=font) <= max_width else ""
    draw.text((x, y), text, font=font, fill=color)


def _difficulty_badge(draw: ImageDraw.ImageDraw, difficulty: str, value: str,
                      box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    difficulty = difficulty.strip().upper()
    fill, outline = DIFFICULTY_COLORS.get(difficulty, (SURFACE, BORDER))
    draw.rounded_rectangle(box, radius=min(24, (y1 - y0) // 2), fill=fill, outline=outline, width=2)
    for text, size, center_y in ((difficulty, 15, y0 + (y1 - y0) * .26),
                                (value, 28, y0 + (y1 - y0) * .65)):
        while size > 10 and draw.textlength(text, font=_font(size)) > x1 - x0 - 12:
            size -= 1
        bounds = draw.textbbox((0, 0), text, font=_font(size))
        y = round(center_y - (bounds[1] + bounds[3]) / 2)
        _write(draw, text, int((x0 + x1 - min(draw.textlength(text, font=_font(size)), x1 - x0 - 12)) / 2),
               y, x1 - x0 - 12, size)


def _wrapped_lines(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, max_lines: int = 3) -> list[str]:
    font = _font(size)
    lines: list[str] = []
    current = ""
    for character in text:
        candidate = current + character
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = character
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    if len(lines) < max_lines and current:
        lines.append(current)
    if lines and "".join(lines) != text:
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def _skill_label(kind: str, locale: str) -> str:
    labels = {
        "zh": {"leaderSkill": "队长技能", "liveSkill": "Live 技能", "gekisouSkill": "激奏技能", "supportSkill": "支援技能", "gekisouSupportSkill": "激奏支援技能"},
        "en": {"leaderSkill": "Leader skill", "liveSkill": "Live skill", "gekisouSkill": "Gekisou skill", "supportSkill": "Support skill", "gekisouSupportSkill": "Gekisou support"},
        "ja": {"leaderSkill": "リーダースキル", "liveSkill": "ライブスキル", "gekisouSkill": "激奏スキル", "supportSkill": "サポートスキル", "gekisouSupportSkill": "激奏サポート"},
    }
    return labels.get(locale, labels["zh"]).get(kind, _label(locale, "skill"))


def _rarity(rarity: int) -> str:
    return "★" * rarity if 0 < rarity <= 5 else "SPECIAL"


def _skill_rows(draw: ImageDraw.ImageDraw, skills: tuple[Skill, ...],
                max_width: int, locale: str) -> list[tuple[str, int, str, int]]:
    rows = []
    for skill in skills:
        title = f"{_skill_label(skill.kind, locale)} · {localized_text(skill, 'name', locale)}"
        rows.extend((line, 24, INK, 36) for line in _wrapped_lines(draw, title, max_width, 24, len(title) + 1))
        description = localized_text(skill, "description", locale)
        if description:
            text = f"{_label(locale, 'level5')}：{description}"
            rows.extend((line, 22, MUTED, 33) for line in _wrapped_lines(draw, text, max_width, 22, len(text) + 1))
        rows.append(("", 22, MUTED, 18))
    return rows


def _draw_skills(draw: ImageDraw.ImageDraw, skills: tuple[Skill, ...], x: int, y: int,
                 max_width: int, locale: str) -> int:
    for text, size, color, advance in _skill_rows(draw, skills, max_width, locale):
        if text:
            _write(draw, text, x, y, max_width, size, color)
        y += advance
    return y


def _asset(url: str, size: tuple[int, int], *, contain: bool = False) -> Image.Image | None:
    from .sources.haneoka.catalog_assets import is_catalog_asset, download_asset
    catalog_asset = is_catalog_asset(url)
    icons = {f"{BASE}/images/CardType{i}.webp" for i in range(1, 6)}
    music_jacket = re.fullmatch(r"https://assets\.bdon\.moe/ja/Image/Jacket/([A-Za-z0-9_-]+)/\1\.webp", url)
    if not catalog_asset and not url.startswith(ASSETS + "/") and not music_jacket and url not in icons and url not in MISSION_ICON_URLS.values():
        return None
    def decode(raw):
        if len(raw) > 6_000_000:
            raise ValueError("asset size limit")
        with Image.open(io.BytesIO(raw)) as image:
            image.load()  # Decode the full source before it becomes reusable cache.
            if contain:
                return ImageOps.contain(image.convert("RGBA"), size, method=Image.Resampling.LANCZOS)
            return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)
    try:
        cache = runtime_data_dir() / ("haneoka-asset-cache" if catalog_asset else "asset-cache")
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".png")
        if path.exists():
            try:
                if path.stat().st_size <= 6_000_000:
                    return decode(path.read_bytes())
            except Exception:
                pass  # Old bad cache is retried once through the existing fetch path.
        try:
            if catalog_asset:
                raw = download_asset(url)
            else:
                raw = None
            referer = "https://haneoka.org/" if url in MISSION_ICON_URLS.values() else BASE + "/"
            if raw is None:
                with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer}), timeout=12) as response:
                    raw = response.read(6_000_001)
        except Exception:
            if catalog_asset:
                raise
            async def download() -> bytes:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=18)) as response:
                        response.raise_for_status()
                        return await response.read()
            raw = asyncio.run(download())
        decoded = decode(raw)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=cache, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
            os.replace(temporary, path)
        except Exception:
            decoded.close()
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return decoded
    except Exception:
        return None


def _prefetch_assets(urls: list[str], size: tuple[int, int]) -> dict[str, Image.Image | None]:
    unique = list(dict.fromkeys(urls))
    size = (size[0] * RENDER_SCALE, size[1] * RENDER_SCALE)
    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(zip(unique, pool.map(lambda url: _asset(url, size), unique)))


def _paste_loaded_asset(canvas: Image.Image, draw: ImageDraw.ImageDraw, image: Image.Image | None,
                        box: tuple[int, int, int, int], locale: str = "zh") -> None:
    x0, y0, x1, y1 = box
    scale = canvas.info.get("render_scale", 1)
    radius = min(26, (x1 - x0) // 4, (y1 - y0) // 4)
    if image:
        target = ((x1 - x0) * scale, (y1 - y0) * scale)
        if image.size != target:
            image = ImageOps.fit(image, target, method=Image.Resampling.LANCZOS)
        sampling = 3
        mask = Image.new("L", (image.width * sampling, image.height * sampling))
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1),
                                              radius=radius * scale * sampling, fill=255)
        mask = mask.resize(image.size, Image.Resampling.BOX)
        canvas.paste(image, (x0 * scale, y0 * scale), mask)
    else:
        draw.rounded_rectangle(box, radius=radius, fill="#DEE3F2")
        size = min(40, (x1 - x0) // 2, (y1 - y0) // 2)
        _motif(draw, "meteor", (x0 + x1 - size) // 2, (y0 + y1 - size) // 2, size, ACCENT)
        if x1 - x0 >= 200:
            _write(draw, _label(locale, "image_missing"), x0 + 18, y1 - 44, x1 - x0 - 36, 20, MUTED)


def _paste_asset(canvas: Image.Image, draw: ImageDraw.ImageDraw, url: str, box: tuple[int, int, int, int], locale: str = "zh") -> None:
    x0, y0, x1, y1 = box
    scale = canvas.info.get("render_scale", 1)
    _paste_loaded_asset(canvas, draw, _asset(url, ((x1 - x0) * scale, (y1 - y0) * scale)), box, locale)


def _bytes(image: Image.Image) -> bytes:
    from .rendering.image_output import encode_image
    return encode_image(image)


def _song_marks(songs):
    colors = sorted({s.traits.color for s in songs if s.traits and s.traits.color in range(1, 6)})
    with ThreadPoolExecutor(max_workers=5) as pool:
        return dict(zip(colors, pool.map(lambda c: _asset(f"{BASE}/images/CardType{c}.webp", (76, 76), contain=True), colors)))


def _mission_marks(songs):
    kinds = sorted({kind for song in songs if song.traits and song.traits.missions
                    for kind in song.traits.missions if kind in MISSION_ICON_URLS})
    with ThreadPoolExecutor(max_workers=3) as pool:
        return dict(zip(kinds, pool.map(lambda kind: _asset(MISSION_ICON_URLS[kind], (64, 64), contain=True), kinds)))


def _song_heading(image, draw, song, x, y, width, size=32, locale="zh", marks=None, measure_only=False):
    """Native attribute mark and title share a baseline; no color word on success."""
    from .sources.haneoka.song_traits import COLORS
    traits = song.traits
    color = traits.color if traits else None
    mark = (marks or {}).get(color)
    offset = 44 if mark else 0
    if mark and not measure_only:
        scale = image.info.get("render_scale", 1)
        mark = mark.resize((34 * scale, 34 * scale), Image.Resampling.LANCZOS)
        image.paste(mark, (x * scale, (y + 4) * scale), mark)
    lines = _wrapped_lines(draw, localized_text(song, "title", locale), width - offset, size, 2)
    for i, line in enumerate(lines) if not measure_only else ():
        _write(draw, line, x + offset, y + i * (size + 5), width - offset, size)
    return y + len(lines) * (size + 5), mark is not None


def _mission_icon(image, draw, mission: str, native: Image.Image, x: int, y: int, size: int) -> None:
    """Tint the game's white mission silhouette for the current reply palette."""
    colors = {"JUST": "#7459A7", "COMBO": "#416FAD", "LUCK": "#43876C"}
    color = colors.get(mission)
    if color is None:
        return
    draw.rounded_rectangle((x, y, x + size, y + size), radius=max(3, size // 4), fill=color)
    scale = image.info.get("render_scale", 1)
    inset = max(2, size // 8)
    side = (size - inset * 2) * scale
    sprite = native.resize((side, side), Image.Resampling.LANCZOS)
    image.paste(sprite, ((x + inset) * scale, (y + inset) * scale), sprite)


def _mission_chip(image, draw, traits, x, y, width, size=21, *,
                  icons=None, mark_available=True, measure_only=False):
    from .sources.haneoka.song_traits import COLORS
    font = _font(size)
    icon_size = min(size, 20)
    rows: list[list[tuple[str, str, int]]] = [[]]
    row_widths = [0]
    available = max(1, width - 26)

    def add(kind: str, label: str, item_width: int) -> None:
        if rows[-1] and row_widths[-1] + item_width > available:
            rows.append([])
            row_widths.append(0)
        rows[-1].append((kind, label, item_width))
        row_widths[-1] += item_width

    add("text", "激奏  ", math.ceil(draw.textlength("激奏  ", font=font)))
    if traits and traits.missions:
        for index, mission in enumerate(traits.missions):
            arrow = " → " if index else ""
            native = (icons or {}).get(mission)
            item_width = (math.ceil(draw.textlength(arrow, font=font))
                          + (icon_size + 5 if native else 0)
                          + math.ceil(draw.textlength(mission, font=font)))
            add("mission", arrow + mission, item_width)
    else:
        add("text", "未获取", math.ceil(draw.textlength("未获取", font=font)))
    if traits and traits.stale:
        add("text", " · 旧缓存", math.ceil(draw.textlength(" · 旧缓存", font=font)))
    chip_width = min(width, max(row_widths) + 26)
    height = len(rows) * (size + 6) + 10
    if not measure_only:
        draw.rounded_rectangle((x, y, x + chip_width, y + height), radius=10,
                               fill=SURFACE, outline="#C7CFE2", width=1)
        for i, row in enumerate(rows):
            cursor = x + 13
            top = y + 5 + i * (size + 6)
            for kind, label, item_width in row:
                left = cursor
                if kind == "mission":
                    mission = label.rsplit(" ", 1)[-1]
                    arrow = label[:-len(mission)]
                    if arrow:
                        _write(draw, arrow, cursor, top, item_width, size, ACCENT)
                        cursor += math.ceil(draw.textlength(arrow, font=font))
                    native = (icons or {}).get(mission)
                    if native:
                        _mission_icon(image, draw, mission, native, cursor,
                                      top + (size - icon_size) // 2, icon_size)
                        cursor += icon_size + 5
                    _write(draw, mission, cursor, top, item_width, size, ACCENT)
                else:
                    _write(draw, label, cursor, top, item_width, size, ACCENT)
                cursor = left + item_width
    if not mark_available:
        label = COLORS.get(traits.color, "未获取") if traits else "未获取"
        if not measure_only:
            _write(draw, "属性：" + label + ("（图标暂缺）" if traits and traits.color in COLORS else ""),
                   x, y + height + 5, width, size - 2, MUTED)
        height += size + 9
    return y + height


def render_song_list(songs: list[Song], query: str, locale: str = "zh", footer: str = "") -> bytes:
    width = 1100
    measure = ImageDraw.Draw(Image.new("RGB", (width, 1)))
    footer_lines = [part for line in footer.splitlines() for part in _wrapped_lines(measure, line, width - 110, 21, len(line) + 1)]
    conditions = _wrapped_lines(measure, query or "条件：不限", width - 110, 23, max(1, len(query)))
    rows_y = 172 + len(conditions) * 31
    # Measure with the same primitives so long titles, unknown data and sequences cannot overlap.
    probe, probe_draw = _background(width, 1)
    layouts = []
    marks = _song_marks(songs)
    mission_marks = _mission_marks(songs)
    for song in songs:
        bottom, mark = _song_heading(probe, probe_draw, song, 212, 10, 438, locale=locale, marks=marks, measure_only=True)
        bottom = _mission_chip(probe, probe_draw, song.traits, 212, max(53, bottom + 6), 438, 19,
                               icons=mission_marks, mark_available=mark, measure_only=True)
        layouts.append(max(140, bottom + 40))
    height = rows_y + sum(h + 10 for h in layouts) + 24 + len(footer_lines) * 31
    image, draw = _canvas(width, height, _label(locale, "songs"))
    for i, line in enumerate(conditions):
        _write(draw, line, 50, 142 + i * 31, width - 100, 23, MUTED)
    jackets = _prefetch_assets([song.jacket_url for song in songs], (108, 108))
    top = rows_y
    for index, (song, row_height) in enumerate(zip(songs, layouts)):
        draw.rounded_rectangle((38, top, width - 38, top + row_height), radius=18,
                               fill="#FFFFFF", outline=BORDER, width=2)
        _write(draw, f"{index + 1:02d}", 53, top + 45, 36, 22, ACCENT)
        _paste_loaded_asset(image, draw, jackets[song.jacket_url], (96, top + 12, 192, top + 108), locale)
        bottom, mark = _song_heading(image, draw, song, 212, top + 10, 438, locale=locale, marks=marks)
        _mission_chip(image, draw, song.traits, 212, max(top + 53, bottom + 6), 438, 19,
                      icons=mission_marks, mark_available=mark)
        _write(draw, f"ID {song.id}  ·  {localized_text(song, 'band', locale)}",
               96, top + row_height - 28, 554, 19, MUTED)
        draw.line((666, top + 30, 666, top + row_height - 32), fill=BORDER, width=1)
        charts = {chart.difficulty: chart for chart in song.charts}
        for column, difficulty in enumerate(DIFFICULTY_COLORS):
            chart = charts.get(difficulty)
            value = f"{chart.display_level:g}" if chart else "—"
            x = 680 + column * 94
            y = top + (row_height - 66) // 2
            _difficulty_badge(draw, difficulty, value, (x, y, x + 86, y + 66))
        top += row_height + 10
    for i, line in enumerate(footer_lines):
        _write(draw, line, 55, top + i * 31, width - 110, 21, MUTED)
    return _bytes(image)


def _score_point(value: dict) -> tuple[float, float, float] | None:
    """Return tick, left edge and width in the source's 24-unit playfield."""
    tick, pos, size = value.get("t"), value.get("pos"), value.get("size")
    if any(isinstance(part, bool) or not isinstance(part, (int, float)) for part in (tick, pos, size)):
        return None
    if not (0 <= tick <= 10_000_000 and 0 <= pos <= 24 and 0 < size <= 24):
        return None
    return float(tick), float(pos), float(size)


_SCORE_GUIDANCE = {
    "zh": ("读谱：每栏从下往上，按栏号从左往右续读。", "起点 ↑"),
    "en": ("Read each column upwards, then continue in the next column to the right.", "START ↑"),
    "ja": ("各列は下から上へ、列番号順に左から右へ読み進めます。", "開始 ↑"),
}


def _score_plot_height(segment: int) -> int:
    # 78 logical pixels per beat (was 52). More columns on long charts prevent
    # the upload height budget from undoing the extra vertical separation.
    return max(3600, min(4800, int(segment / 480 * 78)))


def _score_layout(points):
    first = max(0, (int(min(p[0] for p in points)) // 1920 - 1) * 1920)
    last = (int(max(p[0] for p in points)) // 1920 + 2) * 1920
    columns = 6 if last - first >= 60000 else 4
    segment = max(1920, math.ceil((last - first) / (columns * 1920)) * 1920)
    return first, last, segment, columns, 1300 if columns == 6 else 900


def _draw_score(draw: ImageDraw.ImageDraw, score: dict, top: int, locale: str) -> int:
    notes = score.get("notes", [])
    points = [point for note in notes if isinstance(note, dict)
              for value in (note.get("node", []) if isinstance(note.get("node"), list) else [note])
              if isinstance(value, dict) for point in [_score_point(value)] if point]
    if not points:
        return top
    first, last, segment, max_columns, width = _score_layout(points)
    columns = min(max_columns, math.ceil((last - first) / segment))
    plot_height = _score_plot_height(segment)
    gap = 12
    panel_width = (width - 110 - gap * (columns - 1)) / columns

    def panel_x(index: int) -> float:
        return 55 + index * (panel_width + gap)

    def time_y(tick: float, index: int) -> float:
        return top + plot_height * (1 - (tick - (first + index * segment)) / segment)

    def span(point: tuple[float, float, float], index: int) -> tuple[float, float, float]:
        tick, pos, size = point
        left = panel_x(index) + 4 + max(0, pos) / 24 * (panel_width - 8)
        right = panel_x(index) + 4 + min(24, pos + size) / 24 * (panel_width - 8)
        return left, right, time_y(tick, index)

    draw.rounded_rectangle((47, top - 28, width - 47, top + plot_height + 24), radius=20, fill="#12202C")
    for index in range(columns):
        x = panel_x(index)
        draw.rectangle((x, top, x + panel_width, top + plot_height), fill="#172734", outline="#405366", width=2)
        for lane in (6, 12, 18):
            lx = x + 4 + lane / 24 * (panel_width - 8)
            draw.line((lx, top, lx, top + plot_height), fill="#2B4252", width=1)
        for tick in range(first + index * segment, first + (index + 1) * segment + 1, 480):
            y = time_y(tick, index)
            draw.line((x + 2, y, x + panel_width - 2, y),
                      fill="#40566B" if tick % 1920 == 0 else "#243847", width=2 if tick % 1920 == 0 else 1)
        draw.text((x + 6, top - 24), f"{index + 1} ↑", fill="#8FB7CD", font=_font(17))
    draw.text((panel_x(0) + 6, top + plot_height + 4),
              _SCORE_GUIDANCE[locale][1], fill="#8FB7CD", font=_font(17))

    # Draw long-note bodies before their visible endpoints and ordinary notes.
    for note in notes:
        if not isinstance(note, dict) or note.get("type") not in {"long", "guide"}:
            continue
        nodes = note.get("node", [])
        if not isinstance(nodes, list):
            continue
        for a, b in zip(nodes, nodes[1:]):
            if not isinstance(a, dict) or not isinstance(b, dict):
                continue
            start, end = _score_point(a), _score_point(b)
            if not start or not end or start[0] >= end[0]:
                continue
            for index in range(columns):
                low = max(start[0], first + index * segment)
                high = min(end[0], first + (index + 1) * segment)
                if low >= high:
                    continue
                def interpolate(tick: float) -> tuple[float, float, float]:
                    fraction = (tick - start[0]) / (end[0] - start[0])
                    return tick, start[1] + (end[1] - start[1]) * fraction, start[2] + (end[2] - start[2]) * fraction
                x0, x1, y0 = span(interpolate(low), index)
                x2, x3, y1 = span(interpolate(high), index)
                draw.polygon(((x0, y0), (x1, y0), (x3, y1), (x2, y1)),
                             fill="#31515D" if note["type"] == "long" else "#274A42")
                draw.line((x0, y0, x2, y1), fill="#5CAFC0" if note["type"] == "long" else "#4B9B78", width=2)
                draw.line((x1, y0, x3, y1), fill="#5CAFC0" if note["type"] == "long" else "#4B9B78", width=2)

    for note in notes:
        if not isinstance(note, dict):
            continue
        kind = note.get("type", "tap")
        if kind == "guide":
            continue
        values = note.get("node", []) if kind == "long" else [note]
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict) or value.get("visible") is False:
                continue
            point = _score_point(value)
            if not point or not first <= point[0] <= first + columns * segment:
                continue
            index = min(columns - 1, int((point[0] - first) // segment))
            left, right, y = span(point, index)
            color = "#F7D478" if value.get("crit") else "#68D99C" if kind == "flick" or value.get("type") == "flick" else "#75C5E8" if kind == "tap" else "#80D8CD"
            draw.rounded_rectangle((left + 1, y - 4, max(left + 5, right - 1), y + 4), radius=3, fill=color)
            if kind == "flick" or value.get("type") == "flick":
                center = (left + right) / 2
                direction = value.get("dir", note.get("dir"))
                shift = -8 if direction == "left" else 8 if direction == "right" else 0
                draw.line((center - shift / 2, y - 5, center + shift, y - 13), fill=color, width=2)
    return top + plot_height + 24


def render_chart(song: Song, charts: tuple[Chart, ...], locale: str = "zh", score: dict | None = None,
                 preview_difficulty: str | None = None, *, source_notice: str = "") -> bytes:
    score_points = [point for note in score.get("notes", []) if isinstance(note, dict)
                    for value in (note.get("node", []) if isinstance(note.get("node"), list) else [note])
                    if isinstance(value, dict) for point in [_score_point(value)] if point] if score else []
    width = _score_layout(score_points)[4] if score_points else 900
    marks = _song_marks([song])
    mission_marks = _mission_marks([song])
    probe, pd = _background(width, 1)
    heading_bottom, mark = _song_heading(probe, pd, song, 214, 154, width - 268, 34, locale, marks, measure_only=True)
    chip_y = max(201, heading_bottom + 8)
    chip_bottom = _mission_chip(probe, pd, song.traits, 214, chip_y, width - 268,
                                icons=mission_marks, mark_available=mark, measure_only=True)
    credits = [(key, localized_text(song, key, locale))
               for key in ("composer", "lyricist") if getattr(song, key)]
    credits_y = chip_bottom + 12
    header_bottom = max(320, credits_y + len(credits) * 30 + 42)
    shift = header_bottom - 402
    if score_points:
        segment = _score_layout(score_points)[2]
        image_height = 724 + shift + _score_plot_height(segment) + 100
    else:
        image_height = 694 + shift
    image, draw = _canvas(width, image_height, _label(locale, "chart"))
    draw.rounded_rectangle((38, 140, width - 38, header_bottom), radius=18, fill="#FFFFFF", outline=BORDER, width=2)
    draw.rounded_rectangle((38, 432 + shift, width - 38, 584 + shift), radius=18, fill=SURFACE, outline=BORDER, width=2)
    _paste_asset(image, draw, song.jacket_url, (54, 154, 190, 290), locale)
    _song_heading(image, draw, song, 214, 154, width - 268, 34, locale, marks)
    _mission_chip(image, draw, song.traits, 214, chip_y, width - 268,
                  icons=mission_marks, mark_available=mark)
    for index, (key, value) in enumerate(credits):
        _write(draw, f"{_label(locale, key)}  {value}", 214, credits_y + index * 30, width - 268, 21)
    _write(draw, f"ID {song.id}  ·  {localized_text(song, 'band', locale)}", 214, header_bottom - 36, width - 268, 23, MUTED)
    draw.line((54, 420 + shift, width - 54, 420 + shift), fill=BORDER, width=2)
    for index, chart in enumerate(charts):
        top = 449 + shift
        column_width = (width - 100) // 4
        x = 65 + index * column_width
        badge_width = column_width - 30
        _difficulty_badge(draw, chart.difficulty, f"{chart.display_level:g}", (x, top, x + badge_width, top + 78))
        note_text = f"{chart.notes} Notes" if chart.notes is not None else "Note：暂无资料"
        note_x = x + max(0, (badge_width - draw.textlength(note_text, font=_font(22))) // 2)
        _write(draw, note_text, note_x, top + 86, badge_width, 22)
    draw.line((54, 609 + shift, width - 54, 609 + shift), fill=BORDER, width=2)
    if score_points:
        _write(draw, _label(locale, "score_title").format(difficulty=preview_difficulty or "EXPERT"), 65, 623 + shift, 760, 28, INK)
        _write(draw, _SCORE_GUIDANCE[locale][0], 65, 664 + shift, 760, 18, MUTED)
        bottom = _draw_score(draw, score or {}, 724 + shift, locale)
        footer = _label(locale, "score_note") + (" · " + source_notice if source_notice else "")
        _write(draw, footer, 65, bottom + 20, 760, 18, MUTED)
    else:
        from .sources.haneoka.catalog_assets import is_catalog_asset
        preview_label = _label(locale, "preview")
        if is_catalog_asset(song.jacket_url):
            preview_label = preview_label.replace("Project Yume", "Haneoka JP")
        _write(draw, preview_label, 65, 621 + shift, 760, 18, MUTED)
    return _bytes(image)


def render_meta(answer: MetaAnswer) -> bytes:
    # Reuse one worker's allocator arena as well as bounding concurrent canvases.
    return _META_RENDER_EXECUTOR.submit(_render_meta, answer).result()


def _render_meta(answer: MetaAnswer) -> bytes:
    if answer.panels:
        # Capture native marks once so measuring and drawing use the same assets.
        songs = tuple(song for panel in answer.panels for song in panel.song_records)
        marks = _song_marks(songs)
        mission_marks = _mission_marks(songs)
        dimensions = [_meta_image(panel, size_only=True, marks=marks, mission_marks=mission_marks)
                      for panel in answer.panels]
        gap = 20 * RENDER_SCALE
        panel_width = max(size[0] for size in dimensions)
        width = panel_width * len(answer.panels) + gap * (len(answer.panels) - 1)
        height = max(size[1] for size in dimensions)
        image, _ = _background(width // RENDER_SCALE, math.ceil(height / RENDER_SCALE), scale=RENDER_SCALE)
        try:
            x = 0
            for panel in answer.panels:
                rendered = _meta_image(panel, marks=marks, mission_marks=mission_marks)
                try:
                    image.paste(rendered, (x, 0))
                finally:
                    rendered.close()
                x += panel_width + gap
            return _bytes(image)
        finally:
            image.close()
    image = _meta_image(answer)
    try:
        return _bytes(image)
    finally:
        image.close()


def _meta_image(answer: MetaAnswer, *, size_only=False, marks=None, mission_marks=None) -> Image.Image | tuple[int, int]:
    # Both forms use the captured answer; drawing never fetches or sorts again.
    if answer.cells:
        return _render_meta_table(answer, raw=True, size_only=size_only, marks=marks, mission_marks=mission_marks)
    text = answer.text
    title = answer.title if answer.complete_text else "歌曲效率"
    _, measure = _canvas(900, 200, title)
    lines = [part for line in text.splitlines()
             for part in _wrapped_lines(measure, line, 792, 22, max_lines=len(line) + 1)]
    if size_only:
        return 900 * RENDER_SCALE, (185 + len(lines) * 32) * RENDER_SCALE
    image, draw = _canvas(900, 185 + len(lines) * 32, title)
    draw.rounded_rectangle((38, 136, 862, image.height // RENDER_SCALE - 24), radius=18, fill="#FFFFFF", outline=BORDER, width=2)
    top = 145
    for line in lines:
        _write(draw, line, 54, top, 792, 22, INK)
        top += 32
    return image


def _render_meta_table(answer: MetaAnswer, *, raw=False, size_only=False, marks=None, mission_marks=None) -> bytes | Image.Image | tuple[int, int]:
    # One row per song+difficulty; use the captured values, never recalculate metrics.
    width, row_height = 1500, 112
    edges = (40, 105, 660, 820, 960, 1180, 1460)
    measure = ImageDraw.Draw(Image.new("RGB", (width, 200)))
    full_scope = _wrapped_lines(measure, answer.scope, width - 100, 24, max_lines=len(answer.scope) + 1)
    scope = full_scope[:4]
    rows_y = 158 + 34 * len(scope)
    marks = _song_marks(answer.song_records) if marks is None else marks
    mission_marks = _mission_marks(answer.song_records) if mission_marks is None else mission_marks
    heights = []
    probe, pd = _background(width, 1)
    for i in range(len(answer.cells)):
        bottom = 0
        if i < len(answer.song_records):
            song = answer.song_records[i]
            title_end, mark = _song_heading(probe, pd, song, 202, 10, 442, 28, marks=marks, measure_only=True)
            bottom = _mission_chip(probe, pd, song.traits, 202, max(50, title_end + 6), 442, 17,
                                   icons=mission_marks, mark_available=mark, measure_only=True)
        heights.append(max(row_height, bottom + 12))
    table_bottom = rows_y + sum(heights)
    page_lines = [part for line in answer.page_notice.splitlines()
                  for part in _wrapped_lines(measure, line, width - 136, 25, len(line) + 1)]
    note_lines = [part for line in ((*full_scope[4:], *answer.notes) if len(full_scope) > 4 else answer.notes)
                  for part in (*_wrapped_lines(measure, line, width - 136, 23, len(line) + 1), "")]
    if note_lines:
        note_lines.pop()
    page_y = table_bottom + 20
    notes_y = page_y + 36 + len(page_lines) * 38 + 18
    notes_bottom = notes_y + 52 + sum(18 if not line else 34 for line in note_lines)
    if size_only:
        return width * RENDER_SCALE, (notes_bottom + 46) * RENDER_SCALE
    image, draw = _canvas(width, notes_bottom + 46, answer.title)
    draw.rounded_rectangle((36, 132, width - 36, rows_y - 8), radius=18, fill=PAPER)
    for i, line in enumerate(scope):
        draw.text((48, 142 + i * 34), line, font=_font(24), fill=MUTED)

    def centered(text, left, right, center_y, size=28, color=INK):
        font = _font(size)
        bounds = draw.textbbox((0, 0), text, font=font)
        _write(draw, text, int((left + right - min(draw.textlength(text, font=font), right - left - 24)) / 2),
               round(center_y - (bounds[1] + bounds[3]) / 2), right - left - 24, size, color)

    covers = _prefetch_assets(list(answer.jackets), (68, 68))
    top = rows_y
    for index, row in enumerate(answer.cells):
        row_height = heights[index]
        # A small clear margin separates the fabric from the content outline.
        draw.rounded_rectangle((34, top, 1466, top + row_height), radius=22, fill=PAPER)
        draw.rounded_rectangle((40, top + 6, 1460, top + row_height - 6), radius=18,
                               fill=SURFACE if index % 2 == 0 else "#FFFFFF", outline=BORDER, width=2)
        for x in (105, 660, 820, 960, 1180):
            draw.line((x, top + 20, x, top + row_height - 20), fill=BORDER, width=2)
        if answer.selection_numbers:
            centered(row[0], edges[0], edges[1], top + row_height / 2 - 14, 23, ACCENT)
            centered("选" + str(answer.selection_numbers[index]), edges[0], edges[1], top + row_height / 2 + 18, 17, MUTED)
        else:
            centered(row[0], edges[0], edges[1], top + row_height / 2, 23, ACCENT)
        cover = covers.get(answer.jackets[index])
        _paste_loaded_asset(image, draw, cover, (118, top + (row_height - 68) // 2, 186, top + (row_height + 68) // 2))
        if index < len(answer.song_records):
            song = answer.song_records[index]
            bottom, mark = _song_heading(image, draw, song, 202, top + 10, 442, 28, marks=marks)
            _mission_chip(image, draw, song.traits, 202, max(top + 50, bottom + 6), 442, 17,
                          icons=mission_marks, mark_available=mark)
        else:
            for i, line in enumerate(_wrapped_lines(draw, row[1], 442, 28, max_lines=2)):
                _write(draw, line, 202, top + 10 + i * 35, 442, 28)
        _difficulty_badge(draw, row[2], row[3], (694, top + (row_height - 66) // 2, 786, top + (row_height + 66) // 2))
        labels = answer.columns[4:7] if len(answer.columns) == 7 else ("时长", "得分系数", "每分钟得分效率")
        for value, column, label in ((row[4], 3, labels[0]), (row[5], 4, labels[1]),
                                     (row[6], 5, labels[2])):
            centered(label, edges[column], edges[column + 1], top + row_height / 2 - 18, 18, MUTED)
            centered(value, edges[column], edges[column + 1], top + row_height / 2 + 16, 28,
                     ACCENT if column == 5 else INK)
        top += row_height
    draw.rounded_rectangle((40, page_y, 1460, notes_y - 18), radius=24,
                           fill="#FFFFFF", outline=BORDER, width=2)
    for i, line in enumerate(page_lines):
        _write(draw, line, 68, page_y + 14 + i * 38, width - 136, 25, INK)
    draw.rounded_rectangle((40, notes_y, 1460, notes_bottom), radius=24, fill=SURFACE)
    y = notes_y + 20
    for line in note_lines:
        if line:
            _write(draw, line, 68, y, width - 136, 23, MUTED)
        y += 18 if not line else 34
    return image if raw else _bytes(image)


def render_card(card: Card, locale: str = "zh") -> bytes:
    from .rendering.card_visuals import detail
    return detail(card, locale)


def render_card_list(cards, query: str, locale: str = "zh", footer: str = "") -> bytes:
    from .rendering.card_visuals import grid
    return grid(cards, query, locale, footer)


def render_support_card(card: SupportCard, locale: str = "zh") -> bytes:
    from .rendering.card_visuals import detail
    return detail(card, locale)


def render_support_card_list(cards, query: str, locale: str = "zh", footer: str = "") -> bytes:
    from .rendering.card_visuals import grid
    return grid(cards, query, locale, footer, support=True)


def render_catalog(answer, locale: str = "zh") -> bytes | None:
    if answer.error or not answer.cards:
        return None
    from .rendering.card_visuals import art, detail, grid
    if answer.request.mode == "art":
        return art(answer.cards[0])
    if answer.request.mode == "detail":
        return detail(answer.cards[0], locale)
    return grid(answer.visible, answer.request.query, locale, answer.footer,
                support=answer.request.support) if answer.visible else None

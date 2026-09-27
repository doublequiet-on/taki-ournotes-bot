"""Rounded, high-contrast image replies using the existing Our Notes assets."""

from __future__ import annotations

import io
import asyncio
import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from urllib.request import Request, urlopen

import aiohttp

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import runtime_data_dir
from .data import Card, Chart, Skill, Song, SupportCard, localized_text
from .efficiency_query import MetaAnswer
from .yatta import ASSETS, BASE


PAPER = "#F1F2F8"
INK = "#202B49"
MUTED = "#45516F"
ACCENT = "#475B96"
STAT_COLORS = ("#697DA6", "#8887B2", "#A0A6BF")
BORDER = "#AAB5D0"
SURFACE = "#E6EAF4"
MOTIF_INK = "#A0A8C7"
MOTIF_MUTED = "#BBC1D7"
DIFFICULTY_COLORS = {
    "EASY": ("#DEE6F2", "#A6B6CE"),
    "NORMAL": ("#DFE8E8", "#ABBFC1"),
    "HARD": ("#EEE8DC", "#C4B9A2"),
    "EXPERT": ("#EBDDE6", "#C7AFC2"),
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
    """Scale against the short side; long replies add repeats, not giant symbols."""
    unit = min(width, height)
    large = max(12, round(unit * .058))
    small = max(8, round(unit * .025))
    inset = max(3, round(unit * .004))
    motifs = []
    for index, kind in enumerate(("meteor", "flower", "moon")):
        x = width - round(large * (4.15 - index * 1.35))
        motifs.append((kind, x, round(unit * .018), large, MOTIF_INK))
    step = max(42, round(unit * .26))
    kinds = ("stone", "moon", "flower", "meteor")
    for index, y in enumerate(range(max(170, round(unit * .19)), height - 2 * small, step)):
        motifs.append((kinds[index % 4], inset, y, small, MOTIF_MUTED))
        right_y = y + step // 2
        if right_y + small < height - small:
            motifs.append((kinds[(index + 2) % 4], width - small - 2 * inset, right_y, small, MOTIF_MUTED))
    motifs.extend((("flower", inset, height - small - inset, small, MOTIF_INK),
                   ("stone", width - small - 2 * inset, height - small - inset, small, MOTIF_INK)))
    return motifs


def _background(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    for kind, x, y, size, color in _decoration_layout(width, height):
        _motif(draw, kind, x, y, size, color)
    return image, draw


def _canvas(width: int, height: int, label: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image, draw = _background(width, height)
    # Keep the title as the only heading; reserve the right-hand band for motifs.
    reserved = max(335, round(min(width, height) * .058 * 4.15) + 75)
    _write(draw, label, 50, 40, width - reserved, 36)
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
    fill, outline = DIFFICULTY_COLORS.get(difficulty, (SURFACE, BORDER))
    draw.rounded_rectangle(box, radius=min(24, (y1 - y0) // 2), fill=fill, outline=outline, width=2)
    for text, size, y in ((difficulty, 15, y0 + 7), (value, 28, y0 + 28)):
        while size > 10 and draw.textlength(text, font=_font(size)) > x1 - x0 - 12:
            size -= 1
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


def _asset(url: str, size: tuple[int, int]) -> Image.Image | None:
    if not url.startswith(ASSETS + "/"):
        return None
    try:
        cache = runtime_data_dir() / "asset-cache"
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".png")
        if path.exists():
            raw = path.read_bytes()
        else:
            try:
                with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": BASE + "/"}), timeout=12) as response:
                    raw = response.read(6_000_000)
            except Exception:
                async def download() -> bytes:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=18)) as response:
                            response.raise_for_status()
                            return await response.read()
                raw = asyncio.run(download())
            if len(raw) > 6_000_000:
                return None
            path.write_bytes(raw)
        with Image.open(io.BytesIO(raw)) as image:
            return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)
    except Exception:
        return None


def _prefetch_assets(urls: list[str], size: tuple[int, int]) -> dict[str, Image.Image | None]:
    unique = list(dict.fromkeys(urls))
    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(zip(unique, pool.map(lambda url: _asset(url, size), unique)))


def _paste_loaded_asset(canvas: Image.Image, draw: ImageDraw.ImageDraw, image: Image.Image | None,
                        box: tuple[int, int, int, int], locale: str = "zh") -> None:
    x0, y0, x1, y1 = box
    radius = min(26, (x1 - x0) // 4, (y1 - y0) // 4)
    if image:
        mask = Image.new("L", image.size)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
        canvas.paste(image, (x0, y0), mask)
    else:
        draw.rounded_rectangle(box, radius=radius, fill="#DEE3F2")
        size = min(40, (x1 - x0) // 2, (y1 - y0) // 2)
        _motif(draw, "meteor", (x0 + x1 - size) // 2, (y0 + y1 - size) // 2, size, ACCENT)
        if x1 - x0 >= 200:
            _write(draw, _label(locale, "image_missing"), x0 + 18, y1 - 44, x1 - x0 - 36, 20, MUTED)


def _paste_asset(canvas: Image.Image, draw: ImageDraw.ImageDraw, url: str, box: tuple[int, int, int, int], locale: str = "zh") -> None:
    x0, y0, x1, y1 = box
    _paste_loaded_asset(canvas, draw, _asset(url, (x1 - x0, y1 - y0)), box, locale)


def _bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=86, optimize=True)
    return out.getvalue()


def render_song_list(songs: list[Song], query: str, locale: str = "zh", footer: str = "") -> bytes:
    width, row_height = 1100, 132
    footer_lines = footer.splitlines()[:2]
    height = 218 + len(songs) * row_height + len(footer_lines) * 31
    image, draw = _canvas(width, height, _label(locale, "songs"))
    _write(draw, query, 50, 142, width - 100, 23, MUTED)
    jackets = _prefetch_assets([song.jacket_url for song in songs], (92, 92))
    for index, song in enumerate(songs):
        top = 190 + index * row_height
        draw.rounded_rectangle((38, top, width - 38, top + 120), radius=38,
                               fill=SURFACE if index % 2 else "#FFFFFF", outline=BORDER, width=2)
        draw.line((666, top + 17, 666, top + 103), fill=BORDER, width=2)
        _write(draw, f"{index + 1:02d}", 53, top + 43, 40, 23, ACCENT)
        _paste_loaded_asset(image, draw, jackets[song.jacket_url], (102, top + 14, 194, top + 106), locale)
        title = _wrapped_lines(draw, localized_text(song, "title", locale), 446, 32, 2)
        for line_index, line in enumerate(title):
            _write(draw, line, 214, top + 9 + line_index * 37, 446, 32)
        _write(draw, f"#{song.id}  ·  {localized_text(song, 'band', locale)}", 214, top + 87, 446, 20, MUTED)
        # Match by identity, not tuple position: filtered/missing charts keep their labels and colors.
        charts = {chart.difficulty: chart for chart in song.charts}
        for column, difficulty in enumerate(DIFFICULTY_COLORS):
            chart = charts.get(difficulty)
            value = f"{chart.display_level:g}" if chart else "—"
            x = 680 + column * 94
            _difficulty_badge(draw, difficulty, value, (x, top + 27, x + 86, top + 93))
    for index, line in enumerate(footer_lines):
        _write(draw, line, 55, 195 + len(songs) * row_height + index * 31, width - 110, 21, MUTED)
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
    # Keep a 120-tick gap legible next to an eight-pixel note, even in long songs.
    return max(2900, min(4800, int(segment / 480 * 52)))


def _draw_score(draw: ImageDraw.ImageDraw, score: dict, top: int, locale: str) -> int:
    notes = score.get("notes", [])
    points = [point for note in notes if isinstance(note, dict)
              for value in (note.get("node", []) if isinstance(note.get("node"), list) else [note])
              if isinstance(value, dict) for point in [_score_point(value)] if point]
    if not points:
        return top
    first = max(0, (int(min(point[0] for point in points)) // 1920 - 1) * 1920)
    last = (int(max(point[0] for point in points)) // 1920 + 2) * 1920
    segment = max(1920, math.ceil((last - first) / (4 * 1920)) * 1920)
    columns = min(4, math.ceil((last - first) / segment))
    plot_height = _score_plot_height(segment)
    gap = 12
    panel_width = (790 - gap * (columns - 1)) / columns

    def panel_x(index: int) -> float:
        return 55 + index * (panel_width + gap)

    def time_y(tick: float, index: int) -> float:
        return top + plot_height * (1 - (tick - (first + index * segment)) / segment)

    def span(point: tuple[float, float, float], index: int) -> tuple[float, float, float]:
        tick, pos, size = point
        left = panel_x(index) + 4 + max(0, pos) / 24 * (panel_width - 8)
        right = panel_x(index) + 4 + min(24, pos + size) / 24 * (panel_width - 8)
        return left, right, time_y(tick, index)

    draw.rounded_rectangle((47, top - 28, 853, top + plot_height + 24), radius=20, fill="#12202C")
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
                 preview_difficulty: str | None = None) -> bytes:
    score_points = [point for note in score.get("notes", []) if isinstance(note, dict)
                    for value in (note.get("node", []) if isinstance(note.get("node"), list) else [note])
                    if isinstance(value, dict) for point in [_score_point(value)] if point] if score else []
    if score_points:
        first = max(0, (int(min(point[0] for point in score_points)) // 1920 - 1) * 1920)
        last = (int(max(point[0] for point in score_points)) // 1920 + 2) * 1920
        segment = max(1920, math.ceil((last - first) / (4 * 1920)) * 1920)
        image_height = 790 + _score_plot_height(segment) + 100
    else:
        image_height = 760
    image, draw = _canvas(900, image_height, _label(locale, "chart"))
    _paste_asset(image, draw, song.jacket_url, (54, 155, 284, 385), locale)
    _write(draw, localized_text(song, "title", locale), 315, 172, 520, 37)
    _write(draw, f"#{song.id}  ·  {localized_text(song, 'band', locale)}", 315, 232, 520, 24, MUTED)
    if song.composer:
        _write(draw, f"{_label(locale, 'composer')}  {localized_text(song, 'composer', locale)}", 315, 290, 520, 21)
    if song.lyricist:
        _write(draw, f"{_label(locale, 'lyricist')}  {localized_text(song, 'lyricist', locale)}", 315, 327, 520, 21)
    draw.line((54, 420, 846, 420), fill=BORDER, width=2)
    for index, chart in enumerate(charts):
        top = 449
        x = 65 + index * 200
        _difficulty_badge(draw, chart.difficulty, f"{chart.display_level:g}", (x, top, x + 170, top + 78))
        _write(draw, f"{chart.notes} Notes", x + 5, top + 94, 168, 22)
    draw.line((54, 675, 846, 675), fill=BORDER, width=2)
    if score_points:
        _write(draw, _label(locale, "score_title").format(difficulty=preview_difficulty or "EXPERT"), 65, 689, 760, 28, INK)
        _write(draw, _SCORE_GUIDANCE[locale][0], 65, 730, 760, 18, MUTED)
        bottom = _draw_score(draw, score or {}, 790, locale)
        _write(draw, _label(locale, "score_note"), 65, bottom + 20, 760, 18, MUTED)
    else:
        _write(draw, _label(locale, "preview"), 65, 687, 760, 18, MUTED)
    return _bytes(image)


def render_meta(answer: MetaAnswer) -> bytes:
    # Both forms use the captured answer; drawing never fetches or sorts again.
    if answer.cells:
        return _render_meta_table(answer)
    text = answer.text
    _, measure = _canvas(900, 200, "歌曲效率")
    lines = [part for line in text.splitlines()
             for part in _wrapped_lines(measure, line, 792, 22, max_lines=len(line) + 1)]
    image, draw = _canvas(900, 185 + len(lines) * 32, "歌曲效率")
    top = 145
    for line in lines:
        _write(draw, line, 54, top, 792, 22, INK)
        top += 32
    return _bytes(image)


def _render_meta_table(answer: MetaAnswer) -> bytes:
    # One row per song+difficulty; use the captured values, never recalculate metrics.
    width, row_height = 1500, 86
    edges = (40, 105, 660, 820, 960, 1180, 1460)
    measure = ImageDraw.Draw(Image.new("RGB", (width, 200)))
    scope = _wrapped_lines(measure, answer.scope, width - 100, 24, max_lines=4)
    head_y = 158 + 34 * len(scope)
    table_bottom = head_y + 60 + len(answer.cells) * row_height
    foot_lines = [part for line in (*answer.page_notice.splitlines(), *answer.notes)
                  for part in _wrapped_lines(measure, line, width - 108, 20, max_lines=len(line) + 1)]
    image, draw = _canvas(width, table_bottom + 70 + len(foot_lines) * 29, "日服 · 歌曲分数表")
    for i, line in enumerate(scope):
        draw.text((48, 142 + i * 34), line, font=_font(24), fill=MUTED)
    draw.rounded_rectangle((40, head_y, 1460, head_y + 56), radius=26,
                           fill="#D9E0EF", outline=BORDER, width=2)
    for x in (105, 660, 820, 960, 1180):
        draw.line((x, head_y + 8, x, head_y + 48), fill=BORDER, width=1)
    headers = ("排名", "歌曲", "难度", "时长", "得分系数", "每分钟得分效率")
    def centered(text, left, right, y, size=25, color=INK):
        font = _font(size)
        draw.text(((left + right - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=color)
    for index, label in enumerate(headers):
        if index == 1:
            draw.text((edges[index] + 14, head_y + 17), label, font=_font(22), fill=MUTED)
        else:
            centered(label, edges[index], edges[index + 1], head_y + 17, 22, MUTED)
    covers = _prefetch_assets(list(answer.jackets), (60, 60))
    for index, row in enumerate(answer.cells):
        top = head_y + 60 + index * row_height
        draw.rounded_rectangle((40, top + 3, 1460, top + row_height - 3), radius=28,
                               fill=SURFACE if index % 2 == 0 else "#FFFFFF", outline=BORDER, width=2)
        for x in (105, 660, 820, 960, 1180):
            draw.line((x, top + 10, x, top + row_height - 10), fill=BORDER, width=1)
        centered(row[0], edges[0], edges[1], top + 28, 22, ACCENT)
        cover = covers.get(answer.jackets[index])
        _paste_loaded_asset(image, draw, cover, (118, top + 13, 178, top + 73))
        lines = _wrapped_lines(draw, row[1], 451, 25, max_lines=2)
        title_y = top + (row_height - len(lines) * 31) // 2 - 2
        for line_index, line in enumerate(lines):
            draw.text((192, title_y + line_index * 31), line, font=_font(25), fill=INK)
        _difficulty_badge(draw, row[2], row[3], (694, top + 10, 786, top + 76))
        for value, column in ((row[4], 3), (row[5], 4), (row[6], 5)):
            # Normal metrics fit at 26px; bound unusually large values to their cell.
            available = edges[column + 1] - edges[column] - 24
            font = _font(26)
            if draw.textlength(value, font=font) <= available:
                centered(value, edges[column], edges[column + 1], top + 26, 26,
                         ACCENT if column == 5 else INK)
            else:
                _write(draw, value, edges[column] + 12, top + 26, available, 22)
    for i, line in enumerate(foot_lines):
        draw.text((54, table_bottom + 24 + i * 29), line, fill=MUTED, font=_font(20))
    return _bytes(image)


def render_card(card: Card, locale: str = "zh") -> bytes:
    measure = ImageDraw.Draw(Image.new("RGB", (900, 1)))
    title = localized_text(card, "title", locale)
    title_lines = _wrapped_lines(measure, title, 760, 29, len(title) + 1)
    extra = max(0, len(title_lines) - 1) * 39
    skill_height = sum(row[3] for row in _skill_rows(measure, card.skills, 690, locale))
    panel_bottom = 1682 + extra + max(60, skill_height) + 24
    image, draw = _canvas(900, panel_bottom + 65, _label(locale, "card"))
    _write(draw, localized_text(card, "band", locale), 56, 145, 460, 27, ACCENT)
    _write(draw, localized_text(card, "character", locale), 56, 190, 760, 39)
    for index, line in enumerate(title_lines):
        _write(draw, line, 56, 250 + index * 39, 760, 29)
    _paste_asset(image, draw, card.full_url, (64, 315 + extra, 836, 1345 + extra), locale)
    draw.rounded_rectangle((63, 1370 + extra, 837, panel_bottom), radius=34,
                           fill=SURFACE, outline=BORDER, width=2)
    _write(draw, f"{_rarity(card.rarity)}    ID {card.id}    {_label(locale, 'type')} {card.card_type}", 88, 1393 + extra, 720, 28, ACCENT)
    total = card.performance + card.technic + card.visual
    _write(draw, f"{_label(locale, 'power')}  {total:,}" if total else {"zh": "数值暂不可用", "en": "Stats unavailable", "ja": "ステータス未取得"}.get(locale, "数值暂不可用"), 88, 1447 + extra, 720, 31)
    draw.line((88, 1490 + extra, 812, 1490 + extra), fill=BORDER, width=2)
    values = [(_label(locale, "performance"), card.performance, STAT_COLORS[0]),
              (_label(locale, "technic"), card.technic, STAT_COLORS[1]),
              (_label(locale, "visual"), card.visual, STAT_COLORS[2])]
    for index, (label, value, color) in enumerate(values):
        y = 1504 + extra + index * 55
        _write(draw, f"{label}  {value:,}", 88, y, 270, 22)
        draw.rounded_rectangle((365, y + 7, 780, y + 28), radius=10, fill="#D2D9EA")
        draw.rounded_rectangle((365, y + 7, 365 + int(415 * value / max(1, max(v for _, v, _ in values))), y + 28), radius=10, fill=color)
    if card.skills:
        _draw_skills(draw, card.skills, 88, 1682 + extra, 690, locale)
    elif card.skill_name:
        _write(draw, f"{_label(locale, 'skill')}  {localized_text(card, 'skill_name', locale)}", 88, 1682 + extra, 700, 22, MUTED)
    return _bytes(image)


def render_card_list(cards: list[Card], query: str, locale: str = "zh", footer: str = "") -> bytes:
    height = 225 + len(cards) * 165 + (80 if footer else 0)
    image, draw = _canvas(900, height, _label(locale, "cards"))
    _write(draw, f"{_label(locale, 'card_list')} · {query}", 52, 142, 790, 31)
    thumbnails = _prefetch_assets([card.thumbnail_url for card in cards], (93, 125))
    for index, card in enumerate(cards):
        top = 195 + index * 165
        draw.rounded_rectangle((50, top, 850, top + 145), radius=34,
                               fill=SURFACE if index % 2 else "#FFFFFF", outline=BORDER, width=2)
        draw.line((178, top + 12, 178, top + 133), fill=BORDER, width=2)
        _paste_loaded_asset(image, draw, thumbnails[card.thumbnail_url], (68, top + 10, 161, top + 135), locale)
        _write(draw, localized_text(card, "character", locale), 190, top + 13, 590, 27)
        _write(draw, localized_text(card, "title", locale), 190, top + 55, 590, 22)
        _write(draw, f"#{card.id}  ·  {_rarity(card.rarity)}", 190, top + 101, 590, 20, MUTED)
    if footer:
        for index, line in enumerate(footer.splitlines()[:2]):
            _write(draw, line, 55, height - 95 + index * 31, 790, 21, MUTED)
    return _bytes(image)


def render_support_card(card: SupportCard, locale: str = "zh") -> bytes:
    measure = ImageDraw.Draw(Image.new("RGB", (900, 1)))
    characters, title = localized_text(card, "character", locale), localized_text(card, "title", locale)
    names = _wrapped_lines(measure, characters, 760, 32, len(characters) + 1)
    titles = _wrapped_lines(measure, title, 760, 29, len(title) + 1)
    name_extra = max(0, len(names) - 1) * 44
    extra = name_extra + max(0, len(titles) - 1) * 39
    skill_height = sum(row[3] for row in _skill_rows(measure, card.skills, 690, locale))
    panel_bottom = 1588 + extra + max(40, skill_height) + 24
    image, draw = _canvas(900, panel_bottom + 65, _label(locale, "support_card"))
    for index, line in enumerate(names):
        _write(draw, line, 56, 165 + index * 44, 760, 32, ACCENT)
    for index, line in enumerate(titles):
        _write(draw, line, 56, 225 + name_extra + index * 39, 760, 29)
    _paste_asset(image, draw, card.full_url, (64, 290 + extra, 836, 1245 + extra), locale)
    draw.rounded_rectangle((63, 1270 + extra, 837, panel_bottom), radius=34,
                           fill=SURFACE, outline=BORDER, width=2)
    _write(draw, f"{_rarity(card.rarity)}    ID {card.id}    {_label(locale, 'type')} {card.card_type}", 88, 1295 + extra, 720, 28, ACCENT)
    values = [(_label(locale, "performance"), card.performance / 100, STAT_COLORS[0]),
              (_label(locale, "technic"), card.technic / 100, STAT_COLORS[1]),
              (_label(locale, "visual"), card.visual / 100, STAT_COLORS[2])]
    total = sum(value for _, value, _ in values)
    _write(draw, f"{_label(locale, 'support_bonus')}  {total:g}%" if total else {"zh": "数值暂不可用", "en": "Stats unavailable", "ja": "ステータス未取得"}.get(locale, "数值暂不可用"), 88, 1348 + extra, 720, 31)
    draw.line((88, 1392 + extra, 812, 1392 + extra), fill=BORDER, width=2)
    for index, (label, value, color) in enumerate(values):
        y = 1405 + extra + index * 55
        _write(draw, f"{label}  {value:g}%", 88, y, 270, 22)
        draw.rounded_rectangle((365, y + 7, 780, y + 28), radius=10, fill="#D2D9EA")
        draw.rounded_rectangle((365, y + 7, 365 + int(415 * value / max(1, max(v for _, v, _ in values))), y + 28), radius=10, fill=color)
    if card.skills:
        _draw_skills(draw, card.skills, 88, 1588 + extra, 690, locale)
    return _bytes(image)


def render_support_card_list(cards: list[SupportCard], query: str, locale: str = "zh", footer: str = "") -> bytes:
    height = 225 + len(cards) * 165 + (80 if footer else 0)
    image, draw = _canvas(900, height, _label(locale, "support_cards"))
    _write(draw, f"{_label(locale, 'support_card_list')} · {query}", 52, 142, 790, 31)
    thumbnails = _prefetch_assets([card.thumbnail_url for card in cards], (125, 125))
    for index, card in enumerate(cards):
        top = 195 + index * 165
        draw.rounded_rectangle((50, top, 850, top + 145), radius=34,
                               fill=SURFACE if index % 2 else "#FFFFFF", outline=BORDER, width=2)
        draw.line((208, top + 12, 208, top + 133), fill=BORDER, width=2)
        _paste_loaded_asset(image, draw, thumbnails[card.thumbnail_url], (68, top + 10, 193, top + 135), locale)
        _write(draw, localized_text(card, "character", locale), 220, top + 13, 560, 27)
        _write(draw, localized_text(card, "title", locale), 220, top + 55, 560, 22)
        _write(draw, f"#{card.id}  ·  {_rarity(card.rarity)}", 220, top + 101, 560, 20, MUTED)
    if footer:
        for index, line in enumerate(footer.splitlines()[:2]):
            _write(draw, line, 55, height - 95 + index * 31, 790, 21, MUTED)
    return _bytes(image)

"""Notebook styled image replies using the existing Our Notes assets."""

from __future__ import annotations

import io
import asyncio
import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

import aiohttp

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import runtime_data_dir
from .data import Card, Chart, Skill, Song, SupportCard, localized_text
from .efficiency_query import MetaAnswer
from .yatta import ASSETS, BASE


PAPER = "#FFF9F1"
INK = "#353D4B"
MUTED = "#7B8190"
PINK = "#EE718F"
MINT = "#70C9B0"
BLUE = "#7AA9DE"
BORDER = "#E9DCCF"
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
    for path in FONT_PATHS:
        try:
            if Path(path).is_absolute() and Path(path).exists():
                return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise RuntimeError("未找到可用的中日韩字体；请安装 Noto Sans CJK 或微软雅黑，图片回复将退回文字。")


def _canvas(width: int, height: int, label: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=32, fill="#FFFFFF", outline=BORDER, width=3)
    draw.rounded_rectangle((48, 45, 260, 95), radius=23, fill=PINK)
    draw.text((72, 50), label, fill="white", font=_font(27))
    draw.line((50, 126, width - 50, 126), fill=BORDER, width=2)
    draw.ellipse((width - 112, 48, width - 85, 75), fill=MINT)
    draw.ellipse((width - 80, 75, width - 60, 95), fill="#F3D28C")
    return image, draw


def _write(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, max_width: int, size: int, color: str = INK) -> None:
    font = _font(size)
    while text and draw.textlength(text, font=font) > max_width:
        text = text[:-2] + "…"
    draw.text((x, y), text, font=font, fill=color)


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


def _draw_skills(draw: ImageDraw.ImageDraw, skills: tuple[Skill, ...], x: int, y: int,
                 max_width: int, locale: str) -> int:
    for skill in skills:
        _write(draw, f"{_skill_label(skill.kind, locale)} · {localized_text(skill, 'name', locale)}", x, y, max_width, 21, INK)
        y += 34
        description = localized_text(skill, "description", locale)
        if description:
            prefix = f"{_label(locale, 'level5')}："
            for line in _wrapped_lines(draw, prefix + description, max_width, 18, 3):
                _write(draw, line, x, y, max_width, 18, MUTED)
                y += 27
        y += 15
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
    if image:
        canvas.paste(image, (x0, y0))
    else:
        draw.rounded_rectangle(box, radius=12, fill="#F5EEE7")
        _write(draw, _label(locale, "image_missing"), x0 + 14, y0 + 14, x1 - x0 - 28, 20, MUTED)


def _paste_asset(canvas: Image.Image, draw: ImageDraw.ImageDraw, url: str, box: tuple[int, int, int, int], locale: str = "zh") -> None:
    x0, y0, x1, y1 = box
    _paste_loaded_asset(canvas, draw, _asset(url, (x1 - x0, y1 - y0)), box, locale)


def _bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=86, optimize=True)
    return out.getvalue()


def render_song_list(songs: list[Song], query: str, locale: str = "zh", footer: str = "") -> bytes:
    width = 900
    height = 260 + len(songs) * 125 + (80 if footer else 0)
    image, draw = _canvas(width, height, _label(locale, "songs"))
    _write(draw, f"{_label(locale, 'song_list')} · {query}", 50, 146, width - 100, 32)
    jackets = _prefetch_assets([song.jacket_url for song in songs], (86, 86))
    for index, song in enumerate(songs):
        top = 208 + index * 125
        draw.rounded_rectangle((48, top, width - 48, top + 110), radius=18, fill="#FBF7F2", outline=BORDER, width=2)
        _paste_loaded_asset(image, draw, jackets[song.jacket_url], (64, top + 12, 150, top + 98), locale)
        _write(draw, localized_text(song, "title", locale), 170, top + 14, 470, 27)
        _write(draw, f"#{song.id}  ·  {localized_text(song, 'band', locale)}", 170, top + 55, 470, 20, MUTED)
        xs = (670, 720, 770, 820)
        colors = (BLUE, MINT, "#F3D28C", PINK)
        for x, chart, color in zip(xs, song.charts, colors):
            draw.ellipse((x - 18, top + 38, x + 18, top + 74), fill=color)
            value = f"{chart.display_level:g}"
            draw.text((x - draw.textlength(value, font=_font(19)) / 2, top + 42), value, fill=INK, font=_font(19))
    if footer:
        for index, line in enumerate(footer.splitlines()[:2]):
            _write(draw, line, 55, height - 95 + index * 31, width - 110, 21, MUTED)
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
    colors = {"EASY": BLUE, "NORMAL": MINT, "HARD": "#E7BA66", "EXPERT": PINK}
    for index, chart in enumerate(charts):
        top = 443 + index * 54
        draw.rounded_rectangle((58, top, 240, top + 42), radius=18, fill=colors.get(chart.difficulty, BLUE))
        _write(draw, chart.difficulty, 82, top + 5, 160, 22, "white")
        _write(draw, f"Lv.{chart.display_level:g}", 298, top + 3, 150, 27)
        _write(draw, f"{chart.notes} Notes", 535, top + 5, 250, 23)
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
    head_y = 118 + 34 * len(scope)
    table_bottom = head_y + 60 + len(answer.cells) * row_height
    foot_lines = [part for line in (*answer.page_notice.splitlines(), *answer.notes)
                  for part in _wrapped_lines(measure, line, width - 108, 20, max_lines=len(line) + 1)]
    image = Image.new("RGB", (width, table_bottom + 70 + len(foot_lines) * 29), "#FCFAFD")
    draw = ImageDraw.Draw(image)
    draw.text((46, 30), "日服 · 歌曲分数表", font=_font(39), fill=INK)
    for i, line in enumerate(scope):
        draw.text((48, 89 + i * 34), line, font=_font(24), fill=MUTED)
    draw.rounded_rectangle((40, head_y, 1460, head_y + 60), radius=12, fill="#EEEAF2")
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
    colors = {"EXPERT": ("#FADDDD", "#E68087"), "HARD": ("#FFF0D8", "#DDB568"),
              "NORMAL": ("#DFF2E7", "#81B993"), "EASY": ("#DDEBF9", "#7AA9DE")}
    for index, row in enumerate(answer.cells):
        top = head_y + 60 + index * row_height
        draw.rectangle((40, top, 1460, top + row_height), fill="#F5F1F8" if index % 2 == 0 else "#FCFAFD")
        draw.line((40, top + row_height, 1460, top + row_height), fill="#E3DFE8", width=1)
        centered(row[0], edges[0], edges[1], top + 28, 22, MUTED)
        cover = covers.get(answer.jackets[index])
        if cover is not None:
            image.paste(cover, (118, top + 13))
        else:
            draw.rounded_rectangle((118, top + 13, 178, top + 73), radius=8, fill="#EEE5F2")
            draw.ellipse((132, top + 44, 148, top + 56), fill="#A989B6")
            draw.line((147, top + 49, 147, top + 28, 160, top + 32), fill="#A989B6", width=3)
        lines = _wrapped_lines(draw, row[1], 451, 25, max_lines=2)
        title_y = top + (row_height - len(lines) * 31) // 2 - 2
        for line_index, line in enumerate(lines):
            draw.text((192, title_y + line_index * 31), line, font=_font(25), fill=INK)
        fill, outline = colors.get(row[2], ("#EEEAF2", BORDER))
        draw.rounded_rectangle((694, top + 10, 786, top + 76), radius=14, fill=fill, outline=outline, width=1)
        centered(row[2], 694, 786, top + 17, 13)
        centered(row[3], 694, 786, top + 36, 24 if len(row[3]) <= 3 else 20)
        for value, column in ((row[4], 3), (row[5], 4), (row[6], 5)):
            # Normal metrics fit at 26px; bound unusually large values to their cell.
            available = edges[column + 1] - edges[column] - 24
            font = _font(26)
            if draw.textlength(value, font=font) <= available:
                centered(value, edges[column], edges[column + 1], top + 26, 26,
                         "#6B4C9A" if column == 5 else INK)
            else:
                _write(draw, value, edges[column] + 12, top + 26, available, 22)
    for i, line in enumerate(foot_lines):
        draw.text((54, table_bottom + 24 + i * 29), line, fill=MUTED, font=_font(20))
    return _bytes(image)


def render_card(card: Card, locale: str = "zh") -> bytes:
    image, draw = _canvas(900, 2250, _label(locale, "card"))
    _write(draw, localized_text(card, "band", locale), 56, 145, 460, 27, PINK)
    _write(draw, localized_text(card, "character", locale), 56, 190, 760, 39)
    _write(draw, localized_text(card, "title", locale), 56, 250, 760, 29)
    _paste_asset(image, draw, card.full_url, (64, 315, 836, 1345), locale)
    draw.rounded_rectangle((63, 1370, 837, 2175), radius=22, fill="#FBF7F2", outline=BORDER, width=2)
    _write(draw, f"{_rarity(card.rarity)}    ID {card.id}    {_label(locale, 'type')} {card.card_type}", 88, 1393, 720, 28, PINK)
    total = card.performance + card.technic + card.visual
    _write(draw, f"{_label(locale, 'power')}  {total:,}" if total else {"zh": "数值暂不可用", "en": "Stats unavailable", "ja": "ステータス未取得"}.get(locale, "数值暂不可用"), 88, 1447, 720, 31)
    values = [(_label(locale, "performance"), card.performance, PINK), (_label(locale, "technic"), card.technic, BLUE), (_label(locale, "visual"), card.visual, MINT)]
    for index, (label, value, color) in enumerate(values):
        y = 1504 + index * 55
        _write(draw, f"{label}  {value:,}", 88, y, 270, 22)
        draw.rounded_rectangle((365, y + 7, 780, y + 28), radius=10, fill="#E9E3DE")
        draw.rounded_rectangle((365, y + 7, 365 + int(415 * value / max(1, max(v for _, v, _ in values))), y + 28), radius=10, fill=color)
    if card.skills:
        _draw_skills(draw, card.skills, 88, 1682, 690, locale)
    elif card.skill_name:
        _write(draw, f"{_label(locale, 'skill')}  {localized_text(card, 'skill_name', locale)}", 88, 1682, 700, 20, MUTED)
    return _bytes(image)


def render_card_list(cards: list[Card], query: str, locale: str = "zh", footer: str = "") -> bytes:
    height = 225 + len(cards) * 165 + (80 if footer else 0)
    image, draw = _canvas(900, height, _label(locale, "cards"))
    _write(draw, f"{_label(locale, 'card_list')} · {query}", 52, 142, 790, 31)
    thumbnails = _prefetch_assets([card.thumbnail_url for card in cards], (93, 125))
    for index, card in enumerate(cards):
        top = 195 + index * 165
        draw.rounded_rectangle((50, top, 850, top + 145), radius=18, fill="#FBF7F2", outline=BORDER, width=2)
        _paste_loaded_asset(image, draw, thumbnails[card.thumbnail_url], (68, top + 10, 161, top + 135), locale)
        _write(draw, localized_text(card, "character", locale), 190, top + 13, 590, 27)
        _write(draw, localized_text(card, "title", locale), 190, top + 55, 590, 22)
        _write(draw, f"#{card.id}  ·  {_rarity(card.rarity)}", 190, top + 101, 590, 20, MUTED)
    if footer:
        for index, line in enumerate(footer.splitlines()[:2]):
            _write(draw, line, 55, height - 95 + index * 31, 790, 21, MUTED)
    return _bytes(image)


def render_support_card(card: SupportCard, locale: str = "zh") -> bytes:
    image, draw = _canvas(900, 2100, _label(locale, "support_card"))
    _write(draw, localized_text(card, "character", locale), 56, 165, 760, 36, PINK)
    _write(draw, localized_text(card, "title", locale), 56, 225, 760, 29)
    _paste_asset(image, draw, card.full_url, (64, 290, 836, 1245), locale)
    draw.rounded_rectangle((63, 1270, 837, 2025), radius=22, fill="#FBF7F2", outline=BORDER, width=2)
    _write(draw, f"{_rarity(card.rarity)}    ID {card.id}    {_label(locale, 'type')} {card.card_type}", 88, 1295, 720, 28, PINK)
    values = [(_label(locale, "performance"), card.performance / 100, PINK),
              (_label(locale, "technic"), card.technic / 100, BLUE),
              (_label(locale, "visual"), card.visual / 100, MINT)]
    total = sum(value for _, value, _ in values)
    _write(draw, f"{_label(locale, 'support_bonus')}  {total:g}%" if total else {"zh": "数值暂不可用", "en": "Stats unavailable", "ja": "ステータス未取得"}.get(locale, "数值暂不可用"), 88, 1348, 720, 31)
    for index, (label, value, color) in enumerate(values):
        y = 1405 + index * 55
        _write(draw, f"{label}  {value:g}%", 88, y, 270, 22)
        draw.rounded_rectangle((365, y + 7, 780, y + 28), radius=10, fill="#E9E3DE")
        draw.rounded_rectangle((365, y + 7, 365 + int(415 * value / max(1, max(v for _, v, _ in values))), y + 28), radius=10, fill=color)
    if card.skills:
        _draw_skills(draw, card.skills, 88, 1588, 690, locale)
    return _bytes(image)


def render_support_card_list(cards: list[SupportCard], query: str, locale: str = "zh", footer: str = "") -> bytes:
    height = 225 + len(cards) * 165 + (80 if footer else 0)
    image, draw = _canvas(900, height, _label(locale, "support_cards"))
    _write(draw, f"{_label(locale, 'support_card_list')} · {query}", 52, 142, 790, 31)
    thumbnails = _prefetch_assets([card.thumbnail_url for card in cards], (125, 125))
    for index, card in enumerate(cards):
        top = 195 + index * 165
        draw.rounded_rectangle((50, top, 850, top + 145), radius=18, fill="#FBF7F2", outline=BORDER, width=2)
        _paste_loaded_asset(image, draw, thumbnails[card.thumbnail_url], (68, top + 10, 193, top + 135), locale)
        _write(draw, localized_text(card, "character", locale), 220, top + 13, 560, 27)
        _write(draw, localized_text(card, "title", locale), 220, top + 55, 560, 22)
        _write(draw, f"#{card.id}  ·  {_rarity(card.rarity)}", 220, top + 101, 560, 20, MUTED)
    if footer:
        for index, line in enumerate(footer.splitlines()[:2]):
            _write(draw, line, 55, height - 95 + index * 31, 790, 21, MUTED)
    return _bytes(image)

"""Deterministic song color/mission filtering shared by commands and /问."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .data import localized_text
from .song_traits import COLORS, describe

MARKER = re.compile(r"颜色|属性|[红蓝绿黄紫黑白橙青灰]色|激奏|击奏|纯(?:JUST|COMBO|LUCK)", re.I)
HELP = "例：/查曲 蓝色 MyGO EX 激奏=JUST；/查曲 激奏=纯JUST；/查曲 激奏=JUST/JUST/COMBO。单类型表示包含，斜杠序列按顺序精确匹配。"
DIFFS = {"EZ": "EASY", "EASY": "EASY", "NM": "NORMAL", "NORMAL": "NORMAL", "HD": "HARD", "HARD": "HARD", "EX": "EXPERT", "EXPERT": "EXPERT"}


@dataclass(frozen=True)
class SongFilter:
    query: str
    term: str = ""
    colors: tuple[int, ...] = ()
    mode: str = ""
    missions: tuple[str, ...] = ()
    difficulty: str = ""
    comparison: str = ""
    level: float | None = None
    integer_level: bool = False


def parse_filter(query):
    text = unicodedata.normalize("NFKC", query).strip()
    if not MARKER.search(text):
        return None
    text = re.sub(r"\s*([=/,])\s*", r"\1", text)
    colors, missions, mode, difficulty, comparison, level = (), (), "", "", "", None
    level_text = ""
    # Remove recognized fields; never erase arbitrary remaining words.
    matches = list(re.finditer(r"(?:颜色|属性)(?:=|:)?([^\s]+)|([红蓝绿黄紫黑白橙青灰]色(?:[/,或][红蓝绿黄紫黑白橙青灰]色)*)", text, re.I))
    if len(matches) > 1:
        raise ValueError("颜色请用单个条件列出，例如 颜色=蓝/绿。")
    if matches:
        m = matches[0]
        values = re.split(r"[/,或]", m[1] or m[2])
        mapping = {v.removesuffix("色"): k for k, v in COLORS.items()}
        if any(v.removesuffix("色") not in mapping for v in values):
            raise ValueError("颜色仅支持红、蓝、绿、黄、紫。")
        colors = tuple(sorted({mapping[v.removesuffix("色")] for v in values}))
        text = text[:m.start()] + " " + text[m.end():]
    found = list(re.finditer(r"(?:激奏|击奏)(?:=|:)?([^\s]+)|(?<!\w)(纯(?:JUST|COMBO|LUCK))(?!\w)", text, re.I))
    if len(found) > 1:
        raise ValueError("请只给出一个激奏条件。" + HELP)
    if found:
        m = found[0]
        value = (m[1] or m[2]).upper()
        if value == "混合":
            mode = "mixed"
        else:
            mode = "pure" if value.startswith("纯") else "all" if value.startswith("包含全部") else "exact" if "/" in value else "contains"
            value = re.sub(r"^(?:纯|包含全部|包含)", "", value)
            missions = tuple(re.split(r"[/,+]", value))
            if (not 1 <= len(missions) <= 16 or any(v not in {"JUST", "COMBO", "LUCK"} for v in missions)
                    or mode in {"pure", "contains"} and len(missions) != 1):
                raise ValueError("激奏条件无法识别。" + HELP)
        text = text[:m.start()] + " " + text[m.end():]
    diffs = list(re.finditer(r"(?<![A-Za-z])(?:diff=)?(EXPERT|NORMAL|EASY|HARD|EX|NM|EZ|HD)(?![A-Za-z])", text, re.I))
    if len(diffs) > 1:
        raise ValueError("一次查询请指定一个难度。")
    if diffs:
        m = diffs[0]
        difficulty = DIFFS[m[1].upper()]
        text = text[:m.start()] + " " + text[m.end():]
    levels = list(re.finditer(r"lv\.?\s*(>=|<=|>|<|=)?\s*(\d+(?:\.\d+)?)", text, re.I))
    if len(levels) > 1:
        raise ValueError("一次查询请指定一个等级条件。")
    if levels:
        m = levels[0]
        comparison, level = m[1] or "=", float(m[2])
        level_text = m[2]
        if not 1 <= level <= 40:
            raise ValueError("等级须在 1～40 之间。")
        text = text[:m.start()] + " " + text[m.end():]
    elif (re.search(r"(?:^|\s)\d+(?:\.\d+)?$", text.strip())
          and float(re.search(r"(\d+(?:\.\d+)?)$", text.strip())[1]) <= 40):
        m = re.search(r"(?:^|\s)(\d+(?:\.\d+)?)$", text.strip())
        comparison, level = "=", float(m[1])
        level_text = m[1]
        text = text.strip()[:m.start()]
        if not 1 <= level <= 40:
            raise ValueError("等级须在 1～40 之间。")
    if not colors and not mode or MARKER.search(text) or re.search(r"[=:<>]|\blv\b", text, re.I):
        raise ValueError("无法识别全部歌曲条件，请使用明确字段。" + HELP)
    term = text.strip()
    pieces = [term] if term else []
    if colors:
        pieces.append("颜色=" + "/".join(COLORS[c] for c in colors))
    if mode:
        value = "混合" if mode == "mixed" else ("纯" if mode == "pure" else "包含全部" if mode == "all" else "") + "/".join(missions)
        pieces.append("激奏=" + value)
    if difficulty:
        pieces.append(difficulty)
    if level is not None:
        pieces.append(f"lv{comparison}{level_text}")
    return SongFilter(" ".join(pieces), term, colors, mode, missions, difficulty, comparison, level,
                      comparison == "=" and "." not in level_text)


@dataclass(frozen=True)
class SongAnswer:
    request: SongFilter
    songs: tuple = ()
    page: int = 1
    warning: str = ""
    unavailable: bool = False

    @property
    def footer(self):
        from .commands import page_notice
        value = page_notice("songs", self.request.query, self.page, len(self.songs), "zh")
        return value + ("\n" + self.warning if self.warning else "")

    def text(self, locale="zh"):
        from .commands import page_slice
        if not self.songs:
            return "没有符合条件的歌曲。\n" + (self.warning + "\n" if self.warning else "") + HELP
        lines = ["歌曲筛选 · " + self.request.query]
        for song in page_slice(self.songs, self.page):
            lines.append(f"{song.id}  {localized_text(song, 'title', locale)} · {localized_text(song, 'band', locale)}\n{describe(song)}")
        return "\n".join(lines) + "\n" + self.footer


def execute(request, repository, page=1):
    from .commands import _song_name_matches
    candidates = _song_name_matches(repository, request.term) if request.term else list(repository.songs)
    def charts_match(song):
        charts = [c for c in song.charts if not request.difficulty or c.difficulty == request.difficulty]
        if not charts:
            return False
        if request.level is None:
            return True
        values = [c.level if request.integer_level else c.display_level for c in charts]
        # Retain existing range semantics: unspecified difficulty uses hardest chart.
        if not request.difficulty and request.comparison != "=":
            values = [max(values)]
        target = request.level
        return any({"=": v == target,
                    ">": v > target, ">=": v >= target, "<": v < target, "<=": v <= target}[request.comparison] for v in values)
    candidates = [s for s in candidates if charts_match(s)]
    unknown, matches = 0, []
    for song in candidates:
        t = song.traits
        if not t or request.colors and t.color is None or request.mode and not t.missions:
            unknown += 1
            continue
        if request.colors and t.color not in request.colors:
            continue
        seq = t.missions or ()
        wanted = request.missions
        if request.mode and not {"contains": bool(set(wanted) & set(seq)), "all": set(wanted) <= set(seq),
                "pure": bool(seq) and set(seq) == set(wanted), "mixed": len(set(seq)) > 1,
                "exact": seq == wanted}[request.mode]:
            continue
        matches.append(song)
    warning = f"有 {unknown} 首歌曲缺少所需颜色／激奏数据，未参与筛选。" if unknown else ""
    if candidates and unknown == len(candidates):
        warning = "歌曲颜色／激奏数据暂不可用，等待后台同步后重试。"
    if any(s.traits and s.traits.stale for s in candidates):
        warning += " Haneoka 旧缓存。"
    return SongAnswer(request, tuple(matches), page, warning, bool(candidates and unknown == len(candidates)))


def local_query(query):
    """Translate a small, bounded Chinese grammar, rejecting leftover conditions."""
    if not MARKER.search(query) or re.search(r"卡|SNAP|推荐|攻略|排行|分数表|效率|谱面", query, re.I):
        return None
    text = unicodedata.normalize("NFKC", query).strip()
    text = re.sub(r"(?:哪些歌曲?|歌曲?)是(?=纯|混合)", " ", text)
    text = re.sub(r"(?:激奏|击奏)(?:类型|顺序)?(?:是|为|=|:)?\s*", "激奏=", text)
    text = re.sub(r"(?:带有|包含|带)(JUST|COMBO|LUCK)(?:激奏=)?", r"激奏=\1", text, flags=re.I)
    text = re.sub(r"(?<![=A-Za-z])(纯(?:JUST|COMBO|LUCK))", r"激奏=\1", text, flags=re.I)
    text = re.sub(r"混合激奏=", "激奏=混合", text)
    text = re.sub(r"(\d+(?:\.\d+)?)级(?:及)?以下", r" lv<=\1 ", text)
    text = re.sub(r"(\d+(?:\.\d+)?)级(?:及)?以上", r" lv>=\1 ", text)
    text = re.sub(r"(\d+(?:\.\d+)?)级", r" lv=\1 ", text)
    text = re.sub(r"^(?:请|帮我|查一下|查询|查找|找一下|找|查|看看|看)\s*", "", text)
    text = re.sub(r"有哪些|有哪几首|哪些|所有|全部|歌曲|歌|的|[?？。]", " ", text)
    # Boundaries permit Chinese band/color adjacency without touching arbitrary titles.
    text = re.sub(r"([红蓝绿黄紫黑白橙青灰]色)", r" \1 ", text)
    text = re.sub(r"([红蓝绿黄紫黑白橙青灰]色)\s*(?:或|和)\s*([红蓝绿黄紫黑白橙青灰]色)", r"\1/\2", text)
    from .commands import _split_page
    text, page = _split_page(text.strip())
    if not 1 <= page <= 100:
        return "页码应在 1～100 之间。"
    try:
        request = parse_filter(text)
        if request is None:
            return HELP
        from .structured_query import QuerySpec
        return QuerySpec("song", page=page, song_query=request.query)
    except ValueError as exc:
        return str(exc)

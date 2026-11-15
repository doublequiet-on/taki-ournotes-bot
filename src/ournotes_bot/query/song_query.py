# L3
# Input: 歌曲条件文本或 SongFilter、SongRepository 的歌曲／traits 记录及页码。
# Output: parse_filter 返回 SongFilter | None，非法条件抛 ValueError；execute 返回 SongAnswer，保存确认匹配的歌曲、页码与资料缺失提示，text 展示时切页。
# Pos: Query / Deterministic 的歌曲颜色、激奏顺序与等级条件执行；见 L2-2.md。
# Effects/Dependencies: 读取当前仓库与已关联 traits，不刷新来源；名称解析经 commands 可读本地别名 JSON，保留对根入口的函数内引用。

"""Deterministic song color/mission filtering shared by commands and /问."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..data import localized_text, normalize
from ..i18n import tr
from .song_identity import direct_song_names, protect_entity, song_suggestions, song_suggestion_text
from ..sources.haneoka.song_traits import COLORS, describe

MISSION_TYPE = r"(?:JUST|COMBO|LUCK)"
MARKER = re.compile(rf"颜色|属性|[红蓝绿黄紫黑白橙青灰]色|(?:^|\s)[红蓝绿黄紫](?=\s|$)|激奏|击奏|纯{MISSION_TYPE}", re.I)
HELP = "如 /查曲 MyGO just、/查曲 纯JUST 或 /查曲 JUST/JUST/COMBO。单类型表示包含，斜杠序列按顺序精确匹配。"
MISSION_FILTER = re.compile(r"(?:激奏|击奏)[=:]?(?:\s*(?!(?:激奏|击奏))(\S+))?")
LEGACY_PURE_MISSION_FILTER = re.compile(rf"(?<!\w)纯{MISSION_TYPE}(?!\w)", re.I)
BARE_MISSION_FILTER = re.compile(
    rf"(?<!\S)(?:混合|纯{MISSION_TYPE}|包含全部{MISSION_TYPE}(?:[/,+]{MISSION_TYPE})*"
    rf"|{MISSION_TYPE}(?:/{MISSION_TYPE})*)(?!\S)",
    re.I,
)
LEVEL_NUMBER = r"\d+(?:\.\d+)?"
SYMBOL_LEVEL = re.compile(rf"lv\.?\s*(>=|<=|>|<|=)?\s*({LEVEL_NUMBER})", re.I)
CHINESE_LEVEL = re.compile(
    r"(?<!\S)(?:(大于|小于)\s*([+-]?\d[\d.]*)\s*级?|([+-]?\d[\d.]*)\s*级?\s*(以上|以下))(?!\S)"
)
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
    band: str = ""


def parse_filter(query, repository=None, *, force=False, direct_syntax=False):
    text = unicodedata.normalize("NFKC", query).strip()
    marked = MARKER.search(text)
    if not force and not marked and not (direct_syntax and (BARE_MISSION_FILTER.search(text) or CHINESE_LEVEL.search(text))):
        return None
    text, protected = protect_entity(text, repository)
    text = re.sub(r"\s*([=:/,])\s*", r"\1", text)
    band = ""
    bands = list(re.finditer(r"乐队=", text))
    if len(bands) > 1:
        raise ValueError("一次查询请指定一个乐队条件。")
    if bands:
        m = bands[0]
        from .entity_lexicon import resolve_exact_alias
        remainder = text[m.end():]
        # Canonical names such as Ave Mujica contain spaces. Consume only a
        # complete verified name/alias; leave all remaining conditions intact.
        for token in reversed(list(re.finditer(r"\S+", remainder))):
            value = remainder[:token.end()]
            match = resolve_exact_alias("song", value, repository) if repository else None
            direct_bands = {s.band for s in repository.songs if normalize(value) in {
                normalize(s.band), *(normalize(v) for v in s.localized.get("band", {}).values())}} if repository else set()
            if len(direct_bands) == 1:
                band = next(iter(direct_bands))
            elif match and match.entity and match.entity.kind == "band" and not match.ambiguous:
                band = str(match.entity.value)
            if band:
                text = text[:m.start()] + " " + remainder[token.end():]
                break
        if not band:
            raise ValueError(f"无法识别乐队「{remainder.split()[0] if remainder.split() else ''}」，未忽略此条件。")
    colors, missions, mode, difficulty, comparison, level = (), (), "", "", "", None
    level_text = ""
    # Remove recognized fields; never erase arbitrary remaining words.
    matches = list(re.finditer(r"(?:颜色|属性)(?:=|:)?([^\s]+)|([红蓝绿黄紫黑白橙青灰]色(?:[/,或][红蓝绿黄紫黑白橙青灰]色)*|(?<!\S)[红蓝绿黄紫](?!\S))", text, re.I))
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
    explicit = list(MISSION_FILTER.finditer(text))
    bare_pattern = BARE_MISSION_FILTER if direct_syntax else LEGACY_PURE_MISSION_FILTER
    bare = [
        match for match in bare_pattern.finditer(text)
        if not any(field.start() < match.end() and match.start() < field.end() for field in explicit)
    ]
    if len(explicit) + len(bare) > 1:
        raise ValueError("请只给出一个激奏条件。" + HELP)
    found = explicit or bare
    if found:
        m = found[0]
        value = m[1] if explicit else m[0]
        if value is None:
            raise ValueError("请指定激奏条件的值。" + HELP)
        value = value.upper()
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
    diffs = list(re.finditer(r"(?<![A-Za-z])(?:diff=)?(EXPERT|NORMAL|EASY|HARD|EX|NM|EZ|HD|ALL|全难度)(?![A-Za-z])", text, re.I))
    if len(diffs) > 1:
        raise ValueError("一次查询请指定一个难度。")
    if diffs:
        m = diffs[0]
        difficulty = "ALL" if m[1].upper() in {"ALL", "全难度"} else DIFFS[m[1].upper()]
        text = text[:m.start()] + " " + text[m.end():]
    levels = list(SYMBOL_LEVEL.finditer(text))
    chinese_levels = list(CHINESE_LEVEL.finditer(text)) if direct_syntax else []
    if len(levels) + len(chinese_levels) > 1:
        raise ValueError("一次查询请指定一个等级条件。")
    if levels or chinese_levels:
        if levels:
            m = levels[0]
            comparison, level_text = m[1] or "=", m[2]
        else:
            m = chinese_levels[0]
            comparison = {"大于": ">", "小于": "<", "以上": ">=", "以下": "<="}[m[1] or m[4]]
            level_text = m[2] or m[3]
            if not re.fullmatch(LEVEL_NUMBER, level_text):
                raise ValueError("等级须为 1～40 之间的整数或小数。")
        level = float(level_text)
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
    if not force and not marked and not colors and not mode and not chinese_levels:
        return None
    if (not force and not colors and not mode and not chinese_levels) or MARKER.search(text) or re.search(r"[=:<>]|\blv\b", text, re.I):
        raise ValueError("无法识别全部歌曲条件，请使用明确字段。" + HELP)
    term = text.replace("TAKIENTITYTOKEN", protected).strip()
    pieces = [term] if term else []
    if band:
        pieces.append("乐队=" + band)
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
                      comparison == "=" and "." not in level_text, band)


def serialize_filter(request):
    parts = [request.term] if request.term else []
    if request.band:
        parts.append("乐队=" + request.band)
    if request.colors:
        parts.append("颜色=" + "/".join(COLORS[c] for c in request.colors))
    if request.mode:
        value = "混合" if request.mode == "mixed" else ("纯" if request.mode == "pure" else "包含全部" if request.mode == "all" else "") + "/".join(request.missions)
        parts.append("激奏=" + value)
    if request.difficulty:
        parts.append(request.difficulty)
    if request.level is not None:
        value = str(int(request.level)) if request.integer_level else str(float(request.level))
        parts.append(f"lv{request.comparison}{value}")
    return " ".join(parts)


def _missing_traits(request, traits):
    fields = []
    if request.colors and (traits is None or traits.color is None):
        fields.append("颜色")
    if request.mode and (traits is None or not traits.missions):
        fields.append("激奏")
    return fields


def traits_match(request, song):
    """True/False/None means match/non-match/missing required evidence."""
    if request.band and normalize(song.band) != normalize(request.band):
        return False
    if not (request.colors or request.mode):
        return True
    traits = song.traits
    if traits is None:
        return None
    if request.colors and traits.color is not None and traits.color not in request.colors:
        return False
    seq, wanted = traits.missions, request.missions
    if request.mode and seq:
        matches = {
            "contains": bool(set(wanted) & set(seq)), "all": set(wanted) <= set(seq),
            "pure": set(seq) == set(wanted), "mixed": len(set(seq)) > 1, "exact": seq == wanted,
        }
        if not matches[request.mode]:
            return False
    return None if _missing_traits(request, traits) else True


@dataclass(frozen=True)
class SongAnswer:
    request: SongFilter
    songs: tuple = ()
    page: int = 1
    warning: str = ""
    unavailable: bool = False
    unknown_entity: bool = False
    missing_count: int = 0
    suggestions: tuple = ()
    suggestion_limit: int = 0

    @property
    def status(self):
        if self.suggestions:
            return "ambiguous"
        return "unknown_entity" if self.unknown_entity else "data_unavailable" if self.unavailable else "success" if self.songs else "empty"

    @property
    def footer(self):
        from ..commands import page_notice
        value = page_notice("songs", self.request.query, self.page, len(self.songs), "zh")
        return value + ("\n" + self.warning if self.warning else "")

    def text(self, locale="zh"):
        from ..commands import page_slice
        if self.suggestions:
            return song_suggestion_text(self.suggestions, locale, self.warning, limit=self.suggestion_limit)
        if not self.songs:
            if self.unknown_entity:
                message = f"找不到歌曲或乐队「{self.request.term}」。"
            elif self.unavailable:
                message = "这次无法判断哪些歌曲符合条件。"
            elif self.missing_count:
                message = "现有资料中未确认有歌曲符合条件。"
            else:
                message = "没有符合条件的歌曲。"
            return message + "\n" + (self.warning + "\n" if self.warning else "") + HELP
        lines = ["歌曲筛选 · " + self.request.query]
        for index, song in enumerate(page_slice(self.songs, self.page), 1):
            lines.append(f"{index:02d}. ID {song.id}  {localized_text(song, 'title', locale)} · {localized_text(song, 'band', locale)}\n{describe(song)}")
        return "\n".join(lines) + "\n" + self.footer + "\n" + tr(locale, "song_list_actions")


def execute(request, repository, page=1, *, direct_song_keywords=False):
    from ..commands import _song_name_matches
    catalog = repository.capture_catalog()
    stale = catalog.cache_state == 'stale'
    suggestion_limit = 0
    if direct_song_keywords:
        candidates, suggestion_limit = direct_song_names(repository, request.term)
    else:
        candidates = _song_name_matches(repository, request.term) if request.term else list(repository.songs)
    if request.term and not candidates:
        return SongAnswer(request, page=page, unknown_entity=True,
                          warning='Haneoka 旧缓存；未命中不代表最新资料不存在。' if stale else '')
    def charts_match(song):
        charts = [c for c in song.charts if request.difficulty in {"", "ALL"} or c.difficulty == request.difficulty]
        if not charts:
            return False
        if request.level is None:
            return True
        values = [c.level if request.integer_level else c.display_level for c in charts]
        # Retain existing range semantics: unspecified difficulty uses hardest chart.
        if request.difficulty in {"", "ALL"} and request.comparison != "=":
            values = [max(values)]
        target = request.level
        return any({"=": v == target,
                    ">": v > target, ">=": v >= target, "<": v < target, "<=": v <= target}[request.comparison] for v in values)
    candidates = [s for s in candidates if charts_match(s)]
    unknown, matches = 0, []
    missing_fields = set()
    for song in candidates:
        keep = traits_match(request, song)
        if keep is None:
            unknown += 1
            missing_fields.update(_missing_traits(request, song.traits))
            continue
        if not keep:
            continue
        matches.append(song)
    fields = "／".join(field for field in ("颜色", "激奏") if field in missing_fields)
    unavailable = unknown > 0 and unknown == len(candidates)
    if unavailable:
        warning = f"所需的歌曲资料暂不可用（缺少{fields}），等待后台同步后重试。"
    else:
        warning = f"有 {unknown} 首歌曲缺少所需{fields}资料，未参与筛选。" if unknown else ""
    if stale or any(s.traits and s.traits.stale for s in candidates):
        warning += " Haneoka 旧缓存。"
    return SongAnswer(request, () if suggestion_limit else tuple(matches), page, warning, unavailable=unavailable,
                      missing_count=unknown, suggestions=song_suggestions(matches, suggestion_limit),
                      suggestion_limit=suggestion_limit)


def local_query(query, repository=None):
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
    from ..commands import _split_page
    text, page = _split_page(text.strip())
    if not 1 <= page <= 100:
        return "页码应在 1～100 之间。"
    try:
        request = parse_filter(text, repository)
        if request is None:
            return HELP
        from ..structured_query import QuerySpec
        return QuerySpec("song", page=page, song_query=request.query)
    except ValueError as exc:
        return str(exc)

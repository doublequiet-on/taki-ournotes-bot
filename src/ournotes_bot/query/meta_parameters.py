# L3
# Input: 分数表命令／确定性问句及一份已验证的 Moenotes 视图。
# Output: MetaRequest/QuerySpec 或 QueryProblem；默认自由／激奏双榜、裸颜色／任务词及完整条件。
# Pos: Query / Deterministic 的分数表专用参数合同；见 L2-2.md。
# Effects/Dependencies: 入口惰性取得公开快照并读取审核别名；后续解析无平台／模型调用。
"""Strict, order-independent parameter groups for the finite music model."""
from __future__ import annotations

import re
import shlex
import unicodedata
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from ..data import normalize
from ..sources.moenotes_music_data import DIFFICULTIES
from .entity_lexicon import EntityRef, known_alias_names, resolve_exact_alias
from .song_identity import QueryProblem, exact_songs
from .song_query import DIFFS, SongFilter, parse_filter, serialize_filter

HELP = ("用法：/查分数表 [歌曲或乐队] [HD,EX] [排行=效率/活动/速度/等级/Notes/最长/最短/跳过得分] [前10/20/30] [页2]。\n"
        "默认同图并列自由／激奏四难度效率榜；单曲默认EX。单人=自由，多人=激奏，明确场景只显示一榜。\n"
        "颜色可直接写红/蓝/绿/黄/紫；任务可写JUST、纯COMBO、混合、JUST/JUST/COMBO、包含全部JUST/COMBO，"
        "任务词未指定场景时默认激奏；英文难度与任务词不区分大小写。指标=eff/score和排序保留。")
RANKS = {"效率": "efficiency", "eff": "efficiency", "efficiency": "efficiency", "单局": "score", "score": "score",
         "活动": "event", "event": "event", "速度": "speed", "speed": "speed", "等级": "level", "level": "level",
         "notes": "notes", "note": "notes", "最长": "longest", "longest": "longest", "最短": "shortest", "shortest": "shortest",
         "跳过得分": "skip", "跳过": "skip", "skip": "skip"}
RANK_LABELS = {v: k for k, v in RANKS.items() if k in ("效率", "单局", "活动", "速度", "等级", "Notes", "最长", "最短", "跳过得分")}
RANK_LABELS["notes"] = "Notes"
SCENES = {"自由": "free", "单人": "free", "free": "free",
          "激奏": "battle", "多人": "battle", "battle": "battle"}
TASK_WORD = re.compile(r"(?:纯|包含全部|包含)?(?:JUST|COMBO|LUCK)(?:[/,+](?:JUST|COMBO|LUCK))*|混合", re.I)
REMOVED_FIELDS = {"激奏排名", "great", "just", "技能", "时长", "额外耗时", "前沿", "目标", "综合力", "人数"}


def filter_label(filters):
    """Keep shared song filters intact; meta commands use the shorter task words."""
    text = serialize_filter(replace(filters, query="", term="", band="", difficulty=""))
    return re.sub(r"(?<!\S)激奏=", "", text)


@dataclass(frozen=True)
class MetaRequest:
    song_id: int | None = None
    search: str = ""
    band: str = ""
    band_ids: tuple[int, ...] = ()
    difficulties: tuple[str, ...] = DIFFICULTIES
    ranking: str = "efficiency"
    speed: str = "density"
    scene: str = "free"
    compare_scenes: bool = False
    ranks: tuple[int, ...] = (1, 1, 1)
    great: int = 0
    just: int = 100
    skills: tuple[float, ...] = (100, 100, 100, 100, 100)
    duration: str = "bgm"
    overhead_ms: int = 30000
    front: bool = False
    target: str = "SS"
    power: int = 0
    people: int = 5
    order: str = "desc"
    filters: SongFilter = SongFilter("")
    score_id: int | None = None
    explicit: frozenset[str] = field(default_factory=frozenset, compare=False)
    snapshot: object = field(default=None, repr=False, compare=False)

    def display_summary(self):
        """Readable effective conditions; command_label remains the round-trip form."""
        short = dict(zip(DIFFICULTIES, ("EZ", "NM", "HD", "EX")))
        parts = ["全难度" if self.difficulties == DIFFICULTIES else "/".join(short[d] for d in self.difficulties),
                 RANK_LABELS[self.ranking] + ("升序" if self.order == "asc" else "降序")]
        if self.band:
            parts.append("乐队：" + self.band)
        if self.search:
            parts.append("搜索：" + self.search)
        if self.ranking in {"efficiency", "score", "event"}:
            parts.append("自由／激奏双榜" if self.compare_scenes else "多人（激奏）" if self.scene == "battle" else "单人（自由）")
        if self.ranking == "speed":
            parts.append({"density": "N/s", "bpm": "主要BPM", "bpm_max": "最大BPM"}[self.speed])
        legacy = filter_label(self.filters)
        if legacy:
            parts.append(legacy)
        return " · ".join(parts)

    def command_label(self, *, limit=30, page=1):
        parts = ["查分数表"]
        if self.song_id is not None:
            parts.append("歌曲=" + str(self.song_id))
        if self.search:
            parts.append("搜索=" + shlex.quote(self.search))
        if self.band:
            parts.append("乐队=" + shlex.quote(self.band))
        parts += [",".join(self.difficulties), "排行=" + RANK_LABELS[self.ranking]]
        if self.ranking in {"efficiency", "score", "event"} and not self.compare_scenes:
            parts.append("场景=" + ("多人" if self.scene == "battle" else "单人"))
        if self.ranking == "speed":
            parts.append("速度=" + {"density": "N/s", "bpm": "BPM", "bpm_max": "最大BPM"}[self.speed])
        legacy = filter_label(self.filters)
        if legacy:
            parts.append(legacy)
        parts += [f"排序={self.order}", f"前{limit}", f"页{page}"]
        return " ".join(parts)


def numeric(text, lo, hi, *, places=0):
    # No exponent, NaN, infinity, sign trick, coercion of bool, rounding or clamping.
    pattern = r"[0-9]+" + (r"(?:\.[0-9]{1," + str(places) + r"})?" if places else "")
    if not re.fullmatch(pattern, text):
        raise ValueError("数值格式不正确")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError("数值格式不正确") from None
    if not lo <= value <= hi:
        raise ValueError(f"数值需为 {lo}～{hi}")
    return int(value) if not places else float(value)


def parse_meta(query, repository, *, natural=False):
    from ..structured_query import QuerySpec
    snapshot = repository.music_data.get()
    if snapshot is None:
        return QueryProblem("分数表公开快照暂不可用，请稍后重试；其他查询仍可使用。", "data_unavailable")
    songs = snapshot.songs()
    view = SimpleNamespace(songs=songs, cards=(), support_cards=())
    text = unicodedata.normalize("NFKC", query).strip().strip("?？。!！")
    text = re.sub(r"^/?(?:问\s*|查(?:分数表|效率)\s*)", "", text)
    text = text.replace("，", ",").replace("“", '"').replace("”", '"')
    # Quoted values are opaque to entity protection and punctuation/space
    # normalization. Decode each shell quote once, including escaped quotes.
    quoted = {}
    def hold_quote(match):
        key = f"TAKIMETAQUOTED{len(quoted)}"
        quoted[key] = shlex.split(match[0])[0]
        return key
    text = re.sub(r'''"(?:\\.|[^"\\])*"|'[^']*' ''', hold_quote, text, flags=re.X)
    # Protect every complete known entity, including numeric/parameter-word names.
    names = {n for s in songs for n in (s.title, *s.titles, s.band, *s.localized.get("band", {}).values()) if n}
    names.update(known_alias_names(view))
    placeholders = {}
    for name in sorted(names, key=len, reverse=True):
        # Newly introduced bare words are parameters. Explicit/quoted song and
        # band fields still resolve entities with these exact names.
        if name in {"单人", "多人", "红", "蓝", "绿", "黄", "紫"} or TASK_WORD.fullmatch(name):
            continue
        left = r"(?<![A-Za-z0-9_])" if name[0].isascii() and name[0].isalnum() else ""
        right = r"(?![A-Za-z0-9_])" if name[-1].isascii() and name[-1].isalnum() else ""
        pattern = re.compile(left + re.escape(name) + right, re.I)
        if pattern.search(text):
            key = f"TAKIMETAENTITY{len(placeholders)}"
            text = pattern.sub(" " + key + " ", text)
            placeholders[key] = name
    if natural:
        # Preserve quoted/explicit values while translating the legacy bounded
        # Chinese grammar. Unknown residual words still produce a terminal error.
        held = {}
        def hold(match):
            key = f"TAKIMETAFIELD{len(held)}"
            held[key] = match[0]
            return " " + key + " "
        text = re.sub(r"\S+\s*=\s*(?:\"[^\"]*\"|'[^']*'|\S+)", hold, text)
        text = re.sub(r"(\d+(?:\.\d+)?)级(?:及)?以下", r" lv<=\1 ", text)
        text = re.sub(r"(\d+(?:\.\d+)?)级(?:及)?以上", r" lv>=\1 ", text)
        text = re.sub(r"前\s*(10|20|30|三十|二十|十)\s*(?:首(?:歌曲|歌)?|条)?", r" 前\1 ", text)
        text = re.sub(r"(?<![A-Za-z])(?:EXPERT|NORMAL|EASY|HARD|EX|NM|EZ|HD)(?![A-Za-z])", lambda m: " " + m[0] + " ", text, flags=re.I)
        text = re.sub(r"每分钟得分效率|得分效率|效率榜|效率", " 效率 ", text)
        text = re.sub(r"全难度|所有难度|最高|最低", lambda m: " " + m[0] + " ", text)
        text = re.sub(r"查一下|查询|查看|请|帮我|有哪些|哪些歌|怎么样|如何|排行榜|排行|排名|分数表|(?<![A-Za-z])meta(?![A-Za-z])|的", " ", text, flags=re.I)
        for key, value in held.items():
            text = text.replace(key, value)
    try:
        text = re.sub(r"\s*([=,/])\s*", r"\1", text)
        text = re.sub(r"\blv\s*(<=|>=|<|>|=)\s*", r"lv\1", text, flags=re.I)
        tokens = shlex.split(text)
    except ValueError:
        return QueryProblem("引号未闭合。\n" + HELP)
    values, explicit, difficulty_set, colors, rest, bare = {}, set(), set(), set(), [], []

    def restore(value):
        for key, name in placeholders.items():
            value = value.replace(key, name)
        for key in sorted(quoted, key=len, reverse=True):
            value = value.replace(key, quoted[key])
        return value

    def set_value(key, value):
        if key in values and values[key] != value:
            raise ValueError(f"{key} 条件冲突")
        values[key] = value
        explicit.add(key)

    def band_value(value):
        from ..sources.moenotes_music_data import text_values
        direct = [b for b in snapshot.data["bands"] if normalize(value) in {normalize(n) for n in text_values(b.get("name"))}]
        if len(direct) == 1:
            name = direct[0].get("name", {})
            preferred = name.get("ja") if isinstance(name, dict) else None
            return preferred if isinstance(preferred, str) and preferred else next(iter(text_values(name)), "")
        found = {s.band for s in songs if normalize(value) in {normalize(s.band), *(normalize(v) for v in s.localized.get("band", {}).values())}}
        alias = resolve_exact_alias("song", value, view)
        if alias.entity and alias.entity.kind == "band" and not alias.ambiguous:
            found.add(str(alias.entity.value))
        if len(found) != 1:
            raise ValueError(f"无法唯一识别乐队「{value}」")
        return next(iter(found))

    try:
        for token in tokens:
            t = restore(token)
            key, sep, value = t.partition("=")
            key = key.casefold()
            lower = t.casefold()
            if token in placeholders or token in quoted:
                bare.append(t)
            elif sep and key in REMOVED_FIELDS or lower == "前沿":
                raise ValueError(f"「{key}」参数已移除")
            elif re.match(r"^lv\.?(?:[<>=]|\d)", lower):
                raise ValueError("分数表等级筛选已移除；等级排行仍可使用")
            elif sep and key in {"歌曲", "搜索", "乐队"}:
                if not value:
                    raise ValueError(f"{key}不能为空")
                set_value({"歌曲": "selector", "搜索": "search", "乐队": "band"}[key], band_value(value) if key == "乐队" else value)
            elif sep and key in {"排行", "指标", "metric"}:
                rank = RANKS.get(value.casefold())
                if rank is None or key in {"指标", "metric"} and rank not in {"efficiency", "score"}:
                    raise ValueError("不支持此排行／指标")
                set_value("ranking", rank)
            elif lower in RANKS:
                set_value("ranking", RANKS[lower])
            elif sep and key == "速度":
                v = {"n/s": "density", "bpm": "bpm", "最大bpm": "bpm_max"}.get(value.casefold())
                if v is None:
                    raise ValueError("速度支持 N/s、BPM、最大BPM")
                set_value("speed", v)
                set_value("ranking", "speed")
            elif (sep and key == "场景") or t in {"激奏", "自由", "单人", "多人"}:
                scene = SCENES.get(value.casefold() if sep else t)
                if scene is None:
                    raise ValueError("场景支持单人／自由、多人／激奏")
                set_value("scene", scene)
            elif (sep and key in {"排序", "sort"}) or t in {"最高", "最低"}:
                v = value.casefold() if sep else "asc" if t == "最低" else "desc"
                if v not in {"asc", "desc"}:
                    raise ValueError("排序支持 asc／desc")
                set_value("order", v)
            elif re.fullmatch(r"前(?:10|20|30|十|二十|三十)(?:首|条)?", t):
                v = re.sub(r"^前|[首条]$", "", t)
                set_value("limit", {"十": 10, "二十": 20, "三十": 30}.get(v, int(v) if v.isdecimal() else 0))
            elif re.fullmatch(r"页\d+", t):
                set_value("page", numeric(t[1:], 1, 100))
            elif sep and key == "diff" or all(v.upper() in {*DIFFS, "ALL", "全难度", "所有难度"} for v in t.split(",")):
                ds = value if sep else t
                for v in ds.split(","):
                    if v.upper() in {"ALL", "全难度", "所有难度"}:
                        difficulty_set.update(DIFFICULTIES)
                    elif v.upper() in DIFFS:
                        difficulty_set.add(DIFFS[v.upper()])
                    else:
                        raise ValueError("难度无法识别")
                explicit.add("difficulties")
            elif key in {"颜色", "属性"} and sep or re.fullmatch(r"[红蓝绿黄紫](?:色)?(?:[/,或][红蓝绿黄紫](?:色)?)*", t):
                colors.update(parse_filter("颜色=" + t if not sep else t, view, force=True, efficiency=True).colors)
            elif TASK_WORD.fullmatch(t):
                rest.append("激奏=" + t)
            elif re.match(r"^(?:激奏|击奏)", t, re.I):
                rest.append(t)
            elif t in {"分数表", "排行", "排行榜", "排名", "效率榜", "meta", "哪些歌", "有哪些", "查看", "查一下", "请", "帮我", "的"}:
                continue
            elif sep or any(c in t for c in "<>:"):
                raise ValueError(f"未知或非法字段「{t}」")
            else:
                bare.append(t)
        legacy_values = {}
        for t in rest:
            f = parse_filter(t, view, force=True, efficiency=True)
            if f.term:
                raise ValueError("无法识别全部条件")
            for key in ("mode", "missions"):
                v = getattr(f, key)
                if v not in ("", (), None):
                    if key in legacy_values and legacy_values[key] != v:
                        raise ValueError("任务条件冲突")
                    legacy_values[key] = v
        filters = SongFilter("", colors=tuple(sorted(colors)), **legacy_values)
        filters = replace(filters, query=serialize_filter(filters))
        # Bare complete bands and songs may occur anywhere; all residual words matter.
        selector = values.pop("selector", None)
        for t in bare:
            try:
                b = band_value(t)
            except ValueError:
                if selector is not None and selector != t:
                    raise ValueError("歌曲条件冲突或存在未识别参数")
                selector = t
            else:
                set_value("band", b)
        song_id = None
        if selector is not None:
            candidates = exact_songs(view, selector)
            if not candidates and selector.isascii() and selector.isdecimal() and 0 < int(selector) < 100000:
                candidates = exact_songs(view, str(100000 + int(selector)))
            if len(candidates) != 1:
                message = ("匹配到多首候选歌曲，请用歌曲ID选择：\n" + "\n".join(f"{s.title}（ID {s.id}）" for s in candidates[:10])) if candidates else f"未找到唯一歌曲「{selector}」，未忽略任何条件。"
                return QueryProblem(message, "ambiguous" if candidates else "unknown_entity")
            song_id = candidates[0].id
        limit, page = values.pop("limit", 30), values.pop("page", 1)
        rank = values.get("ranking", "efficiency")
        values.setdefault("scene", "battle" if filters.mode else "free")
        values["compare_scenes"] = rank in {"efficiency", "score", "event"} and "scene" not in explicit and not filters.mode
        if rank not in {"efficiency", "score", "event"} and "scene" in explicit:
            raise ValueError("此排行不适用计算场景")
        if "order" not in values:
            values["order"] = "asc" if rank == "shortest" or rank == "event" and not values.get("power") else "desc"
        ds = tuple(d for d in DIFFICULTIES if d in difficulty_set) if difficulty_set else ("EXPERT",) if song_id is not None else DIFFICULTIES
        from ..sources.moenotes_music_data import text_values
        band_ids = tuple(b["id"] for b in snapshot.data["bands"] if values.get("band") and normalize(values["band"]) in {normalize(n) for n in text_values(b.get("name"))})
        request = MetaRequest(song_id=song_id, difficulties=ds, filters=filters, explicit=frozenset(explicit), snapshot=snapshot, band_ids=band_ids, **values)
        subject = EntityRef("song", song_id) if song_id is not None else EntityRef("band", request.band) if request.band else None
        return QuerySpec("efficiency", subject, ds[0] if len(ds) == 1 else "ALL", filters.comparison, filters.level,
                         page, str(song_id) if song_id else request.band, metric="score" if rank == "score" else "eff",
                         order=request.order, limit=limit, song_filter=filters, meta_request=request)
    except (ValueError, InvalidOperation, TypeError) as exc:
        return QueryProblem(str(exc) + "。\n" + HELP)

# L3
# Input: 分数表命令／确定性问句及一份已验证的 Moenotes 视图。
# Output: MetaRequest/QuerySpec 或 QueryProblem；完整命令可复制，可读摘要保留有效条件，引号内文本不改写。
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
        "默认四难度混合效率榜、激奏、三段第1、Great=0、Just=100、五技能100%、BGM、曲外30秒；单曲默认EX。\n"
        "可用：场景=自由/激奏 激奏排名=1,2,3 技能=150,130,120,100,100 时长=谱面 额外耗时=30 前沿；"
        "活动另支持目标=SS 综合力=300000 人数=5；旧颜色、激奏任务、lv边界、指标=eff/score和排序保留。")
RANKS = {"效率": "efficiency", "eff": "efficiency", "efficiency": "efficiency", "单局": "score", "score": "score",
         "活动": "event", "event": "event", "速度": "speed", "speed": "speed", "等级": "level", "level": "level",
         "notes": "notes", "note": "notes", "最长": "longest", "longest": "longest", "最短": "shortest", "shortest": "shortest",
         "跳过得分": "skip", "跳过": "skip", "skip": "skip"}
RANK_LABELS = {v: k for k, v in RANKS.items() if k in ("效率", "单局", "活动", "速度", "等级", "Notes", "最长", "最短", "跳过得分")}
RANK_LABELS["notes"] = "Notes"


@dataclass(frozen=True)
class MetaRequest:
    song_id: int | None = None
    search: str = ""
    band: str = ""
    band_ids: tuple[int, ...] = ()
    difficulties: tuple[str, ...] = DIFFICULTIES
    ranking: str = "efficiency"
    speed: str = "density"
    scene: str = "battle"
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
            parts += ["激奏" if self.scene == "battle" else "自由", f"Great {self.great}%"]
            if self.scene == "battle":
                parts += [f"Just {self.just}%", "三段名次 " + "/".join(map(str, self.ranks))]
            skills = (f"全{self.skills[0]:g}%" if len(set(self.skills)) == 1
                      else "/".join(f"{v:g}" for v in self.skills) + "%")
            parts += ["技能 " + skills, f"曲外 {self.overhead_ms / 1000:g}秒"]
        if self.ranking == "speed":
            parts.append({"density": "N/s", "bpm": "主要BPM", "bpm_max": "最大BPM"}[self.speed])
        parts.append("时长 " + ("BGM" if self.duration == "bgm" else "谱面"))
        if self.ranking in {"efficiency", "event"}:
            parts.append("仅前沿" if self.front else "不限前沿")
        if self.ranking == "event":
            parts += ["目标 " + self.target, f"综合力 {self.power}" if self.power else "综合力未设"]
            if self.scene == "battle":
                parts.append(f"{self.people}人同分房间")
        legacy = serialize_filter(replace(self.filters, query="", term="", band="", difficulty=""))
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
        if self.ranking in {"efficiency", "score", "event"}:
            parts += ["场景=" + ("激奏" if self.scene == "battle" else "自由"), f"Great={self.great}",
                      "技能=" + ",".join(f"{v:g}" for v in self.skills)]
            if self.scene == "battle":
                parts += ["激奏排名=" + ",".join(map(str, self.ranks)), f"Just={self.just}"]
            parts.append(f"额外耗时={self.overhead_ms / 1000:g}")
        if self.ranking == "speed":
            parts.append("速度=" + {"density": "N/s", "bpm": "BPM", "bpm_max": "最大BPM"}[self.speed])
        parts.append("时长=" + ("BGM" if self.duration == "bgm" else "谱面"))
        if self.ranking in {"efficiency", "event"}:
            parts.append("前沿=" + ("是" if self.front else "否"))
        if self.ranking == "event":
            parts += [f"目标={self.target}", f"综合力={self.power}"]
            if self.scene == "battle":
                parts.append(f"人数={self.people}")
        legacy = serialize_filter(replace(self.filters, query="", term="", band="", difficulty=""))
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
            elif (sep and key == "场景") or t in {"激奏", "自由"}:
                scene = {"激奏": "battle", "自由": "free", "battle": "battle", "free": "free"}.get(value.casefold() if sep else t)
                if scene is None:
                    raise ValueError("场景支持激奏／自由")
                set_value("scene", scene)
            elif sep and key == "激奏排名":
                ranks = tuple(numeric(v, 1, 5) for v in value.split(","))
                if len(ranks) not in {1, 3}:
                    raise ValueError("激奏排名需为一个或三个整数")
                set_value("ranks", ranks * 3 if len(ranks) == 1 else ranks)
            elif sep and key in {"great", "just"}:
                set_value(key, numeric(value.removesuffix("%"), 0, 100))
            elif sep and key == "技能":
                preset = {"全150": (150,) * 5, "全100": (100,) * 5, "无": (0,) * 5}.get(value)
                skills = preset if preset is not None else tuple(numeric(v.removesuffix("%"), 0, 150, places=2) for v in value.split(","))
                if len(skills) != 5:
                    raise ValueError("技能必须恰好五值")
                set_value("skills", skills)
            elif sep and key == "时长":
                v = {"bgm": "bgm", "谱面": "chart", "chart": "chart"}.get(value.casefold())
                if v is None:
                    raise ValueError("时长支持 BGM／谱面")
                set_value("duration", v)
            elif sep and key == "额外耗时":
                set_value("overhead_ms", int(Decimal(value) * 1000) if numeric(value, 0, 600, places=2) >= 0 else 0)
            elif t == "前沿" or sep and key == "前沿":
                v = {"是": True, "否": False, "true": True, "false": False}.get(value.casefold() if sep else "是")
                if v is None:
                    raise ValueError("前沿支持是／否")
                set_value("front", v)
            elif sep and key == "目标":
                if value.upper() not in {"D", "C", "B", "A", "S", "SS"}:
                    raise ValueError("目标支持 D/C/B/A/S/SS")
                set_value("target", value.upper())
            elif sep and key in {"综合力", "人数"}:
                set_value("power" if key == "综合力" else "people", numeric(value, 0 if key == "综合力" else 1, 2**31 - 1 if key == "综合力" else 5))
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
            elif key in {"颜色", "属性"} and sep or re.fullmatch(r"[红蓝绿黄紫]色(?:[/,或][红蓝绿黄紫]色)*", t):
                colors.update(parse_filter(t, view, force=True, efficiency=True).colors)
            elif re.match(r"^(?:激奏|击奏|纯(?:JUST|COMBO|LUCK)|lv)", t, re.I):
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
            for key in ("mode", "missions", "comparison", "level"):
                v = getattr(f, key)
                if v not in ("", (), None):
                    if key in legacy_values and legacy_values[key] != v:
                        raise ValueError("任务或等级条件冲突")
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
        scene = values.get("scene", "battle")
        if rank not in {"efficiency", "score", "event"} and explicit & {"scene", "ranks", "great", "just", "skills", "overhead_ms"}:
            raise ValueError("此排行不适用场景／准度／技能／曲外耗时")
        if rank != "event" and explicit & {"target", "power", "people"}:
            raise ValueError("目标、综合力、人数仅适用活动排行")
        if rank not in {"efficiency", "event"} and "front" in explicit:
            raise ValueError("前沿仅适用效率及活动排行")
        if scene == "free" and explicit & {"just", "ranks", "people"}:
            raise ValueError("自由场景不适用 Just、激奏排名或人数")
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

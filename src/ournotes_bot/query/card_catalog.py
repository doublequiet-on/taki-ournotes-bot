# L3
# Input: 筛选文本、SongRepository、成员／SNAP 模式、卡牌 ID 与页码；成员卡直接命令支持卡名片段。
# Output: CardRequest 与 CardAnswer；cards 保存完整选择集合，visible 提供当前页，text 输出列表／详情／原卡面说明或错误。
# Pos: Query / Deterministic 的成员卡与 SNAP 确定性筛选及展示模式选择；见 L2-2.md。
# Effects/Dependencies: 实体解析可读别名 JSON；ID／详情路径可经仓库补全详情并联网，不调用模型或绘图。

"""Platform independent card catalog: validated filters, complete sets and output modes."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..domain.card_query import CardFilters, CardRequest
from ..data import Card, SupportCard, localized_text, normalize, character_identity
from .entity_lexicon import card_alias_spellings, resolve_exact_alias, resolve_character_fragment, CharacterAmbiguity

RARITIES = {"R": 2, "SR": 3, "SSR": 4, "EX": 10, "BD": 20}
# Public /images/CardType{attribute}.webp, inspected against the five actual icons.
# These are Chinese colour aliases, not invented official attribute names.
TYPES = {1: "红色（日轮）", 2: "蓝色（星球）", 3: "绿色（流星）", 4: "黄色（闪光）", 5: "紫色（月亮）"}
LIVE = {"score": "分数提升", "life": "LIFE回复", "judgement": "判定强化", "duration": "技能延长"}
PAGE_SIZE = 16
_FIELDS = {"稀有度": "rarity", "星级": "rarity", "rarity": "rarity",
           "颜色": "type", "属性": "type", "类型": "type", "type": "type",
           "乐队": "band", "band": "band", "角色": "character", "character": "character",
           "live": "live", "击奏": "gekisou", "激奏": "gekisou", "gekisou": "gekisou",
           "得意": "tags", "得意乐曲": "tags", "tags": "tags"}
_CHARACTER_ID_FIELDS = {"角色id", "characterid"}
_DIRECT_FIELDS = {**_FIELDS, "卡名": "title", "id": "id",
                  **dict.fromkeys(_CHARACTER_ID_FIELDS, "character_ids")}
_MEMBER_FIELDS = {**_DIRECT_FIELDS, "演出": "live", "队长": "leader"}
_LEADER_TYPES = {"同色", "同技", "同激", "异色"}
_LIVE_SHORT = {"分卡": "score", "奶卡": "life", "判卡": "judgement"}
_SNAP_LIVE_ALIASES = {**_LIVE_SHORT, "延长": "duration",
                      **{label.casefold(): category for category, label in LIVE.items()}}
_SNAP_SKILL_FIELDS = {"live", "激奏", "击奏", "gekisou"}
_JUST_WINDOW = "just_window_expand"
_LIVE_CONDITIONS = {"血量加分": "life", "life加分": "life", "perfect加分": "perfect"}
_LIVE_CONDITION_LABELS = {
    "zh": {"life": "血量加分", "perfect": "PERFECT加分"},
    "en": {"life": "LIFE threshold", "perfect": "PERFECT bonus"},
    "ja": {"life": "LIFE条件", "perfect": "PERFECT加点"},
}
_PERCENT = r"\d+(?:\.\d{1,2})?%?"
_SCORE_ARG = re.compile(rf"({_PERCENT})(?:(以上|以下)|到({_PERCENT}))?")
_SEPARATORS = ",，、|/或"
_NAME_END = re.compile(rf"(?=$|[\s{re.escape(_SEPARATORS)}])")
_MEMBER_FIELD = re.compile(rf"([^\s=:{re.escape(_SEPARATORS)}]+)\s*[=:]\s*")
_WORD = re.compile(rf"[^\s{re.escape(_SEPARATORS)}]+")
_QUOTED_TITLE = re.compile(r'"([^"]*)"')
_UNRESTRICTED = {"全部", "不限", "すべて"}
_COLOURS = {"红": 1, "蓝": 2, "绿": 3, "黄": 4, "紫": 5}
_SNAP_ATTRIBUTE_NAMES = {"红赤": 1, "绀碧": 2, "翡翠": 3, "琉金": 4, "紫苑": 5}
_RARITY_NUMBER = r"[0-9零〇一二两三四五六七八九十百.]+"
_MEMBER_RARITY = re.compile(
    rf"(?:星(?:级)?{_RARITY_NUMBER}|[★☆]+(?:{_RARITY_NUMBER})?|"
    rf"(?:至少|至多|最多|不低于|不高于|超过|低于|高于|大于|小于|[<>≤≥+\-])?"
    rf"{_RARITY_NUMBER}(?:[\-~至到]{_RARITY_NUMBER})?(?:星(?:级)?|[★☆]|-?stars?)"
    r"(?:及?以上|及?以下|以内|起)?)", re.I)


class UnknownCardEntity(ValueError):
    """An untyped value can still be a member card title."""


def rarity_name(card):
    names = {2: "R", 3: "SR", 4: "SSR"}
    if isinstance(card, SupportCard):
        names[10] = "EX"
    else:
        names[20] = "BD"
    return names.get(card.rarity, f"未知稀有度({card.rarity})")


def labels(card):
    cats = card.catalog.get("categories", {})
    def label(key):
        values = cats.get(key) or [None]
        return "/".join("不适用" if v == "not_applicable" else
                        (LIVE.get(v, "分类未确认") if key == "live" else v or "分类未确认") for v in values)
    return label("live"), label("gekisou")


@dataclass(frozen=True)
class CardAnswer:
    request: CardRequest
    cards: tuple = ()
    error: str = ""
    status: str = "success"
    cache_state: str = 'unknown'

    @property
    def cache_notice(self):
        return '\n当前使用旧缓存；未命中不代表最新资料不存在。' if self.cache_state == 'stale' else ''

    def __post_init__(self):
        if self.status == "success" and not self.cards:
            object.__setattr__(self, "status", "invalid_arguments" if self.error else "empty")

    @property
    def visible(self):
        start = (self.request.page - 1) * PAGE_SIZE
        return self.cards[start:start + PAGE_SIZE] if self.request.page > 0 else ()

    @property
    def footer(self):
        req, total = self.request, len(self.cards)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        if not 1 <= req.page <= pages:
            return f"页码超出范围，共 {pages} 页。发送 {req.command} {req.query} 页1"
        start = (req.page - 1) * PAGE_SIZE + 1 if total else 0
        text = f"共 {total} 张 · 第 {req.page}/{pages} 页 · 本页 {start}–{min(req.page * PAGE_SIZE, total)}"
        if req.page < pages:
            text += f"\n下一页：{req.command} {req.query} 页{req.page + 1}"
        if any(c.catalog.get("detail_stale") for c in self.cards):
            text += "\n部分详情使用上次有效缓存。"
        return text + self.cache_notice

    @property
    def is_candidate_list(self):
        req = self.request
        return req.mode == "list" and (req.matched_title_ids is not None or req.has_live_details)

    def live_match_label(self, card, locale="zh"):
        req = self.request
        if not req.has_live_details:
            return ""
        bands = card.catalog.get("live_score_bands") or ()
        if req.live_score_ranges:
            values = _matching_score_bands(bands, req)
            matched = "/".join(f"+{value / 100:g}%" for value in values)
        else:
            conditions = {band["condition"] for band in bands}
            names = _LIVE_CONDITION_LABELS.get(locale, _LIVE_CONDITION_LABELS["zh"])
            matched = "/".join(names[key] for key in req.live_conditions if key in conditions)
        label = {"en": "Matched", "ja": "該当"}.get(locale, "命中")
        return f"{label} {matched}" if matched else ""

    def live_evidence(self, card, locale="zh"):
        marker = self.live_match_label(card, locale)
        if not marker:
            return ""
        text = card.catalog.get("live_score_summary", "") if locale == "zh" else ""
        if not text:
            skill = next((skill for skill in card.skills if skill.kind == "liveSkill"), None)
            text = localized_text(skill, "description", locale) if skill else ""
        text = " ".join(text.split())
        return f"Lv.5 · {marker} · {text}" if text else f"Lv.5 · {marker}"

    def candidate_choices(self, cards, locale="zh"):
        if not self.is_candidate_list:
            return ""
        lines = []
        for card in cards:
            line = f"{localized_text(card, 'title', locale)} · ID {card.id}"
            evidence = self.live_evidence(card, locale)
            if evidence:
                line += " · " + evidence
            lines.append(f"{line} · {self.request.command} {card.id}")
        return "\n".join(lines)

    def text(self, locale="zh"):
        if self.error:
            return self.error + self.cache_notice
        req = self.request
        title = "留影卡（SNAP）" if req.support else "角色卡"
        if not self.cards:
            return f"{title}：未找到符合条件的卡牌。条件：{req.query or '不限'}（未放宽筛选）" + self.cache_notice
        if req.mode == "detail":
            return detail_text(self.cards[0], locale) + self.cache_notice
        if req.mode == "art":
            return f"{title} ID {self.cards[0].id} · 卡面（公开 full 版本）" + self.cache_notice
        lines = [f"{title}列表 · 条件：{req.query or '不限'}"]
        for index, card in enumerate(self.visible, 1):
            live, gek = labels(card)
            text = (f"{index:02d}. ID {card.id} · {rarity_name(card)} · {TYPES.get(card.card_type, '未知类型')} · "
                    f"{localized_text(card, 'character', locale)} · {localized_text(card, 'title', locale)}"
                    f"\n  LIVE {live} / 击奏 {gek}")
            evidence = self.live_evidence(card, locale)
            if evidence:
                text += "\n  " + evidence
            if self.is_candidate_list:
                text += f"\n  {req.command} {card.id}"
            lines.append(text)
        lines.append(self.footer)
        if any(not c.catalog.get("detail_loaded") for c in self.cards):
            lines.append("部分技能资料未获取，未知分类不会当作无技能。")
        lines.append("按稀有度降序、ID升序；查询详情请指定ID。")
        return "\n".join(lines)


def _entity(value, dimension, repository):
    found = set()
    for c in repository.cards:
        for kind, field_name in (("character", "character"), ("band", "band")):
            names = [getattr(c, field_name), *c.localized.get(field_name, {}).values()]
            if (dimension is None or kind == dimension) and normalize(value) in {normalize(n) for n in names}:
                found.add((kind, character_identity(c.character) if kind == "character" else c.band))
    for c in repository.support_cards:
        if dimension in {None, "character"}:
            found.update(("character", character_identity(n)) for n in c.characters if normalize(n) == normalize(value))
        if dimension in {None, "band"}:
            found.update(("band", n) for n in c.catalog.get("bands", []) if normalize(n) == normalize(value))
    if len(found) == 1:
        return next(iter(found))
    if len(found) > 1:
        raise ValueError(f"名称“{value}”有歧义，请显式指定角色=或乐队=。")
    match = resolve_exact_alias("card", value, repository)
    if match.ambiguous:
        raise ValueError(f"名称“{value}”有歧义，请使用完整角色名或乐队名。")
    if match.entity and match.entity.kind in {"character", "band"}:
        if dimension and match.entity.kind != dimension:
            raise ValueError(f"“{value}”不是{dimension}条件。")
        return match.entity.kind, str(match.entity.value)
    if dimension != "band":
        fragment = resolve_character_fragment(value, repository)
        if fragment:
            return "character", fragment
    # Built-in civil/stage-name equivalence is also used by old commands.
    from ..data import resolve_character_alias
    alias = resolve_character_alias(value)
    if alias and dimension != "band":
        return "character", alias.display
    raise UnknownCardEntity(f"无法识别筛选值“{value}”；请使用已收录的角色/乐队简称或完整名称。")


def card_entity_filters(value, repository, *, support=False, dimension=None):
    """Resolve one model-proposed entity, not a filter expression."""
    value = unicodedata.normalize("NFKC", value).strip()
    if dimension is None and value.isdecimal():
        return CardFilters(title_ids=(int(value),))
    try:
        kind, name = _entity(value, dimension, repository)
        return CardFilters(**{kind: (name,)})
    except ValueError:
        if dimension is not None:
            raise
        source = repository.support_cards if support else repository.cards
        exact = tuple(c.id for c in source if normalize(value) in
                      {normalize(c.title), *(normalize(n) for n in c.localized.get("title", {}).values())})
        alias = resolve_exact_alias("support_card" if support else "card", value, repository)
        if not alias.ambiguous and alias.entity and alias.entity.kind == ("support_card" if support else "card"):
            exact = tuple(c.id for c in source if c.id == alias.entity.value)
        if not exact:
            raise
        return CardFilters(title_ids=tuple(dict.fromkeys(exact)))


def _card_titles(card):
    return (card.title, *card.localized.get("title", {}).values())


def _exact_card_ids(value, repository, *, support=False):
    source = repository.support_cards if support else repository.cards
    kind = "support_card" if support else "card"
    alias = resolve_exact_alias(kind, value, repository)
    if not alias.ambiguous and alias.entity and alias.entity.kind == kind:
        return [card.id for card in source if card.id == alias.entity.value]
    needle = normalize(value)
    return [card.id for card in source if any(normalize(name) == needle for name in _card_titles(card))]


def _bare_filter(value, *, legacy=False):
    upper = value.upper()
    looks_like_rarity = (re.search(r"星|[★☆]|-?stars?$", value, re.I) if legacy
                         else _MEMBER_RARITY.fullmatch(value))
    if upper in RARITIES or looks_like_rarity:
        return "rarity"
    if value.removesuffix("色") in _COLOURS or (legacy and value.endswith("色") and not value.endswith("角色")):
        return "type"
    if upper in {"JUST", "COMBO", "LUCK"}:
        return "gekisou"
    return None


def _card_filter(value, key, repository, *, support=False):
    from ..commands import split_card_rarity
    upper = value.upper()
    if key == "character_ids":
        if not re.fullmatch(r"[1-9][0-9]{0,15}", value) or int(value) > 2**53 - 1:
            raise ValueError("角色ID须为正整数，例如 角色ID=2。")
        return key, int(value)
    if key == "rarity":
        if value in {"2", "3", "4"}:
            rarity = int(value)
        elif upper in RARITIES:
            rarity = RARITIES[upper]
        else:
            try:
                rest, rarity = split_card_rarity(value)
            except ValueError:
                raise ValueError("请指定 SSR/SR/R（兼容四/三/二星旧别名）；不支持稀有度范围。") from None
            if rest or rarity is None:
                raise ValueError("未知稀有度；角色卡可用 BD/SSR/SR/R，SNAP 可用 EX/SSR/SR/R。")
        if rarity == 10 and not support:
            raise ValueError("EX 仅在 SNAP 数据中已核实，角色卡不支持 EX 筛选。")
        if rarity == 20 and support:
            raise ValueError("BD 仅在角色卡数据中已核实，SNAP 不支持 BD 筛选。")
        return "rarity", rarity
    if key == "type":
        color = _COLOURS.get(value.removesuffix("色"))
        if color is None:
            raise ValueError("未知颜色；可用红色、蓝色、绿色、黄色、紫色。")
        return "type", color
    if key == "live":
        category = {"分数提升": "score", "分数up": "score", "スコアアップ": "score", "スコアup": "score",
                    "life回复": "life", "life回復": "life", "life恢复": "life", "回复": "life",
                    "判定强化": "judgement", "判定強化": "judgement", "技能延长": "duration"}.get(value.lower())
        if not category:
            raise ValueError("未知LIVE分类；可用分数提升、LIFE回复、判定强化、技能延长（SNAP）。")
        if category == "duration" and not support:
            raise ValueError("技能延长目前仅在 SNAP 的 LIVE 支援中核实。")
        return key, category
    if key == "gekisou":
        if upper not in {"JUST", "COMBO", "LUCK"}:
            raise ValueError("未知击奏分类；可用 JUST、COMBO、LUCK。")
        return "gekisou", upper
    if key == "leader":
        if value not in _LEADER_TYPES:
            raise ValueError("队长类型可用同色、同技、同激、异色。")
        return key, value
    if key == "tags":
        if support:
            raise ValueError("SNAP 数据没有已核实的得意乐曲字段。")
        available = {k: names for c in repository.cards for k, names in c.catalog.get("tags", {}).items()}
        found = [k for k, names in available.items() if normalize(value) in {normalize(n) for n in names.values()}]
        if not found:
            _, band = _entity(value, "band", repository)
            found = [k for k, names in available.items() if normalize(band) in {normalize(n) for n in names.values()}]
        if len(found) != 1:
            raise ValueError("得意乐曲标签不存在或映射不完整；未扩大筛选范围。")
        return key, found[0]
    return _entity(value, key, repository)


def _card_parts(query, repository, *, support):
    # Protect complete source names before treating punctuation or spaces as syntax.
    names = set(card_alias_spellings(support=support))
    for card in repository.cards:
        names.update((card.character, card.band))
        names.update(card.localized.get("character", {}).values())
        names.update(card.localized.get("band", {}).values())
        if not support:
            names.update(_card_titles(card))
            for tag_names in card.catalog.get("tags", {}).values():
                names.update(tag_names.values())
    for card in repository.support_cards:
        names.update(card.characters)
        names.update(card.catalog.get("bands", ()))
        if support:
            names.update(_card_titles(card))
    names = {normalize(name) for name in names if name}
    max_name = max(map(len, names), default=0)
    start = 0
    while start < len(query):
        if query[start].isspace():
            start += 1
            continue
        field = _MEMBER_FIELD.match(query, start)
        name_end = start
        if not (field and (field[1] == "卡名" or field[1].casefold() in _CHARACTER_ID_FIELDS)):
            for boundary in _NAME_END.finditer(query, start + 1):
                stop = boundary.start()
                if query[stop - 1].isspace():
                    continue
                spelling = normalize(query[start:stop])
                if len(spelling) > max_name:
                    break
                if spelling and spelling in names:
                    name_end = stop
        if name_end > start:
            end, kind = name_end, "value"
        elif query[start] == '"':
            quoted = _QUOTED_TITLE.match(query, start)
            if quoted is None:
                raise ValueError("卡名引号未闭合。")
            end, kind = quoted.end(), "quoted"
        elif field:
            end, kind = field.end(), "field"
        elif query[start] in _SEPARATORS:
            end, kind = start + 1, "separator"
        else:
            end, kind = _WORD.match(query, start).end(), "value"
        yield start, end, kind, field[1] if kind == "field" else query[start:end]
        start = end


def _score_bound(value):
    integer, _, fraction = value.removesuffix("%").partition(".")
    return int(integer) * 100 + int(fraction.ljust(2, "0"))


def _score_argument(value):
    match = _SCORE_ARG.fullmatch(value)
    if not match:
        return None
    first, suffix, last = match.groups()
    low = _score_bound(first)
    high = _score_bound(last) if last is not None else low
    if low > high:
        raise ValueError("演出加分区间的起点不能大于终点。")
    return (None if suffix == "以下" else low, None if suffix == "以上" else high)


def _parse_direct_filters(query, page, repository, *, support):
    filters = {}
    title_spans = []
    explicit_title = quoted_title = False
    field = field_name = None
    pending = separated = have_value = False
    explicit_id = None
    live_conditions = []
    score_ranges = []
    explicit_score = False
    fields = _DIRECT_FIELDS if support else _MEMBER_FIELDS
    bare_fields = {"id", *_SNAP_SKILL_FIELDS} if support else {"id", "演出", "队长"}

    def add(dimension, value):
        filters.setdefault(dimension, []).append(value)

    for start, end, kind, value in _card_parts(query, repository, support=support):
        short = value.casefold()
        snap_live = _SNAP_LIVE_ALIASES.get(short) if support else None
        snap_color = _SNAP_ATTRIBUTE_NAMES.get(value.removesuffix("属性")) if support else None
        snap_just_window = support and value == "扩判"
        if kind == "value" and not pending and not separated:
            # A complete title can share a spelling with a newly accepted bare alias.
            if support and (snap_live or snap_color or snap_just_window or short in _SNAP_SKILL_FIELDS):
                if _exact_card_ids(value, repository, support=True):
                    title_spans.append((start, end))
                    field = None
                    have_value = True
                    continue
            if short in bare_fields:
                kind = "field"
        if kind == "field":
            if pending or separated:
                raise ValueError(f"{field_name or '筛选条件'}缺少筛选值。")
            field_name = value
            field = fields.get(value.casefold())
            if field is None:
                available = ("ID、卡名、稀有度、颜色、乐队、角色、角色ID、LIVE、击奏" if support
                             else "ID、卡名、稀有度、颜色、乐队、角色、角色ID、队长、演出、击奏、得意")
                raise ValueError(f"未知筛选维度“{value}”；可用 {available}。")
            pending = True
            continue
        if kind == "separator":
            if not have_value or pending or separated:
                raise ValueError("分隔符两侧都需要筛选值。")
            separated = True
            continue
        key = field if pending or separated else None
        field = key
        pending = separated = False
        have_value = True
        if kind == "quoted" and key != "title":
            raise ValueError("含引号的卡名请用 卡名= 指定。")
        score_literal = value.removeprefix("演出") if key is None else value
        if key == "title":
            explicit_title = True
            quoted_title |= kind == "quoted"
            title_spans.append((start, end))
        elif key == "id" or (support and key is None and value.isdecimal()):
            if not value.isdecimal() or explicit_id is not None:
                raise ValueError("详情查询只能指定一个卡牌 ID。")
            explicit_id = int(value)
        elif snap_live and key in (None, "live"):
            add("live", snap_live)
            field = "live"
        elif snap_just_window and key in (None, "gekisou"):
            add("gekisou", _JUST_WINDOW)
            field = "gekisou"
        elif snap_color and key in (None, "type"):
            add("type", snap_color)
            field = "type"
        elif not support and key in (None, "live") and short in _LIVE_CONDITIONS:
            live_conditions.append(_LIVE_CONDITIONS[short])
            field = "live"
        elif not support and key in (None, "live") and short in _LIVE_SHORT:
            add("live", _LIVE_SHORT[short])
            field = "live"
        elif not support and key in (None, "live", "score") and (bounds := _score_argument(score_literal)) is not None:
            if key is None and not value.isdecimal() and _exact_card_ids(value, repository):
                title_spans.append((start, end))
            else:
                score_ranges.append(bounds)
                explicit_score |= key == "live" or not value.isdecimal()
                field = "score"
        elif key == "score":
            raise ValueError("演出数值可用 150、150以上、150以下 或 120到155。")
        elif value in _UNRESTRICTED and key != "character_ids":
            continue
        elif dimension := key or _bare_filter(value):
            add(*_card_filter(value, dimension, repository, support=support))
        else:
            try:
                add(*_entity(value, None, repository))
            except UnknownCardEntity:
                title_spans.append((start, end))
    if pending or separated:
        raise ValueError(f"{field_name if pending else '分隔符后'}缺少筛选值。")
    title_ids = (_card_title_matches(query, title_spans, repository, support=support,
                                     explicit=explicit_title, quoted=quoted_title)
                 if title_spans else None)
    if score_ranges and not (explicit_score or filters or live_conditions or title_ids or explicit_title or explicit_id is not None):
        raise ValueError("演出数值请搭配乐队、角色等卡牌条件，或写成 演出150。")
    if explicit_id is not None:
        add("title_ids", explicit_id)
    return CardRequest(support=support, mode="detail" if explicit_id is not None else "list", page=page, query=query,
                       filters=CardFilters(**{key: tuple(dict.fromkeys(values)) for key, values in filters.items()}),
                       matched_title_ids=title_ids, live_conditions=tuple(dict.fromkeys(live_conditions)),
                       live_score_ranges=tuple(dict.fromkeys(score_ranges)))


def _card_title_matches(query, spans, repository, *, support, explicit, quoted):
    contiguous = not any(_WORD.search(query, left[1], right[0]) for left, right in zip(spans, spans[1:]))
    title = query[spans[0][0]:spans[-1][1]]
    if quoted and len(spans) != 1:
        raise ValueError("请只指定一段连续卡名，可用 卡名=明确指定。")
    if quoted:
        title = _QUOTED_TITLE.fullmatch(title)[1]
    if contiguous:
        exact = _exact_card_ids(title, repository, support=support)
        if exact:
            return tuple(exact)
    if not explicit and len(spans) > 1:
        # Keep legacy multi-select of complete names. Only one fragment is allowed.
        parts = [_exact_card_ids(query[start:end], repository, support=support) for start, end in spans]
        if all(parts):
            return tuple(dict.fromkeys(card_id for ids in parts for card_id in ids))
    if not contiguous:
        raise ValueError("请只指定一段连续卡名，可用 卡名=明确指定。")
    needle = normalize(title)
    if not needle:
        raise ValueError("请提供卡名或一段连续的卡名。")
    source = repository.support_cards if support else repository.cards
    return tuple(card.id for card in source
                 if any(needle in normalize(name) for name in _card_titles(card)))


def parse_card_request(query, repository, *, support=False, art=False, direct_command=False):
    from ..commands import _split_page
    full_query = unicodedata.normalize("NFKC", query).strip()
    direct_command = direct_command and not art
    query, page = _split_page(full_query)
    if direct_command and query != full_query and len(full_query) <= 512:
        # A page-shaped suffix can belong to a complete title after other filters.
        parts = list(_card_parts(full_query, repository, support=support))
        start, end, kind, value = parts[-1]
        explicit_name = len(parts) > 1 and parts[-2][2:] == ("field", "卡名")
        if (end == len(full_query) and kind == "value"
                and (start < len(query) or start == 0 or explicit_name)
                and _exact_card_ids(value, repository, support=support)):
            query, page = full_query, 1
    if len(query) > 512:
        raise ValueError("筛选条件过长，请精简到512字以内。")
    if page < 1:
        raise ValueError("页码必须从1开始。")
    ident = re.fullmatch(r"(?:ID\s*[:=]?\s*)?(\d+)", query, re.I)
    if ident:
        return CardRequest(support, "art" if art else "detail", int(ident[1]), page, query)
    if art:
        raise ValueError("只输出卡面需要明确卡牌ID；请先用 /查卡 或 /查留影卡 按条件查列表。")
    if direct_command:
        return _parse_direct_filters(query, page, repository, support=support)
    filters = {}
    explicit_id = None
    def add(key, value):
        filters.setdefault(key, []).append(value)
    fields = list(re.finditer(r"([^\s=：:]+)\s*[=：:]\s*", query))
    bare = query[:fields[0].start()].strip() if fields else query
    # Preserve catalog names containing spaces when combined with bare rarity aliases.
    names = {n for c in (*repository.cards, *repository.support_cards)
             for n in (c.title, c.character, getattr(c, "band", ""),
                       *(v for group in c.localized.values() for v in group.values())) if " " in n}
    chunks, remaining = [], bare
    while remaining:
        matched = next((n for n in sorted(names, key=len, reverse=True)
                        if re.match(re.escape(n) + r"(?=\s|$)", remaining, re.I)), None)
        if matched:
            chunks.append((None, remaining[:len(matched)]))
            remaining = remaining[len(matched):].strip()
        else:
            parts = remaining.split(None, 1)
            chunks.append((None, parts[0]))
            remaining = parts[1] if len(parts) > 1 else ""
    if bare:
        try:
            _entity(bare, None, repository)
            chunks = [(None, bare)]
        except ValueError:
            source = repository.support_cards if support else repository.cards
            if any(normalize(bare) in {normalize(c.title), *(normalize(n) for n in c.localized.get("title", {}).values())} for c in source):
                chunks = [(None, bare)]
    for i, match in enumerate(fields):
        key = _FIELDS.get(match[1].lower())
        if not key:
            raise ValueError(f"未知筛选维度“{match[1]}”；可用：稀有度、颜色、乐队、角色、LIVE、击奏、得意。")
        value = query[match.end():fields[i + 1].start() if i + 1 < len(fields) else len(query)].strip()
        if not value:
            raise ValueError(f"{match[1]}缺少筛选值。")
        chunks.append((key, value))
    for key, values in chunks:
        for value in re.split(r"[,，、|/]|或", values):
            value = value.strip()
            if value in _UNRESTRICTED:
                continue
            dimension = key or _bare_filter(value, legacy=True)
            if dimension is not None:
                add(*_card_filter(value, dimension, repository, support=support))
                continue
            if value.isdecimal():
                if explicit_id is not None:
                    raise ValueError("详情查询只能指定一个卡牌ID。")
                explicit_id = int(value)
                add("title_ids", explicit_id)
                continue
            try:
                add(*_entity(value, None, repository))
            except ValueError:
                exact = _exact_card_ids(value, repository, support=support)
                if not exact:
                    raise
                for card_id in exact:
                    add("title_ids", card_id)
    return CardRequest(support, mode="detail" if explicit_id is not None else "list", page=page,
                       query=query, filters=CardFilters(**{k: tuple(dict.fromkeys(v)) for k, v in filters.items()}))


def _matching_score_bands(bands, req):
    return tuple(dict.fromkeys(
        band["value"] for band in bands
        if (not req.live_conditions or band["condition"] in req.live_conditions)
        and (not req.live_score_ranges or any(
            (low is None or low <= band["value"]) and (high is None or band["value"] <= high)
            for low, high in req.live_score_ranges
        ))
    ))


def execute_card_request(req, repository):
    catalog = repository.capture_catalog()
    cache_state = catalog.cache_state
    if req.filters.character_ids:
        if catalog.metadata.get('info_catalog_available', {}).get('characters') is not True:
            return CardAnswer(req, error="角色 ID 目录暂不可用，请等待资料同步。", status="data_unavailable",
                              cache_state=cache_state)
        unknown_ids = set(req.filters.character_ids) - {person.id for person in catalog.characters}
        if unknown_ids:
            ids = "、".join(str(ident) for ident in sorted(unknown_ids))
            return CardAnswer(req, error=f"未找到角色 ID {ids}。", status="unknown_entity", cache_state=cache_state)
    cards = catalog.support_cards if req.support else catalog.cards
    if req.card_id is not None:
        matches = [c for c in cards if c.id == req.card_id]
        if len(matches) > 1:
            return CardAnswer(req, error="卡牌 ID 映射冲突，请等待数据核对。", status="ambiguous", cache_state=cache_state)
        if not matches:
            return CardAnswer(req, error=f"未找到{'SNAP' if req.support else '角色卡'} ID {req.card_id}，没有改查其他对象。", status="unknown_entity", cache_state=cache_state)
        if req.mode == "art":
            return CardAnswer(req, (matches[0],), cache_state=cache_state)
        detail = repository.support_card_with_detail if req.support else repository.card_with_detail
        return CardAnswer(req, (detail(matches[0]),), cache_state=cache_state)
    if req.matched_title_ids is not None:
        matching_ids = set(req.matched_title_ids)
        cards = [card for card in cards if card.id in matching_ids]
    if req.has_live_details and req.filters.live and "score" not in req.filters.live:
        return CardAnswer(req, cache_state=cache_state)
    selected = []
    missing = set()
    for card in {c.id: c for c in cards}.values():
        keep = True
        card_missing = set()
        for key, values in req.filters.conditions():
            if key == "live" and req.has_live_details:
                continue
            unknown = False
            if key == "rarity":
                match = card.rarity in values
            elif key == "type":
                match = card.card_type in values
            elif key == "title_ids":
                match = card.id in values
            elif key == "character":
                if card.catalog.get("character_links_complete") is False:
                    card_missing.add("角色关联")
                    unknown = True
                names = card.characters if isinstance(card, SupportCard) else (card.character,)
                match = bool({normalize(character_identity(n)) for n in names} & {normalize(character_identity(n)) for n in values})
            elif key == "character_ids":
                # Haneoka validates these source IDs independently of localized names.
                ids = card.catalog.get("character_ids")
                if not ids:
                    card_missing.add("角色 ID 关联")
                    unknown = True
                match = bool(ids and set(ids).intersection(values))
            elif key == "band":
                bands = card.catalog.get("bands", []) if isinstance(card, SupportCard) else [card.band]
                if not bands or "未知乐队" in bands or card.catalog.get("character_links_complete") is False:
                    card_missing.add("乐队关联")
                    unknown = True
                match = bool({normalize(n) for n in bands} & {normalize(n) for n in values})
            elif key == "tags":
                if "tags" not in card.catalog or card.catalog.get("tags_complete") is False:
                    card_missing.add("得意乐曲")
                    unknown = True
                match = set(values) <= set(card.catalog.get("tags", {}))
            elif key == "leader":
                leader_type = card.catalog.get("member_leader_type")
                if not isinstance(leader_type, str) or (leader_type and leader_type not in _LEADER_TYPES):
                    card_missing.add("队长技能分类")
                    unknown = True
                match = leader_type in values
            else:
                actual = card.catalog.get("categories", {}).get(key, [])
                if not actual or None in actual:
                    card_missing.add("LIVE/击奏分类")
                    unknown = True
                match = bool(set(actual) & set(values))
                if key == "gekisou" and _JUST_WINDOW in values and "JUST" in actual and not match:
                    expanded = card.catalog.get(_JUST_WINDOW)
                    match = expanded is True
                    if expanded is None:
                        card_missing.add("JUST 扩判效果")
                        unknown = True
            # Unknown is not a proven mismatch; evaluate every dimension before
            # deciding whether this card could still belong to the result.
            if not match and not unknown:
                keep = False
        if keep and req.has_live_details:
            bands = card.catalog.get("live_score_bands")
            if bands is None:
                card_missing.add("演出技能数值与条件")
            elif not _matching_score_bands(bands, req):
                keep = False
        if keep:
            if card_missing:
                missing.update(card_missing)
            else:
                selected.append(card)
    if missing:
        return CardAnswer(req, error="卡牌索引不完整（" + "、".join(sorted(missing)) + "），不能保证完整筛选；请等待数据同步后重试，未将未知分类当作无技能。", status="data_unavailable", cache_state=cache_state)
    if req.mode == "detail" and selected:
        detail = repository.support_card_with_detail if req.support else repository.card_with_detail
        selected = [detail(selected[0])]
    return CardAnswer(req, tuple(sorted(selected, key=lambda c: (-c.rarity, c.id))), cache_state=cache_state)


def query_cards(query, repository, *, support=False, art=False, direct_command=False):
    try:
        req = parse_card_request(query, repository, support=support, art=art, direct_command=direct_command)
        return execute_card_request(req, repository)
    except ValueError as exc:
        return CardAnswer(CardRequest(support, query=query), error=str(exc),
                          status="ambiguous" if isinstance(exc, CharacterAmbiguity) else "invalid_arguments")


def detail_text(card, locale="zh"):
    support = isinstance(card, SupportCard)
    live, gek = labels(card)
    bands = card.catalog.get("bands", []) if support else [localized_text(card, "band", locale)]
    lines = [f"{'留影卡详情' if support else '成员卡详情'} · ID {card.id}",
             localized_text(card, "title", locale), localized_text(card, "character", locale),
             "乐队：" + (" / ".join(bands) or "关联未获取"),
             f"{rarity_name(card)} · {TYPES.get(card.card_type, f'未知类型({card.card_type})')}",
             f"LIVE{'支援' if support else '技能'}：{live} · 击奏{'支援' if support else '技能'}：{gek}"]
    stats = tuple(v / 100 if support else v for v in (card.performance, card.technic, card.visual))
    state = "满级基础值"
    if card.catalog.get("source") == "haneoka" or any(stats):
        unit = "%" if support else ""
        lines.append(f"资料展示状态：{state}（非玩家培养状态）")
        lines.append(" / ".join(f"{name} {value:g}{unit}" for name, value in zip(("表演", "技巧", "形象"), stats)))
    else:
        lines.append("数值状态未核实，暂不展示培养数值。")
    if not support:
        tags = card.catalog.get("tags")
        lines.append("得意乐曲：" + ("映射未确认" if card.catalog.get("tags_complete") is False else
                                    " / ".join(n.get(locale) or n.get("ja") or k for k, n in tags.items()) if tags else "标签未获取"))
    names = {"leaderSkill": "队长技能", "liveSkill": "LIVE技能", "gekisouSkill": "击奏技能",
             "supportSkill": "LIVE支援", "gekisouSupportSkill": "击奏支援"}
    for index, skill in enumerate(card.skills):
        lang = card.catalog.get("skill_languages", {}).get(skill.kind, "未知")
        lines += [f"{names.get(skill.kind, skill.kind)} · {localized_text(skill, 'name', locale)}",
                  "效果（Lv.5；来源语言 " + lang + "）：" + (localized_text(skill, "description", locale) or "详情未获取")]
        levels = card.catalog.get("skill_levels", [])
        if index < len(levels):
            lines.extend(f"Lv.{i + 1} " + (d.get(locale) or d.get("ja") or "未知")
                         for i, d in enumerate(levels[index]))
    for relation in card.catalog.get("relations", []):
        lines.append(relation["kind"] + " ID " + str(relation["id"]) + " " +
                     (relation["name"].get(locale) or relation["name"].get("ja") or "未知"))
    if not card.skills:
        lines.append("技能详情暂不可用；不代表无技能。")
    source = "Haneoka JP" if card.catalog.get("source") == "haneoka" else "Project Yume"
    lines.append(source + " 公开资料 · 卡面为公开 full 版本")
    if card.catalog.get("detail_stale"):
        lines.append("详情使用上次有效缓存。")
    return "\n".join(lines)


def catalog_notice(cards):
    """Status retained by every complete-list image and text fallback."""
    notices = []
    if any(c.catalog.get("detail_stale") for c in cards):
        notices.append("部分详情使用上次有效缓存。")
    if any(not c.catalog.get("detail_loaded") for c in cards):
        notices.append("部分技能资料未获取，未知分类不会当作无技能。")
    return "\n" + "\n".join(notices) if notices else ""

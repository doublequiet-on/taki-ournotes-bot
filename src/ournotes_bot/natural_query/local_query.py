# L3
# Input: 自然语言问题与 SongRepository 中的实体记录。
# Output: QuerySpec | str | None：可信条件、确定性澄清／拒绝提示，或交回上层继续解析。
# Pos: Query / Natural 的零模型本地解析与能力分流；见 L2-2.md。
# Effects/Dependencies: 不调用模型、无直接网络；经实体／别名解析可读取本地别名 JSON。

"""Deterministic, zero-AI parsing for common /问 queries."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from ..commands import CARD_RARITY_HELP, _split_page, split_card_rarity
from ..data import SongRepository
from ..query.entity_lexicon import find_anchor, resolve_entity
from .query_terms import SKILL_CLARIFICATION, is_skill_placeholder
from .query_validation import AMBIGUOUS_ENTITY, UNKNOWN_ENTITY
from ..query.song_conditions import (
    DIFFICULTY_NAMES as _DIFFICULTY_NAMES,
    DIFFICULTY_PATTERN as _DIFFICULTY,
    LEVEL_PHRASES as _LEVEL_PHRASES,
    extract_song_conditions,
    remove_song_conditions,
)
from ..structured_query import QuerySpec


_SONG_FILLERS = re.compile(
    r"查一下|找一下|都有哪些|有哪些|有什么|多少首|帮我|给我|请|查询|查找|列出|看看|想看|想要|"
    r"歌曲|乐曲|曲目|谱面|难度|等级|歌|的|中|里|所有|全部|是|吗|呢|查|找|songs?",
    re.I,
)
_ENTITY_QUESTIONS = (
    ("song", re.compile(r"^(?P<term>.+?)\s*的?\s*(?:歌曲|曲目|歌)\s*(?:都?有(?:哪些|什么)|列表)?[?？]?$")),
    ("song", re.compile(r"^(?P<term>.+?)\s*有(?:哪些|什么)\s*(?:歌曲|曲目|歌)[?？]?$")),
    ("support_card", re.compile(r"^(?P<term>.+?)\s*(?:号)?\s*的?\s*(?:支援卡|支援卡牌)\s*(?:详情|都?有(?:哪些|什么)|列表)?[?？]?$")),
    ("support_card", re.compile(r"^(?P<term>.+?)\s*有(?:哪些|什么)\s*(?:支援卡|支援卡牌)[?？]?$")),
    ("card", re.compile(r"^(?P<term>.+?)\s*的?\s*(?:成员卡|角色卡|卡牌|卡面|(?<!支援)卡)\s*(?:都?有(?:哪些|什么)|列表)?[?？]?$")),
    ("card", re.compile(r"^(?P<term>.+?)\s*有(?:哪些|什么)\s*(?:成员卡|角色卡|卡牌|卡面|(?<!支援)卡)[?？]?$")),
)
_ALL_SUPPORT_CARDS = re.compile(r"^(?:所有|全部)?\s*(?:支援卡|支援卡牌)\s*(?:都?有(?:哪些|什么)|列表)?[?？]?$", re.I)
_SKILL_QUESTIONS = (
    re.compile(r"^(?:(?P<term>.+?)\s*的\s*)?(?P<skill>.+?)技能(?:所属|对应)?的?\s*(?:成员卡|角色卡|卡牌|卡)\s*(?:都?有(?:哪些|什么)|列表)?[?？]?$", re.I),
    re.compile(r"^(?:有哪些|哪些)\s*(?:(?P<term>.+?)\s*的\s*)?(?:成员卡|角色卡|卡牌|卡)\s*(?:有|带有|包含)\s*(?P<skill>.+?)技能[?？]?$", re.I),
    re.compile(r"^(?P<kind>队长|leader|live|激奏)技能(?:名称|效果)?\s*(?:包含|带有|有|是)\s*(?P<skill>.+?)\s*的?\s*(?:成员卡|角色卡|卡牌|卡)[?？]?$", re.I),
    re.compile(r"^(?:技能|技能效果)\s*(?:包含|带有|有|是)\s*(?P<skill>.+?)\s*的?\s*(?:成员卡|角色卡|卡牌|卡)[?？]?$", re.I),
)


def local_song_filter(query: str, repository: SongRepository) -> QuerySpec | None:
    text = unicodedata.normalize("NFKC", query).strip()
    page_match = re.search(r"(?:第\s*(\d+)\s*页|页\s*(\d+))\s*$", text)
    page = int(next(part for part in page_match.groups() if part)) if page_match else 1
    if not 1 <= page <= 100:
        return None
    if page_match:
        text = text[:page_match.start()].strip()
    evidence = extract_song_conditions(text)
    if evidence.conflict or not evidence.has_level:
        return None
    operator = evidence.comparison
    level = evidence.level
    assert level is not None
    if not 1 <= level <= 40:
        return None
    difficulty = evidence.difficulty
    remainder = remove_song_conditions(text, evidence)
    term = _SONG_FILLERS.sub("", remainder).strip(" \t，,。？?！!、·・")
    subject = None
    if term:
        if resolve_entity("song", term, repository) is None:
            return None
        anchor = find_anchor("song", term, repository)
        if anchor.entity is None:
            return None
        subject = anchor.entity
    return QuerySpec("song", subject, difficulty, operator, level, page, term)


def local_entity_question(query: str, repository: SongRepository) -> QuerySpec | None:
    text = unicodedata.normalize("NFKC", query).strip()
    page_match = re.search(r"(?:第\s*(\d+)\s*页|页\s*(\d+))\s*$", text)
    page = int(next(part for part in page_match.groups() if part)) if page_match else 1
    if not 1 <= page <= 100:
        return None
    if page_match:
        text = text[:page_match.start()].strip()
    if _ALL_SUPPORT_CARDS.fullmatch(text):
        return QuerySpec("support_card", page=page, display_name="全部支援卡")
    for intent, pattern in _ENTITY_QUESTIONS:
        match = pattern.fullmatch(text)
        if match:
            term = re.sub(r"号$", "", match.group("term").strip())
            if resolve_entity(intent, term, repository) is None:
                continue
            anchor = find_anchor(intent, term, repository)
            if anchor.entity and not anchor.ambiguous:
                return QuerySpec(intent, anchor.entity, page=page,
                                 display_name=str(anchor.entity.value))
    return None


def local_skill_question(query: str, repository: SongRepository) -> QuerySpec | str | None:
    text = unicodedata.normalize("NFKC", query).strip()
    page_match = re.search(r"(?:第\s*(\d+)\s*页|页\s*(\d+))\s*$", text)
    page = int(next(part for part in page_match.groups() if part)) if page_match else 1
    if not 1 <= page <= 100:
        return None
    if page_match:
        text = text[:page_match.start()].strip()
    kinds = {"队长": "leader", "leader": "leader", "live": "live", "激奏": "gekisou"}
    for pattern in _SKILL_QUESTIONS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        skill = match.group("skill").strip(" \t，,。？?！!、·・“”\"'")
        kind = kinds.get((match.groupdict().get("kind") or "").casefold(), "")
        if not kind:
            suffix = re.search(r"(?:的)?(队长|leader|live|激奏)$", skill, re.I)
            if suffix:
                kind = kinds[suffix.group(1).casefold()]
                skill = skill[:suffix.start()].rstrip("的 ")
        if is_skill_placeholder(skill):
            return SKILL_CLARIFICATION
        if not ((1 <= len(skill) <= 30) or (kind and not skill)):
            continue
        subject = None
        display = ""
        term = (match.groupdict().get("term") or "").strip()
        if term:
            if resolve_entity("card", term, repository) is None:
                continue
            anchor = find_anchor("card", term, repository)
            if anchor.entity is None or anchor.ambiguous:
                continue
            subject = anchor.entity
            display = str(subject.value)
        return QuerySpec("card", subject, page=page, display_name=display,
                         skill_query=skill, skill_kind=kind)
    return None


def local_card_rarity_question(query: str, repository: SongRepository) -> QuerySpec | str | None:
    try:
        text, rarity = split_card_rarity(query)
    except ValueError:
        return CARD_RARITY_HELP["zh"]
    if rarity is None:
        return None
    text, page = _split_page(text)
    if not 1 <= page <= 100:
        return "页码应在 1～100 之间。"
    skill_spec = local_skill_question(text, repository)
    if isinstance(skill_spec, str):
        return skill_spec
    if skill_spec is not None:
        return replace(skill_spec, rarity=rarity, page=page)
    text = re.sub(
        r"^(?:(?:有什么样的|有哪些|哪些|有什么|有啥|请|帮我|给我|查询|查看|查一下|找一下|列出|看看|想看|只看|查|找|看|有没有|全部|所有)\s*)+",
        "", text,
    ).strip(" 的，,。？?！!")
    spec = local_entity_question(text, repository) or local_skill_question(text, repository)
    if isinstance(spec, str):
        return spec
    if spec and spec.intent in {"card", "support_card"}:
        return replace(spec, rarity=rarity, page=page)
    if re.fullmatch(r"(?:的\s*)?(?:成员卡|角色卡|卡牌|卡面|卡)?\s*(?:都?有(?:哪些|什么)|列表)?", text):
        return QuerySpec("card", rarity=rarity, page=page)
    if "技能" in text or "支援" in text:
        return UNKNOWN_ENTITY
    term = re.sub(r"\s*(?:的\s*)?(?:成员卡|角色卡|卡牌|卡面|卡)\s*(?:都?有(?:哪些|什么)|列表)?$", "", text).strip()
    anchor = find_anchor("card", term, repository)
    if anchor.ambiguous:
        return AMBIGUOUS_ENTITY
    if resolve_entity("card", term, repository) is None or anchor.entity is None:
        return UNKNOWN_ENTITY
    return QuerySpec("card", anchor.entity, rarity=rarity, page=page,
                     display_name=str(anchor.entity.value))


def parse_local_query(query: str, repository: SongRepository) -> QuerySpec | str | None:
    from ..query.event_cutoff_query import parse_natural_cutoff
    cutoff = parse_natural_cutoff(query)
    if cutoff is not None:
        return QuerySpec("event_cutoff", cutoff_request=cutoff)
    from ..query.field_query import parse_field_question
    field = parse_field_question(query, repository)
    if field is not None:
        return field
    from ..query.song_query import local_query as local_song_traits
    song = local_song_traits(query, repository)
    if song is not None:
        return song
    catalog = local_card_catalog(query, repository)
    if catalog is not None:
        return catalog
    from ..query.efficiency_query import parse_efficiency
    efficiency = parse_efficiency(query, repository)
    if efficiency is not None:
        return efficiency
    rarity = local_card_rarity_question(query, repository)
    if rarity is not None:
        return rarity
    return (local_song_filter(query, repository)
            or local_entity_question(query, repository)
            or local_skill_question(query, repository))


def local_card_catalog(query: str, repository: SongRepository) -> QuerySpec | str | None:
    """Explicit catalog dimensions use the same parser as direct commands, without AI."""
    from ..query.card_catalog import parse_card_request
    text = unicodedata.normalize("NFKC", query).strip()
    generic_id = re.fullmatch(r"(?:查一下|查询|查看|查)?\s*(?:卡牌?\s*)?(?:ID\s*[:=]?\s*)?(\d+)(?:号卡(?:牌)?|卡(?:牌)?(?:详情)?)?", text, re.I)
    if generic_id:
        number = int(generic_id[1])
        member = any(c.id == number for c in repository.cards)
        support_id = any(c.id == number for c in repository.support_cards)
        if member and support_id:
            return f"ID {number} 同时存在于角色卡与 SNAP，请指定 /查卡 {number} 或 /查支援卡 {number}。"
    obj = re.search(r"SNAP|支援卡|角色卡|成员卡|卡牌|卡", text, re.I)
    if not obj or (obj[0].lower() != "snap" and not re.search(r"[=：:]|[红蓝绿黄紫黑白橙青灰]色|JUST|COMBO|LUCK|LIVE\s*(?:分数提升|LIFE回复|判定强化|技能延长)|得意|(?<![A-Za-z])(?:BD|EX)(?![A-Za-z])|(?:SSR|SR|R)[,，或 /]+(?:SSR|SR|R)", text, re.I)):
        return None
    support = obj[0].lower() in {"snap", "支援卡"}
    text = text[:obj.start()] + " " + text[obj.end():]
    text = re.sub(r"^(?:(?:请|帮我|给我|查一下|查询|查看|找一下|列出|看看|想看|查|找)\s*)+", "", text)
    text = re.sub(r"(?:有哪些|有哪几张|列表|所有|的|[？?。])", " ", text).strip()
    # Split common unspaced Chinese colour/rarity phrases before field parsing.
    if not re.search(r"[=：:]", text):
        text = re.sub(r"([红蓝绿黄紫]色)", r" \1 ", text)
        text = re.sub(r"(?<![A-Za-z])(SSR|SR|R|BD|EX)(?![A-Za-z])", r" \1 ", text, flags=re.I)
    text = re.sub(r"(?i)(LIVE)\s*(?![=：:])(?=分数提升|LIFE回复|判定强化|技能延长)", r"LIVE=", text)
    text = re.sub(r"(?:击奏|激奏)\s*(?![=：:])(?=JUST|COMBO|LUCK)", "击奏=", text, flags=re.I)
    try:
        req = parse_card_request(text, repository, support=support)
    except ValueError as exc:
        return str(exc)
    return QuerySpec("support_card" if support else "card", page=req.page, card_query=text)


def is_explicit_empty_subject_query(query: str, intent: str,
                                    repository: SongRepository) -> bool:
    """Return whether deterministic parsing proves the user requested all records.

    A missing catalog anchor is not enough: it may mean the user supplied an
    unknown entity. Only the same strict local grammar used for zero-AI queries
    may authorize a subject-less model action.
    """
    parsed = parse_local_query(query, repository)
    if (isinstance(parsed, QuerySpec) and parsed.intent == intent
            and parsed.subject is None):
        return True

    # Some legitimate skill phrasings deliberately need the model, but their
    # syntax can still prove that "成员卡" is the collection being searched
    # rather than an unknown character/card name. Keep this check structural:
    # unknown subjects such as "不存在角色的……成员卡" do not start this way.
    text = unicodedata.normalize("NFKC", query).strip()
    text = re.sub(
        r"^(?:(?:请|帮我|给我|查询|查看|查一下|找一下|列出|看看|想看|查|找)\s*)+",
        "", text,
    ).strip(" ，,。？?！!")
    if intent == "card" and "技能" in text:
        return bool(re.match(
            r"^(?:有哪些|哪些)\s*(?:成员卡|角色卡|卡牌|卡)\s*的?\s*(?:技能|技能效果)",
            text, re.I,
        ))
    return False

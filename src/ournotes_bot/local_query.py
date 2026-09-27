"""Deterministic, zero-AI parsing for common /问 queries."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from .commands import CARD_RARITY_HELP, _split_page, split_card_rarity
from .data import SongRepository
from .entity_lexicon import find_anchor, resolve_entity
from .query_validation import AMBIGUOUS_ENTITY, UNKNOWN_ENTITY
from .structured_query import QuerySpec


_LEVEL = r"(?:lv\.?\s*)?(?P<level>\d+(?:\.\d+)?)\s*级?"
_LEVEL_PHRASES = (
    (re.compile(_LEVEL + r"\s*(?:及以上|或以上|以上|起|或更高)", re.I), ">="),
    (re.compile(_LEVEL + r"\s*(?:及以下|或以下|以下|以内|或更低)", re.I), "<="),
    (re.compile(r"(?:不低于|不小于|不少于|至少|起码)\s*" + _LEVEL, re.I), ">="),
    (re.compile(r"(?<!不)(?:高于|超过|大于|多于)\s*" + _LEVEL, re.I), ">"),
    (re.compile(r"(?:不超过|不高于|不大于|至多|最多)\s*" + _LEVEL, re.I), "<="),
    (re.compile(r"(?<!不)(?:低于|小于|少于|不到|不足|未满)\s*" + _LEVEL, re.I), "<"),
    (re.compile(r"(?:>=|≥)\s*" + _LEVEL, re.I), ">="),
    (re.compile(r"(?:<=|≤)\s*" + _LEVEL, re.I), "<="),
    (re.compile(r"(?<![<>=])>\s*" + _LEVEL, re.I), ">"),
    (re.compile(r"(?<![<>=])<\s*" + _LEVEL, re.I), "<"),
)
_DIFFICULTY = re.compile(
    r"(?<![A-Za-z])(?:EXPERT|EXP|EX|HARD|HD|NORMAL|NM|EASY|EZ)(?![A-Za-z])"
    r"|(?:专家|困难|普通|简单)(?:难度)?", re.I,
)
_DIFFICULTY_NAMES = {
    "EXPERT": "EXPERT", "EXP": "EXPERT", "EX": "EXPERT", "专家": "EXPERT",
    "HARD": "HARD", "HD": "HARD", "困难": "HARD",
    "NORMAL": "NORMAL", "NM": "NORMAL", "普通": "NORMAL",
    "EASY": "EASY", "EZ": "EASY", "简单": "EASY",
}
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
    found = [(match.start(), match, operator) for pattern, operator in _LEVEL_PHRASES
             if (match := pattern.search(text))]
    if len(found) != 1:
        return None
    _, match, operator = found[0]
    level = float(match.group("level"))
    if not 1 <= level <= 40:
        return None
    remainder = text[:match.start()] + " " + text[match.end():]
    difficulties = list(_DIFFICULTY.finditer(remainder))
    if len(difficulties) > 1:
        return None
    difficulty = ""
    if difficulties:
        item = difficulties[0]
        difficulty = _DIFFICULTY_NAMES[re.sub(r"难度$", "", item.group().upper())]
        remainder = remainder[:item.start()] + " " + remainder[item.end():]
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


def local_skill_question(query: str, repository: SongRepository) -> QuerySpec | None:
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
    text = re.sub(
        r"^(?:(?:请|帮我|给我|查询|查看|查一下|找一下|列出|看看|想看|只看|查|找|看|有没有|全部|所有)\s*)+",
        "", text,
    ).strip(" 的，,。？?！!")
    spec = local_entity_question(text, repository) or local_skill_question(text, repository)
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
    rarity = local_card_rarity_question(query, repository)
    if rarity is not None:
        return rarity
    return (local_song_filter(query, repository)
            or local_entity_question(query, repository)
            or local_skill_question(query, repository))


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

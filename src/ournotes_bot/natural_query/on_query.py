# L3
# Input: ON question text and current repository identities.
# Output: Bounded local QuerySpec / diagnostic or an information command.
# Pos: Natural Query; shared lexical evidence for model validation, see L2-2.md.
# Effects: Reads local aliases; no model, source refresh or platform calls.
"""ON vocabulary mapped to the existing deterministic query contracts."""
from __future__ import annotations

import re
import unicodedata

from ..commands import split_page
from ..query.entity_lexicon import known_alias_names, resolve_entity, find_anchor
from ..query.song_identity import QueryProblem
from ..query.song_query import parse_filter
from ..query.song_conditions import extract_song_conditions, remove_song_conditions
from ..query.card_catalog import parse_card_request
from ..structured_query import QuerySpec


def protected_text(question, repository):
    text = unicodedata.normalize("NFKC", question).strip()
    names = set(known_alias_names(repository))
    for row in (*repository.songs, *repository.cards, *repository.support_cards):
        names.update((row.title, getattr(row, "band", ""), getattr(row, "character", "")))
        names.update(n for values in row.localized.values() for n in values.values())
    names.discard("")
    # Longest full names first, including adjacent Chinese grammar. English
    # fragments must not match inside unrelated words. Restore exact text later.
    saved = {}
    color_words = tuple(re.finditer(r"颜色|顏色|属性|屬性|[红紅蓝藍绿綠黄紫黑白橙青灰]色", text))
    candidates = sorted({unicodedata.normalize("NFKC", n) for n in names}, key=lambda n: (-len(n), n))
    if candidates:
        pattern = "|".join((r"(?<![A-Za-z])" if n[0].isascii() and n[0].isalpha() else "")
                           + re.escape(n) + (r"(?![A-Za-z])" if n[-1].isascii() and n[-1].isalpha() else "") for n in candidates)
        def keep(match):
            # A short entity such as the song 颜 must not consume part of 颜色.
            # Complete names (including titles containing color words) still win.
            if any(word.start() <= match.start() and match.end() <= word.end()
                   and match.end() - match.start() < word.end() - word.start()
                   for word in color_words):
                return match[0]
            index = len(saved)
            token = f"ZZENTITY{index}ZZ"
            while token.casefold() in text.casefold() or token in saved:
                index += 1
                token = f"ZZENTITY{index}ZZ"
            saved[token] = match[0]
            return token
        text = re.sub(pattern, keep, text, flags=re.I)
    replacements = {"請": "请", "國服": "国服", "臺服": "台服", "韓服": "韩服", "國際服": "国际服", "分類": "分类", "成員": "成员", "樂隊": "乐队", "樂曲": "乐曲", "藍色": "蓝色", "綠色": "绿色", "紅色": "红色",
                    "譜面": "谱面", "等級": "等级", "擊奏": "击奏", "純": "纯", "歷史": "历史", "活動": "活动",
                    "資料": "资料", "詳情": "详情", "查詢": "查询", "幫我": "帮我", "顏色": "颜色", "屬性": "属性",
                    "回復": "回复", "強化": "强化", "延長": "延长", "頁": "页", "級": "级",
                    "什麼": "什么", "簡介": "简介", "幾": "几", "為": "为"}
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text, saved


def restore(text, saved):
    for token, name in saved.items():
        text = text.replace(token, name)
    return text.strip()


def trim(text):
    text = re.sub(r"^(?:(?:请|帮我|给我|麻烦|查一下|找一下|查询|查看|查找|列出|看看|想看|只看|查|找|看)\s*)+", "", text)
    text = re.sub(r"(?:都有哪些|有哪些|有什么|有啥|有哪几首|有哪几张|哪些|所有|(?<!包含)全部|列表|的|[？?。！!])", " ", text)
    return text.strip()


def color_conditions(text):
    """Normalize color grammar after complete entity names have been protected."""
    text = re.sub(r"([红蓝绿黄紫黑白橙青灰])(?:色)?属性", r"\1色", text)
    text = re.sub(r"(?:颜色|属性)(?:是|为)?\s*(?:[=：:]\s*)?", "颜色=", text)
    text = re.sub(r"([红蓝绿黄紫黑白橙青灰])(?=卡|歌曲|歌|成员|角色|SNAP|留影|支援)", r"\1色", text, flags=re.I)
    return text


def information_command(question, repository):
    text, saved = protected_text(question, repository)
    if re.search(r"[红蓝绿黄紫黑白橙青灰]色|颜色|属性", color_conditions(text)):
        return None  # Colored members refer to cards, not character profiles.
    text = re.sub(r"^(?:(?:请|帮我|查一下|查询|查看|看看|想看)\s*)+", "", text).strip(" ？?。！!")
    calendar = re.fullmatch(
        r"(?:(jp|intl|tw|en|日服|台服|国际服|英服)\s*)?"
        r"(现在|当前|最近|这期|本期|这次|下期|下一期|下次|下一次|下一个)(?:的)?\s*活动\s*"
        r"((?:啥时候|什么时候|何时|几号)(?:开始|结束)|(?:开始|结束)(?:时间|日期)(?:是什么|是啥|是几号)?|"
        r"结束了|结束|还在|还在进行|还没结束)(?:啊|呀|呢|吗|嘛|吧)?", text, re.I)
    if calendar:
        region, which, request = calendar.groups()
        if which in {'下期', '下一期', '下次', '下一次', '下一个'} and '开始' not in request:
            return None
        mode = '下期' if which in {'下期', '下一期', '下次', '下一次', '下一个'} else '日程开始' if '开始' in request else '日程'
        return '/查活动 ' + (region or '') + ' ' + mode
    current = re.fullmatch(
        r"(?:(jp|hk|tw|kr|en|日服|国服|台服|韩服|英服|国际服)\s*)?"
        r"(?:现在|当前|最近|这期|本期|这次)(?:的)?\s*(活动|卡池)\s*"
        r"(?:是什么|有哪些|有什么|详情|(?:啥时候|什么时候|何时|几号)(?:开始|结束)|"
        r"(?:开始|结束)(?:时间|日期)(?:是什么|是啥|是几号)?)?(?:啊|呀|呢|吗|嘛|吧)?", text, re.I)
    if current:
        region, kind = current[1] or "", current[2]
        if kind == "卡池" and region not in {"", "jp", "日服"}:
            return None
        return "/查活动 " + region + " 当前" if kind == "活动" else "/查卡池 当前"
    listing = re.fullmatch(r"(历史)?(活动|卡池)(?:有哪些|有什么|列表)", text)
    if listing:
        return ("/查活动 " if listing[2] == "活动" else "/查卡池 ") + ("历史" if listing[1] else "列表")
    detail = re.fullmatch(r"(?:活动|卡池)\s*(.+?)\s*(?:的)?详情", text)
    if detail:
        return ("/查活动 " if text.startswith("活动") else "/查卡池 ") + restore(detail[1], saved)
    detail = re.fullmatch(r"(.+?)\s*的?\s*(活动|卡池)详情", text)
    if detail:
        return ("/查活动 " if detail[2] == "活动" else "/查卡池 ") + restore(detail[1], saved).removesuffix("的")
    if re.search(r"歌曲|谱面|SNAP|成员卡|角色卡|技能", text, re.I):
        return None
    person = re.fullmatch(r"(.+?)的?(?:生日(?:是什么|是哪天|在什么时候)?|(?:角色|人物)?(?:资料|简介|介绍))", text)
    if person:
        name = restore(person[1].removesuffix("的"), saved)
        if name:
            return "/查角色 " + name
    band = re.fullmatch(r"(.+?)的?(?:成员|角色)(?:有哪些|都有谁)", text)
    if band:
        return "/查角色 乐队=" + restore(band[1].removesuffix("的"), saved)
    if text in {"角色目录", "角色有哪些", "所有角色", "全部角色"}:
        return "/查角色"
    return None


def activity_time_field(question, repository):
    """A short reply only for an entirely grounded current-activity time question."""
    command = information_command(question, repository)
    if command and command.startswith('/查活动 ') and command.split()[-1] in {'日程', '日程开始', '下期'}:
        return 'calendar'
    if command and command.startswith('/查活动 ') and command.endswith(' 当前'):
        for word, field in (('结束', 'end'), ('开始', 'start')):
            if word in question:
                return field
    return None


def parse_on_query(question, repository, *, include_legacy=False):
    text, saved = protected_text(question, repository)
    text = color_conditions(text)
    if re.search(r"[红蓝绿黄紫黑白橙青灰]色|颜色=", text):
        text = re.sub(r"(?:角色|成员)(?=\s*(?:有哪些|都有谁|有什么|的|[?？]|$))", "成员卡", text)
    card = re.search(r"SNAP|留影卡|支援卡|成员卡|角色卡|卡牌|卡", text, re.I)
    if card:
        if "技能" in text and not re.search(r"LIVE(?:技能)?(?:分类|类型)?(?:是|为)?(?:=)?(?:分数提升|LIFE回复|判定强化|技能延长)|(?:击奏|激奏)(?:技能)?(?:分类|类型)?(?:是|为)?(?:=)?(?:JUST|COMBO|LUCK)", text, re.I):
            return None  # Preserve textual skill search and field-short answers.
        # Textual skill search stays in the existing skill grammar. These are
        # source-classified LIVE / gekisou categories, not skill-description guesses.
        trigger = re.search(r"SNAP|生日卡|[红蓝绿黄紫黑白橙青灰]色|(?<![A-Za-z])(?:BD|EX)(?![A-Za-z])|得意|LIVE|击奏|激奏|(?:SSR|SR|R)\s*(?:或|/|、)\s*(?:SSR|SR|R)|[=：:]", text, re.I)
        if not trigger and not (include_legacy and re.search(r"(?<![A-Za-z])(?:SSR|SR|R)(?![A-Za-z])", text, re.I)):
            return None
        support = card[0].casefold() in {"snap", "留影卡", "支援卡"}
        text = text[:card.start()] + " " + re.sub(r"^\s*(?:是|为)", "", text[card.end():])
        text = trim(text)
        text = re.sub(r"生日", " BD ", text)
        text = re.sub(r"(?i)(LIVE)(?:技能(?!延长))?(?:分类|类型)?(?:是|为)?\s*(?:[=：:]\s*)?", " LIVE=", text)
        text = re.sub(r"(?:激奏|击奏)(?:技能)?(?:分类|类型)?(?:是|为)?\s*(?:[=：:]\s*)?", " 击奏=", text)
        text = re.sub(r"得意(?:乐曲)?(?:标签)?(?:是|为)?\s*(?:[=：:]\s*)?", " 得意=", text)
        # Grammar-only tokens are masked above, so a title's color/rarity is intact.
        text = re.sub(r"([红蓝绿黄紫黑白橙青灰]色)", r" \1 ", text)
        text = re.sub(r"(?<![A-Za-z])(?:SSR|SR|R|BD|EX)(?![A-Za-z])", lambda m: " " + m[0] + " ", text, flags=re.I)
        text = re.sub(r"\s*(?:或|、)\s*", "/", text)
        text = re.sub(r"\s*[/,，|]\s*", "/", text)
        # Free entities and bare filters precede named dimensions. A protected
        # name after a dimension is deliberately retained as that field's value.
        text = restore(text, saved)
        try:
            req = parse_card_request(text, repository, support=support)
        except ValueError as exc:
            return QueryProblem(str(exc), "ambiguous" if "歧义" in str(exc) else "invalid_arguments")
        if not 1 <= req.page <= 100:
            return QueryProblem("页码应在 1～100 之间。")
        return QuerySpec("support_card" if support else "card", page=req.page, card_request=req)
    # Existing range-only grammar retains its contract. New exact levels and
    # colors/missions use SongFilter, including integer vs decimal equality.
    if not re.search(r"歌曲|乐曲|曲目|歌|谱面", text):
        return None
    advanced = re.search(r"[红蓝绿黄紫黑白橙青灰]色|颜色|属性|激奏|击奏|纯(?:JUST|COMBO|LUCK)|\d+(?:\.\d+)?级|lv", text, re.I)
    if not advanced:
        chart = re.fullmatch(r"(?:查一下|查询|查看|看看)?\s*(ZZENTITY\d+ZZ|\d{1,6})\s*(EXPERT|EASY|NORMAL|HARD|EX|HD|EZ|NM)?\s*的?谱面", text, re.I)
        if chart:
            from ..query.song_query import DIFFS
            subject = find_anchor("chart", restore(chart[1], saved), repository)
            if subject.ambiguous:
                return QueryProblem("匹配多个歌曲，请用 ID 选择。", "ambiguous")
            if subject.entity and subject.entity.kind == "song":
                return QuerySpec("chart", subject.entity, DIFFS.get((chart[2] or "").upper(), ""), display_name=restore(chart[1], saved))
        return None
    # Let legacy inequalities retain their QuerySpec representation unless new
    # dimensions, decimal/integer equality, or full difficulty are requested.
    if not re.search(r"[红蓝绿黄紫黑白橙青灰]色|颜色|属性|激奏|击奏|纯|全难度", text) and extract_song_conditions(text).has_level:
        return None
    text = trim(text)
    text = re.sub(r"(?:歌曲|乐曲|曲目|谱面|歌)(?:是)?", " ", text)
    text = re.sub(r"(?:激奏|击奏)(?:类型|顺序)?(?:是|为)?\s*(?:[=：:]\s*)?", " 激奏=", text)
    text = re.sub(r"激奏=同时包含", "激奏=包含全部", text)
    text = re.sub(r"(?<![=全])(?:带有|包含|带)(JUST|COMBO|LUCK)(?:\s*激奏=)?", r" 激奏=\1 ", text, flags=re.I)
    text = re.sub(r"(?<![=A-Za-z])(纯(?:JUST|COMBO|LUCK))", r" 激奏=\1 ", text, flags=re.I)
    text = re.sub(r"混合\s*激奏=", " 激奏=混合 ", text)
    text = re.sub(r"([红蓝绿黄紫黑白橙青灰]色)", r" \1 ", text)
    text = re.sub(r"\s*(?:或|、)\s*", "/", text)
    text = re.sub(r"([红蓝绿黄紫黑白橙青灰]色)\s*和\s*([红蓝绿黄紫黑白橙青灰]色)", r"\1/\2", text)
    evidence = extract_song_conditions(text, include_explicit_lv=True)
    if evidence.conflict:
        return QueryProblem("一次查询请指定一个等级条件和一个难度。")
    if evidence.has_level:
        text = remove_song_conditions(text, evidence) + f" {evidence.difficulty} lv{evidence.comparison}{evidence.level:g}"
    else:
        text = re.sub(r"(\d+(?:\.\d+)?)级", r" lv=\1 ", text)
    text, page = split_page(restore(text, saved))
    if not 1 <= page <= 100:
        return QueryProblem("页码应在 1～100 之间。")
    try:
        req = parse_filter(text, repository, force=True)
    except ValueError as exc:
        return QueryProblem(str(exc))
    if req.term and find_anchor("song", req.term, repository).ambiguous:
        return QueryProblem("匹配多个歌曲，请使用完整名称或 ID。", "ambiguous")
    if req.term and resolve_entity("song", req.term, repository) is None:
        return QueryProblem("当前数据中无法确认这个查询对象，请用完整名称或 ID。", "unknown_entity")
    return QuerySpec("song", page=page, song_query=req.query)

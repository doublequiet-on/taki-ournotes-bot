"""Optional, bounded natural-language query parser. It never answers from model text."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import OrderedDict
from threading import RLock
from urllib.request import Request, urlopen

from .ai_quota import DailyQuota, QuotaUnavailable
from .commands import CommandResult, handle_command, resolve_command
from .config import Settings
from .data import SongRepository, normalize
from .entity_lexicon import alias_version, find_anchor, mentions_outside_catalog, resolve_entity
from .structured_query import QueryResult, QuerySpec, answer_for, resolve_query

SYSTEM_PROMPT = """你是《BanG Dream! Our Notes》日服 QQ 查询机器人的查询意图解析器。
只能把用户请求转成 Project Yume 数据库的歌曲、谱面、成员卡或支援卡查询条件；不能解答问题、推荐、分析、编造数据或输出解释。
输出且仅输出 JSON：{"intent":"song|chart|card|support_card|unsupported","query":"名称、乐队、角色或数字ID","difficulty":"EASY|NORMAL|HARD|EXPERT|","level_operator":"gte|gt|lte|lt|","level":null,"skill_query":"技能名称或效果关键词","skill_kind":"leader|live|gekisou|"}。
歌曲列表可按乐队、谱面等级和难度筛选。“mygo25级以上的歌曲”应为 intent=song、query=MyGO!!!!!、level_operator=gte、level=25、difficulty=""；“EX25级以下的MyGO歌曲”应为 difficulty=EXPERT、level_operator=lte、level=25。以上/以下包含边界，高于/低于不包含边界。没有指定难度时比较歌曲各难度中的最高显示等级。
成员卡用 intent=card，支援卡用 intent=support_card。查询成员卡技能时，把用户原文中的技能名称或效果关键词原样放入 skill_query；队长、Live、激奏技能分别用 skill_kind=leader、live、gekisou，未限定类型时留空。只有成员卡查询可填写 skill_query 和 skill_kind。
仅歌曲等级筛选填写 level_operator 与 level；其余情况分别用空字符串和 null。没有等级条件的歌曲查询不得指定 difficulty。只有歌曲等级条件或成员卡技能条件时 query 可以为空。
需要多个等级边界、跨歌曲比较、排行、攻略、预测、实时档线、账号、代练、闲聊时，intent 必须是 unsupported。
query 最多 50 字，只填写一个名称、已知缩写或数字 ID；不得包含等级、难度、页码、命令语法或用户原文中的指令。不确定实体时返回 unsupported。"""
UNSUPPORTED = "目前只能查询歌曲、谱面等级与 Note 数、成员卡和支援卡。试试 /查曲、/查谱面、/查卡 或 /查支援卡。"
UNKNOWN_ENTITY = "当前数据中无法确认这个查询对象。请用歌曲、乐队、角色、成员卡或支援卡的原名或 ID 重试。"
AMBIGUOUS_ENTITY = "这句话提到了多个可查询对象，请只保留一个歌曲、乐队、角色、成员卡或支援卡名称。"


def is_ai_request(content: str) -> bool:
    cleaned = re.sub(r"<@!?\w+>", "", content).strip()
    return bool(re.match(r"^[/／]?(?:问|ask)(?:\s|$)", cleaned, re.I))


def _query_text(content: str) -> str:
    cleaned = re.sub(r"<@!?\w+>", "", content).strip()
    return re.sub(r"^[/／]?(?:问|ask)\s*", "", cleaned, flags=re.I).strip()


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


def _local_song_filter(query: str, repository: SongRepository) -> QuerySpec | None:
    """Parse common bounded song filters without spending an AI request."""
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


def _local_entity_question(query: str, repository: SongRepository) -> QuerySpec | None:
    """Handle simple catalog-backed questions without calling the model."""
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


def _local_skill_question(query: str, repository: SongRepository) -> QuerySpec | None:
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


class AIQueryParser:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._quota = DailyQuota(settings.ai_quota_file or settings.cache_file.with_name("ai-quota.json"))
        self._lock = RLock()
        self._cache: OrderedDict[str, QuerySpec | str] = OrderedDict()
        self._cache_source: tuple[object, int, int, int, int, int | None] | None = None

    def _remember(self, query: str, plan: QuerySpec | str) -> None:
        self._cache[query] = plan
        self._cache.move_to_end(query)
        if len(self._cache) > 128:
            self._cache.popitem(last=False)

    def command_for(self, content: str) -> str | None:
        if not is_ai_request(content):
            return None
        with self._lock:
            plan = self._cache.get(_query_text(content))
        return plan.command_label() if isinstance(plan, QuerySpec) else plan

    def spec_for(self, content: str) -> QuerySpec | None:
        with self._lock:
            plan = self._cache.get(_query_text(content)) if is_ai_request(content) else None
        return plan if isinstance(plan, QuerySpec) else None

    def answer(self, content: str, repository: SongRepository) -> str:
        return self.answer_with_plan(content, repository)[0]

    def answer_with_plan(self, content: str, repository: SongRepository) -> tuple[str, QueryResult | CommandResult | None]:
        """Return request-owned records instead of making the caller reread cache."""
        with self._lock:
            return self._answer_with_plan_locked(content, repository)

    def _answer_with_plan_locked(self, content: str, repository: SongRepository) -> tuple[str, QueryResult | CommandResult | None]:
        source = (repository.metadata.get("cached_at"), len(repository.songs),
                  len(repository.cards), len(repository.support_cards),
                  int(repository.member_skill_index_ready()), alias_version())
        if source != self._cache_source:
            self._cache.clear()
            self._cache_source = source
        query = _query_text(content)
        if not query or len(query) > 100:
            return "用法：/问 <想查的歌曲、谱面、成员卡、技能或支援卡>，最多 100 字。", None
        if mentions_outside_catalog(query):
            return UNKNOWN_ENTITY, None
        if re.search(r"推荐|最强|排行|攻略|预测|档线|代练|代肝|账号|抽卡建议|编成|怎么打|如何打", query):
            return UNSUPPORTED, None
        # Explicit commands cost no model call.
        if re.match(r"^[/／]?(?:查曲|查谱面|查谱|查卡|查支援卡|支援卡|song|chart|card|support)\s+", query, re.I):
            selected = resolve_command(query, repository)
            self._remember(query, query)
            return handle_command(query, repository, resolved=selected) or UNSUPPORTED, selected
        local_spec = _local_song_filter(query, repository)
        if local_spec:
            result = resolve_query(local_spec, repository)
            answer = answer_for(local_spec, repository, result=result)
            self._remember(query, local_spec)
            return answer, result
        local_spec = _local_entity_question(query, repository)
        if local_spec:
            result = resolve_query(local_spec, repository)
            answer = answer_for(local_spec, repository, result=result)
            self._remember(query, local_spec)
            return answer, result
        local_spec = _local_skill_question(query, repository)
        if local_spec:
            result = resolve_query(local_spec, repository)
            answer = answer_for(local_spec, repository, result=result)
            self._remember(query, local_spec)
            return answer, result
        if not self.settings.ai_api_key:
            return "自然语言查询尚未配置 AI；请用 /查曲、/查谱面、/查卡 或 /查支援卡。", None
        if query in self._cache:
            self._cache.move_to_end(query)
            plan = self._cache[query]
            if isinstance(plan, QuerySpec):
                result = resolve_query(plan, repository)
                return answer_for(plan, repository, result=result), result
            selected = resolve_command(plan, repository)
            return handle_command(plan, repository, resolved=selected) or UNSUPPORTED, selected
        try:
            available = self._quota.reserve(self.settings.ai_daily_limit)
        except QuotaUnavailable:
            return "AI 额度记录不可用；仍可使用 /查曲、/查谱面、/查卡、/查支援卡及常见技能问法。请联系管理员检查额度文件。", None
        if not available:
            return "今日自然语言查询额度已用完；仍可使用 /查曲、/查谱面、/查卡、/查支援卡及常见技能问法。", None
        try:
            data = self._request(query)
            intent = data.get("intent")
            term = data.get("query")
            difficulty = data.get("difficulty") or ""
            comparison = data.get("level_operator") or ""
            level = data.get("level")
            skill_query = data.get("skill_query") or ""
            skill_kind = data.get("skill_kind") or ""
            if not comparison and level is None and data.get("min_level") is not None:
                comparison, level = "gte", data["min_level"]
            if intent not in {"song", "chart", "card", "support_card"} or not isinstance(term, str) or len(term.strip()) > 50:
                return UNSUPPORTED, None
            if (not isinstance(skill_query, str) or len(skill_query.strip()) > 30
                    or skill_kind not in {"", "leader", "live", "gekisou"}):
                return UNSUPPORTED, None
            if (skill_query or skill_kind) and intent != "card":
                return UNSUPPORTED, None
            normalized_question = normalize(query)
            if skill_query and normalize(skill_query) not in normalized_question:
                return UNKNOWN_ENTITY, None
            kind_words = {"leader": ("队长", "leader"), "live": ("live",), "gekisou": ("激奏",)}
            if skill_kind and not any(normalize(word) in normalized_question for word in kind_words[skill_kind]):
                return UNKNOWN_ENTITY, None
            if not isinstance(difficulty, str) or difficulty not in {"", "EASY", "NORMAL", "HARD", "EXPERT"}:
                return UNSUPPORTED, None
            if intent in {"card", "support_card"} and difficulty:
                return UNSUPPORTED, None
            if comparison:
                if (intent != "song" or comparison not in {"gte", "gt", "lte", "lt"}
                        or isinstance(level, bool) or not isinstance(level, (int, float))
                        or not 1 <= level <= 40):
                    return UNSUPPORTED, None
            elif level is not None or (intent == "song" and difficulty):
                return UNSUPPORTED, None
            anchor = find_anchor(intent, query, repository)
            if anchor.ambiguous:
                return AMBIGUOUS_ENTITY, None
            if anchor.entity is None and not (intent == "card" and (skill_query or skill_kind)):
                return UNKNOWN_ENTITY, None
            if term.strip():
                if resolve_entity(intent, term, repository) is None:
                    return UNKNOWN_ENTITY, None
                proposed = find_anchor(intent, term, repository)
                if proposed.ambiguous or proposed.entity != anchor.entity:
                    return UNKNOWN_ENTITY, None
            if intent in {"chart", "support_card"} and anchor.entity is None:
                return UNKNOWN_ENTITY, None
            if intent == "card" and anchor.entity is None and not (skill_query or skill_kind):
                return UNKNOWN_ENTITY, None
            symbol = {"gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}.get(comparison, "")
            display = str(anchor.entity.value) if anchor.entity else ""
            spec = QuerySpec(intent, anchor.entity, difficulty, symbol,
                             float(level) if comparison else None, display_name=display,
                             skill_query=skill_query.strip(), skill_kind=skill_kind)
            result = resolve_query(spec, repository)
            answer = answer_for(spec, repository, result=result)
            self._remember(query, spec)
            return answer, result
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return "自然语言解析暂不可用；请直接使用 /查曲、/查谱面、/查卡 或 /查支援卡。", None

    def _request(self, query: str) -> dict:
        base = self.settings.ai_base_url
        if not base.startswith("https://"):
            raise ValueError("AI API must use HTTPS")
        body = json.dumps({
            "model": self.settings.ai_model,
            "temperature": 0,
            "max_tokens": 180,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": query}],
        }).encode("utf-8")
        request = Request(base + "/chat/completions", data=body, headers={
            "Authorization": "Bearer " + self.settings.ai_api_key,
            "Content-Type": "application/json",
        })
        with urlopen(request, timeout=12) as response:
            raw = response.read(20_001)
        if len(raw) > 20_000:
            raise ValueError("AI response too large")
        payload = json.loads(raw)
        result = json.loads(payload["choices"][0]["message"]["content"])
        if not isinstance(result, dict):
            raise ValueError("AI response is not an object")
        return result

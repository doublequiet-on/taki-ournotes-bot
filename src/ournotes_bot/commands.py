# L3
# Input: 命令文本、SongRepository；handle_command 可复用已捕获的 CommandResult。
# Output: resolve_command → CommandResult | None；handle_command → str | None，含确定性查询、帮助或状态文本。
# Pos: Query / Deterministic 的直接命令入口与结果契约；见 query/L2-2.md。
# Effects/Dependencies: 依赖 query 专项模块、文案与进程内调试计数；实体解析可读别名，详情／效率可经 Data 联网或读写缓存，不调用模型。

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import TypeVar

from .data import Card, Skill, Song, SongRepository, SupportCard, character_identity, localized_text, normalize, resolve_character_alias
from .query.entity_lexicon import known_alias_names, resolve_exact_alias
from .i18n import tr
from .natural_query.query_debug import QUERY_DEBUG_COUNTERS
from .query.efficiency_query import MetaAnswer, HELP as EFFICIENCY_HELP
from .query.song_query import SongAnswer, parse_filter, execute as execute_song_filter
from .sources.haneoka.song_traits import describe as describe_song
from .query.card_catalog import CardAnswer, query_cards
from .query.event_cutoff_query import CutoffAnswer, parse_cutoff, execute_cutoff
from .bot_info import INTRO


HELP_TEXT = """Taki · Our Notes 日服资料查询
群内请先 @机器人，再发送以下指令。
/查曲 [歌名/ID/乐队] [颜色] [激奏=类型] [EX] [lv<=25] [页N]：歌曲列表
颜色：红、蓝、绿、黄、紫；例 /查曲 颜色=蓝/绿
激奏=JUST 表示包含；激奏=纯JUST 表示全段同类；激奏=混合；激奏=JUST/JUST/COMBO 按顺序匹配
/查谱面 歌名或ID [难度]：查看等级与 Note 数；100001 可简写为 1
/查分数表 [乐队] [EX] [lv<=25] [页N]：全难度每分钟得分效率前30条，含难度、时长和得分系数；也可指定歌名或ID
/查榜线 [jp/hk/kr/en] [歌名] [T1～T100]：日服（jp）、国服（hk）、韩服（kr）、英服（en），默认日服；省略歌名查全部歌曲，省略名次显示 T1/2/3/10/100；如 /查榜线 100
/查卡 [SSR/SR/R] [颜色=红色] [角色=tmr] [乐队=MyGO] [LIVE=分数提升] [击奏=JUST] [页N]：条件卡牌列表；指定ID看详情
/查支援卡（或 /查SNAP）：同上筛选，另支持 EX、LIVE=技能延长
/查卡面 ID、/查支援卡面 ID：只输出卡面；条件多选用逗号，同维度任选、跨维度同时满足
/查缩写 昵称：查看昵称对应的角色、乐队等，也可直接用于查曲或查卡
/问 想查的内容：自然语言查询歌曲、谱面、歌曲效率、成员卡技能或支援卡（复杂问法需配置 AI）
/数据状态：查看进程、最近同步与缓存状态
/调试数据：查看本次进程的 AI API 成功调用与有效检索次数
/帮助：查看本说明
/介绍：了解 Taki 与数据来源
示例：/查曲 蓝色 MyGO EX 激奏=JUST；/问 哪些歌是纯COMBO；/查谱面 100001 EX
歌曲颜色／激奏来自 Haneoka 日服缓存；未获取与旧缓存会明确标注。"""
HELP_TEXTS = {
    "zh": HELP_TEXT + "\n/语言：查看英文和日文指令",
    "en": """Our Notes commands
/song [title or ID] [level or lv level] [page N]: search songs
/chart <title or ID> [difficulty]: view level and note count
/card <character, title or ID> [page N]: search cards
/support <character, title or ID> [page N]: search support cards
/abbrev <nickname>: look up a character or band nickname
/ask <query>: natural language song, chart, member-skill, or support-card lookup
/status: process and data status  /language: language guide  /help: this guide
Example: /song mygo 27, /chart 100001 EXPERT, /card tomori""",
    "ja": """Our Notes コマンド
/曲 [曲名またはID] [レベルまたはlvレベル] [ページN]：楽曲を検索
/譜面 <曲名またはID> [難易度]：レベルとノーツ数
/カード <キャラクター名・カード名・ID> [ページN]：カードを検索
/サポート <キャラクター名・カード名・ID> [ページN]：サポートカードを検索
/略称 <略称>：キャラクター・バンドなどの略称
/問 <内容>：楽曲・譜面・メンバースキル・サポートカードの自然言語検索
/状態：プロセスとデータの状態  /言語：言語案内  /ヘルプ：この案内
例：/曲 mygo 27、/譜面 100001 EXPERT、/カード ともり""",
}

COMMAND_HELP = {
    "efficiency": EFFICIENCY_HELP,
    "songs": "查询歌曲列表，每页16首。支持歌名、昵称、乐队、ID、等级；27包含27.5，lv比较式未指定难度时按最高等级。可加颜色=蓝/绿、激奏=JUST、激奏=纯COMBO、激奏=混合或激奏=JUST/JUST/COMBO，并与EX、lv<=25、页N组合。同颜色多选任选，不同维度同时满足；包含全部用 激奏=包含全部JUST/COMBO。\n例：/查曲 蓝色 MyGO EX 激奏=JUST 页2",
    "chart": "查询谱面等级与 Note 数，支持歌名或曲目 ID。100001 可简写为 1。可选难度：EASY、NORMAL、HARD、EXPERT；不指定时显示全部难度。\n用法：/查谱面 <歌名或ID> [难度]\n示例：/查谱面 1 EXPERT、/查谱面 100001 EXPERT",
    "cards": "角色卡条件查询始终返回列表，每页16张，完整总数和翻页指令可见。ID精确查详情，/查卡面 ID 只发公开full卡面。\n用法：/查卡 [SSR/SR/R] [颜色=红色,蓝色] [角色=tmr,skk] [乐队=MyGO] [LIVE=分数提升] [击奏=JUST] [得意=MyGO] [页N]\n不同维度AND、同维度多选OR，得意标签全部包含。BD映射尚未核实；旧二/三/四星别名保留。",
    "support_cards": "SNAP条件查询始终返回列表，每页16张；ID精确查详情，/查支援卡面 ID 只发卡面。\n用法：/查SNAP [SSR/EX/SR/R] [颜色=红色,蓝色] [角色=tmr,skk] [乐队=MyGO] [LIVE=技能延长] [击奏=LUCK] [页N]\nLIVE可选分数提升、LIFE回复、判定强化、技能延长；以实际资料为准。角色任选且去重，乐队另行筛选；不支持得意标签。BD映射尚未核实。",
    "abbrev": "查询已收录的角色、乐队等昵称；也可直接用昵称查曲或查卡。\n用法：/查缩写 <昵称>\n示例：/查缩写 skk、/查缩写 茉团、/查卡 墨缇丝",
}
COMMAND_HELPS = {
    "zh": COMMAND_HELP,
    "en": {
        "songs": "Search by title, band, ID, or level; 16 songs per page. 27 and lv27 match base level 27; 27.5 and lv27.5 match the displayed level exactly. An exact song ID takes priority over a bare number.\nUsage: /song [title or ID] [level or lv level] [page N]\nExample: /song mygo 27 page 2",
        "chart": "Look up level and note count by title or ID. Song 100001 can be shortened to 1. Optional difficulty: EASY, NORMAL, HARD, EXPERT.\nUsage: /chart <title or ID> [difficulty]\nExample: /chart 1 EXPERT",
        "cards": "Search by card ID, character, card title, or band, 16 per page.\nUsage: /card <query or ID> [page N]\nExample: /card tomori or /card mygo page 2",
        "support_cards": "Search support cards by ID, character, or title, 16 per page.\nUsage: /support <query or ID> [page N]\nExample: /support tomori or /support 1",
        "abbrev": "Look up verified character and band nicknames.\nUsage: /abbrev <nickname>\nExample: /abbrev skk",
    },
    "ja": {
        "songs": "曲名・バンド・ID・レベルで検索します。1ページ16曲。27とlv27は基本レベル27、27.5とlv27.5は表示レベルを完全一致で検索します。数字が曲IDと一致する場合はIDを優先します。\n使い方：/曲 [曲名またはID] [レベルまたはlvレベル] [ページN]\n例：/曲 mygo 27 ページ2",
        "chart": "曲名またはIDでレベルとノーツ数を確認します。100001は1と省略できます。難易度省略時は全難易度を表示します。\n使い方：/譜面 <曲名またはID> [難易度]\n例：/譜面 1 EXPERT",
        "cards": "カードID、キャラクター名、カード名、バンド名で検索します。1ページ16枚。\n使い方：/カード <名前またはID> [ページN]\n例：/カード 祥子、/カード mygo ページ2、/カード 51",
        "support_cards": "サポートカードID、キャラクター名、カード名で検索します。1ページ16枚。\n使い方：/サポート <名前またはID> [ページN]\n例：/サポート ともり、/サポート 1",
        "abbrev": "登録済みのキャラクター・バンドなどの略称を調べます。\n使い方：/略称 <略称>\n例：/略称 skk",
    },
}

ALIASES = {
    "查榜线": "event_cutoff", "榜线": "event_cutoff",
    "查分数表": "efficiency", "查效率": "efficiency",
    "查曲": "songs", "song": "songs", "songs": "songs", "曲": "songs", "楽曲": "songs",
    "查谱面": "chart", "查谱": "chart", "谱面": "chart", "chart": "chart", "譜面": "chart",
    "查卡": "cards", "查角色卡": "cards", "查卡面": "cards", "card": "cards", "cards": "cards", "カード": "cards",
    "查支援卡": "support_cards", "查snap": "support_cards", "snap": "support_cards", "查支援卡面": "support_cards", "查snap卡面": "support_cards", "支援卡": "support_cards", "support": "support_cards",
    "supportcard": "support_cards", "supportcards": "support_cards", "サポート": "support_cards",
    "查缩写": "abbrev", "abbrev": "abbrev", "略称": "abbrev",
}
CANONICAL = {"efficiency": "/查分数表", "songs": "/查曲", "chart": "/查谱面", "cards": "/查卡", "support_cards": "/查支援卡", "abbrev": "/查缩写"}
CANONICAL_BY_LOCALE = {
    "zh": CANONICAL,
    "en": {"songs": "/song", "chart": "/chart", "cards": "/card", "support_cards": "/support", "abbrev": "/abbrev"},
    "ja": {"songs": "/曲", "chart": "/譜面", "cards": "/カード", "support_cards": "/サポート", "abbrev": "/略称"},
}
ENGLISH_COMMANDS = {"song", "songs", "chart", "card", "cards", "support", "supportcard", "supportcards", "abbrev", "help", "status", "language", "event", "gacha", "prediction"}
JAPANESE_COMMANDS = {"曲", "楽曲", "譜面", "カード", "サポート", "略称", "ヘルプ", "状態", "言語", "イベント", "ガチャ", "予想線"}
UNAVAILABLE_COMMANDS = {"查活动", "查卡池", "ycx", "预测线", "查预测线", "event", "gacha", "prediction", "イベント", "ガチャ", "予想線"}
UNAVAILABLE_REPLY = "该功能暂未上线"
PAGE_SIZE = 16


def _clean_message(content: str) -> str:
    content = re.sub(r"<@!?\w+>", "", content)
    return content.strip().lstrip("/／").strip()


_MULTI_COMMAND_SPLIT = re.compile(r"[\r\n]+")


def split_commands(content: str) -> list[str]:
    """Split one message into its individual commands, one per line.

    A single-line message yields exactly one entry, so ordinary
    single-command behaviour is untouched. Blank lines are dropped, and the
    mention/leading-slash cleanup is applied per line so a mention on the
    first line cannot leak into the query text of that line.
    """
    return [line for line in (_clean_message(part) for part in _MULTI_COMMAND_SPLIT.split(content)) if line]


def locale_for(content: str) -> str:
    text = _clean_message(content)
    first = text.split(None, 1)[0].casefold() if text else ""
    if first in ENGLISH_COMMANDS:
        return "en"
    if first in JAPANESE_COMMANDS:
        return "ja"
    return "zh"


def _split_page(query: str) -> tuple[str, int]:
    match = re.search(r"(?:\s+|(?=第|页|ページ))(?:页\s*(\d+)|第\s*(\d+)\s*页|p(\d+)|page\s+(\d+)|ページ\s*(\d+))\s*$", query, re.I)
    if not match:
        return query, 1
    return query[:match.start()].strip(), int(next(value for value in match.groups() if value is not None))


def _song_name_matches(repository: SongRepository, term: str) -> list[Song]:
    if not term:
        return list(repository.songs)
    match = resolve_exact_alias("song", term, repository)
    if match.ambiguous:
        return []
    if match.entity:
        if match.entity.kind == "song":
            return [song for song in repository.songs if song.id == match.entity.value]
        return [song for song in repository.songs
                if normalize(song.band) == normalize(str(match.entity.value))]
    return repository.search(term, limit=len(repository.songs))


_RARITY_NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百]+)"
_CARD_RARITY = re.compile(
    rf"(?:星级\s*=?|rarity\s*=|[★☆])\s*(?P<prefix>{_RARITY_NUMBER})"
    rf"|(?P<suffix>{_RARITY_NUMBER})\s*(?:星(?:级)?|[★☆]|-?stars?(?![A-Za-z]))"
    r"|(?P<stars>[★☆]+)|(?<![A-Za-z])(?P<grade>SSR|SR|R)(?![A-Za-z])", re.I,
)
CARD_RARITIES = {"SSR": 4, "SR": 3, "R": 2}
CARD_RARITY_HELP = {
    "zh": "请指定 SSR（四星）、SR（三星）或 R（二星），例如 /查卡 SSR 或 /查卡 高松灯 四星；暂不支持星级范围。",
    "en": "Choose SSR (4-star), SR (3-star), or R (2-star), e.g. /card SSR or /card tomori 4-star. Rarity ranges are not supported.",
    "ja": "SSR（星4）・SR（星3）・R（星2）から1つ指定してください。例：/カード SSR、/カード ともり ★4。範囲指定には対応していません。",
}
_CardT = TypeVar("_CardT", Card, SupportCard)


def split_card_rarity(query: str) -> tuple[str, int | None]:
    """Extract one explicit rarity; bare IDs and skill levels remain untouched."""
    text = unicodedata.normalize("NFKC", query)
    matches = list(_CARD_RARITY.finditer(text))
    if not matches:
        return text, None
    if len(matches) != 1:
        raise ValueError("expected one card rarity")
    match = matches[0]
    number = match.group("prefix") or match.group("suffix")
    rarity = (CARD_RARITIES[match.group("grade").upper()] if match.group("grade") else
              len(match.group("stars")) if number is None else
              int(number) if number.isdecimal() else
              {"二": 2, "两": 2, "三": 3, "四": 4}.get(number, 0))
    before, after = text[:match.start()], text[match.end():]
    if (rarity not in CARD_RARITIES.values()
            or re.search(r"(?:至少|至多|最多|不低于|不高于|超过|低于|高于|大于|小于|[<>≤≥=~～到至.\-])\s*$", before)
            or re.match(r"\s*(?:及?以上|及?以下|以内|起|[到至~～\-])", after)):
        raise ValueError("invalid or ranged card rarity")
    return (before + " " + after).strip(), rarity


def filter_card_rarity(cards: list[_CardT], rarity: int | None) -> list[_CardT]:
    return [card for card in cards if rarity is None or card.rarity == rarity]


def card_matches(repository: SongRepository, query: str) -> list[Card]:
    term, rarity = split_card_rarity(query)
    cards = list(repository.cards) if not term and rarity is not None else _card_name_matches(repository, term)
    return filter_card_rarity(sorted(cards, key=lambda card: (-card.rarity, card.id)), rarity)


def _card_name_matches(repository: SongRepository, query: str) -> list[Card]:
    """Use the same verified nickname match for text and QQ images."""
    built_in = resolve_character_alias(query)
    if built_in:
        cards = [card for card in repository.cards
                 if normalize(character_identity(card.character)) == normalize(built_in.display)]
        return sorted(cards, key=lambda card: (-card.rarity, card.id))
    match = resolve_exact_alias("card", query, repository)
    if match.ambiguous:
        return []
    if match.entity:
        entity = match.entity
        if entity.kind == "card":
            return [card for card in repository.cards if card.id == entity.value]
        if entity.kind == "character":
            cards = [card for card in repository.cards
                     if normalize(character_identity(card.character)) == normalize(str(entity.value))]
        else:
            cards = [card for card in repository.cards
                     if normalize(card.band) == normalize(str(entity.value))]
        return sorted(cards, key=lambda card: (-card.rarity, card.id))
    return repository.search_cards(query, limit=len(repository.cards))


def support_card_matches(repository: SongRepository, query: str) -> list[SupportCard]:
    term, rarity = split_card_rarity(query)
    cards = (list(repository.support_cards) if not term and rarity is not None
             else _support_card_name_matches(repository, term))
    return filter_card_rarity(sorted(cards, key=lambda card: (-card.rarity, card.id)), rarity)


def _support_card_name_matches(repository: SongRepository, query: str) -> list[SupportCard]:
    built_in = resolve_character_alias(query)
    if built_in:
        cards = [card for card in repository.support_cards if any(
            normalize(character_identity(name)) == normalize(built_in.display) for name in card.characters
        )]
        return sorted(cards, key=lambda card: (-card.rarity, card.id))
    match = resolve_exact_alias("support_card", query, repository)
    if match.ambiguous:
        return []
    if match.entity:
        if match.entity.kind == "support_card":
            return [card for card in repository.support_cards if card.id == match.entity.value]
        cards = [card for card in repository.support_cards if any(
            normalize(character_identity(name)) == normalize(str(match.entity.value))
            for name in card.characters
        )]
        return sorted(cards, key=lambda card: (-card.rarity, card.id))
    return repository.search_support_cards(query, limit=len(repository.support_cards))


def song_matches(repository: SongRepository, query: str) -> list[Song]:
    """Apply an optional trailing level filter after the title/band/ID search."""
    normalized = unicodedata.normalize("NFKC", query)
    level_range = re.search(
        r"(?:^|\s)lv\s*(>=|>|<=|<)\s*(\d+(?:\.\d+)?)"
        r"(?:\s+diff=(EASY|NORMAL|HARD|EXPERT))?\s*$", normalized, re.I,
    )
    if level_range:
        term = normalized[:level_range.start()].strip()
        matches = _song_name_matches(repository, term)
        operator, value, difficulty = level_range.groups()
        level = float(value)
        compare = {
            ">=": lambda actual: actual >= level,
            ">": lambda actual: actual > level,
            "<=": lambda actual: actual <= level,
            "<": lambda actual: actual < level,
        }[operator]
        if difficulty:
            return [song for song in matches if any(
                chart.difficulty == difficulty.upper() and compare(chart.display_level)
                for chart in song.charts
            )]
        return [song for song in matches if song.charts and compare(
            max(chart.display_level for chart in song.charts)
        )]
    prefixed = re.search(r"(?:^|\s)lv\.?\s*(\d+(?:\.\d+)?)\s*$", normalized, re.I)
    level_match = prefixed or re.search(r"(?:^|\s)(\d+(?:\.\d+)?)\s*$", normalized)
    if level_match and not prefixed and normalized.strip() == level_match.group(1) and level_match.group(1).isdigit():
        exact_id = [song for song in repository.songs if song.id == int(level_match.group(1))]
        if exact_id:
            return exact_id
    term = normalized[:level_match.start()].strip() if level_match else query
    matches = _song_name_matches(repository, term)
    if not level_match:
        return matches
    level_text = level_match.group(1)
    if "." in level_text:
        level = float(level_text)
        return [song for song in matches if any(chart.display_level == level for chart in song.charts)]
    level = int(level_text)
    return [song for song in matches if any(chart.level == level for chart in song.charts)]


def page_slice(items: list, page: int) -> list:
    return items[(page - 1) * PAGE_SIZE:page * PAGE_SIZE] if page >= 1 else []


def page_notice(kind: str, query: str, page: int, total: int, locale: str) -> str:
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    command = CANONICAL_BY_LOCALE[locale][kind]
    if not 1 <= page <= pages:
        first = f"{command} {query}"
        return {"zh": f"页码超出范围：共 {pages} 页。发送 {first} 查看首页。",
                "en": f"Page out of range: {pages} page(s). Use {first} for page 1.",
                "ja": f"ページは全{pages}ページです。{first} で1ページ目を表示します。"}[locale]
    unit = {"zh": {"songs": "首", "cards": "张", "support_cards": "张"},
            "en": {"songs": " songs", "cards": " cards", "support_cards": " support cards"},
            "ja": {"songs": "曲", "cards": "枚", "support_cards": "枚"}}[locale][kind]
    summary = {"zh": f"第 {page}/{pages} 页 · 共 {total}{unit}",
               "en": f"Page {page}/{pages} · {total}{unit}",
               "ja": f"{page}/{pages}ページ · 全{total}{unit}"}[locale]
    if page < pages:
        next_query = f"{command} {query} " + ({"zh": f"页{page + 1}", "en": f"page {page + 1}", "ja": f"ページ{page + 1}"}[locale])
        return summary + "\n" + {"zh": "下一页：", "en": "Next: ", "ja": "次："}[locale] + next_query
    return summary


def parse_query(content: str) -> tuple[str, str, str | int | None] | None:
    text = _clean_message(content)
    parts = text.split(None, 1)
    if len(parts) != 2:
        return None
    kind = ALIASES.get(parts[0].casefold())
    query = parts[1].strip()
    if not query:
        return None
    if kind == "songs":
        query, page = _split_page(query)
        return ("songs", query, page) if query else None
    if kind == "chart":
        parts = query.rsplit(None, 1)
        difficulties = {"E": "EASY", "EASY": "EASY", "N": "NORMAL", "NORMAL": "NORMAL", "H": "HARD", "HARD": "HARD", "EX": "EXPERT", "EXP": "EXPERT", "EXPERT": "EXPERT", "イージー": "EASY", "ノーマル": "NORMAL", "ハード": "HARD", "エキスパート": "EXPERT", "简单": "EASY", "普通": "NORMAL", "困难": "HARD", "专家": "EXPERT"}
        if len(parts) == 2 and parts[1].upper() in difficulties:
            query, difficulty = parts[0], difficulties[parts[1].upper()]
        else:
            difficulty = None
        # Only chart lookup accepts the shortened 100000-series song ID.
        short_id = unicodedata.normalize("NFKC", query)
        if short_id.isdecimal() and 1 <= int(short_id) < 100000:
            query = str(100000 + int(short_id))
        return "chart", query, difficulty
    if kind == "cards":
        query, page = _split_page(query)
        return ("cards", query, page) if query else None
    if kind == "support_cards":
        query, page = _split_page(query)
        return ("support_cards", query, page) if query else None
    return None


def _command_tip(content: str) -> str | None:
    text = _clean_message(content)
    if not text:
        return None
    locale = locale_for(content)
    helps = COMMAND_HELPS[locale]
    canonical = CANONICAL_BY_LOCALE[locale]
    parts = text.split(None, 1)
    first, rest = parts[0], parts[1] if len(parts) > 1 else ""
    if any(first.casefold().startswith(name) for name in UNAVAILABLE_COMMANDS):
        return tr(locale, "unavailable")
    kind = ALIASES.get(first.casefold())
    if kind and not rest.strip():
        return helps[kind]

    # A pasted command with its argument joined directly to the name.
    for alias in sorted(ALIASES, key=len, reverse=True):
        if first.casefold().startswith(alias) and len(first) > len(alias):
            suffix = first[len(alias):]
            if suffix and not suffix.isalpha():
                tail = f" {rest.strip()}" if rest.strip() else ""
                return tr(locale, "missing_space", command=canonical[ALIASES[alias]], argument=suffix + tail) + "\n" + helps[ALIASES[alias]]
            if suffix and alias in {"查曲", "查谱面", "查卡", "查卡面", "查支援卡", "支援卡", "查分数表", "查效率"}:
                if suffix in {"角色"}:
                    return tr(locale, "unknown") + "\n" + HELP_TEXTS[locale]
                return tr(locale, "missing_space", command=canonical[ALIASES[alias]], argument=suffix) + "\n" + helps[ALIASES[alias]]

    candidates = {
        "zh": ("查曲", "查谱面", "查分数表", "查卡", "查支援卡", "查缩写", "查活动", "查卡池", "ycx", "数据状态", "帮助"),
        "en": ("song", "chart", "card", "support", "abbrev", "event", "gacha", "ycx", "status", "help"),
        "ja": ("曲", "譜面", "カード", "サポート", "略称", "イベント", "ガチャ", "予想線", "状態", "ヘルプ"),
    }[locale]
    candidate = max(candidates, key=lambda name: SequenceMatcher(None, first, name).ratio())
    similarity = SequenceMatcher(None, first, candidate).ratio()
    if similarity >= 0.55 and (content.strip().startswith(("/", "／")) or first[:1] in {"查", "谱", "卡", "数", "帮"}):
        argument = f" {rest.strip()}" if rest.strip() else ""
        if candidate in UNAVAILABLE_COMMANDS:
            return tr(locale, "typo", typed=first, command=f"/{candidate}{argument}") + "\n" + tr(locale, "unavailable")
        return tr(locale, "typo", typed=first, command=f"/{candidate}{argument}") + "\n" + helps.get(ALIASES.get(candidate, ""), HELP_TEXTS[locale])
    if content.strip().startswith(("/", "／")):
        return tr(locale, "unknown") + "\n" + HELP_TEXTS[locale]
    return None


def _suggest(query: str, values: list[tuple[str, str]], locale: str) -> str:
    needle = normalize(query)
    if not needle or needle.isdigit():
        return ""
    ranked = []
    for name, label in values:
        score = SequenceMatcher(None, needle, normalize(name)).ratio()
        if score >= 0.5:
            ranked.append((score, label))
    ranked.sort(key=lambda item: -item[0])
    labels = list(dict.fromkeys(label for _, label in ranked[:3]))
    return tr(locale, "suggest", names=("、" if locale == "zh" else ", ").join(labels)) if labels else ""


def _format_charts(song: Song) -> str:
    return "\n".join(
        f"{chart.difficulty:<6} Lv.{chart.display_level:g} · {chart.notes} Notes"
        for chart in song.charts
    ) or "暂无谱面数据"


_SKILL_LABELS = {
    "zh": {"leaderSkill": "队长技能", "liveSkill": "Live 技能", "gekisouSkill": "激奏技能",
           "supportSkill": "支援技能", "gekisouSupportSkill": "激奏支援技能", "skill": "技能"},
    "en": {"leaderSkill": "Leader skill", "liveSkill": "Live skill", "gekisouSkill": "Gekisou skill",
           "supportSkill": "Support skill", "gekisouSupportSkill": "Gekisou support", "skill": "Skill"},
    "ja": {"leaderSkill": "リーダースキル", "liveSkill": "ライブスキル", "gekisouSkill": "激奏スキル",
           "supportSkill": "サポートスキル", "gekisouSupportSkill": "激奏サポート", "skill": "スキル"},
}


def rarity_text(rarity: int) -> str:
    return {2: "R", 3: "SR", 4: "SSR"}.get(rarity, f"未知稀有度({rarity})")


def _skill_lines(skills: tuple[Skill, ...], locale: str) -> list[str]:
    labels = _SKILL_LABELS[locale]
    lines = []
    for skill in skills:
        label = labels.get(skill.kind, labels["skill"])
        line = f"{label}：{localized_text(skill, 'name', locale)}"
        description = localized_text(skill, "description", locale)
        lines.append(line + (f"\n效果（Lv.5）：{description}" if description else ""))
    return lines


def _format_card_detail(card: Card, locale: str) -> str:
    from .query.card_catalog import detail_text
    return detail_text(card, locale)


def _format_support_card_detail(card: SupportCard, locale: str) -> str:
    from .query.card_catalog import detail_text
    return detail_text(card, locale)


def _choose(repository: SongRepository, query: str, locale: str = "zh") -> tuple[Song | None, str | None]:
    matches = repository.search(query)
    if not matches:
        return None, tr(locale, "not_found_generic", query=query)
    is_exact = query.strip().isdigit() or any(
        normalize(query) == normalize(title) for title in
        (*matches[0].titles, *matches[0].localized.get("title", {}).values())
    )
    if len(matches) > 1 and not is_exact:
        first = matches[0]
        alternatives = "、".join(f"{localized_text(song, 'title', locale)}({song.id})" for song in matches[1:4])
        if alternatives:
            return first, tr(locale, "maybe", names=alternatives)
    return matches[0], None


@dataclass(frozen=True)
class CommandResult:
    parsed: tuple[str, str, str | int | None]
    songs: tuple[Song, ...] = ()
    cards: tuple[Card, ...] = ()
    support_cards: tuple[SupportCard, ...] = ()
    hint: str | None = None
    meta: MetaAnswer | None = None
    catalog: CardAnswer | None = None
    song_selection: SongAnswer | None = None
    cutoff: CutoffAnswer | None = None


def resolve_command(content: str, repository: SongRepository) -> CommandResult | None:
    """Select records once so text and image use the same request result."""
    text = _clean_message(content)
    parts = text.split(None, 1)
    head = parts[0].casefold() if parts else ""
    if head in {"查榜线", "榜线"}:
        answer = execute_cutoff(parse_cutoff(text), repository)
        return CommandResult(("event_cutoff", answer.request.query, answer.request.rank), cutoff=answer)
    kind = ALIASES.get(head)
    if kind in {"cards", "support_cards"} or head in {"查角色卡", "查snap", "snap", "查支援卡面", "查snap卡面"}:
        support = kind == "support_cards" or head in {"查snap", "snap", "查支援卡面", "查snap卡面"}
        query = parts[1] if len(parts) > 1 else ""
        answer = query_cards(query, repository, support=support, art=head in {"查卡面", "查支援卡面", "查snap卡面"})
        kind = "support_cards" if support else "cards"
        return CommandResult((kind, answer.request.query, answer.request.page),
                             cards=() if support else answer.cards,
                             support_cards=answer.cards if support else (), catalog=answer)
    if re.match(r"^查(?:分数表|效率)(?:\s|$)", text):
        from .query.efficiency_query import MetaAnswer, parse_efficiency, execute_efficiency
        spec = parse_efficiency(text, repository, direct=True)
        answer = MetaAnswer(spec, status="invalid_arguments") if isinstance(spec, str) else execute_efficiency(spec, repository)
        return CommandResult(("efficiency", text, None), meta=answer)
    parsed = parse_query(content)
    if not parsed:
        return None
    kind, query, _ = parsed
    if kind == "songs":
        try:
            request = parse_filter(query)
            if request is not None:
                answer = execute_song_filter(request, repository, int(parsed[2]))
                return CommandResult(parsed, songs=answer.songs, song_selection=answer)
        except ValueError as exc:
            return CommandResult(parsed, hint=str(exc))
        return CommandResult(parsed, songs=tuple(song_matches(repository, query)))
    if kind in {"cards", "support_cards"}:
        try:
            if kind == "cards":
                return CommandResult(parsed, cards=tuple(card_matches(repository, query)))
            return CommandResult(parsed, support_cards=tuple(support_card_matches(repository, query)))
        except ValueError:
            return CommandResult(parsed, hint=CARD_RARITY_HELP[locale_for(content)])
    song, hint = _choose(repository, query, locale_for(content))
    return CommandResult(parsed, songs=(song,) if song else (), hint=hint)


def _safe_sync_time(value: object, locale: str) -> str:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                beijing = timezone(timedelta(hours=8))
                return parsed.astimezone(beijing).strftime("%Y-%m-%d %H:%M UTC+08:00")
        except ValueError:
            pass
    return tr(locale, "unknown_time")


def handle_command(content: str, repository: SongRepository,
                   resolved: CommandResult | None = None) -> str | None:
    text = _clean_message(content)
    locale = locale_for(content)
    if text.casefold() in {"介绍", "about", "简介"}:
        return INTRO
    if not text:
        return HELP_TEXTS[locale]
    if text.casefold() in {"帮助", "help", "菜单", "指令", "ヘルプ"}:
        return HELP_TEXTS[locale]
    if text.casefold() in {"语言", "language", "言語"}:
        return tr(locale, "language")
    if text.split(None, 1)[0].casefold() in UNAVAILABLE_COMMANDS:
        return tr(locale, "unavailable")
    if text.casefold() == "调试数据":
        snapshot = QUERY_DEBUG_COUNTERS.snapshot()
        return (
            "AI 调试数据（本次进程启动后）\n"
            f"成功 API 调用：{snapshot.successful_api_calls} 次\n"
            f"产生实际检索内容的 AI 查询：{snapshot.useful_ai_queries} 次"
        )
    if text.casefold() in {"数据状态", "状态", "版本", "status", "状態"}:
        state = repository.cache_state
        if state not in {"fresh", "cached", "stale", "unsaved"}:
            state = "unknown"
        status = tr(
            locale, "version", songs=len(repository.songs), cards=len(repository.cards),
            support_cards=len(repository.support_cards),
            skill_index=f"{sum(bool(card.skills) for card in repository.cards)}/{len(repository.cards)}",
            time=_safe_sync_time(repository.last_successful_sync_at, locale),
            cache=tr(locale, "cache_" + state),
        )
        traits = repository.song_traits
        saved = traits.saved
        count = sum(bool(s.traits and s.traits.color and s.traits.missions) for s in repository.songs)
        return (status + f"\n歌曲颜色／激奏：{count}/{len(repository.songs)} · Haneoka 日服 · "
                + ("旧缓存" if traits.stale else "有效缓存" if saved else "等待后台获取")
                + (f"\n属性版本：{saved['release']}\n本机获取：{_safe_sync_time(saved['fetched_at'], locale)}" if saved else ""))

    if text.casefold() in {"查缩写", "abbrev", "略称"}:
        return COMMAND_HELPS[locale]["abbrev"]
    if text.split(None, 1)[0].casefold() in {"查缩写", "abbrev", "略称"} and len(text.split(None, 1)) > 1:
        abbreviation = text.split(None, 1)[1].strip()
        alias = resolve_character_alias(abbreviation)
        if alias:
            cards = repository.search_cards(abbreviation, limit=1)
            name = localized_text(cards[0], "character", locale) if cards else alias.display
            return tr(locale, "alias_found" if cards else "alias_missing_card", query=abbreviation, name=name)
        song_alias = resolve_exact_alias("song", abbreviation, repository)
        card_alias = resolve_exact_alias("card", abbreviation, repository)
        if song_alias.ambiguous or card_alias.ambiguous:
            return tr(locale, "alias_ambiguous", query=abbreviation)
        found = song_alias.entity or card_alias.entity
        if song_alias.entity and card_alias.entity and song_alias.entity != card_alias.entity:
            return tr(locale, "alias_ambiguous", query=abbreviation)
        if found:
            if found.kind == "band":
                return tr(locale, "alias_found_band", query=abbreviation, name=found.value)
            if found.kind == "song":
                return tr(locale, "alias_found_song", query=abbreviation, name=found.value)
            if found.kind == "card":
                return tr(locale, "alias_found", query=abbreviation, name=found.value)
            cards = card_matches(repository, abbreviation)
            name = localized_text(cards[0], "character", locale) if cards else found.value
            return tr(locale, "alias_found", query=abbreviation, name=name)
        known = "、".join(known_alias_names(repository))
        return tr(locale, "alias_unknown", query=abbreviation, names=known)

    selection = resolved if resolved is not None else resolve_command(content, repository)
    parsed = selection.parsed if selection else None
    if selection and selection.cutoff is not None:
        return selection.cutoff.text
    if selection and selection.catalog is not None:
        return selection.catalog.text(locale)
    if selection and selection.meta is not None:
        return selection.meta.text
    if selection and selection.song_selection is not None:
        return selection.song_selection.text(locale)
    if parsed and parsed[0] in {"songs", "cards", "support_cards"} and selection.hint:
        return selection.hint
    if parsed and parsed[0] == "songs":
        matches = selection.songs
        if not matches:
            variants = [(title, f"{localized_text(song, 'title', locale)}（{song.id}）") for song in repository.songs
                        for title in (*song.titles, *song.localized.get("title", {}).values())]
            return tr(locale, "not_found_song", query=parsed[1]) + _suggest(parsed[1], variants, locale) + "\n" + COMMAND_HELPS[locale]["songs"]
        page = int(parsed[2])
        visible = page_slice(matches, page)
        if not visible:
            return page_notice("songs", parsed[1], page, len(matches), locale)
        return tr(locale, "songs") + "\n" + "\n".join(
            f"{song.id}  {localized_text(song, 'title', locale)} · {localized_text(song, 'band', locale)}  " + " / ".join(f"{chart.display_level:g}" for chart in song.charts)
            + "\n" + describe_song(song) for song in visible
        ) + "\n" + page_notice("songs", parsed[1], page, len(matches), locale) + "\n" + tr(locale, "next_chart")

    if parsed and parsed[0] == "chart":
        query = parsed[1]
        song = selection.songs[0] if selection.songs else None
        hint = selection.hint
        if not song:
            variants = [(title, f"{localized_text(item, 'title', locale)}（{item.id}）") for item in repository.songs
                        for title in (*item.titles, *item.localized.get("title", {}).values())]
            return tr(locale, "not_found_chart", query=query) + _suggest(query, variants, locale) + "\n" + COMMAND_HELPS[locale]["chart"]
        charts = song.charts if parsed[2] is None else tuple(c for c in song.charts if c.difficulty == parsed[2])
        result = f"[{tr(locale, 'chart')}] {localized_text(song, 'title', locale)}（ID {song.id}）\n" + ("\n".join(f"{c.difficulty} Lv.{c.display_level:g} · {c.notes} Notes" for c in charts) or tr(locale, "no_charts"))
        result += "\n" + describe_song(song)
        return f"{result}\n{hint}" if hint else result

    if parsed and parsed[0] == "cards":
        matches = selection.cards
        if not matches:
            alias = resolve_character_alias(parsed[1])
            if alias:
                return tr(locale, "alias_card", query=parsed[1], name=alias.display)
            variants = [(value, f"{localized_text(card, 'character', locale)}（{card.id}）") for card in repository.cards
                        for value in (card.title, card.character, card.band, *(name for fields in card.localized.values() for name in fields.values()))]
            return tr(locale, "not_found_card", query=parsed[1]) + _suggest(parsed[1], variants, locale) + "\n" + COMMAND_HELPS[locale]["cards"]
        if parsed[1].isdigit() and matches[0].id == int(parsed[1]):
            return _format_card_detail(repository.card_with_detail(matches[0]), locale)
        page = int(parsed[2])
        visible = page_slice(matches, page)
        if not visible:
            return page_notice("cards", parsed[1], page, len(matches), locale)
        return tr(locale, "cards") + "\n" + "\n".join(f"{c.id}  {rarity_text(c.rarity)} {localized_text(c, 'character', locale)} · {localized_text(c, 'title', locale)}" for c in visible) + "\n" + page_notice("cards", parsed[1], page, len(matches), locale) + "\n" + tr(locale, "next_card")

    if parsed and parsed[0] == "support_cards":
        matches = selection.support_cards
        if not matches:
            variants = [(value, f"{localized_text(card, 'character', locale)}（{card.id}）")
                        for card in repository.support_cards
                        for value in (card.title, card.character, *card.characters,
                                      *(name for fields in card.localized.values() for name in fields.values()))]
            return tr(locale, "not_found_support_card", query=parsed[1]) + _suggest(parsed[1], variants, locale) + "\n" + COMMAND_HELPS[locale]["support_cards"]
        if parsed[1].isdigit() and matches[0].id == int(parsed[1]):
            return _format_support_card_detail(repository.support_card_with_detail(matches[0]), locale)
        page = int(parsed[2])
        visible = page_slice(matches, page)
        if not visible:
            return page_notice("support_cards", parsed[1], page, len(matches), locale)
        return tr(locale, "support_cards") + "\n" + "\n".join(
            f"{card.id}  {rarity_text(card.rarity)} {localized_text(card, 'character', locale)} · {localized_text(card, 'title', locale)}"
            for card in visible
        ) + "\n" + page_notice("support_cards", parsed[1], page, len(matches), locale) + "\n" + tr(locale, "next_support_card")

    return _command_tip(content)

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from .data import Card, Song, SongRepository, character_identity, localized_text, normalize, resolve_character_alias
from .entity_lexicon import known_alias_names, resolve_exact_alias
from .i18n import tr


HELP_TEXT = """Our Notes 查询指令
/查曲 [歌名或ID] [等级或lv比较式] [页N]：搜索歌曲列表，可翻页
/查谱面 歌名或ID [难度]：查看等级与 Note 数；100001 可简写为 1
/查卡 角色名或卡牌ID [页N]：搜索卡面，可翻页
/查缩写 昵称：查看昵称对应的角色、乐队等，也可直接用于查曲或查卡
/问 想查的内容：自然语言查询（复杂问法需配置 AI）
/数据状态：查看进程、最近同步与缓存状态
/帮助：查看本说明
示例：/查曲 mygo 27、/查曲 lv27、/查谱面 100001 EXPERT、/查卡 skk"""
HELP_TEXTS = {
    "zh": HELP_TEXT + "\n/语言：查看英文和日文指令",
    "en": """Our Notes commands
/song [title or ID] [level or lv level] [page N]: search songs
/chart <title or ID> [difficulty]: view level and note count
/card <character, title or ID> [page N]: search cards
/abbrev <nickname>: look up a character or band nickname
/ask <query>: natural language lookup (AI configuration needed for complex queries)
/status: process and data status  /language: language guide  /help: this guide
Example: /song mygo 27, /chart 100001 EXPERT, /card tomori""",
    "ja": """Our Notes コマンド
/曲 [曲名またはID] [レベルまたはlvレベル] [ページN]：楽曲を検索
/譜面 <曲名またはID> [難易度]：レベルとノーツ数
/カード <キャラクター名・カード名・ID> [ページN]：カードを検索
/略称 <略称>：キャラクター・バンドなどの略称
/問 <内容>：自然言語検索（複雑な質問にはAI設定が必要）
/状態：プロセスとデータの状態  /言語：言語案内  /ヘルプ：この案内
例：/曲 mygo 27、/譜面 100001 EXPERT、/カード ともり""",
}

COMMAND_HELP = {
    "songs": "查询歌曲列表，支持歌名、乐队、曲目 ID 或等级，每页 16 首。27 与 lv27 相同，均匹配整数等级 27（含显示等级 27.5）；27.5 与 lv27.5 均精确匹配显示等级。lv>=25、lv>25、lv<=25、lv<25 按歌曲最高显示等级筛选；可追加 diff=EXPERT 等难度，只比较该难度。纯数字若恰好是曲目 ID，优先按 ID 查询。\n用法：/查曲 [歌名或ID] [等级或lv比较式] [页N]\n示例：/查曲 迷星叫、/查曲 27、/查曲 mygo lv>=25、/查曲 mygo lv>=25 diff=EXPERT 页2",
    "chart": "查询谱面等级与 Note 数，支持歌名或曲目 ID。100001 可简写为 1。可选难度：EASY、NORMAL、HARD、EXPERT；不指定时显示全部难度。\n用法：/查谱面 <歌名或ID> [难度]\n示例：/查谱面 1 EXPERT、/查谱面 100001 EXPERT",
    "cards": "查询卡面，支持卡牌 ID、角色名、卡牌名或乐队名，每页 16 张。\n用法：/查卡 <关键词或ID> [页N]\n示例：/查卡 高松灯、/查卡 mygo 页2、/查卡 51\n找到多张卡时会显示列表，再用卡牌 ID 查看大图。",
    "abbrev": "查询已收录的角色、乐队等昵称；也可直接用昵称查曲或查卡。\n用法：/查缩写 <昵称>\n示例：/查缩写 skk、/查缩写 茉团、/查卡 墨缇丝",
}
COMMAND_HELPS = {
    "zh": COMMAND_HELP,
    "en": {
        "songs": "Search by title, band, ID, or level; 16 songs per page. 27 and lv27 match base level 27; 27.5 and lv27.5 match the displayed level exactly. An exact song ID takes priority over a bare number.\nUsage: /song [title or ID] [level or lv level] [page N]\nExample: /song mygo 27 page 2",
        "chart": "Look up level and note count by title or ID. Song 100001 can be shortened to 1. Optional difficulty: EASY, NORMAL, HARD, EXPERT.\nUsage: /chart <title or ID> [difficulty]\nExample: /chart 1 EXPERT",
        "cards": "Search by card ID, character, card title, or band, 16 per page.\nUsage: /card <query or ID> [page N]\nExample: /card tomori or /card mygo page 2",
        "abbrev": "Look up verified character and band nicknames.\nUsage: /abbrev <nickname>\nExample: /abbrev skk",
    },
    "ja": {
        "songs": "曲名・バンド・ID・レベルで検索します。1ページ16曲。27とlv27は基本レベル27、27.5とlv27.5は表示レベルを完全一致で検索します。数字が曲IDと一致する場合はIDを優先します。\n使い方：/曲 [曲名またはID] [レベルまたはlvレベル] [ページN]\n例：/曲 mygo 27 ページ2",
        "chart": "曲名またはIDでレベルとノーツ数を確認します。100001は1と省略できます。難易度省略時は全難易度を表示します。\n使い方：/譜面 <曲名またはID> [難易度]\n例：/譜面 1 EXPERT",
        "cards": "カードID、キャラクター名、カード名、バンド名で検索します。1ページ16枚。\n使い方：/カード <名前またはID> [ページN]\n例：/カード 祥子、/カード mygo ページ2、/カード 51",
        "abbrev": "登録済みのキャラクター・バンドなどの略称を調べます。\n使い方：/略称 <略称>\n例：/略称 skk",
    },
}

ALIASES = {
    "查曲": "songs", "song": "songs", "songs": "songs", "曲": "songs", "楽曲": "songs",
    "查谱面": "chart", "查谱": "chart", "谱面": "chart", "chart": "chart", "譜面": "chart",
    "查卡": "cards", "查卡面": "cards", "card": "cards", "cards": "cards", "カード": "cards",
    "查缩写": "abbrev", "abbrev": "abbrev", "略称": "abbrev",
}
CANONICAL = {"songs": "/查曲", "chart": "/查谱面", "cards": "/查卡", "abbrev": "/查缩写"}
CANONICAL_BY_LOCALE = {
    "zh": CANONICAL,
    "en": {"songs": "/song", "chart": "/chart", "cards": "/card", "abbrev": "/abbrev"},
    "ja": {"songs": "/曲", "chart": "/譜面", "cards": "/カード", "abbrev": "/略称"},
}
ENGLISH_COMMANDS = {"song", "songs", "chart", "card", "cards", "abbrev", "help", "status", "language", "event", "gacha", "prediction"}
JAPANESE_COMMANDS = {"曲", "楽曲", "譜面", "カード", "略称", "ヘルプ", "状態", "言語", "イベント", "ガチャ", "予想線"}
UNAVAILABLE_COMMANDS = {"查活动", "查卡池", "ycx", "预测线", "查预测线", "event", "gacha", "prediction", "イベント", "ガチャ", "予想線"}
UNAVAILABLE_REPLY = "该功能暂未上线"
PAGE_SIZE = 16


def _clean_message(content: str) -> str:
    content = re.sub(r"<@!?\w+>", "", content)
    return content.strip().lstrip("/／").strip()


def locale_for(content: str) -> str:
    text = _clean_message(content)
    first = text.split(None, 1)[0].casefold() if text else ""
    if first in ENGLISH_COMMANDS:
        return "en"
    if first in JAPANESE_COMMANDS:
        return "ja"
    return "zh"


def _split_page(query: str) -> tuple[str, int]:
    match = re.search(r"\s+(?:页\s*(\d+)|第\s*(\d+)\s*页|p(\d+)|page\s+(\d+)|ページ\s*(\d+))\s*$", query, re.I)
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


def card_matches(repository: SongRepository, query: str) -> list[Card]:
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
    unit = {"zh": {"songs": "首", "cards": "张"}, "en": {"songs": " songs", "cards": " cards"},
            "ja": {"songs": "曲", "cards": "枚"}}[locale][kind]
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
            if suffix and alias in {"查曲", "查谱面", "查卡", "查卡面"}:
                if suffix in {"角色"}:
                    return tr(locale, "unknown") + "\n" + HELP_TEXTS[locale]
                return tr(locale, "missing_space", command=canonical[ALIASES[alias]], argument=suffix) + "\n" + helps[ALIASES[alias]]

    candidates = {
        "zh": ("查曲", "查谱面", "查卡", "查缩写", "查活动", "查卡池", "ycx", "数据状态", "帮助"),
        "en": ("song", "chart", "card", "abbrev", "event", "gacha", "ycx", "status", "help"),
        "ja": ("曲", "譜面", "カード", "略称", "イベント", "ガチャ", "予想線", "状態", "ヘルプ"),
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
    hint: str | None = None


def resolve_command(content: str, repository: SongRepository) -> CommandResult | None:
    """Select records once so text and image use the same request result."""
    parsed = parse_query(content)
    if not parsed:
        return None
    kind, query, _ = parsed
    if kind == "songs":
        return CommandResult(parsed, songs=tuple(song_matches(repository, query)))
    if kind == "cards":
        return CommandResult(parsed, cards=tuple(card_matches(repository, query)))
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
    if not text:
        return HELP_TEXTS[locale]
    if text.casefold() in {"帮助", "help", "菜单", "指令", "ヘルプ"}:
        return HELP_TEXTS[locale]
    if text.casefold() in {"语言", "language", "言語"}:
        return tr(locale, "language")
    if text.split(None, 1)[0].casefold() in UNAVAILABLE_COMMANDS:
        return tr(locale, "unavailable")
    if text.casefold() in {"数据状态", "状态", "版本", "status", "状態"}:
        state = repository.cache_state
        if state not in {"fresh", "cached", "stale", "unsaved"}:
            state = "unknown"
        return tr(
            locale, "version", songs=len(repository.songs), cards=len(repository.cards),
            time=_safe_sync_time(repository.last_successful_sync_at, locale),
            cache=tr(locale, "cache_" + state),
        )

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
            for song in visible
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
            return tr(locale, "cards") + "\n" + f"{matches[0].id}  {'★' * matches[0].rarity} {localized_text(matches[0], 'character', locale)} · {localized_text(matches[0], 'title', locale)}"
        page = int(parsed[2])
        visible = page_slice(matches, page)
        if not visible:
            return page_notice("cards", parsed[1], page, len(matches), locale)
        return tr(locale, "cards") + "\n" + "\n".join(f"{c.id}  {'★' * c.rarity} {localized_text(c, 'character', locale)} · {localized_text(c, 'title', locale)}" for c in visible) + "\n" + page_notice("cards", parsed[1], page, len(matches), locale) + "\n" + tr(locale, "next_card")

    return _command_tip(content)

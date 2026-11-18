# L3
# Input: 命令文本、SongRepository；handle_command 可复用已捕获的 CommandResult。
# Output: resolve_command → CommandResult | None；handle_command → str | None，含确定性查询、帮助或状态文本。
# Pos: Query / Deterministic 的直接命令入口与结果契约；见 query/L2-2.md。
# Effects/Dependencies: 依赖 query 专项模块、文案与进程内调试计数；实体解析可读别名，详情可经 Data 联网或读写缓存，不调用模型。

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import TypeVar

from .data import Card, CatalogView, Skill, Song, SongRepository, SupportCard, character_identity, localized_text, normalize, resolve_character_alias
from .query.entity_lexicon import alias_status, known_alias_names, resolve_exact_alias, unverified_alias_count
from .i18n import tr
from .natural_query.query_debug import QUERY_DEBUG_COUNTERS
from .query.song_query import SongAnswer, parse_filter, execute as execute_song_filter
from .query.song_identity import direct_song_names, exact_songs, song_suggestions, song_suggestion_text
from .sources.haneoka.song_traits import describe as describe_song
from .query.card_catalog import CardAnswer, query_cards
from .query.event_cutoff_query import CutoffAnswer, parse_cutoff, execute_cutoff
from .query.contracts.info import InfoAnswer
from .bot_info import INTRO


HELP_TEXT = """Taki · Our Notes 日服资料查询
群内查询先 @机器人，回复 @发送者；启用聊天后，/聊 或叫立希、taki 无需 @，回复不 @。
/查曲 [歌名关键词/ID/乐队] [颜色] [激奏条件] [EX] [25以下]（歌名关键词用空格分隔，如 /查曲 迷 叫）
颜色：红、蓝、绿、黄、紫；例 /查曲 颜色=蓝/绿
如 /查曲 MyGO just 或 /查曲 激奏 just。just 表示包含，纯JUST 表示全段同类，混合 表示多种类型，JUST/JUST/COMBO 按顺序匹配。类型大小写均可。
/查谱面 歌名或ID [难度] [镜像／反转] [5/6.5/8/5/5]：歌名或 ID 在前，参数顺序不限；100001 可简写为 1
/查榜线 [jp/hk/kr/en] [id1或活动="名称"] [歌名或歌曲ID=100001] [50或20-40] [仅数值]：顺序不限，默认日服全部歌曲；无排名看趋势，有排名看表。50查40～60，20-40查区间，50-50查一名，1-100全榜；id1查往期本地末次观测，非确认终榜，裸数仍为排名
Haneoka 未确认原始歌曲序号，请用歌名或歌曲ID；旧T50也查附近，旧多T排名最多五个
/查活动 [jp/hk/kr/en]：当前／最近活动详情；/查活动 列表、/查活动 ID或名称 查询日服收录资料
/查卡池 [ID或名称]：日服当前卡池与UP卡；/查卡池 历史 查看已结束卡池
/查角色 [姓名/昵称/ID/乐队]：人物档案、乐队目录与关联卡；无参数查看角色目录
榜线当前排名同时显示分数、数字ID与用户名；缺失会标注，历史曲线仅记录分数。
四服：日服（jp）、国服（hk）、韩服（kr）、英服（en）；仅数值只回文字
/查卡 [BD/SSR/SR/R] [颜色=红色] [角色=tmr] [乐队=MyGO] [LIVE=分数提升] [击奏=JUST] [页N]：条件卡牌列表；指定ID看详情
/查留影卡 [卡名] [稀有度/颜色/角色/乐队] [LIVE分类] [激奏类型]：留影列表，支持 EX 延长、黄 just、扩判，发不完用 /继续（兼容 /查支援卡、/查SNAP）
/查卡面 ID、/查留影卡面 ID：只输出卡面；条件多选用逗号，同维度任选、跨维度同时满足
/查缩写 昵称：查看昵称对应的角色、乐队等，也可直接用于查曲或查卡
/聊 想聊的内容：聊天；也可在正文提到立希或 taki（需启用聊天 Agent）
/问、/ask：兼容 /查 的旧查询入口
/查 想查的内容：歌曲、谱面、成员卡和SNAP；活动、卡池和人物资料支持本地固定问法（其他复杂问法需配置 AI）
需要 AI 的问题会发往配置的 AI 服务；短期上下文最多保留 10 分钟。
/查 迷星叫 EX 物量：等级、Note、颜色、激奏、难度、数量及明确卡牌类型与ID的技能可本地短答
/继续：续发未发送完的结果；/详情：查看单项或已选结果；列表详情请按ID查询
自然续查支持 /查 第 N 个、看详情、换成 EX；10 分钟未成功续查失效，请单独发送
/数据状态：查看最近同步、缓存、可统计的未确认项与最近新增
/调试数据：本进程 AI API 成功调用与有效检索次数
/帮助：查看本说明
/介绍：了解 Taki 与数据来源
示例：/查曲 蓝色 MyGO EX 激奏 just、/查 哪些歌是纯COMBO、/查谱面 100001 EX
歌曲颜色／激奏来自 Haneoka 日服；未获取与旧缓存分别标注。"""
HELP_TEXTS = {
    "zh": HELP_TEXT + "\n/语言：查看英文和日文指令",
    "en": """Our Notes commands
/song [title keywords or ID] [level or lv level] (separate title keywords with spaces)
/chart <title or ID> [difficulty]: view level and note count
/card <character, title or ID> [page N]: search cards
/support <character, title or ID>: search support cards, use /继续 for unsent results
/查活动 [ID or title], /查卡池 [ID or title], /查角色 [name or ID]: JP information in Chinese
/abbrev <nickname>: look up a character or band nickname
/ask <query>: song, chart, member-skill or SNAP lookup; bounded Chinese event/gacha/profile questions also run locally
/status: process and data status  /language: language guide  /help: this guide
Example: /song mygo 27, /chart 100001 EXPERT, /card tomori""",
    "ja": """Our Notes コマンド
/曲 [曲名のキーワードまたはID] [レベルまたはlvレベル]（キーワードは空白で区切る）
/譜面 <曲名またはID> [難易度]：レベルとノーツ数
/カード <キャラクター名・カード名・ID> [ページN]：カードを検索
/サポート <キャラクター名・カード名・ID>：サポートカードを検索。未送信分は /继续
/查活动 [ID・名称]、/查卡池 [ID・名称]、/查角色 [名前・ID]：日本版資料（中国語表示）
/略称 <略称>：キャラクター・バンドなどの略称
/問 <内容>：楽曲・譜面・メンバースキル・SNAPを検索。中国語の定型文でイベント・ガチャ・人物資料もローカル検索
/状態：プロセスとデータの状態  /言語：言語案内  /ヘルプ：この案内
例：/曲 mygo 27、/譜面 100001 EXPERT、/カード ともり""",
}
HELP_TEXT += ('\n/查竞技场 [服] [乐队] [总览/日程/曲池/段位/奖励/榜单/人数] [赛季=N] [排名=1-20]'
              '\n/查玩家 玩家ID [服] [名片 [页N]]'
              '\n/查活动 [服或日服ID] 加成/奖励/任务/卡池'
              '\n/查活动 历史 ID 2到5 MyGO 红,蓝 挑战演出：日服活动筛选；列表同样适用'
              '\n/查活动 [jp/intl] 日程/日程开始/下期：静态日程，北京时间')
HELP_TEXTS['zh'] = HELP_TEXT + '\n/语言：查看英文和日文指令'

COMMAND_HELP = {
    "songs": (
        "/查曲 迷 叫（按歌名片段找歌）\n"
        "/查曲 MyGO 蓝色 EX（按条件筛歌）\n"
        "/查曲 100001（看歌曲详情）\n"
        "没发完时用 /继续。\n\n"
        "歌名关键词用空格分隔，也可用已收录昵称、乐队或 ID。"
        "按等级筛选可用 /查曲 MyGO EX 25以下。25以上、25以下含 25，大于25、小于25 不含 25，"
        "也可加「级」或使用小数，例如 27.5级以下。原 lv<=25 等写法仍可用，省略难度按最高等级。"
        "裸等级 27 包含 27.5。\n"
        "颜色=蓝/绿 匹配任一颜色。用 /查曲 MyGO just 查包含 JUST 的歌曲，也可写 激奏 just。类型大小写均可。"
        "纯COMBO 匹配整首同类，混合 匹配多种类型，JUST/JUST/COMBO 按顺序匹配，"
        "包含全部JUST/COMBO 要求同时包含两种类型。颜色、激奏等不同维度的条件都要满足。\n"
        "页N 指定文字列表的页码，例如 /查曲 蓝色 MyGO EX just 页2。"
    ),
    "chart": "查询谱面等级与 Note 数，支持歌名或曲目 ID。100001 可简写为 1。难度可写 EZ、NM、HD、EX 或全称。省略难度时，文字列出各档数据，图片只绘制一档，优先 EXPERT。\n歌名或 ID 在前，难度、镜像／反转和覆盖时间顺序不限。镜像左右翻转音符与划键方向。覆盖时间 5/6.5/8/5/5 按 S1～S5 指定最终秒数，也可写 覆盖=5/6.5/8/5/5。\n示例：/查谱面 1 HD 反转、/查谱面 1 镜像 5/6.5/8/5/5 EX",
    "cards": "角色卡条件查询始终返回列表，QQ单图优先，超限完整续发，显示完整总数，剩余内容用 /继续。ID精确查详情，/查卡面 ID 只发公开full卡面。\n用法：/查卡 [卡名] [BD/SSR/SR/R] [颜色=红色,蓝色] [角色=tmr,skk] [乐队=MyGO] [队长 同色] [LIVE=分数提升] [击奏=JUST] [得意=MyGO] [页N]\n不同维度AND、同维度多选OR，得意标签全部包含。BD为生日卡；旧二/三/四星别名保留。\n队长类型：同色、同技、同激、异色。例如 /查卡 mygo 队长 异色。\n演出短写：分卡、奶卡、判卡、血量加分、PERFECT加分。纯数字查ID，带筛选的数字查Lv.5演出加分，例如 /查卡 mygo 155、/查卡 SSR 150以上、/查卡 演出120到155。ID加条件用 /查卡 id 72 mygo。",
    "support_cards": (
        "/查留影卡 [卡名] [SSR/EX/SR/R] [颜色] [角色] [乐队] [LIVE分类] [激奏类型]\n"
        "例如 /查留影卡 EX 延长、/查留影卡 黄 just。LIVE 可写分卡、奶卡、判卡、延长，"
        "也可写 LIVE 延长 或 LIVE=技能延长。激奏可直接写 JUST、COMBO、LUCK，也可写 激奏 just。"
        "扩判筛 JUST 判定范围扩大，也可写 激奏 扩判。\n"
        "颜色可用红、蓝、绿、黄、紫，也可用红赤、绀碧、翡翠、琉金、紫苑。"
        "支持完整卡名和一段连续卡名，重名用 卡名=。条件可换序，同类多选任选。\n"
        "输入 ID 查详情，/查留影卡面 ID 只发卡面。QQ 单图优先，发不完用 /继续。"
    ),
    "abbrev": "查询已收录的角色、乐队等昵称；也可直接用昵称查曲或查卡。\n用法：/查缩写 <昵称>\n示例：/查缩写 skk、/查缩写 茉团、/查卡 墨缇丝",
}
COMMAND_HELPS = {
    "zh": COMMAND_HELP,
    "en": {
        "songs": (
            "/song 迷 叫 (find a song by title keywords)\n"
            "/song MyGO 蓝色 EX (filter by band, blue color, and difficulty)\n"
            "/song 100001 (view song details)\n"
            "Use /继续 if QQ has more results to send.\n\n"
            "Separate title keywords with spaces. Band names and registered nicknames also work. "
            "27 and lv27 match base level 27. 27.5 and lv27.5 match the displayed level exactly. "
            "An exact song ID takes priority over a bare number.\n"
            "For example, /song MyGO EX 25以下 includes level 25 and below. The lv<=25 form also works.\n"
            "Use /song MyGO just or /song 激奏 just to find songs containing JUST. Mission types are case-insensitive.\n"
            "Use page N for text pagination, for example /song mygo 27 page 2."
        ),
        "chart": "Look up level and note count by title or ID. Song 100001 can be shortened to 1. Use EZ, NM, HD, EX, or the full difficulty name. If omitted, text lists all available difficulties and the image shows one chart, preferring EXPERT.\nPut the title or ID first. Difficulty, mirror (镜像 or 反转), and five skill durations can follow in any order. The durations are one group in S1–S5 order.\nExample: /chart 1 镜像 5/6.5/8/5/5 EX",
        "cards": "Search by card ID, character, card title, or band. QQ shows complete results; use /继续 for unsent parts.\nUsage: /card <query or ID> [page N]\nExamples: /card tomori, /card mygo page 2. A lone integer is a card ID. To filter Lv.5 LIVE score gain, use /card mygo 155 or /card 155%. To test an ID against the band filter, use /card id 72 mygo.",
        "support_cards": "Search support cards by ID, character, exact title or one title fragment. Combine filters in any order. QQ shows complete results. Use /继续 for unsent parts.\nUsage: /support <query or ID>\nExamples: /support EX Abraca or /support 1",
        "abbrev": "Look up verified character and band nicknames.\nUsage: /abbrev <nickname>\nExample: /abbrev skk",
    },
    "ja": {
        "songs": (
            "/曲 迷 叫（曲名の一部で探す）\n"
            "/曲 MyGO 蓝色 EX（バンド・青属性・難易度で絞り込む）\n"
            "/曲 100001（楽曲情報を見る）\n"
            "QQで結果がまだ残っている場合は /继续 を送ってください。\n\n"
            "曲名のキーワードは空白で区切ります。バンド名や登録済みの略称も使えます。"
            "27とlv27は基本レベル27、27.5とlv27.5は表示レベル27.5に一致します。"
            "数字が曲IDと一致する場合はIDを優先します。\n"
            "/曲 MyGO EX 25以下 でレベル25以下を検索できます。lv<=25 も使えます。\n"
            "/曲 MyGO just や /曲 激奏 just で JUST を含む楽曲を検索します。タイプ名の大文字・小文字は区別しません。\n"
            "ページN はテキスト一覧のページ指定に使います。例 /曲 mygo 27 ページ2"
        ),
        "chart": "曲名またはIDでレベルとノーツ数を確認します。100001は1と省略できます。難易度はEZ、NM、HD、EXまたは正式名で指定できます。難易度を省略すると、テキストには取得済みの全難易度を表示し、画像はEXPERTを優先して1つの難易度を描画します。\n最初に曲名またはIDを書き、その後に難易度、ミラー（镜像 または 反转）、スキル時間を任意の順で指定できます。スキル時間はS1～S5の順にまとめて入力します。\n例：/譜面 1 镜像 5/6.5/8/5/5 EX",
        "cards": "カードID、キャラクター名、カード名、バンド名で検索します。QQでは全件表示し、未送信分は /继续 で受け取れます。\n使い方：/カード <名前またはID> [ページN]\n例：/カード 祥子、/カード mygo ページ2、/カード 51。整数だけの場合はカードIDです。Lv.5ライブスコア上昇率の検索は /カード mygo 155 または /カード 155%。IDとバンド指定の併用は /カード id 72 mygo。",
        "support_cards": "サポートカードID、キャラクター名、カード名で検索します。カード名は完全名か連続した一部分を指定でき、条件の順番は自由です。QQでは全件表示し、未送信分は /继续 で受け取れます。\n使い方：/サポート <名前またはID>\n例：/サポート EX Abraca、/サポート 1",
        "abbrev": "登録済みのキャラクター・バンドなどの略称を調べます。\n使い方：/略称 <略称>\n例：/略称 skk",
    },
}

ALIASES = {
    "查榜线": "event_cutoff", "榜线": "event_cutoff",
    "查曲": "songs", "song": "songs", "songs": "songs", "曲": "songs", "楽曲": "songs",
    "查谱面": "chart", "查谱": "chart", "谱面": "chart", "chart": "chart", "譜面": "chart",
    "查卡": "cards", "查角色卡": "cards", "查卡面": "cards", "card": "cards", "cards": "cards", "カード": "cards",
    "查留影卡": "support_cards", "查留影卡面": "support_cards", "查支援卡": "support_cards", "查snap": "support_cards", "snap": "support_cards", "查支援卡面": "support_cards", "查snap卡面": "support_cards", "支援卡": "support_cards", "support": "support_cards",
    "supportcard": "support_cards", "supportcards": "support_cards", "サポート": "support_cards",
    "查缩写": "abbrev", "abbrev": "abbrev", "略称": "abbrev",
}
CANONICAL = {"songs": "/查曲", "chart": "/查谱面", "cards": "/查卡", "support_cards": "/查留影卡", "abbrev": "/查缩写"}
CANONICAL_BY_LOCALE = {
    "zh": CANONICAL,
    "en": {"songs": "/song", "chart": "/chart", "cards": "/card", "support_cards": "/support", "abbrev": "/abbrev"},
    "ja": {"songs": "/曲", "chart": "/譜面", "cards": "/カード", "support_cards": "/サポート", "abbrev": "/略称"},
}
ENGLISH_COMMANDS = {"song", "songs", "chart", "card", "cards", "support", "supportcard", "supportcards", "abbrev", "help", "status", "language", "event", "gacha", "prediction"}
JAPANESE_COMMANDS = {"曲", "楽曲", "譜面", "カード", "サポート", "略称", "ヘルプ", "状態", "言語", "イベント", "ガチャ", "予想線"}
UNAVAILABLE_COMMANDS = {"ycx", "预测线", "查预测线", "gacha", "prediction", "ガチャ", "予想線"}
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


def split_page(query: str) -> tuple[str, int]:
    match = re.search(r"(?:\s+|(?=第|页|ページ))(?:页\s*(\d+)|第\s*(\d+)\s*页|p(\d+)|page\s+(\d+)|ページ\s*(\d+))\s*$", query, re.I)
    if not match:
        return query, 1
    return query[:match.start()].strip(), int(next(value for value in match.groups() if value is not None))


# Historical internal entry point remains available to existing callers.
_split_page = split_page

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


def song_matches(repository: SongRepository, query: str, *, match_names=_song_name_matches) -> list[Song]:
    """Apply an optional trailing level filter after the title/band/ID search."""
    exact = exact_songs(repository, query)
    if exact:
        return list(exact)
    normalized = unicodedata.normalize("NFKC", query)
    level_range = re.search(
        r"(?:^|\s)lv\s*(>=|>|<=|<)\s*(\d+(?:\.\d+)?)"
        r"(?:\s+diff=(EASY|NORMAL|HARD|EXPERT))?\s*$", normalized, re.I,
    )
    if level_range:
        term = normalized[:level_range.start()].strip()
        matches = match_names(repository, term)
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
    matches = match_names(repository, term)
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


_CHART_DIFFICULTIES = {
    "E": "EASY", "EZ": "EASY", "EASY": "EASY", "イージー": "EASY", "简单": "EASY",
    "N": "NORMAL", "NM": "NORMAL", "NORMAL": "NORMAL", "ノーマル": "NORMAL", "普通": "NORMAL",
    "H": "HARD", "HD": "HARD", "HARD": "HARD", "ハード": "HARD", "困难": "HARD",
    "EX": "EXPERT", "EXP": "EXPERT", "EXPERT": "EXPERT", "エキスパート": "EXPERT", "专家": "EXPERT",
}


def _chart_difficulty(word: str) -> str | None:
    return _CHART_DIFFICULTIES.get(unicodedata.normalize("NFKC", word).upper())


def _looks_like_chart_coverage(word: str) -> bool:
    if word.startswith("覆盖="):
        return True
    if "/" not in word:
        return False
    values = word.split("/")
    # Keep numeric slash groups and recognize a mistyped value in a five-part group.
    number_like = sum(bool(re.fullmatch(r"[0-9.eE+-]*|[+-]?(?:nan|inf(?:inity)?)", value, re.I))
                      for value in values)
    return number_like == len(values) or (len(values) == 5 and number_like >= 3)


def _parse_chart_coverage(word: str, locale: str) -> tuple[float, ...]:
    values = word.removeprefix("覆盖=").split("/")
    if len(values) != 5 or any(not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", v) for v in values):
        raise ValueError(tr(locale, "chart_coverage_invalid"))
    durations = tuple(float(value) for value in values)
    if any(not math.isfinite(duration) or duration <= 0 for duration in durations):
        raise ValueError(tr(locale, "chart_coverage_invalid"))
    return durations


def _parse_chart_arguments(
    query: str, repository: SongRepository | None = None, locale: str = "zh",
) -> tuple[str, str | None, bool, tuple[float, ...]]:
    """Consume a suffix of chart options, stopping at the longest exact song name."""
    from .query.song_identity import exact_songs

    difficulty = None
    mirror = False
    coverage = ()
    while True:
        if repository is not None and exact_songs(repository, query):
            return query, difficulty, mirror, coverage
        parts = query.rsplit(None, 1)
        if len(parts) == 1:
            break
        name, word = parts
        requested_difficulty = _chart_difficulty(word)
        if requested_difficulty:
            if difficulty is not None:
                raise ValueError(tr(locale, "chart_difficulty_repeated"))
            difficulty = requested_difficulty
        elif word in {"镜像", "反转"}:
            if mirror:
                raise ValueError(tr(locale, "chart_mirror_repeated"))
            mirror = True
        elif _looks_like_chart_coverage(word):
            if coverage:
                raise ValueError(tr(locale, "chart_coverage_repeated"))
            coverage = _parse_chart_coverage(word, locale)
        else:
            break
        query = name

    # Only chart lookup accepts the shortened 100000-series song ID.
    short_id = unicodedata.normalize("NFKC", query)
    if short_id.isdecimal() and 1 <= int(short_id) < 100000:
        query = str(100000 + int(short_id))
    return query, difficulty, mirror, coverage


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
        try:
            query, difficulty, _, _ = _parse_chart_arguments(query)
        except ValueError:
            return None
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
    for name in ("查活动", "查卡池", "查角色"):
        if first.startswith(name) and first != name and not first.startswith("查角色卡"):
            argument = first[len(name):] + (" " + rest if rest else "")
            return tr(locale, "missing_space", command="/" + name, argument=argument)
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
            if suffix and alias in {"查曲", "查谱面", "查卡", "查卡面", "查留影卡", "查留影卡面", "查支援卡", "支援卡"}:
                if suffix in {"角色"}:
                    return tr(locale, "unknown") + "\n" + HELP_TEXTS[locale]
                return tr(locale, "missing_space", command=canonical[ALIASES[alias]], argument=suffix) + "\n" + helps[ALIASES[alias]]

    candidates = {
        "zh": ("查曲", "查谱面", "查卡", "查留影卡", "查缩写", "查活动", "查卡池", "查角色", "ycx", "数据状态", "帮助"),
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
    from .data import note_text
    return "\n".join(
        f"{chart.difficulty:<6} Lv.{chart.display_level:g} · {note_text(chart.notes)}"
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
    from .query.song_identity import unique_candidates, candidate_text
    matches = unique_candidates(repository, query)
    if not matches:
        return None, tr(locale, "not_found_generic", query=query)
    from .query.song_identity import literal_song_match
    if len(matches) > 1 or (matches and not literal_song_match(repository, query, matches[0])):
        return None, candidate_text(matches)
    return matches[0], None


@dataclass(frozen=True)
class CommandResult:
    parsed: tuple[str, str, str | int | None]
    songs: tuple[Song, ...] = ()
    cards: tuple[Card, ...] = ()
    support_cards: tuple[SupportCard, ...] = ()
    hint: str | None = None
    catalog: CardAnswer | None = None
    song_selection: SongAnswer | None = None
    cutoff: CutoffAnswer | None = None
    status: str = "success"
    candidates: tuple[Song, ...] = ()
    query_spec: object | None = None
    activity: object | None = None
    info: InfoAnswer | None = None
    chart_mirror: bool = False
    chart_coverage: tuple[float, ...] = ()

    def __post_init__(self):
        for answer in (self.cutoff, self.catalog, self.song_selection, self.activity, self.info):
            if answer is not None:
                object.__setattr__(self, "status", answer.status)
                return
        if self.status == "success" and self.parsed[0] in {"songs", "cards", "support_cards"}:
            if not (self.songs or self.cards or self.support_cards):
                object.__setattr__(self, "status", "empty")


def resolve_command(content: str, repository: SongRepository, *,
                    direct_song_keywords=True) -> CommandResult | None:
    """Select records once so text and image use the same request result."""
    text = _clean_message(content)
    parts = text.split(None, 1)
    head = parts[0].casefold() if parts else ""
    if head in {"查竞技场", "查玩家"}:
        from .query.public_queries import execute_arena, execute_player
        execute = execute_arena if head == "查竞技场" else execute_player
        answer = execute(parts[1] if len(parts) > 1 else "", repository)
        return CommandResult(("arena" if head == "查竞技场" else "player", text, None), info=answer)
    if head == "查角色":
        from .query.character_query import execute_character
        answer = execute_character(parts[1] if len(parts) > 1 else "", repository)
        return CommandResult(("character", text, None), info=answer)
    if head == "查卡池":
        from .query.gacha_query import execute_gacha
        answer = execute_gacha(parts[1] if len(parts) > 1 else "", repository)
        return CommandResult(("gacha", text, None), info=answer)
    if head in {"查活动", "event", "イベント"}:
        from .query.activity_query import execute_activity
        answer = execute_activity(parts[1] if len(parts) > 1 else "", repository)
        return CommandResult(("activity", text, None), activity=answer)
    if head in {"查榜线", "榜线"}:
        answer = execute_cutoff(parse_cutoff(text), repository)
        return CommandResult(("event_cutoff", answer.request.query, answer.request.rank), cutoff=answer)
    kind = ALIASES.get(head)
    if kind in {"cards", "support_cards"} or head in {"查角色卡", "查snap", "snap", "查支援卡面", "查snap卡面"}:
        support = kind == "support_cards" or head in {"查snap", "snap", "查支援卡面", "查snap卡面"}
        query = parts[1] if len(parts) > 1 else ""
        art = head in {"查留影卡面", "查卡面", "查支援卡面", "查snap卡面"}
        answer = query_cards(query, repository, support=support, art=art, direct_command=True)
        kind = "support_cards" if support else "cards"
        return CommandResult((kind, answer.request.query, answer.request.page),
                             cards=() if support else answer.cards,
                             support_cards=answer.cards if support else (), catalog=answer)
    # Page syntax can also be part of a complete title.
    if kind == "songs" and direct_song_keywords and len(parts) > 1:
        raw_query = parts[1].strip()
        if split_page(raw_query)[0] != raw_query:
            exact = exact_songs(repository, raw_query)
            if exact:
                return CommandResult(("songs", raw_query, 1), songs=tuple({song.id: song for song in exact}.values()))
    mirror = False
    coverage = ()
    if kind == "chart" and len(parts) > 1:
        try:
            query, difficulty, mirror, coverage = _parse_chart_arguments(parts[1], repository, locale_for(content))
        except ValueError as exc:
            return CommandResult(("chart", parts[1], None), hint=str(exc), status="invalid_arguments")
        parsed = ("chart", query, difficulty)
    else:
        parsed = parse_query(content)
    if not parsed:
        return None
    kind, query, _ = parsed
    if kind == "songs":
        try:
            exact = exact_songs(repository, query)
            if exact:
                return CommandResult(parsed, songs=tuple({song.id: song for song in exact}.values()))
            has_conditions = re.search(r"颜色|属性|激奏|击奏|乐队=|\blv|[<>=]|(?:^|\s)(?:EX|HD|NM|EZ|EXPERT|HARD|NORMAL|EASY)(?:\s|$)", query, re.I)
            request = parse_filter(query, repository, force=bool(has_conditions), direct_syntax=direct_song_keywords)
            if request is not None:
                answer = execute_song_filter(request, repository, int(parsed[2]),
                                             direct_song_keywords=direct_song_keywords)
                return CommandResult(parsed, songs=answer.songs, song_selection=answer, candidates=answer.suggestions)
        except ValueError as exc:
            return CommandResult(parsed, hint=str(exc), status="invalid_arguments")
        if direct_song_keywords:
            suggestion_limit = 0

            def select_names(repo, term):
                nonlocal suggestion_limit
                songs, suggestion_limit = direct_song_names(repo, term)
                return list(songs)

            songs = tuple(song_matches(repository, query, match_names=select_names))
            if songs and not suggestion_limit:
                return CommandResult(parsed, songs=songs)
            locale = locale_for(content)
            warning = ("Haneoka 旧缓存；未命中不代表最新资料不存在。"
                       if repository.capture_catalog().cache_state == "stale" else "")
            if songs:
                candidates = song_suggestions(songs, suggestion_limit)
                return CommandResult(parsed, status="ambiguous", candidates=candidates,
                                     hint=song_suggestion_text(candidates, locale, warning, limit=suggestion_limit))
            hint = tr(locale, "not_found_song", query=query)
            return CommandResult(parsed, hint="\n".join(filter(None, (hint, warning))))
        return CommandResult(parsed, songs=tuple(song_matches(repository, query)))
    if kind in {"cards", "support_cards"}:
        try:
            if kind == "cards":
                return CommandResult(parsed, cards=tuple(card_matches(repository, query)))
            return CommandResult(parsed, support_cards=tuple(support_card_matches(repository, query)))
        except ValueError:
            return CommandResult(parsed, hint=CARD_RARITY_HELP[locale_for(content)])
    from .query.song_identity import chart_candidates, candidate_text, literal_song_match
    matches, short_typo = chart_candidates(repository, query)
    if matches and (short_typo or len(matches) > 1 or not literal_song_match(repository, query, matches[0])):
        hint = candidate_text(matches, parsed[2], mirror=mirror, coverage=coverage,
                              short_typo=short_typo, locale=locale_for(content))
        return CommandResult(parsed, hint=hint,
                             status="ambiguous", candidates=matches, chart_mirror=mirror, chart_coverage=coverage)
    song = matches[0] if matches else None
    hint = None if song else tr(locale_for(content), "not_found_generic", query=query)
    return CommandResult(parsed, songs=(song,) if song else (), hint=hint, chart_mirror=mirror, chart_coverage=coverage,
                         status="success" if song else "unknown_entity")


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


def _catalog_unknowns(catalog: CatalogView) -> dict[str, int]:
    """Count explicit unresolved fields, not inferred missing game content."""
    return {
        "notes": sum(chart.notes is None for song in catalog.songs for chart in song.charts),
        "traits": sum(not song.traits or not song.traits.color or not song.traits.missions
                      for song in catalog.songs),
        "member_skills": sum(card.catalog.get("skill_index_complete") is False for card in catalog.cards),
        "member_summaries": sum(card.catalog.get("compact_summary_complete",
                                                  card.catalog.get("member_summary_complete")) is False
                                for card in catalog.cards),
        "member_tags": sum(card.catalog.get("tags_complete") is False for card in catalog.cards),
        "support_skills": sum(card.catalog.get("skill_index_complete") is False
                              for card in catalog.support_cards),
        "activity_times": sum(item.start_at is None or item.end_at is None for item in catalog.activities),
        "gacha_times": sum(item.start_at is None or item.end_at is None for item in catalog.gachas),
    }


def _catalog_change_text(catalog: CatalogView, locale: str) -> str:
    summary = catalog.metadata.get("change_summary")
    if not isinstance(summary, dict) or not isinstance(summary.get("ids"), dict):
        return tr(locale, "changes_unavailable")
    names = {
        "songs": {item.id: localized_text(item, "title", locale) for item in catalog.songs},
        "cards": {item.id: localized_text(item, "title", locale) for item in catalog.cards},
        "support-cards": {item.id: localized_text(item, "title", locale) for item in catalog.support_cards},
        "characters": {item.id: item.names.get(locale) or item.name for item in catalog.characters},
        "gacha": {item.id: item.names.get(locale) or item.title for item in catalog.gachas},
        "events": {item.id: item.names.get(locale) or item.title for item in catalog.activities},
    }
    labels = {
        "zh": ("歌曲", "成员卡", "留影卡", "人物", "卡池", "活动"),
        "en": ("songs", "member cards", "support cards", "characters", "gachas", "events"),
        "ja": ("楽曲", "メンバーカード", "サポートカード", "キャラクター", "ガチャ", "イベント"),
    }[locale]
    rows = []
    for key, label in zip(names, labels):
        ids = summary["ids"].get(key, ())
        if not isinstance(ids, list) or not ids:
            continue
        examples = []
        for ident in ids[:3]:
            title = " ".join(str(names[key].get(ident, "")).split())[:36]
            examples.append(f"{ident} {title}".strip())
        rows.append(f"{label} {len(ids)}：{'、'.join(examples)}" + (f" 等" if len(ids) > 3 else ""))
    stamp = summary.get("at")
    when = _safe_sync_time(datetime.fromtimestamp(stamp, timezone.utc).isoformat(), locale) if type(stamp) in (int, float) and 0 < stamp < 253402300800 else tr(locale, "unknown_time")
    heading = "changes_unsaved" if catalog.metadata.get("change_summary_saved") is False else "changes_header"
    return tr(locale, heading, time=when) + "\n" + ("\n".join(rows) if rows else tr(locale, "changes_none"))


def handle_command(content: str, repository: SongRepository,
                   resolved: CommandResult | None = None) -> str | None:
    text = _clean_message(content)
    locale = locale_for(content)
    if text.casefold() in {"介绍", "about", "简介"}:
        return INTRO
    if not text:
        return HELP_TEXTS[locale]
    from .query.continuation import parse_operation, MISSING
    if parse_operation(text) is not None:
        return MISSING
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
        catalog = repository.capture_catalog()
        state = catalog.cache_state
        if state not in {"fresh", "cached", "stale", "unsaved"}:
            state = "unknown"
        status = tr(
            locale, "version", songs=len(catalog.songs), cards=len(catalog.cards),
            support_cards=len(catalog.support_cards),
            skill_index=f"{sum(bool(card.skills) for card in catalog.cards)}/{len(catalog.cards)}",
            time=_safe_sync_time(catalog.last_successful_sync_at, locale),
            cache=tr(locale, "cache_" + state),
        )
        traits = repository.song_traits
        saved = traits.saved
        main_catalog = getattr(repository, "data_source", "yume") == "haneoka"
        if main_catalog:
            status = status.replace("Project Yume", "Haneoka JP")
            saved = {"release": catalog.metadata.get("data_version", "未知"),
                     "fetched_at": catalog.metadata.get("cached_at")}
        count = sum(bool(s.traits and s.traits.color and s.traits.missions) for s in catalog.songs)
        history = repository.event_cutoffs.history
        history_text = "\n" + history.status_text() if history else "\n榜线历史：未配置"
        sampler = getattr(repository, "cutoff_sampler", None)
        history_text += "\n周期采样：" + (sampler.last_state if sampler else "未启动")
        catalog_text = ""
        if main_catalog:
            counts = _catalog_unknowns(catalog)
            labels = {
                "zh": ("谱面 Note", "歌曲属性／激奏", "成员卡技能", "成员卡摘要", "得意标签", "留影卡技能", "活动时间", "卡池时间"),
                "en": ("chart notes", "song traits", "member skills", "member summaries", "best-song tags", "support skills", "event times", "gacha times"),
                "ja": ("譜面ノーツ", "楽曲属性／激奏", "メンバースキル", "メンバー概要", "得意タグ", "サポートスキル", "イベント時刻", "ガチャ時刻"),
            }[locale]
            detail = "、".join(f"{label} {value}" for label, value in zip(labels, counts.values()) if value)
            catalog_text = "\n" + tr(locale, "unknown_catalog", count=sum(counts.values()), detail=detail or "0")
            catalog_text += "\n" + _catalog_change_text(catalog, locale)
        return (status + "\n" + tr(locale, "aliases_" + alias_status()) + tr(locale, "aliases_unverified", count=unverified_alias_count(repository)) + f"\n歌曲颜色／激奏：{count}/{len(catalog.songs)} · Haneoka 日服 · "
                + ("旧缓存" if (catalog.cache_state == "stale" if main_catalog else traits.stale) else "有效缓存" if saved else "等待后台获取")
                + (f"\n属性版本：{saved['release']}\n本机获取：{_safe_sync_time(saved['fetched_at'], locale)}" if saved else "") + catalog_text + history_text)

    if text.casefold() in {"查缩写", "abbrev", "略称"}:
        return COMMAND_HELPS[locale]["abbrev"]
    if text.split(None, 1)[0].casefold() in {"查缩写", "abbrev", "略称"} and len(text.split(None, 1)) > 1:
        abbreviation = text.split(None, 1)[1].strip()
        alias = resolve_character_alias(abbreviation)
        if alias:
            cards = repository.search_cards(abbreviation, limit=1)
            name = localized_text(cards[0], "character", locale) if cards else alias.display
            key = "alias_found" if cards else "alias_found_support" if support_card_matches(repository, abbreviation) else "alias_missing_card"
            return tr(locale, key, query=abbreviation, name=name)
        song_alias = resolve_exact_alias("song", abbreviation, repository)
        card_alias = resolve_exact_alias("card", abbreviation, repository)
        support_alias = resolve_exact_alias("support_card", abbreviation, repository)
        if song_alias.ambiguous or card_alias.ambiguous or support_alias.ambiguous:
            return tr(locale, "alias_ambiguous", query=abbreviation)
        entities = {r.entity for r in (song_alias, card_alias, support_alias) if r.entity}
        found = next(iter(entities), None)
        if len(entities) > 1:
            return tr(locale, "alias_ambiguous", query=abbreviation)
        if found:
            if found.kind == "band":
                return tr(locale, "alias_found_band", query=abbreviation, name=found.value)
            if found.kind == "song":
                return tr(locale, "alias_found_song", query=abbreviation, name=found.value)
            if found.kind == "support_card":
                return tr(locale, "alias_found_support", query=abbreviation, name=found.value)
            if found.kind == "card":
                return tr(locale, "alias_found", query=abbreviation, name=found.value)
            cards = card_matches(repository, abbreviation)
            name = localized_text(cards[0], "character", locale) if cards else found.value
            return tr(locale, "alias_found" if cards else "alias_found_support", query=abbreviation, name=name)
        known = "、".join(known_alias_names(repository))
        return tr(locale, "alias_unknown", query=abbreviation, names=known)

    selection = resolved if resolved is not None else resolve_command(content, repository)
    parsed = selection.parsed if selection else None
    if selection and selection.info is not None:
        return selection.info.text
    if selection and selection.activity is not None:
        return selection.activity.text
    if selection and selection.status in {"ambiguous", "invalid_arguments"} and selection.hint:
        return selection.hint
    if selection and selection.cutoff is not None:
        return selection.cutoff.text
    if selection and selection.catalog is not None:
        return selection.catalog.text(locale)
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
            f"{index:02d}. ID {song.id}  {localized_text(song, 'title', locale)} · {localized_text(song, 'band', locale)}  " + " / ".join(f"{chart.display_level:g}" for chart in song.charts)
            + "\n" + describe_song(song) for index, song in enumerate(visible, 1)
        ) + "\n" + page_notice("songs", parsed[1], page, len(matches), locale) + "\n" + tr(locale, "song_list_actions")

    if parsed and parsed[0] == "chart":
        query = parsed[1]
        song = selection.songs[0] if selection.songs else None
        hint = selection.hint
        if not song:
            from .query.song_identity import is_three_han_title
            suggestion = ""
            if not is_three_han_title(normalize(query)):
                variants = [(title, f"{localized_text(item, 'title', locale)}（{item.id}）") for item in repository.songs
                            for title in (*item.titles, *item.localized.get("title", {}).values())]
                suggestion = _suggest(query, variants, locale)
            return tr(locale, "not_found_chart", query=query) + suggestion + "\n" + COMMAND_HELPS[locale]["chart"]
        charts = song.charts if parsed[2] is None else tuple(c for c in song.charts if c.difficulty == parsed[2])
        from .data import note_text
        result = f"[{tr(locale, 'chart')}] {localized_text(song, 'title', locale)}（ID {song.id}）\n" + ("\n".join(f"{c.difficulty} Lv.{c.display_level:g} · {note_text(c.notes)}" for c in charts) or tr(locale, "no_charts"))
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

# L3
# Input: 来源记录／详情、主缓存、数据基址与缓存路径、加载／刷新请求及名称检索词。
# Output: Song／Chart／Card／SupportCard／Skill、名称规则、SongRepository 聚合记录与缓存／完整度状态。
# Pos: Data / Catalog 的领域记录、主资料聚合、详情与主缓存实现；见 L2-2-Catalog.md。
# Effects/Dependencies: 调用 Sources，调度并发详情并读写主缓存；分数表 music_data 独立惰性读取，不参加主目录事务；frozen 记录含 dict。

from __future__ import annotations

import json
import re
import time
from threading import RLock
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .sources.haneoka.song_traits import SongTraits, SongTraitsRepository


@dataclass(frozen=True)
class Chart:
    difficulty: str
    level: int
    display_level: float
    notes: int | None
    chart_file: str


def note_text(notes: int | None) -> str:
    return f"{notes} Notes" if notes is not None else "Note：暂无资料"


def known_notes(value: object) -> int | None:
    """Only the source can establish a real zero; missing data is not zero."""
    return value if type(value) is int and value >= 0 else None


@dataclass(frozen=True)
class Song:
    id: int
    title: str
    titles: tuple[str, ...]
    band: str
    composer: str
    lyricist: str
    arranger: str
    start_at: str
    jacket_url: str
    charts: tuple[Chart, ...]
    localized: dict[str, dict[str, str]] = field(default_factory=dict)
    traits: SongTraits | None = None


@dataclass(frozen=True)
class Skill:
    kind: str
    name: str
    description: str
    localized: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Card:
    id: int
    asset_id: int
    title: str
    character: str
    band: str
    rarity: int
    card_type: int
    performance: int
    technic: int
    visual: int
    start_at: str
    skill_name: str
    full_url: str
    thumbnail_url: str
    localized: dict[str, dict[str, str]] = field(default_factory=dict)
    skills: tuple[Skill, ...] = ()
    catalog: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SupportCard:
    id: int
    title: str
    character: str
    characters: tuple[str, ...]
    rarity: int
    card_type: int
    performance: int
    technic: int
    visual: int
    start_at: str
    full_url: str
    thumbnail_url: str
    localized: dict[str, dict[str, str]] = field(default_factory=dict)
    skills: tuple[Skill, ...] = ()
    catalog: dict[str, Any] = field(default_factory=dict)


class DataError(RuntimeError):
    pass


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.translate(str.maketrans({"灯": "燈", "爱": "愛"}))
    # Match Japanese hiragana and katakana spellings of the same title.
    value = "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in value)
    return "".join(char for char in value if not char.isspace() and char not in "-_·・!！?？'\"“”‘’")


def localized_text(item: Song | Card | SupportCard | Skill, field_name: str, locale: str = "zh") -> str:
    original = str(getattr(item, field_name))
    return item.localized.get(field_name, {}).get(locale) or original


_ROMAJI = {
    "kya": "きゃ", "kyu": "きゅ", "kyo": "きょ", "sha": "しゃ", "shu": "しゅ", "sho": "しょ",
    "cha": "ちゃ", "chu": "ちゅ", "cho": "ちょ", "nya": "にゃ", "nyu": "にゅ", "nyo": "にょ",
    "hya": "ひゃ", "hyu": "ひゅ", "hyo": "ひょ", "mya": "みゃ", "myu": "みゅ", "myo": "みょ",
    "rya": "りゃ", "ryu": "りゅ", "ryo": "りょ", "gya": "ぎゃ", "gyu": "ぎゅ", "gyo": "ぎょ",
    "ja": "じゃ", "ju": "じゅ", "jo": "じょ", "bya": "びゃ", "byu": "びゅ", "byo": "びょ",
    "pya": "ぴゃ", "pyu": "ぴゅ", "pyo": "ぴょ", "shi": "し", "chi": "ち", "tsu": "つ", "fu": "ふ",
    "ya": "や", "yu": "ゆ", "yo": "よ", "wa": "わ", "wo": "を",
    "a": "あ", "i": "い", "u": "う", "e": "え", "o": "お",
}
for _consonant, _kana in {
    "k": "かきくけこ", "s": "さしすせそ", "t": "たちつてと", "n": "なにぬねの",
    "h": "はひふへほ", "m": "まみむめも", "r": "らりるれろ",
    "g": "がぎぐげご", "z": "ざじずぜぞ", "d": "だぢづでど",
    "b": "ばびぶべぼ", "p": "ぱぴぷぺぽ",
}.items():
    _ROMAJI.update({f"{_consonant}{vowel}": character for vowel, character in zip("aiueo", _kana) if character != " "})


def roman_to_hiragana(value: str) -> str:
    """Add phonetic search aliases for romanized Japanese names, not translations."""
    text = value.casefold().split("(", 1)[0].replace(" ", "")
    if not re.fullmatch(r"[a-z]+", text):
        return ""
    result = []
    index = 0
    while index < len(text):
        if index + 1 < len(text) and text[index] == text[index + 1] and text[index] not in "aeioun":
            result.append("っ")
            index += 1
            continue
        if text[index] == "n" and (index + 1 == len(text) or text[index + 1] not in "aeiouy"):
            result.append("ん")
            index += 1
            continue
        match = next((text[index:index + size] for size in (3, 2, 1) if text[index:index + size] in _ROMAJI), None)
        if not match:
            return ""
        result.append(_ROMAJI[match])
        index += len(match)
    return "".join(result)


@dataclass(frozen=True)
class CharacterAlias:
    display: str
    search_terms: tuple[str, ...]


# The roster follows https://bang-dream-on.bushimo.jp/ . Short Latin forms include
# user-requested nicknames and unambiguous abbreviations of the official names.
# Ave Mujica members need both their civilian and stage names for card search.
CHARACTER_GROUPS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("灯", ("高松燈",), ("tmr", "tomori", "灯", "燈")),
    ("爱音", ("千早愛音",), ("anon", "爱音", "愛音")),
    ("乐奈", ("要楽奈", "要乐奈"), ("rana", "乐奈", "楽奈")),
    ("素世", ("長崎そよ", "长崎爽世", "长崎素世"), ("soyo", "素世", "爽世", "そよ")),
    ("立希", ("椎名立希",), ("rikki", "rkk", "taki", "立希")),
    ("初华", ("三角初華", "三角初华", "ドロリス"), ("uika", "uik", "doloris", "dls", "初华", "初華")),
    ("睦", ("若葉睦", "モーティス"), ("mtm", "mutsumi", "mortis", "睦", "若叶睦", "若葉睦")),
    ("海铃", ("八幡海鈴", "八幡海铃", "ティモリス"), ("umiri", "umr", "timoris", "海铃", "海鈴")),
    ("喵梦", ("祐天寺にゃむ", "祐天寺若麦", "アモーリス"), ("nyamu", "nym", "amoris", "にゃむ", "喵梦")),
    ("祥子", ("豊川祥子", "丰川祥子", "オブリビオニス"), ("skk", "saki", "sakiko", "oblivionis", "祥子")),
    ("阿拉蕾", ("仲町あられ", "仲町阿拉蕾"), ("arl", "arale", "阿拉蕾", "あられ")),
    ("野乃花", ("宮永ののか", "宫永野乃花"), ("nnk", "nonoka", "野乃花", "ののか")),
    ("峰月律", ("峰月律",), ("rts", "ritsu", "峰月律")),
    ("藤都子", ("藤都子",), ("myk", "miyako", "藤都子")),
    ("千石由乃", ("千石ユノ",), ("yuno", "千石由乃", "千石ユノ")),
    ("汐见萤", ("汐見蛍",), ("htr", "hotaru", "汐见萤", "汐見蛍")),
    ("伊泽枣", ("伊沢なつめ", "伊泽枣", "伊泽夏目"), ("ntsm", "natsume", "伊泽枣", "伊泽夏目", "伊沢なつめ")),
    ("琴平凪", ("琴平凪",), ("nagi", "琴平凪")),
    ("滨崎茉幌", ("浜崎まほろ", "滨崎茉幌", "滨崎真幌"), ("mhr", "mahoro", "滨崎茉幌", "滨崎真幌", "浜崎まほろ")),
    ("和泉朋花", ("和泉朋花",), ("hka", "houka", "和泉朋花")),
    ("须贺蕾叶", ("須賀蕾叶",), ("raika", "rka", "须贺蕾叶", "須賀蕾叶")),
    ("马桥心玖", ("馬橋心玖",), ("miku", "mku", "马桥心玖", "馬橋心玖")),
    ("矢仓蓬咲", ("矢倉蓬咲",), ("ymg", "yomogi", "矢仓蓬咲", "矢倉蓬咲")),
    ("梅里千樱梨", ("梅里ちえり", "梅里千樱梨", "梅里千绘里"), ("chr", "chieri", "梅里千樱梨", "梅里千绘里", "梅里ちえり")),
    ("四宫宁月", ("四宮寧月",), ("szk", "shizuku", "四宫宁月", "四宮寧月")),
)

CHARACTER_ALIASES: dict[str, CharacterAlias] = {
    normalize(name): CharacterAlias(display, search_terms)
    for display, search_terms, names in CHARACTER_GROUPS
    for name in (*search_terms, *names)
}
for _display, _terms, _names in CHARACTER_GROUPS:
    for _term in _names:
        if _term.isascii() and len(_term) > 3:
            _kana = roman_to_hiragana(_term)
            if _kana:
                CHARACTER_ALIASES.setdefault(normalize(_kana), CharacterAlias(_display, _terms))


def resolve_character_alias(query: str) -> CharacterAlias | None:
    return CHARACTER_ALIASES.get(normalize(query))


def character_identity(name: str) -> str:
    """Treat an Ave Mujica stage name and its civilian name as one character."""
    for part in name.split("/"):
        alias = resolve_character_alias(part.strip())
        if alias:
            return alias.display
    return name.strip()


class SongRepository:
    CACHE_SCHEMA = 4

    def __init__(self, data_base: str, cache_file: Path, cache_ttl_hours: float = 6, *, meta_source="moenotes") -> None:
        self.data_base = data_base.rstrip("/")
        self.cache_file = cache_file
        self.cache_ttl_hours = cache_ttl_hours
        self._song_lock = RLock()
        self.songs: list[Song] = []
        self.cards: list[Card] = []
        self.support_cards: list[SupportCard] = []
        self.metadata: dict[str, Any] = {}
        self._detail_cards: dict[int, Card] = {}
        self._detail_support_cards: dict[int, SupportCard] = {}
        self.cache_state = "unknown"
        self.last_successful_sync_at: str | None = None
        from .sources.haneoka.song_meta import MetaRepository
        self.song_meta = MetaRepository(cache_file.with_name("haneoka-meta-jp.json"))
        from .sources.moenotes_music_data import MusicDataRepository
        if meta_source not in {"moenotes", "haneoka"}:
            raise ValueError("unsupported meta source")
        self.meta_source = meta_source
        self.music_data = MusicDataRepository(cache_file.with_name("moenotes-music-data-v1.json"))
        self.song_traits = SongTraitsRepository(cache_file.with_name("haneoka-song-traits-jp.json"))
        from .sources.moenotes_events import EventCutoffRepository
        self.event_cutoffs = EventCutoffRepository(cache_file.parent / "moenotes-cutoff-v1")

    def refresh_song_traits(self) -> None:
        """Run off the QQ event loop; source failure cannot invalidate the main catalog."""
        self.song_traits.refresh()
        with self._song_lock:
            self.songs = self.song_traits.apply(self.songs)

    def load(self, refresh: bool = False) -> None:
        if refresh:
            self.refresh()
            return
        if not self._cache_is_fresh():
            try:
                self.refresh()
                return
            except DataError:
                if not self.cache_file.exists():
                    raise
                self._load_cache()
                self.cache_state = "stale"
                return
        self._load_cache()
        if (self.metadata.get("schema") != self.CACHE_SCHEMA
                or not self.metadata.get("member_skill_index_complete", False)
                or self.metadata.get("card_catalog_version") != 1):
            try:
                self.refresh()
            except DataError:
                if self.cache_state != "unsaved":
                    self.cache_state = "stale"
            return
        self.cache_state = "cached"

    def refresh(self) -> None:
        from .sources import yatta
        if self.data_base != yatta.BASE:
            raise DataError("仅允许 Project Yume 作为游戏数据源")
        try:
            from concurrent.futures import ThreadPoolExecutor
            names = ("characters", "membercards", "songs", "songsmeta", "supportcards")
            with ThreadPoolExecutor(max_workers=5) as pool:
                payloads = list(pool.map(lambda name: yatta.fetch_json(f"{yatta.MASTER}/{name}.json"), names))
            songs, cards = yatta.build_data(*payloads[:4])
            support_cards = yatta.build_support_cards(payloads[0], payloads[4], payloads[1].get("refs", {}).get("musicTags", {}))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.cache_state = "stale" if self.songs or self.cache_file.exists() else "unknown"
            raise DataError(f"Project Yume 数据同步失败，已保留原缓存：{exc}") from exc
        previous_details = {card.id: card for card in self.cards if card.skills}

        def fetch_member_detail(card: Card) -> Card:
            try:
                return self._merge_card_detail(card, yatta.card_detail(card.id))
            except (OSError, ValueError, KeyError, TypeError):
                previous = previous_details.get(card.id)
                if previous is None:
                    return card
                return replace(
                    card, performance=previous.performance, technic=previous.technic,
                    visual=previous.visual, skill_name=previous.skill_name, skills=previous.skills,
                    catalog={**previous.catalog, **card.catalog, "detail_stale": True},
                    localized={**card.localized,
                               "skill_name": previous.localized.get("skill_name", {})},
                )

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(cards)))) as pool:
            cards = list(pool.map(fetch_member_detail, cards))
        detail_count = sum(bool(card.skills) for card in cards)

        previous_support = {card.id: card for card in self.support_cards if card.skills}
        def fetch_support_detail(card: SupportCard) -> SupportCard:
            try:
                return self._merge_support_detail(card, yatta.support_card_detail(card.id))
            except (OSError, ValueError, KeyError, TypeError):
                old = previous_support.get(card.id)
                return replace(card, skills=old.skills,
                               catalog={**old.catalog, **card.catalog, "detail_stale": True}) if old else card
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(support_cards)))) as pool:
            support_cards = list(pool.map(fetch_support_detail, support_cards))

        with self._song_lock:
            self.songs = self.song_traits.apply(songs)
        self.cards = cards
        self.support_cards = support_cards
        self._detail_cards.clear()
        self._detail_support_cards.clear()
        self.metadata = {
            "source": yatta.BASE, "data_version": "Project Yume public JSON",
            "upstream_fetched_at": None,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "song_count": len(songs), "card_count": len(cards),
            "support_card_count": len(support_cards), "schema": self.CACHE_SCHEMA,
            "member_card_detail_count": detail_count,
            "member_skill_index_complete": bool(cards) and detail_count == len(cards),
            "card_catalog_version": 1 if all(c.catalog.get("detail_loaded") for c in cards + support_cards) else 0,
        }
        try:
            self._save_cache()
        except OSError as exc:
            self.cache_state = "unsaved"
            raise DataError("数据已获取，但本地缓存保存失败") from exc
        self.last_successful_sync_at = self.metadata["cached_at"]
        self.cache_state = "fresh"

    def card_with_detail(self, card: Card) -> Card:
        from .sources import yatta
        if card.id in self._detail_cards:
            return self._detail_cards[card.id]
        if card.skills:
            return card
        try:
            detailed = self._merge_card_detail(card, yatta.card_detail(card.id))
            self._detail_cards[card.id] = detailed
            return detailed
        except (OSError, ValueError, KeyError, TypeError):
            return card

    @staticmethod
    def _merge_card_detail(card: Card, raw: dict[str, Any]) -> Card:
        from .sources import yatta
        if not isinstance(raw, dict) or raw.get("id", card.id) != card.id:
            raise ValueError("member detail ID mismatch")
        stats = raw.get("statsMax") or []
        values = [int(value) for value in stats[:3]] if len(stats) >= 3 else [card.performance, card.technic, card.visual]
        skills = yatta.build_skills(raw.get("skills"))
        live_skill = next((skill for skill in skills if skill.kind == "liveSkill"), skills[0] if skills else None)
        return replace(
            card, performance=values[0], technic=values[1], visual=values[2], skills=skills,
            catalog={**card.catalog, **yatta.detail_catalog(raw)},
            skill_name=live_skill.name if live_skill else card.skill_name,
            localized={
                **card.localized,
                "skill_name": live_skill.localized.get("name", {}) if live_skill else card.localized.get("skill_name", {}),
            },
        )

    def member_skill_index_ready(self) -> bool:
        return bool(self.cards) and all(card.skills for card in self.cards)

    def support_card_with_detail(self, card: SupportCard) -> SupportCard:
        from .sources import yatta
        if card.id in self._detail_support_cards:
            return self._detail_support_cards[card.id]
        if card.catalog.get("detail_loaded"):
            return card
        try:
            raw = yatta.support_card_detail(card.id)
            detailed = self._merge_support_detail(card, raw)
            self._detail_support_cards[card.id] = detailed
            return detailed
        except (OSError, ValueError, KeyError, TypeError):
            return card

    @staticmethod
    def _merge_support_detail(card: SupportCard, raw: dict[str, Any]) -> SupportCard:
        from .sources import yatta
        if not isinstance(raw, dict) or raw.get("id", card.id) != card.id:
            raise ValueError("support detail ID mismatch")
        stats = raw.get("statsMax") or []
        values = [int(v) for v in stats[:3]] if len(stats) >= 3 else [card.performance, card.technic, card.visual]
        return replace(card, performance=values[0], technic=values[1], visual=values[2],
                       skills=yatta.build_skills(raw.get("skills")),
                       catalog={**card.catalog, **yatta.detail_catalog(raw)})

    def _save_cache(self) -> None:
        # The updater shares this file with the previous release. Its schema-2
        # reader passes card rows straight to Card(**row), so new fields belong
        # in top-level extensions that it safely ignores. Keep one atomic file.
        notes_known = {str(song.id): {chart.difficulty: chart.notes is not None
                                     for chart in song.charts} for song in self.songs}
        payload = {
            "metadata": {**self.metadata, "schema": 2,
                         "extended_schema": self.metadata.get("schema", self.CACHE_SCHEMA),
                         # The previous release preserves metadata on load/save,
                         # but drops unknown top-level extensions. A full legacy
                         # refresh rebuilds metadata, so its zero stays unverified.
                         "chart_notes_known": notes_known},
            "songs": [
                {**{k: v for k, v in asdict(song).items() if k != "traits"},
                 "charts": [{**asdict(chart), "notes": chart.notes if chart.notes is not None else 0}
                            for chart in song.charts]}
                for song in self.songs
            ],
            "chart_notes_known": notes_known,
            "cards": [{key: value for key, value in asdict(card).items() if key not in {"skills", "catalog"}}
                      for card in self.cards],
            "member_skills": {str(card.id): [asdict(skill) for skill in card.skills]
                              for card in self.cards},
            "support_cards": [{k: v for k, v in asdict(card).items() if k != "catalog"} for card in self.support_cards],
            "card_catalog": {"member": {str(c.id): c.catalog for c in self.cards},
                             "support": {str(c.id): c.catalog for c in self.support_cards}},
        }
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_file.with_suffix(self.cache_file.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.cache_file)

    def _cache_is_fresh(self) -> bool:
        if not self.cache_file.exists():
            return False
        age_hours = (time.time() - self.cache_file.stat().st_mtime) / 3600
        return age_hours < self.cache_ttl_hours

    def _load_cache(self) -> None:
        try:
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
            self.metadata = dict(payload["metadata"])
            if self.metadata.get("schema") == 2:
                self.metadata["schema"] = self.metadata.get("extended_schema", 2)
            self.songs = [
                Song(
                    **{key: value for key, value in row.items() if key not in {"charts", "titles"}},
                    titles=tuple(row["titles"]),
                    charts=tuple(Chart(**{**chart, "notes": self._cached_notes(payload, row["id"], chart)})
                                 for chart in row["charts"]),
                )
                for row in payload["songs"]
            ]
            self.cards = [Card(
                **{key: value for key, value in row.items() if key not in {"skills", "catalog"}},
                catalog=row.get("catalog", payload.get("card_catalog", {}).get("member", {}).get(str(row["id"]), {})),
                skills=tuple(Skill(**skill) for skill in row.get(
                    "skills", payload.get("member_skills", {}).get(str(row["id"]), []))),
            ) for row in payload.get("cards", [])]
            self.support_cards = [SupportCard(
                **{key: value for key, value in row.items() if key not in {"characters", "skills", "catalog"}},
                characters=tuple(row.get("characters", [])),
                catalog=row.get("catalog", payload.get("card_catalog", {}).get("support", {}).get(str(row["id"]), {})),
                skills=tuple(Skill(**skill) for skill in row.get("skills", [])),
            ) for row in payload.get("support_cards", [])]
        except Exception as exc:
            raise DataError(f"本地缓存损坏：{self.cache_file} ({exc})") from exc
        from .sources import yatta
        if (self.metadata.get("source") != yatta.BASE
                or self.metadata.get("schema") not in {2, 3, self.CACHE_SCHEMA}):
            raise DataError("缓存来自旧数据源，请重新运行 sync")
        self.last_successful_sync_at = self.metadata.get("cached_at")
        with self._song_lock:
            self.songs = self.song_traits.apply(self.songs)

    @staticmethod
    def _cached_notes(payload: dict, song_id: int, chart: dict) -> int | None:
        value = known_notes(chart.get("notes"))
        states = payload.get("chart_notes_known",
                             payload.get("metadata", {}).get("chart_notes_known", {}))
        state = states.get(str(song_id), {}).get(chart["difficulty"])
        if state is False or (value == 0 and state is not True):
            return None
        return value

    def search(self, query: str, limit: int = 5) -> list[Song]:
        needle = normalize(query)
        if not needle:
            return []
        if needle.isdigit():
            exact = [song for song in self.songs if song.id == int(needle)]
            if exact:
                return exact

        ranked: list[tuple[float, Song]] = []
        for song in self.songs:
            variants = [name for fields in song.localized.values() for name in fields.values()]
            phonetic = roman_to_hiragana(song.localized.get("title", {}).get("en", ""))
            choices = [normalize(title) for title in (*song.titles, song.band, *variants, phonetic) if title]
            if needle in choices:
                score = 1.0
            elif any(needle in choice for choice in choices):
                score = 0.9
            else:
                score = max((SequenceMatcher(None, needle, choice).ratio() for choice in choices), default=0)
            fuzzy_threshold = 0.75 if len(needle) <= 3 else 0.62
            if score >= fuzzy_threshold:
                ranked.append((score, song))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [song for _, song in ranked[:limit]]

    def search_cards(self, query: str, limit: int = 8) -> list[Card]:
        alias = resolve_character_alias(query)
        needle = normalize(query)
        if not needle:
            return []
        if needle.isdigit():
            exact = [card for card in self.cards if card.id == int(needle)]
            if exact:
                return exact
        if alias:
            terms = tuple(normalize(term) for term in alias.search_terms)
            matches = [
                card for card in self.cards
                if any(term in normalize(name) for term in terms
                       for name in (card.character, *card.localized.get("character", {}).values()))
            ]
            matches.sort(key=lambda card: (-card.rarity, card.id))
            return matches[:limit]
        ranked = []
        for card in self.cards:
            variants = [name for fields in card.localized.values() for name in fields.values()]
            choices = [normalize(name) for name in (card.title, card.character, card.band, *variants)]
            if needle in choices:
                score = 1.0
            elif any(needle in choice for choice in choices):
                score = 0.9
            else:
                score = max((SequenceMatcher(None, needle, choice).ratio() for choice in choices), default=0)
            if score >= (0.75 if len(needle) <= 3 else 0.62):
                ranked.append((score, card))
        ranked.sort(key=lambda pair: (-pair[0], -pair[1].rarity, pair[1].id))
        return [card for _, card in ranked[:limit]]

    def search_support_cards(self, query: str, limit: int = 8) -> list[SupportCard]:
        alias = resolve_character_alias(query)
        needle = normalize(query)
        if not needle:
            return []
        if needle.isdigit():
            exact = [card for card in self.support_cards if card.id == int(needle)]
            if exact:
                return exact
        if alias:
            terms = tuple(normalize(term) for term in alias.search_terms)
            matches = [
                card for card in self.support_cards
                if any(term in normalize(name) for term in terms for name in (
                    *card.characters, card.character, *card.localized.get("character", {}).values(),
                ))
            ]
            matches.sort(key=lambda card: (-card.rarity, card.id))
            return matches[:limit]
        ranked: list[tuple[float, SupportCard]] = []
        for card in self.support_cards:
            variants = [name for fields in card.localized.values() for name in fields.values()]
            choices = [normalize(name) for name in (card.title, card.character, *card.characters, *variants)]
            if needle in choices:
                score = 1.0
            elif any(needle in choice for choice in choices):
                score = 0.9
            else:
                score = max((SequenceMatcher(None, needle, choice).ratio() for choice in choices), default=0)
            if score >= (0.75 if len(needle) <= 3 else 0.62):
                ranked.append((score, card))
        ranked.sort(key=lambda pair: (-pair[0], -pair[1].rarity, pair[1].id))
        return [card for _, card in ranked[:limit]]

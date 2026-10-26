# L3
# Input: 查询意图、用户文本、SongRepository 实体集合及别名 JSON。
# Output: AnchorResult（EntityRef／歧义）、实体名称或 None，以及别名文件状态版本。
# Pos: Query / Deterministic 的共享实体定位与别名解析；见 L2-2.md。
# Effects/Dependencies: 读取别名 JSON；内容版本与最后有效快照按路径保存在进程内，失败保留旧值并记录日志，无网络。

"""Resolve model-proposed names against the current Project Yume cache."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from ..data import CHARACTER_GROUPS, CHARACTER_ALIASES, SongRepository, character_identity, normalize, resolve_character_alias

def _default_alias_file() -> Path:
    source_root = Path(__file__).resolve().parents[3]
    source_file = source_root / "query_aliases.json"
    if (source_root / "pyproject.toml").is_file() and source_file.is_file():
        return source_file
    # pip --target installs data-files beside site-packages; a virtualenv puts
    # them under sys.prefix/share. Both are read-only defaults at runtime.
    for base in (Path(__file__).resolve().parents[2], Path(sys.prefix)):
        candidate = base / "share" / "ournotes-qq-bot" / "query_aliases.json"
        if candidate.is_file():
            return candidate
    return source_file


ALIAS_FILE = _default_alias_file()


def _alias_file() -> Path:
    configured = os.getenv("OURNOTES_ALIAS_FILE", "").strip()
    return Path(configured).expanduser().resolve() if configured else ALIAS_FILE


@dataclass(frozen=True)
class EntityRef:
    kind: str
    value: int | str


@dataclass(frozen=True)
class AnchorResult:
    entity: EntityRef | None
    ambiguous: bool = False


_lock = RLock()
_snapshots: dict[Path, tuple[dict, int, str | None]] = {}
_failures: dict[Path, str] = {}
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AliasSnapshot:
    entries: dict
    version: int
    status: str | None


_active_aliases: ContextVar[AliasSnapshot | None] = ContextVar('query_alias_snapshot', default=None)


@contextmanager
def alias_scope(*, enabled=True):
    """Use one effective alias file snapshot throughout a logical query."""
    if not enabled or _active_aliases.get() is not None:
        yield
        return
    snapshot = AliasSnapshot(*_load_aliases())
    token = _active_aliases.set(snapshot)
    try:
        yield
    finally:
        _active_aliases.reset(token)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _validate_aliases(payload: object) -> dict:
    groups = {"song", "band", "character", "card", "support_card", "outside_catalog"}
    if not isinstance(payload, dict) or set(payload) - groups:
        raise ValueError("invalid_categories")
    for kind, entries in payload.items():
        if kind == "outside_catalog":
            if not isinstance(entries, list) or any(not isinstance(v, str) or not normalize(v) for v in entries):
                raise ValueError("invalid_outside_catalog")
            continue
        if not isinstance(entries, dict):
            raise ValueError("invalid_entries")
        normalized = {}
        for name, target in entries.items():
            # Existing multi-server song aliases may have several scoped IDs.
            if kind == "song" and (isinstance(target, list) or type(target) is int):
                values = target if isinstance(target, list) else [target]
                if not values or any(type(v) not in (str, int) or not str(v).strip()
                                     or (str(v).lstrip("+-").isdecimal() and int(v) <= 0) for v in values):
                    raise ValueError("invalid_target")
            elif not isinstance(target, str) or not normalize(target):
                raise ValueError("invalid_target")
            if not normalize(name):
                raise ValueError("invalid_target")
            if isinstance(target, str) and target.lstrip("+-").isdecimal() and int(target) <= 0:
                raise ValueError("invalid_id")
            key = normalize(name)
            if key in normalized and normalized[key] != target:
                raise ValueError("conflicting_alias")
            normalized[key] = target
    return payload


def _load_aliases(path: Path | None = None) -> tuple[dict, int, str | None]:
    snapshot = _active_aliases.get() if path is None else None
    if snapshot is not None:
        return snapshot.entries, snapshot.version, snapshot.status
    path = (path or _alias_file()).resolve()
    with _lock:
        previous = _snapshots.get(path)
        try:
            raw = path.read_bytes()
            version = int.from_bytes(hashlib.sha256(str(path).encode() + b"\0" + raw).digest(), "big")
            if previous and previous[1] == version:
                snapshot = (previous[0], version, None)
            else:
                payload = _validate_aliases(json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object))
                snapshot = (payload, version, None)
            _snapshots[path] = snapshot
            _failures.pop(path, None)
            return snapshot
        except (OSError, ValueError) as exc:
            reason = str(exc) if type(exc) is ValueError else type(exc).__name__
            if _failures.get(path) != reason:
                _logger.warning("Alias lexicon load failed (%s); retaining last valid snapshot if available", reason)
            _failures[path] = reason
            if previous:
                return (previous[0], previous[1], "stale")
            version = int.from_bytes(hashlib.sha256(str(path).encode()).digest(), "big")
            return ({}, version, "unavailable")


def alias_version() -> int:
    return _load_aliases()[1]


def alias_status() -> str:
    """Effective process-local snapshot status; never expose lexicon contents."""
    return _load_aliases()[2] or "current"


def unverified_alias_count(repository: SongRepository) -> int:
    """Count string targets absent/ambiguous in this catalog, not proven bad data.

    Multi-server song ID lists are checked by scoped_song_matches instead.
    """
    return sum(_resolve_alias_target(kind, target, repository) is None
               for kind, entries in _aliases().items() if isinstance(entries, dict)
               for target in entries.values() if isinstance(target, str))


def _aliases(path: Path | None = None) -> dict:
    return _load_aliases(path)[0]


def information_alias_targets(term: str) -> tuple[tuple[str, str], ...]:
    """Read exact profile/band aliases; callers verify targets against the roster."""
    needle = normalize(term)
    entries = _aliases()
    targets = []
    for kind in ("character", "band"):
        aliases = entries.get(kind, {})
        if not isinstance(aliases, dict):
            continue
        targets.extend((kind, target) for alias, target in aliases.items()
                       if isinstance(alias, str) and isinstance(target, str) and normalize(alias) == needle)
    return tuple(targets)


def scoped_song_matches(term: str, catalog: tuple, *, partial: bool = False) -> tuple[str, ...]:
    """Resolve names/aliases only against the requested server's Master catalog."""
    needle = normalize(term)
    if not needle:
        return ()
    exact = tuple(key for key, names in catalog if needle == key or needle in {normalize(n) for n in names})
    if exact:
        return exact
    entries = _aliases().get("song", {})
    targets = []
    if isinstance(entries, dict):
        for alias, target in entries.items():
            if normalize(alias) == needle:
                targets.extend(target if isinstance(target, list) else [target])
    targets = {normalize(str(t)) for t in targets if isinstance(t, (str, int))}
    found = tuple(key for key, names in catalog if targets.intersection({key, *(normalize(n) for n in names)}))
    if found or not partial:
        return found
    return tuple(key for key, names in catalog if any(needle in normalize(n) for n in names))


def mentions_outside_catalog(query: str) -> bool:
    """Known unsupported nicknames are resolved before asking the model."""
    text = normalize(query)
    entries = _aliases().get("outside_catalog", [])
    return isinstance(entries, list) and any(
        isinstance(alias, str) and normalize(alias) and normalize(alias) in text
        for alias in entries
    )


def _names(item, field: str) -> set[str]:
    values = [getattr(item, field), *item.localized.get(field, {}).values()]
    if field == "title" and hasattr(item, "titles"):
        values.extend(item.titles)
    return {normalize(value) for value in values if value}


def _support_character_names(card, index: int) -> set[str]:
    names = {card.characters[index]}
    for value in card.localized.get("character", {}).values():
        parts = [part.strip() for part in value.split("/")]
        if index < len(parts) and parts[index]:
            names.add(parts[index])
    return names


def _resolve_source_entity(intent: str, term: str, repository: SongRepository) -> str | None:
    """Return a safe canonical query, or None when a name is absent or ambiguous.

    A model can propose a name, but it cannot add an entity or command syntax to
    the cache. Song and card titles become numeric IDs before reaching commands.
    """
    needle = normalize(term)
    if not needle:
        return None

    if needle.isdecimal():
        number = int(needle)
        if intent in {"song", "chart"}:
            song_ids = {song.id for song in repository.songs}
            if intent == "chart" and number < 100000 and number + 100000 in song_ids:
                number += 100000
            return str(number) if number in song_ids else None
        if intent == "card":
            return str(number) if any(card.id == number for card in repository.cards) else None
        if intent == "support_card":
            return str(number) if any(card.id == number for card in repository.support_cards) else None
        return None

    if intent in {"song", "chart"}:
        title_ids = {song.id for song in repository.songs if needle in _names(song, "title")}
        bands = {song.band for song in repository.songs
                 if intent == "song" and needle in _names(song, "band")}
        if len(title_ids) + len(bands) != 1:
            return None
        return str(next(iter(title_ids))) if title_ids else next(iter(bands))

    if intent == "card":
        alias = resolve_character_alias(term)
        if alias and repository.search_cards(alias.display, limit=1):
            return alias.display
        card_ids = {card.id for card in repository.cards if needle in _names(card, "title")}
        characters = {
            character_identity(card.character) for card in repository.cards
            if needle in _names(card, "character")
            or any(needle == normalize(part.strip()) for part in card.character.split("/"))
        }
        bands = {card.band for card in repository.cards if needle in _names(card, "band")}
        if len(card_ids) + len(characters) + len(bands) != 1:
            return None
        if card_ids:
            return str(next(iter(card_ids)))
        return next(iter(characters or bands))

    if intent == "support_card":
        alias = resolve_character_alias(term)
        if alias and repository.search_support_cards(alias.display, limit=1):
            return alias.display
        card_ids = {card.id for card in repository.support_cards if needle in _names(card, "title")}
        characters = {
            character_identity(name)
            for card in repository.support_cards
            for index, name in enumerate(card.characters)
            if any(needle == normalize(value) for value in _support_character_names(card, index))
        }
        if len(card_ids) + len(characters) != 1:
            return None
        return str(next(iter(card_ids))) if card_ids else next(iter(characters))

    return None


def band_name_variants(name: str) -> set[str]:
    names = {normalize(name)}
    dream = {normalize(n) for n in ('夢限大みゅーたいぷ', '梦限大MewType', '夢限大MewType')}
    return dream if names & dream else names


def band_aliases() -> tuple[tuple[str, str], ...]:
    """Names only; callers verify targets against their own resource identity."""
    return tuple(_aliases().get('band', {}).items())


def character_name_variants(name: str) -> set[str]:
    """Full civilian names/established translations, excluding invented nicknames."""
    identity = character_identity(name)
    names = {normalize(name)}
    for display, full_names, short_names in CHARACTER_GROUPS:
        if identity == display:
            names.update(normalize(n) for n in (display, *full_names, *short_names))
    return names


class CharacterAmbiguity(ValueError):
    """A name fragment names several currently available characters."""


def resolve_character_fragment(term: str, repository: SongRepository) -> str | None:
    """Only a standalone Han character is a fuzzy character selector."""
    needle = normalize(term)
    if not re.fullmatch(r"[\u3400-\u9fff]", needle):
        return None
    names = [n for c in repository.cards for n in (c.character, *c.localized.get('character', {}).values())]
    names.extend(n for c in repository.support_cards for n in c.characters)
    matches = {}
    for name in names:
        if any(needle in variant for variant in character_name_variants(name)):
            matches.setdefault(character_identity(name), name.rsplit('/', 1)[-1].strip())
    if len(matches) > 1:
        raise CharacterAmbiguity('角色名不唯一：' + '、'.join(sorted(matches.values())) + '。请用完整角色名消除歧义。')
    return next(iter(matches), None)


def _resolve_alias_target(kind: str, target: str, repository: SongRepository) -> str | None:
    """Keep each manually edited alias in its declared entity category."""
    needle = normalize(target)
    if kind in {"song", "card", "support_card"}:
        items = (repository.songs if kind == "song" else
                 repository.cards if kind == "card" else repository.support_cards)
        matches = {item.id for item in items if
                   (needle.isdecimal() and item.id == int(needle)) or needle in _names(item, "title")}
        return str(next(iter(matches))) if len(matches) == 1 else None
    if kind == "character":
        matches = {
            character_identity(card.character) for card in repository.cards
            if normalize(character_identity(target)) == normalize(character_identity(card.character))
            or needle in _names(card, "character")
            or any(needle == normalize(part.strip()) for part in card.character.split("/"))
        }
        matches.update(
            character_identity(name) for card in repository.support_cards for name in card.characters
            if normalize(character_identity(target)) == normalize(character_identity(name)) or needle == normalize(name)
        )
        return next(iter(matches)) if len(matches) == 1 else None
    if kind == "band":
        matches = {item.band for item in (*repository.songs, *repository.cards)
                   if band_name_variants(target).intersection(_names(item, "band"))}
        return next(iter(matches)) if len(matches) == 1 else None
    return None


def _source_name_exists(intent: str, term: str, repository: SongRepository) -> bool:
    """Keep an exact cache name or ID ahead of a user-maintained nickname."""
    needle = normalize(term)
    if not needle:
        return False
    if intent in {"song", "chart"}:
        return any(
            needle == str(song.id) or needle in _names(song, "title")
            or (intent == "song" and needle in _names(song, "band"))
            for song in repository.songs
        )
    if intent == "card":
        return resolve_character_alias(term) is not None or any(
            needle == str(card.id) or needle in _names(card, "title")
            or needle in _names(card, "character") or needle in _names(card, "band")
            or any(needle == normalize(part.strip()) for part in card.character.split("/"))
            for card in repository.cards
        )
    if intent == "support_card":
        return resolve_character_alias(term) is not None or any(
            needle == str(card.id) or needle in _names(card, "title")
            or any(needle == normalize(value)
                   for index in range(len(card.characters))
                   for value in _support_character_names(card, index))
            for card in repository.support_cards
        )
    return False


def resolve_exact_alias(intent: str, term: str, repository: SongRepository) -> AnchorResult:
    """Resolve one exact verified nickname; report cross-category collisions."""
    kinds = {"song": ("song", "band"), "chart": ("song",),
             "card": ("card", "character", "band"),
             "support_card": ("support_card", "character")}.get(intent, ())
    needle = normalize(term)
    if not needle or _source_name_exists(intent, term, repository):
        return AnchorResult(None)
    entries = _aliases()
    found: set[EntityRef] = set()
    for kind in kinds:
        aliases = entries.get(kind, {})
        if not isinstance(aliases, dict):
            continue
        for alias, target in aliases.items():
            if not isinstance(alias, str) or not isinstance(target, str) or normalize(alias) != needle:
                continue
            canonical = _resolve_alias_target(kind, target, repository)
            if canonical is not None:
                found.add(EntityRef(kind, int(canonical) if kind in {"song", "card", "support_card"} else canonical))
    return AnchorResult(next(iter(found)) if len(found) == 1 else None, len(found) > 1)


def known_alias_names(repository: SongRepository) -> tuple[str, ...]:
    """Only list nicknames that ordinary song/card commands can actually use."""
    entries = _aliases()
    names = [key for key, alias in CHARACTER_ALIASES.items()
             if any(normalize(character_identity(card.character)) == normalize(alias.display)
                    for card in repository.cards) or any(
                        normalize(character_identity(name)) == normalize(alias.display)
                        for card in repository.support_cards for name in card.characters)]
    for kind in ("song", "band", "character", "card", "support_card"):
        aliases = entries.get(kind, {})
        if not isinstance(aliases, dict):
            continue
        intent = "song" if kind in {"song", "band"} else "support_card" if kind == "support_card" else "card"
        for alias in aliases:
            if isinstance(alias, str) and (resolve_exact_alias(intent, alias, repository).entity
                    or (kind == "character" and resolve_exact_alias("support_card", alias, repository).entity)):
                names.append(alias)
    return tuple(dict.fromkeys(names))


def resolve_entity(intent: str, term: str, repository: SongRepository) -> str | None:
    """Resolve a source name first, then a manually verified alias."""
    direct = _resolve_source_entity(intent, term, repository)
    if direct:
        return direct
    match = resolve_exact_alias(intent, term, repository)
    if match.entity:
        return str(match.entity.value)
    if intent in {'card', 'support_card'}:
        try:
            return resolve_character_fragment(term, repository)
        except CharacterAmbiguity:
            return None
    return None


def _mentioned(text: str, name: str) -> bool:
    needle = normalize(name)
    if not needle:
        return False
    if needle.isdecimal():
        raw = unicodedata.normalize("NFKC", text)
        return bool(re.search(rf"(?<!\d){re.escape(needle)}(?!\d)", raw))
    if len(needle) == 1 and needle.isascii() and needle.isalpha():
        return bool(re.search(r'(?<![a-z])' + re.escape(needle) + r'(?![a-z])', unicodedata.normalize('NFKC', text).casefold()))
    return needle in normalize(text)


def _explicit_card_id(text: str, number: int) -> bool:
    text = unicodedata.normalize("NFKC", text)
    card = r"(?:成员卡|角色卡|卡牌|卡|card)"
    return bool(re.search(
        rf"(?:{card}\s*(?:ID\s*[:：]?\s*)?#?\s*|(?:\bID|编号)\s*[:：#]?\s*)"
        rf"{number}(?![\d.%])|(?<![\d.]){number}\s*号\s*{card}", text, re.I))


def find_anchor(intent: str, text: str, repository: SongRepository, *,
                explicit_card_ids: bool = False) -> AnchorResult:
    """Ground a query in names or verified aliases present in the user's text."""
    allowed = {"song": {"song", "band"}, "chart": {"song"},
               "card": {"card", "character", "band"},
               "support_card": {"support_card", "character"}}.get(intent, set())
    found: set[EntityRef] = set()
    exact = resolve_exact_alias(intent, text, repository)
    if exact.entity or exact.ambiguous:
        return exact
    if intent in {'card', 'support_card'}:
        try:
            fragment = resolve_character_fragment(text, repository)
        except CharacterAmbiguity:
            return AnchorResult(None, True)
        if fragment:
            return AnchorResult(EntityRef('character', fragment))

    if "song" in allowed:
        for song in repository.songs:
            names = {str(song.id), *song.titles, *song.localized.get("title", {}).values()}
            if intent == "chart" and 100000 < song.id < 200000:
                names.add(str(song.id - 100000))
            if any(_mentioned(text, name) for name in names):
                found.add(EntityRef("song", song.id))
    if "band" in allowed:
        for song in repository.songs:
            if any(_mentioned(text, name) for name in
                   {song.band, *song.localized.get("band", {}).values()}):
                found.add(EntityRef("band", song.band))
        if intent == "card":
            for card in repository.cards:
                if any(_mentioned(text, name) for name in
                       {card.band, *card.localized.get("band", {}).values()}):
                    found.add(EntityRef("band", card.band))
    if "card" in allowed:
        for card in repository.cards:
            names = {card.title, *card.localized.get("title", {}).values()}
            id_matches = (_explicit_card_id(text, card.id) if explicit_card_ids
                          else _mentioned(text, str(card.id)))
            if id_matches or any(_mentioned(text, name) for name in names):
                found.add(EntityRef("card", card.id))
    if "support_card" in allowed:
        for card in repository.support_cards:
            if any(_mentioned(text, name) for name in
                   {str(card.id), card.title, *card.localized.get("title", {}).values()}):
                found.add(EntityRef("support_card", card.id))
    if "character" in allowed:
        if intent == "card":
            for card in repository.cards:
                if any(_mentioned(text, name) for name in
                       {card.character, *card.localized.get("character", {}).values()}):
                    found.add(EntityRef("character", character_identity(card.character)))
        if intent == "support_card":
            for card in repository.support_cards:
                for index, name in enumerate(card.characters):
                    if any(_mentioned(text, value) for value in _support_character_names(card, index)):
                        found.add(EntityRef("character", character_identity(name)))
        for alias_name in CHARACTER_ALIASES:
            if _mentioned(text, alias_name):
                if intent == "support_card":
                    alias = resolve_character_alias(alias_name)
                    if alias is None:
                        continue
                    cards = repository.search_support_cards(alias_name, limit=len(repository.support_cards))
                    found.update(EntityRef("character", character_identity(name))
                                 for card in cards for name in card.characters
                                 if normalize(character_identity(name)) == normalize(alias.display))
                else:
                    cards = repository.search_cards(alias_name, limit=len(repository.cards))
                    found.update(EntityRef("character", character_identity(card.character)) for card in cards)

    aliases = _aliases()
    for kind in allowed:
        entries = aliases.get(kind, {})
        if not isinstance(entries, dict):
            continue
        for alias_name, target in entries.items():
            if not isinstance(alias_name, str) or not isinstance(target, str) or not _mentioned(text, alias_name):
                continue
            if len(normalize(alias_name)) == 1 and not normalize(alias_name).isascii():
                # Single-letter band names are selectors, not arbitrary substrings
                # of a song title, a character name or another band's full name.
                pattern = r'(?:^|[\s,，。！？?!=：:]|查询|查看|查一下|查|找)' + re.escape(alias_name) + r'(?=$|[\s,，。！？?!=：:]|的|歌曲|曲目|歌|成员卡|角色卡|卡牌|卡|留影卡|支援卡|乐队|团)'
                if not re.search(pattern, text, re.I):
                    continue
            canonical = _resolve_alias_target(kind, target, repository)
            if canonical is not None:
                found.add(EntityRef(kind, int(canonical) if kind in {"song", "card", "support_card"} else canonical))

    # A specific song/card accompanied by its own band or character is one subject.
    for item in tuple(found):
        if item.kind == "song":
            song = next((row for row in repository.songs if row.id == item.value), None)
            if song:
                found.discard(EntityRef("band", song.band))
        if item.kind == "card":
            card = next((row for row in repository.cards if row.id == item.value), None)
            if card:
                found.discard(EntityRef("band", card.band))
                found.discard(EntityRef("character", character_identity(card.character)))
        if item.kind == "support_card":
            card = next((row for row in repository.support_cards if row.id == item.value), None)
            if card:
                for name in card.characters:
                    found.discard(EntityRef("character", character_identity(name)))
        if item.kind == "character":
            for card in repository.cards:
                if character_identity(card.character) == item.value:
                    found.discard(EntityRef("band", card.band))

    return AnchorResult(next(iter(found)) if len(found) == 1 else None, len(found) > 1)

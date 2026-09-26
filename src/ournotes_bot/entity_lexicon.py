"""Resolve model-proposed names against the current Project Yume cache."""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .data import CHARACTER_ALIASES, SongRepository, character_identity, normalize, resolve_character_alias

def _default_alias_file() -> Path:
    source_root = Path(__file__).resolve().parents[2]
    source_file = source_root / "query_aliases.json"
    if (source_root / "pyproject.toml").is_file() and source_file.is_file():
        return source_file
    # pip --target installs data-files beside site-packages; a virtualenv puts
    # them under sys.prefix/share. Both are read-only defaults at runtime.
    for base in (Path(__file__).resolve().parents[1], Path(sys.prefix)):
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


def alias_version() -> int | None:
    try:
        return _alias_file().stat().st_mtime_ns
    except OSError:
        return None


def _aliases(path: Path | None = None) -> dict:
    try:
        payload = json.loads((path or _alias_file()).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


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
            if needle == normalize(character_identity(card.character))
            or needle in _names(card, "character")
            or any(needle == normalize(part.strip()) for part in card.character.split("/"))
        }
        matches.update(
            character_identity(name) for card in repository.support_cards for name in card.characters
            if needle == normalize(character_identity(name)) or needle == normalize(name)
        )
        return next(iter(matches)) if len(matches) == 1 else None
    if kind == "band":
        matches = {item.band for item in (*repository.songs, *repository.cards)
                   if needle in _names(item, "band")}
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
                    for card in repository.cards)]
    for kind in ("song", "band", "character", "card"):
        aliases = entries.get(kind, {})
        if not isinstance(aliases, dict):
            continue
        intent = "song" if kind in {"song", "band"} else "card"
        for alias in aliases:
            if isinstance(alias, str) and resolve_exact_alias(intent, alias, repository).entity:
                names.append(alias)
    return tuple(dict.fromkeys(names))


def resolve_entity(intent: str, term: str, repository: SongRepository) -> str | None:
    """Resolve a source name first, then a manually verified alias."""
    direct = _resolve_source_entity(intent, term, repository)
    if direct:
        return direct
    match = resolve_exact_alias(intent, term, repository)
    return str(match.entity.value) if match.entity else None


def _mentioned(text: str, name: str) -> bool:
    needle = normalize(name)
    if not needle:
        return False
    if needle.isdecimal():
        raw = unicodedata.normalize("NFKC", text)
        return bool(re.search(rf"(?<!\d){re.escape(needle)}(?!\d)", raw))
    return needle in normalize(text)


def find_anchor(intent: str, text: str, repository: SongRepository) -> AnchorResult:
    """Ground a query in names or verified aliases present in the user's text."""
    allowed = {"song": {"song", "band"}, "chart": {"song"},
               "card": {"card", "character", "band"},
               "support_card": {"support_card", "character"}}.get(intent, set())
    found: set[EntityRef] = set()

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
            if any(_mentioned(text, name) for name in
                   {str(card.id), card.title, *card.localized.get("title", {}).values()}):
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

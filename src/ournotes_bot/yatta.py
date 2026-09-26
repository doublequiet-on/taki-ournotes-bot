"""Read the public Project Yume data files used by its website."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

BASE = "https://bdon.yatta.moe"
MASTER = f"{BASE}/Resources/en/MasterParsed"
ASSETS = f"{BASE}/Resources/en/Assets/AddressableResources"
LANGUAGES = {"ja": 0, "en": 1, "zh": 3}
DIFFICULTIES = ("EASY", "NORMAL", "HARD", "EXPERT")


def fetch_json(url: str, attempts: int = 3) -> Any:
    if not url.startswith(MASTER + "/") or not url.endswith(".json"):
        raise ValueError("Only Project Yume MasterParsed JSON is allowed")
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": BASE + "/"})
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except Exception:
            if attempt + 1 == attempts:
                raise
            time.sleep(0.5 * (attempt + 1))


def text(value: Any, locale: str = "zh") -> str:
    if isinstance(value, list):
        for index in (LANGUAGES.get(locale, 3), 0, 1):
            if index < len(value) and isinstance(value[index], str) and value[index].strip():
                return value[index].strip()
    return value.strip() if isinstance(value, str) else ""


def localized(value: Any) -> dict[str, str]:
    return {locale: result for locale in LANGUAGES if (result := text(value, locale))}


def variants(value: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(entry.strip() for entry in value if isinstance(entry, str) and entry.strip())) if isinstance(value, list) else ()


def timestamp(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def items(payload: Any, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
        raise ValueError(f"Unexpected {label} list format")
    return payload["items"]


def _effect_value(effect: dict[str, Any], locale: str, level: int) -> str:
    data = effect.get("data") or {}
    value = data.get(str(level), data.get(level, "")) if isinstance(data, dict) else ""
    if effect.get("type") == "targets" and isinstance(value, dict):
        return text(value.get("bandType"), locale)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return str(value) if value is not None else ""
    formula = effect.get("formula") or {}
    divide = formula.get("divide") or 1
    number = value / divide if isinstance(divide, (int, float)) and divide else value
    digits = {"F0": 0, "F1": 1, "F2": 2}.get(formula.get("format"))
    rendered = f"{number:.{digits}f}" if digits is not None else f"{number:g}"
    condition = effect.get("condition") or {}
    if condition:
        if value:
            return f"{condition.get('prefix', '')}{rendered if condition.get('withParam') else ''}{condition.get('ifTrue', '')}"
        return str(condition.get("ifFalse", ""))
    return rendered


def skill_description(raw: dict[str, Any], locale: str = "zh", level: int = 5) -> str:
    description = text(raw.get("description"), locale)
    effects = raw.get("effects") or {}
    rows = effects.get(str(LANGUAGES.get(locale, 3)), []) if isinstance(effects, dict) else []
    if not rows and isinstance(effects, dict):
        rows = effects.get("0", []) or effects.get("1", [])
    for effect in rows if isinstance(rows, list) else []:
        if not isinstance(effect, dict) or not effect.get("param"):
            continue
        description = description.replace("{" + str(effect["param"]) + "}", _effect_value(effect, locale, level))
    description = re.sub(r"</?color(?:=[^>]*)?>", "", description, flags=re.I)
    description = re.sub(r"\{[^{}]+}", "?", description)
    return " ".join(description.replace("\r", "\n").split())


def build_skills(payload: Any):
    from .data import Skill

    rows = payload if isinstance(payload, list) else []
    result = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        descriptions = {locale: skill_description(raw, locale) for locale in LANGUAGES}
        result.append(Skill(
            kind=str(raw.get("type") or "skill"),
            name=text(raw.get("name")) or "技能",
            description=descriptions.get("zh", ""),
            localized={"name": localized(raw.get("name")),
                       "description": {locale: value for locale, value in descriptions.items() if value}},
        ))
    return tuple(result)


def build_data(characters: Any, card_payload: Any, song_payload: Any, song_meta: Any):
    from .data import Card, Chart, Song

    if not all(isinstance(value, dict) for value in (characters, song_meta)):
        raise ValueError("Unexpected Project Yume data format")
    cards_raw = items(card_payload, "card")
    songs_raw = items(song_payload, "song")
    card_bands = card_payload.get("refs", {}).get("musicTags", {})
    song_bands = song_payload.get("refs", {}).get("musicTags", {})
    cards = []
    for raw in cards_raw.values():
        card_id = int(raw["id"])
        character = characters.get(str(raw.get("character")), {})
        band_id = character.get("band") or next(iter(raw.get("musicTags") or []), 0)
        band_names = card_bands.get(str(band_id), [])
        cards.append(Card(
            id=card_id, asset_id=card_id, title=text(raw.get("subtitle")) or f"卡牌 {card_id}",
            character=text(raw.get("name")) or text(character.get("name")) or f"角色 {raw.get('character')}",
            band=text(band_names) or "未知乐队", rarity=int(raw.get("rarity") or 0),
            card_type=int(raw.get("attribute") or 0), performance=0, technic=0, visual=0,
            start_at=timestamp(raw.get("startAt")), skill_name="",
            full_url=f"{ASSETS}/MemberCard/{card_id}/member_full.webp",
            thumbnail_url=f"{ASSETS}/MemberCard/{card_id}/member_thumbnail.webp",
            localized={"title": localized(raw.get("subtitle")), "character": localized(raw.get("name")),
                       "band": localized(band_names)},
        ))
    songs = []
    for raw in songs_raw.values():
        song_id = int(raw["id"])
        band_id = next(iter(raw.get("bands") or []), 0)
        band_names = song_bands.get(str(band_id), [])
        levels = raw.get("difficulties") or []
        metadata = song_meta.get(str(song_id)) or []
        charts = tuple(Chart(difficulty=name, level=int(level), display_level=float(level),
                             notes=int(metadata[i][4]) if i < len(metadata) and len(metadata[i]) > 4 else 0,
                             chart_file="")
                       for i, (name, level) in enumerate(zip(DIFFICULTIES, levels)))
        jacket = str(raw.get("jacket") or "")
        if not jacket or not jacket.replace("_", "").isalnum():
            raise ValueError(f"Invalid jacket name for song {song_id}")
        songs.append(Song(
            id=song_id, title=text(raw.get("title")) or f"曲目 {song_id}",
            titles=variants(raw.get("title")), band=text(band_names) or "未知乐队",
            composer="", lyricist="", arranger="", start_at=timestamp(raw.get("startAt")),
            jacket_url=f"{ASSETS}/Image/Jacket/{jacket}.webp", charts=charts,
            localized={"title": localized(raw.get("title")), "band": localized(band_names)},
        ))
    if not cards or not songs:
        raise ValueError("Project Yume returned an empty card or song list")
    return sorted(songs, key=lambda item: item.id), sorted(cards, key=lambda item: item.id)


def build_support_cards(characters: Any, support_payload: Any):
    from .data import SupportCard

    if not isinstance(characters, dict):
        raise ValueError("Unexpected Project Yume character data format")
    supports_raw = items(support_payload, "support card")
    cards = []
    for raw in supports_raw.values():
        card_id = int(raw["id"])
        character_rows = [characters.get(str(character_id), {}) for character_id in raw.get("characters", [])]
        names = tuple(text(row.get("name")) for row in character_rows if text(row.get("name")))
        localized_characters = {
            locale: " / ".join(text(row.get("name"), locale) for row in character_rows if text(row.get("name"), locale))
            for locale in LANGUAGES
        }
        cards.append(SupportCard(
            id=card_id, title=text(raw.get("subtitle")) or text(raw.get("name")) or f"支援卡 {card_id}",
            character=" / ".join(names) or "未知角色", characters=names,
            rarity=int(raw.get("rarity") or 0), card_type=int(raw.get("attribute") or 0),
            performance=0, technic=0, visual=0, start_at=timestamp(raw.get("startAt")),
            full_url=f"{ASSETS}/SupportCard/{card_id}/snap_full.webp",
            thumbnail_url=f"{ASSETS}/SupportCard/{card_id}/snap_thumbnail.webp",
            localized={"title": localized(raw.get("subtitle") or raw.get("name")),
                       "character": {locale: value for locale, value in localized_characters.items() if value}},
        ))
    if not cards:
        raise ValueError("Project Yume returned an empty support card list")
    return sorted(cards, key=lambda item: item.id)


def card_detail(card_id: int) -> dict[str, Any]:
    return fetch_json(f"{MASTER}/membercards/{card_id}.json")


def support_card_detail(card_id: int) -> dict[str, Any]:
    return fetch_json(f"{MASTER}/supportcards/{int(card_id)}.json")

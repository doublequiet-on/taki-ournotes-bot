# L3
# Input: Validated pinned JP/intl documents already collected by CatalogRepository.
# Output: Character, gacha and event records with explicit source/relationship coverage.
# Pos: Sources / Haneoka information catalog; see ../L2-2.md.
# Effects/Dependencies: Pure conversion; no I/O, card-derived roster or foreign-source fill.

from __future__ import annotations

from datetime import date
import re

from ...domain.info_catalog import ActivityInfo, CardRef, CharacterInfo, GachaInfo, InfoCatalog
from .catalog_assets import asset_url
from .catalog_text import LANGUAGES, localized, original, translated


def _integer(value, minimum=0, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid information catalog integer")
    return value


def _plain(value):
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</?(?:color|size|b|i)(?:=[^>]*)?>", "", value, flags=re.I)
    return value.replace("\r\n", "\n").strip()


def _text(row, peer, key, matched):
    value = row.get(key, [])
    if key == "bandPart" and isinstance(value, str):
        return {locale: _plain(value) for locale in LANGUAGES}
    return {locale: _plain(text) for locale, text in
            localized(translated(value, peer.get(key), matched=matched)).items()}


def _image(path, release):
    if not path:
        return ""
    # Optional source artwork may lack an approved asset route. Facts remain useful;
    # only allowlisted images are sent to the renderer/download boundary.
    try:
        return asset_url(path, release)
    except ValueError:
        return ""


def _time(value):
    if value is None or value == []:
        return None
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError("invalid information catalog time")
    if value[0] is None:
        return None
    milliseconds = _integer(value[0], 0, 253402300799999)
    return milliseconds / 1000 if milliseconds else None


def _rows(document, key):
    rows = document.get(key)
    if rows is None:
        return {}, False
    if not isinstance(rows, dict) or len(rows) > 5000:
        raise ValueError("invalid information catalog collection")
    return rows, True


def _identity(key, row, field):
    if not isinstance(row, dict) or not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*", key):
        raise ValueError("invalid information catalog row")
    ident = int(key)
    if str(row.get(field)) != key:
        raise ValueError("information catalog identity mismatch")
    return _integer(ident, 1)


def _sequence(row, name):
    value = row.get(name)
    if value is None:
        return (), False
    if not isinstance(value, list) or len(value) > 20000 or any(not isinstance(v, dict) for v in value):
        raise ValueError("invalid information catalog relation")
    return value, True


def _card_refs(rows):
    refs = set()
    for row in rows:
        resource_type = row.get("resourceType")
        if resource_type in (2, 3) and type(resource_type) is int:
            refs.add(CardRef(resource_type, _integer(row.get("resourceId"), 1)))
    return tuple(sorted(refs))


def convert_info(documents, identity_jp, fetched_at):
    jp, other = documents["jp"], documents["intl"]
    release = identity_jp["releaseId"]
    common = dict(release=release, source_id=identity_jp["sourceId"], fetched_at=fetched_at)
    characters, gachas, activities, available = [], [], [], set()
    people, ready = _rows(jp, "characters")
    if ready:
        available.add("characters")
    bands, _ = _rows(jp, "bands")
    other_people, _ = _rows(other, "characters")
    other_bands, _ = _rows(other, "bands")
    text_fields = ("nickname", "englishName", "bandPart", "voiceActor", "description", "catchCopy",
                   "height", "constellation", "school", "schoolClass", "favoriteFood", "hatedFood", "hobby")
    for key, row in people.items():
        ident = _identity(key, row, "characterId")
        peer = other_people.get(key, {})
        matched = (peer.get("characterId") == ident and row.get("bandId") == peer.get("bandId")
                   and original(row.get("characterName")) == original(peer.get("characterName")))
        names = _text(row, peer, "characterName", matched)
        band_id = row.get("bandId")
        if band_id is not None:
            band_id = _integer(band_id) or None
        band = bands.get(str(band_id), {})
        band_peer = other_bands.get(str(band_id), {})
        band_names = _text(band, band_peer, "bandName", bool(band) and band.get("bandId") == band_peer.get("bandId"))
        birthday = row.get("birthday")
        born = None
        if birthday:
            if not isinstance(birthday, dict):
                raise ValueError("invalid character birthday")
            month, day = birthday.get("month"), birthday.get("day")
            if month and day:
                date(2000, _integer(month, 1, 12), _integer(day, 1, 31))
                born = (month, day)
        color = row.get("colorCode", "")
        if not isinstance(color, str) or not re.fullmatch(r"#?[a-fA-F0-9]{6}", color):
            color = ""
        characters.append(CharacterInfo(
            ident, names["zh"] or f"角色 {ident}", names, band_id, band_names["zh"], band_names,
            _integer(row.get("displayOrder", 0)),
            _image(row.get("profileImage") or row.get("thumbnailImage"), release),
            _image(row.get("faceImage"), release), color,
            {field: _text(row, peer, field, matched) for field in text_fields}, born, **common))

    for resource, label in (("gacha", "gachas"), ("events", "activities")):
        rows, ready = _rows(jp, resource)
        if ready:
            available.add(label)
        peers, _ = _rows(other, resource)
        for key, row in rows.items():
            ident = _identity(key, row, "id")
            if resource == "events" and row.get("kind") != "game-event":
                continue
            peer = peers.get(key, {})
            matched = (str(peer.get("id")) == key and row.get("kind") == peer.get("kind")
                       and original(row.get("title")) == original(peer.get("title")))
            names = _text(row, peer, "title", matched)
            base = dict(id=ident, title=names["zh"] or f"{ident}", names=names,
                        image_url=_image(row.get("image"), release), start_at=_time(row.get("startAt")),
                        end_at=_time(row.get("endAt")), description=_text(row, peer, "description", matched), **common)
            relations = set()
            if resource == "gacha":
                featured, known = _sequence(row, "featured")
                if known:
                    relations.add("featured_cards")
                    if all(type(r.get("pickup")) is bool for r in featured):
                        relations.add("pickup_cards")
                condition = row.get("displayCondition")
                if condition is not None:
                    condition = _integer(condition)
                category = row.get("category", "")
                if not isinstance(category, str) or len(category) > 64:
                    raise ValueError("invalid gacha category")
                limited = row.get("limited")
                if limited is not None and type(limited) is not bool:
                    raise ValueError("invalid gacha limited flag")
                gachas.append(GachaInfo(**base, display_condition=condition, category=category, limited=limited,
                    pickup_cards=_card_refs([r for r in featured if r.get("pickup") is True]),
                    featured_cards=_card_refs(featured), available_relations=frozenset(relations)))
            else:
                effects, known = _sequence(row, "effects")
                if known:
                    relations.add("bonus_cards")
                targets = []
                for effect in effects:
                    linked = effect.get("targets", {})
                    if not isinstance(linked, dict):
                        raise ValueError("invalid event effect targets")
                    for kind in ("memberCard", "supportCard"):
                        if kind in linked:
                            if not isinstance(linked[kind], dict):
                                raise ValueError("invalid event card target")
                            targets.append(linked[kind])
                rewards, known = _sequence(row, "rewards")
                if known and not any(r.get("unresolvedRewardId") for r in rewards):
                    relations.add("reward_cards")
                pickups, known = _sequence(row, "pickupCards")
                if known:
                    relations.add("pickup_cards")
                music_id = row.get("musicId")
                if music_id is not None:
                    music_id = _integer(music_id)
                activities.append(ActivityInfo(**base, display_end_at=_time(row.get("displayEndAt")),
                    bonus_cards=_card_refs(targets), reward_cards=_card_refs(rewards),
                    pickup_cards=_card_refs(pickups), song_ids=(music_id,) if music_id else (),
                    available_relations=frozenset(relations)))
    return InfoCatalog(tuple(sorted(characters, key=lambda c: (c.band_id or 0, c.order, c.id))),
                       tuple(sorted(gachas, key=lambda g: g.id)), tuple(sorted(activities, key=lambda e: e.id)),
                       frozenset(available))

# L3
# Input: Complete pinned JP Catalog entities/references and separately pinned intl text candidates.
# Output: Existing Song/Card/SupportCard/Skill records with JP values and explicit completeness.
# Pos: Sources / Haneoka Catalog deterministic conversion; see ../L2-2.md.
# Effects/Dependencies: Pure conversion and existing member-summary contracts, no network or QQ.

from __future__ import annotations

from datetime import datetime, timezone

from ...data import Card, Chart, Skill, Song, SupportCard
from .catalog_assets import asset_url
from .catalog_text import (UNKNOWN, LANGUAGES, description, effect_context, language, localized,
                           mechanics, original, references, translated)
from .chart_data import _asset_url
from .song_traits import SongTraits, MISSIONS, COLORS


def integer(value, minimum=0, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid Catalog integer")
    return value


def stamp(value):
    if not isinstance(value, list) or not value or value[0] is None:
        return ""
    return datetime.fromtimestamp(integer(value[0]) / 1000, timezone.utc).isoformat()


def identity(row, key, expected):
    if not isinstance(row, dict) or integer(row.get(key), 1) != expected:
        raise ValueError("Catalog entity identity mismatch")


def matching(row, other, id_field, fields):
    return bool(isinstance(other, dict) and row.get(id_field) == other.get(id_field)
                and all(row.get(key) == other.get(key) for key in fields))


def field(row, other, name, matched=True):
    return translated(row.get(name, []), (other or {}).get(name), matched=matched)


def skill_record(kind, jp, other, jp_refs, other_refs):
    matched = False
    try:
        matched = (isinstance(other, dict) and jp.get("id") == other.get("id")
                   and mechanics(jp, jp_refs) == mechanics(other, other_refs)
                   and original(jp.get("description")) == original(other.get("description")))
    except (ValueError, KeyError, TypeError):
        pass
    names = field(jp, other, "skillName", matched)
    templates = field(jp, other, "description", matched)
    # Referenced target names are text too; each carries its own Japanese/raw identity check.
    refs = {**jp_refs, "targets": dict(jp_refs["targets"])}
    if matched:
        for key, row in refs["targets"].items():
            peer = other_refs["targets"].get(key, {})
            refs["targets"][key] = {**row, "name": translated(row.get("name", []), peer.get("name"),
                                                              matched=row.get("raw") == peer.get("raw"))}
    descriptions, complete, chinese_language = {}, True, "ja/en"
    for locale, index in LANGUAGES.items():
        selected = next((i for i in (index, 0, 1) if i < len(templates) and templates[i].strip()), None)
        if selected is None:
            descriptions[locale], complete = UNKNOWN, False
            continue
        actual_locale = next((key for key, i in LANGUAGES.items() if i == selected), "ja")
        try:
            value, valid = description(templates[selected], effect_context(jp, refs, actual_locale))
        except (ValueError, KeyError, TypeError, IndexError):
            value, valid = UNKNOWN, False
        if not valid and selected != 0 and templates and templates[0]:
            selected = 0
            try:
                value, valid = description(templates[0], effect_context(jp, refs, "ja"))
            except (ValueError, KeyError, TypeError, IndexError):
                value, valid = UNKNOWN, False
        descriptions[locale] = value
        if locale == "zh" and selected == 3:
            chinese_language = "zh"
        complete &= valid
    return Skill(kind, language(names) or "技能", descriptions["zh"],
                 {"name": localized(names), "description": descriptions}), complete, chinese_language


def level1(row, progression, support):
    name = "supportCard" if support else "memberCard"
    levels = [r for r in progression.get(name + "Levels", [])
              if r.get("group") == row.get(name + "LevelGroup") and r.get("level") == 1]
    ranks = [r["raw"] for r in progression.get(name + "Ranks", [])
             if r["raw"].get("_group") == row.get(name + "RankGroup") and r["raw"].get("_rank") == 1]
    if len(levels) != 1 or len(ranks) != 1:
        return None
    values = []
    for field_name, rate in (("performance", "performanceRate"), ("technique", "technicRate"), ("visual", "visualRate")):
        base = integer(row["stat"][field_name])
        factor = integer(levels[0][rate]) + (0 if support else integer(ranks[0]["_" + rate]))
        value = base * factor // 10000
        values.append(value / 100 if support else value)
    return values


def category(kind, skill):
    if kind in {"gekisouSkill", "gekisouSupportSkill"}:
        return MISSIONS.get(skill.get("gekisouMissionType"))
    if kind == "leaderSkill":
        return None
    kinds = {e.get("effectType") for e in skill.get("effects", []) if e.get("level") == 5}
    # Explicit effect IDs, not Japanese prefixes or artwork; unknown mechanics stay unknown.
    if kinds and kinds <= {2000, 2004}:
        return "score"
    return {3001: "life", 12006: "judgement", 15000: "duration"}.get(next(iter(kinds))) if len(kinds) == 1 else None


def summary_skill(skill):
    # Entity batches emit integral seconds as JSON 5; the original skill resource
    # emits 5.0. Preserve the same numeric value for the existing strict fingerprint.
    effects = []
    for effect in skill["effects"]:
        raw = dict(effect.get("raw", {}))
        if "_activationTimeSecond" in raw:
            raw["_activationTimeSecond"] = float(raw["_activationTimeSecond"])
        item = {**effect, "raw": raw}
        if "activationTimeSecond" in item:
            item["activationTimeSecond"] = float(item["activationTimeSecond"])
        effects.append(item)
    return {**skill, "effects": effects}


def convert(documents, identity_jp, fetched_at, *, stale=False):
    jp, other = documents["jp"], documents["intl"]
    refs, other_refs = references(jp["skill-reference"]), references(other["skill-reference"])
    release, source_id = identity_jp["releaseId"], identity_jp["sourceId"]

    def names(resource, key, name, id_field, extra=()):
        row = jp[resource].get(str(key))
        if row is None:
            return [], False
        identity(row, id_field, key)
        peer = other[resource].get(str(key))
        return field(row, peer, name, matching(row, peer, id_field, extra)), True

    songs = []
    for key, row in jp["songs"].items():
        song_id = integer(int(key), 1)
        identity(row, "musicId", song_id)
        peer = other["songs"].get(key)
        matched = matching(row, peer, "musicId", ("bandIds",)) and original(row["musicTitle"]) == original((peer or {}).get("musicTitle"))
        title = field(row, peer, "musicTitle", matched)
        band_ids = row.get("bandIds")
        if band_ids is not None and not isinstance(band_ids, list):
            raise ValueError("invalid song band identity")
        bands = ([names("bands", integer(b, 1), "bandName", "bandId")[0] for b in band_ids]
                 if band_ids else [field(row, peer, "bandName", matched)])
        band = {locale: " / ".join(language(b, locale) or "未知乐队" for b in bands) for locale in LANGUAGES}
        charts, seen = [], set()
        for entry in row["difficulty"]:
            index = integer(entry["difficulty"], 0, 3)
            if index in seen or entry.get("difficultyName") != ("easy", "normal", "hard", "expert")[index]:
                raise ValueError("invalid chart difficulty")
            seen.add(index)
            display = entry.get("displayLevel", entry["playLevel"])
            if type(display) not in (int, float) or not 0 < display < 100:
                raise ValueError("invalid chart level")
            note_count = entry.get("noteCount")
            if note_count is not None:
                integer(note_count, 0, 1_000_000)
            charts.append(Chart(("EASY", "NORMAL", "HARD", "EXPERT")[index], integer(entry["playLevel"], 1, 99),
                                float(display), note_count, _asset_url(entry["file"], release)))
        if seen != {0, 1, 2, 3}:
            raise ValueError("incomplete song difficulties")
        color = row.get("musicType")
        missions = (row.get("gekisou") or {}).get("missionTypes")
        if type(color) is not int or color not in COLORS:
            color = None
        if not isinstance(missions, list) or not missions or any(type(m) is not int or m not in MISSIONS for m in missions):
            missions = None
        credits = {name: field(row, peer, name, matched) for name in ("composer", "lyricist", "arranger")}
        songs.append(Song(song_id, language(title), tuple(dict.fromkeys(t for t in title if t)), band["zh"],
                          *(language(credits[n]) for n in ("composer", "lyricist", "arranger")),
                          stamp(row.get("publishedAt")), asset_url(row["jacketUrl"], release),
                          tuple(sorted(charts, key=lambda c: ("EASY", "NORMAL", "HARD", "EXPERT").index(c.difficulty))),
                          {"title": localized(title), "band": band, **{k: localized(v) for k, v in credits.items()}},
                          SongTraits(color, tuple(MISSIONS[m] for m in missions) if missions else None, stale, release, source_id)))

    member_refs = None
    try:
        from .haneoka_members import reference_index
        member_refs = reference_index(jp["skill-reference"])
    except (KeyError, ValueError, TypeError):
        pass
    cards, supports = [], []
    for support, resource, id_field in ((False, "cards", "cardId"), (True, "support-cards", "supportCardId")):
        for key, row in jp[resource].items():
            card_id = integer(int(key), 1)
            identity(row, id_field, card_id)
            raw = row.get("raw", {})
            if raw.get("_id") != card_id or raw.get("_assetID") != row.get("assetId"):
                raise ValueError("card raw identity mismatch")
            asset_id = integer(row.get("assetId"), 1)
            directory, prefix = ("SupportCard", "snap") if support else ("MemberCard", "member")
            for size in ("full", "thumbnail"):
                expected = f"/assets/jp/Assets/AddressableResources/{directory}/{asset_id}/{prefix}_{size}.png"
                if row["images"][size] != expected:
                    raise ValueError("card image identity mismatch")
            peer = other[resource].get(key)
            linked = matching(row, peer, id_field, ("assetId", "characterIds" if support else "characterId"))
            title = field(row, peer, "prefix", linked)
            char_ids = row.get("characterIds") if support else [row.get("characterId")]
            if not isinstance(char_ids, list) or not char_ids or len(char_ids) != len(set(char_ids)):
                raise ValueError("invalid character links")
            character_names, band_names, band_ids, links_ready = [], [], [], True
            for cid in char_ids:
                cid = integer(cid, 1)
                char, valid = names("characters", cid, "characterName", "characterId", ("bandId",))
                character_names.append(char)
                links_ready &= valid and bool(language(char))
                band_id = jp["characters"].get(str(cid), {}).get("bandId")
                if band_id is not None and band_id not in band_ids:
                    band_id = integer(band_id, 1)
                    band_ids.append(band_id)
                    bnames, valid = names("bands", band_id, "bandName", "bandId")
                    band_names.append(bnames)
                    links_ready &= valid
            skills, evidence, skill_languages, cats = [], [], {}, {"live": [], "gekisou": []}
            complete = True
            slots = {}
            resolved = row.get("resolvedSkills", {})
            peer_resolved = (peer or {}).get("resolvedSkills", {}) if linked else {}
            if support:
                kinds = (("support", "supportSkill", "_supportSkillId"), ("gekisouSupport", "gekisouSupportSkill", "_gekisouSupportSkillId"))
            else:
                kinds = (("leader", "leaderSkill", "_leaderSkillID"), ("live", "liveSkill", "_liveSkillID"), ("gekisou", "gekisouSkill", "_gekisouSkillID"))
            for slot_name, kind, raw_field in kinds:
                category_key = "gekisou" if "gekisou" in kind else "live"
                slot_values = [raw.get(raw_field + suffix) for suffix in ("01", "02")] if support else [raw.get(raw_field)]
                slots[kind] = slot_values
                if any(type(v) is not int or v < 0 for v in slot_values):
                    complete = False
                    if kind != "leaderSkill":
                        cats[category_key].append(None)
                    continue
                values = resolved.get(slot_name, []) if support else [resolved.get(slot_name)]
                peers = peer_resolved.get(slot_name, []) if support else [peer_resolved.get(slot_name)]
                by_id = {v["id"]: v for v in values if isinstance(v, dict)}
                peer_by_id = {v["id"]: v for v in peers if isinstance(v, dict)}
                if len(by_id) != len([v for v in values if isinstance(v, dict)]):
                    raise ValueError("duplicate resolved skill")
                if set(by_id) != {v for v in slot_values if v}:
                    complete = False
                    if kind != "leaderSkill":
                        cats[category_key].append(None)
                for skill_id in slot_values:
                    if not skill_id:
                        continue
                    skill = by_id.get(skill_id)
                    if skill is None:
                        continue
                    converted, valid, lang = skill_record(kind, skill, peer_by_id.get(skill_id), refs, other_refs)
                    complete &= valid
                    skills.append(converted)
                    skill_languages[kind] = lang
                    cat = category(kind, skill)
                    if kind != "leaderSkill":
                        cats[category_key].append(cat)
                    evidence.append({"kind": kind, "id": skill_id, "name_ja": language(skill["skillName"], "ja"), "category": cat})
            for name in cats:
                cats[name] = list(dict.fromkeys(cats[name])) or (["not_applicable"] if complete else [None])
            stats = [integer(row["stat"][name], 0, 100_000_000) for name in ("performance", "technique", "visual")]
            if stats != [raw.get("_" + name + "PowerMax") for name in ("performance", "technic", "visual")]:
                raise ValueError("card stat identity mismatch")
            lv1 = level1(row, jp["progression"], support)
            catalog = {"source": "haneoka", "release": release, "source_id": source_id, "fetched_at": fetched_at,
                       "character_ids": char_ids, "character_links_complete": links_ready, "band_ids": band_ids,
                       "bands": [language(b) for b in band_names], "categories": cats, "skill_evidence": evidence,
                       "skill_languages": skill_languages, "stats_level1": lv1, "skill_slots": slots,
                       "detail_loaded": complete, "detail_stale": stale, "skill_index_complete": complete}
            # Tags need a confirmed name association; numeric band IDs are not automatically tag IDs.
            if not support:
                catalog["tags"] = {str(integer(t, 1)): {} for t in row.get("bestMusicTagIds", [])}
                catalog["tags_complete"] = not bool(catalog["tags"])
                if member_refs is not None:
                    from .haneoka_members import summarize_leader, summarize_skill, UNKNOWN as SUMMARY_UNKNOWN
                    try:
                        catalog["member_summary"] = [summarize_leader(summary_skill(resolved["leader"]), member_refs),
                                                     summarize_skill(summary_skill(resolved["live"]), member_refs),
                                                     summarize_skill(summary_skill(resolved["gekisou"]), member_refs)]
                    except (ValueError, KeyError, TypeError):
                        catalog["member_summary"] = [SUMMARY_UNKNOWN] * 3
            loc = {"title": localized(title), "character": {locale: " / ".join(language(n, locale) or "未知角色" for n in character_names) for locale in LANGUAGES}}
            common = dict(id=card_id, title=language(title) or f"卡牌 {card_id}", character=loc["character"]["zh"],
                          rarity=integer(row["rarity"], 1, 100), card_type=integer(row["cardType"], 1, 5),
                          performance=stats[0], technic=stats[1], visual=stats[2], start_at=stamp(row.get("releasedAt")),
                          full_url=asset_url(row["images"]["full"], release), thumbnail_url=asset_url(row["images"]["thumbnail"], release),
                          skills=tuple(skills), localized=loc, catalog=catalog)
            if support:
                supports.append(SupportCard(**common, characters=tuple(language(n) for n in character_names)))
            else:
                loc["band"] = {locale: " / ".join(language(n, locale) for n in band_names) or "未知乐队" for locale in LANGUAGES}
                live = next((s for s in skills if s.kind == "liveSkill"), None)
                loc["skill_name"] = live.localized["name"] if live else {}
                cards.append(Card(**common, asset_id=integer(row["assetId"], 1), band=loc["band"]["zh"], skill_name=live.name if live else ""))
    return sorted(songs, key=lambda r: r.id), sorted(cards, key=lambda r: r.id), sorted(supports, key=lambda r: r.id)

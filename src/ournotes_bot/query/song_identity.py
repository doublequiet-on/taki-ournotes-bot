# L3
# Input: 歌曲目录、名称、ID 或已核实别名。
# Output: 精确实体、直接查曲的字面匹配或待确认候选，以及消歧指令。
# Pos: Query / Deterministic；见 L2-2.md。
# Effects/Dependencies: 读取本地别名；无联网、绘图或平台依赖。
"""Entity-first song resolution, shared by unique-object and list queries."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from unicodedata import name as character_name

from ..data import localized_text, normalize, roman_to_hiragana
from ..i18n import tr
from .entity_lexicon import scoped_song_matches, resolve_exact_alias


class QueryProblem(str):
    """String-compatible parse diagnostic with a machine-readable terminal code."""
    def __new__(cls, message, status="invalid_arguments"):
        instance = super().__new__(cls, message)
        instance.status = status
        return instance


def exact_songs(repository, term: str):
    songs = tuple(repository.songs)
    catalog = tuple((str(s.id), (s.title, *s.titles, *s.localized.get("title", {}).values()))
                    for s in songs)
    needle = normalize(term)
    direct = tuple(s for s in songs if needle == str(s.id) or needle in {
        normalize(t) for t in (s.title, *s.titles, *s.localized.get("title", {}).values())})
    if direct:
        return direct
    if resolve_exact_alias("song", term, repository).ambiguous:
        return ()
    ids = set(scoped_song_matches(term, catalog))
    return tuple(s for s in songs if str(s.id) in ids)


SONG_SUGGESTION_LIMIT = 10
SHORT_SONG_SUGGESTION_LIMIT = 3


def _three_han_characters(value: str) -> bool:
    return len(value) == 3 and all(
        character == "〇" or character_name(character, "").startswith(
            ("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-"))
        for character in value
    )


def direct_song_names(repository, term: str):
    """Return song records and their suggestion limit, or zero for literal matches."""
    songs = tuple({song.id: song for song in repository.songs}.values())
    if not term:
        return songs, 0
    exact = exact_songs(repository, term)
    if exact:
        return tuple({song.id: song for song in exact}.values()), 0
    needle = normalize(term)
    if not needle or needle.isdecimal():
        return (), 0

    alias = resolve_exact_alias("song", term, repository)
    if alias.ambiguous:
        return (), 0
    if alias.entity and alias.entity.kind == "band":
        return tuple(song for song in songs if normalize(song.band) == normalize(alias.entity.value)), 0

    bands = {song.id: tuple(normalize(name) for name in (
        song.band, *song.localized.get("band", {}).values())) for song in songs}
    exact_band = tuple(sorted((song for song in songs if needle in bands[song.id]), key=lambda song: song.id))
    if exact_band:
        return exact_band, 0

    keywords = tuple(word for part in term.split() if (word := normalize(part)))
    confirmed = []
    unmatched = []
    for song in songs:
        titles = {normalize(title) for title in (song.title, *song.titles, *song.localized.get("title", {}).values())}
        phonetic = normalize(roman_to_hiragana(song.localized.get("title", {}).get("en", "")))
        continuous = (*titles, *bands[song.id], phonetic)
        if needle in continuous:
            confirmed.append((0, song))
        elif any(needle in title for title in continuous):
            confirmed.append((1, song))
        elif len(keywords) > 1 and any(all(word in title for word in keywords) for title in titles):
            confirmed.append((2, song))
        else:
            unmatched.append((song, titles))

    if confirmed:
        confirmed.sort(key=lambda pair: (pair[0], pair[1].id))
        return tuple(song for _, song in confirmed), 0
    if _three_han_characters(needle):
        candidates = [song for song, titles in unmatched if any(
            _three_han_characters(title) and sum(a != b for a, b in zip(needle, title)) == 1
            for title in titles
        )]
        return tuple(sorted(candidates, key=lambda song: song.id)), SHORT_SONG_SUGGESTION_LIMIT
    if len(needle) < 4:
        return (), 0

    threshold = 0.75 if len(needle) == 4 else 0.72
    approximate = []
    for song, titles in unmatched:
        score = max(SequenceMatcher(None, needle, title).ratio() for title in titles)
        if score >= threshold:
            approximate.append((-score, song))
    approximate.sort(key=lambda pair: (pair[0], pair[1].id))
    return tuple(song for _, song in approximate), SONG_SUGGESTION_LIMIT


def song_suggestions(songs, limit):
    """Keep the full short-title count so the reply can report ambiguity."""
    return tuple(songs if limit == SHORT_SONG_SUGGESTION_LIMIT else songs[:limit])


def song_suggestion_text(songs, locale, warning="", *, limit):
    if limit == SHORT_SONG_SUGGESTION_LIMIT and len(songs) > limit:
        text = tr(locale, "song_suggestions_broad", count=len(songs))
        return text + ("\n" + warning.strip() if warning else "")
    rows = [tr(locale, "song_suggestions")]
    rows.extend(f"{localized_text(song, 'title', locale)} · {localized_text(song, 'band', locale)} · ID {song.id}"
                for song in songs[:limit])
    if warning:
        rows.append(warning.strip())
    return "\n".join(rows)


def unique_candidates(repository, term: str):
    exact = exact_songs(repository, term)
    if exact:
        return exact
    if resolve_exact_alias("song", term, repository).ambiguous:
        return ()
    return tuple(repository.search(term, limit=len(repository.songs)))


def candidate_text(songs, difficulty: str | None = None, *, mirror: bool = False, coverage: tuple[float, ...] = ()) -> str:
    suffix = (f" {difficulty}" if difficulty else "") + (" 镜像" if mirror else "")
    if coverage:suffix += " " + "/".join(str(v) for v in coverage)
    rows = ["匹配到多首歌曲，请用 ID 选择：" if len(songs) > 1 else "未确认完整歌曲名称，请核对候选并用 ID 选择："]
    rows.extend(f"{s.title}（ID {s.id}）→ /查谱面 {s.id}{suffix}" for s in songs[:10])
    if len(songs) > 10:
        rows.append(f"共 {len(songs)} 个候选，请补充完整名称缩小范围。")
    return "\n".join(rows)


def literal_song_match(repository, term, song):
    """A unique substring may resolve; edit-distance suggestions need confirmation."""
    if song in exact_songs(repository, term):
        return True
    needle = normalize(term)
    names = (song.title, *song.titles, *song.localized.get("title", {}).values())
    return bool(needle and any(needle in normalize(name) for name in names))


def protect_entity(text, repository):
    """Protect the longest complete entity span before consuming condition tokens."""
    if repository is None:
        return text, ""
    words = list(re.finditer(r"\S+", text))
    for size in range(len(words), 0, -1):
        for start in range(len(words) - size + 1):
            left, right = words[start].start(), words[start + size - 1].end()
            value = text[left:right]
            if exact_songs(repository, value):
                return text[:left] + "TAKIENTITYTOKEN" + text[right:], value
    return text, ""

# L3
# Input: 歌曲目录、名称、ID 或已核实别名。
# Output: 全部精确候选或唯一对象的候选集合与消歧指令。
# Pos: Query / Deterministic；见 L2-2.md。
# Effects/Dependencies: 读取本地别名；无联网、绘图或平台依赖。
"""Entity-first song resolution, shared by unique-object and list queries."""
from __future__ import annotations

import re
from unicodedata import name as character_name

from ..data import localized_text, normalize
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


def unique_candidates(repository, term: str):
    exact = exact_songs(repository, term)
    if exact:
        return exact
    if resolve_exact_alias("song", term, repository).ambiguous:
        return ()
    return tuple(repository.search(term, limit=len(repository.songs)))


SHORT_SONG_SUGGESTION_LIMIT = 3


def is_three_han_title(value: str) -> bool:
    """Check a normalized title for exactly three Han characters."""
    return len(value) == 3 and all(
        character == "〇" or character_name(character, "").startswith(
            ("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-"))
        for character in value
    )


def chart_candidates(repository, term: str):
    """Use a single positional substitution for three-Han titles after literal lookup."""
    needle = normalize(term)
    if not is_three_han_title(needle):
        return unique_candidates(repository, term), False
    exact = exact_songs(repository, term)
    if exact:
        return exact, False
    if resolve_exact_alias("song", term, repository).ambiguous:
        return (), False

    literal = []
    typos = {}
    for song in repository.songs:
        titles = {normalize(title) for title in (song.title, *song.titles, *song.localized.get("title", {}).values())}
        if any(needle in title for title in titles):
            literal.append(song)
        elif any(is_three_han_title(title) and sum(a != b for a, b in zip(needle, title)) == 1
                 for title in titles):
            typos.setdefault(song.id, song)
    if literal:
        return tuple(sorted(literal, key=lambda song: song.id)), False
    return tuple(typos[song_id] for song_id in sorted(typos)), True


def candidate_text(songs, difficulty: str | None = None, *, mirror: bool = False,
                   coverage: tuple[float, ...] = (), short_typo: bool = False, locale: str = "zh") -> str:
    suffix = (f" {difficulty}" if difficulty else "") + (" 镜像" if mirror else "")
    if coverage:
        suffix += " " + "/".join(str(v).removesuffix(".0") if short_typo else str(v) for v in coverage)
    if short_typo:
        if len(songs) > SHORT_SONG_SUGGESTION_LIMIT:
            return tr(locale, "song_suggestions_broad", count=len(songs))
        rows = [tr(locale, "chart_short_suggestions")]
        for song in songs:
            title = localized_text(song, "title", locale)
            band = localized_text(song, "band", locale)
            rows.append(f"{title} · {band} · ID {song.id} → /查谱面 {song.id}{suffix}")
        return "\n".join(rows)
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

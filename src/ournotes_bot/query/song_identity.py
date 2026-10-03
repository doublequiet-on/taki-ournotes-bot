# L3
# Input: 歌曲目录、名称、ID 或已核实别名。
# Output: 全部精确候选或唯一对象的候选集合与消歧指令。
# Pos: Query / Deterministic；见 L2-2.md。
# Effects/Dependencies: 读取本地别名；无联网、绘图或平台依赖。
"""Entity-first song resolution, shared by unique-object and list queries."""
from __future__ import annotations

import re

from ..data import normalize
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


def candidate_text(songs, difficulty: str | None = None) -> str:
    suffix = f" {difficulty}" if difficulty else ""
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

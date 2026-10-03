# L3
# Input: 歌曲目录、名称、ID 或已核实别名。
# Output: 全部精确候选或唯一对象的候选集合与消歧指令。
# Pos: Query / Deterministic；见 L2-2.md。
# Effects/Dependencies: 读取本地别名；无联网、绘图或平台依赖。
"""Entity-first song resolution, shared by unique-object and list queries."""
from __future__ import annotations

from ..data import normalize
from .entity_lexicon import scoped_song_matches, resolve_exact_alias


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
    rows = ["匹配到多首歌曲，请用 ID 选择："]
    rows.extend(f"{s.title}（ID {s.id}）→ /查谱面 {s.id}{suffix}" for s in songs[:10])
    if len(songs) > 10:
        rows.append(f"共 {len(songs)} 个候选，请补充完整名称缩小范围。")
    return "\n".join(rows)

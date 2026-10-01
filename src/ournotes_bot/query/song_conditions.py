# L3
# Input: 命令或自然语言文本，以及移除条件时的 SongConditionEvidence。
# Output: extract_song_conditions 返回 SongConditionEvidence；remove_song_conditions 返回去除已识别条件后的 str。
# Pos: Query / Deterministic 的共享歌曲条件词法证据提取；见 L2-2.md。
# Effects/Dependencies: 纯文本处理，不选择实体、不补默认难度，无外部 I/O 或状态写入。

"""Dependency-free extraction of explicit song level and difficulty conditions."""

from __future__ import annotations

import re
from dataclasses import dataclass


_LEVEL = r"(?:lv\.?\s*)?(?P<level>\d+(?:\.\d+)?)\s*级?"
LEVEL_PHRASES = (
    (re.compile(_LEVEL + r"\s*(?:及以上|或以上|以上|起|或更高)", re.I), ">="),
    (re.compile(_LEVEL + r"\s*(?:及以下|或以下|以下|以内|或更低)", re.I), "<="),
    (re.compile(r"(?:不低于|不小于|不少于|至少|起码)\s*" + _LEVEL, re.I), ">="),
    (re.compile(r"(?<!不)(?:高于|超过|大于|多于)\s*" + _LEVEL, re.I), ">"),
    (re.compile(r"(?:不超过|不高于|不大于|至多|最多)\s*" + _LEVEL, re.I), "<="),
    (re.compile(r"(?<!不)(?:低于|小于|少于|不到|不足|未满)\s*" + _LEVEL, re.I), "<"),
    (re.compile(r"(?:>=|≥)\s*" + _LEVEL, re.I), ">="),
    (re.compile(r"(?:<=|≤)\s*" + _LEVEL, re.I), "<="),
    (re.compile(r"(?<![<>=])>\s*" + _LEVEL, re.I), ">"),
    (re.compile(r"(?<![<>=])<\s*" + _LEVEL, re.I), "<"),
)
EXPLICIT_LV = re.compile(r"lv\s*(?P<operator>>=|<=|>|<)\s*(?P<level>\d+(?:\.\d+)?)", re.I)
DIFFICULTY_PATTERN = re.compile(
    r"(?<![A-Za-z])(?:EXPERT|EXP|EX|HARD|HD|NORMAL|NM|EASY|EZ)(?![A-Za-z])"
    r"|(?:专家|困难|普通|简单)(?:难度)?", re.I,
)
DIFFICULTY_NAMES = {
    "EXPERT": "EXPERT", "EXP": "EXPERT", "EX": "EXPERT", "专家": "EXPERT",
    "HARD": "HARD", "HD": "HARD", "困难": "HARD",
    "NORMAL": "NORMAL", "NM": "NORMAL", "普通": "NORMAL",
    "EASY": "EASY", "EZ": "EASY", "简单": "EASY",
}


@dataclass(frozen=True)
class SongConditionEvidence:
    comparison: str = ""
    level: float | None = None
    level_span: tuple[int, int] | None = None
    difficulty: str = ""
    difficulty_span: tuple[int, int] | None = None
    level_conflict: bool = False
    difficulty_conflict: bool = False

    @property
    def has_level(self) -> bool:
        return self.level_span is not None and self.level is not None

    @property
    def has_difficulty(self) -> bool:
        return self.difficulty_span is not None and bool(self.difficulty)

    @property
    def conflict(self) -> bool:
        return self.level_conflict or self.difficulty_conflict


def extract_song_conditions(text: str, *, include_explicit_lv: bool = False) -> SongConditionEvidence:
    """Extract unique explicit conditions without resolving an entity or choosing defaults."""
    levels: list[tuple[int, int, str, float]] = []
    explicit_spans: list[tuple[int, int]] = []
    if include_explicit_lv:
        for match in EXPLICIT_LV.finditer(text):
            explicit_spans.append(match.span())
            levels.append((*match.span(), match.group("operator"), float(match.group("level"))))
    for pattern, operator in LEVEL_PHRASES:
        for match in pattern.finditer(text):
            if any(match.start() < end and start < match.end()
                   for start, end in explicit_spans):
                continue
            levels.append((*match.span(), operator, float(match.group("level"))))
    levels = list(dict.fromkeys(levels))

    difficulties = []
    for match in DIFFICULTY_PATTERN.finditer(text):
        key = re.sub(r"难度$", "", match.group().upper())
        difficulties.append((*match.span(), DIFFICULTY_NAMES[key]))
    difficulties = list(dict.fromkeys(difficulties))

    level = levels[0] if len(levels) == 1 else None
    difficulty = difficulties[0] if len(difficulties) == 1 else None
    return SongConditionEvidence(
        comparison=level[2] if level else "",
        level=level[3] if level else None,
        level_span=(level[0], level[1]) if level else None,
        difficulty=difficulty[2] if difficulty else "",
        difficulty_span=(difficulty[0], difficulty[1]) if difficulty else None,
        level_conflict=len(levels) > 1,
        difficulty_conflict=len(difficulties) > 1,
    )


def remove_song_conditions(text: str, evidence: SongConditionEvidence) -> str:
    """Replace the recognized condition spans with spaces while preserving other text."""
    spans = [span for span in (evidence.level_span, evidence.difficulty_span) if span]
    for start, end in sorted(set(spans), reverse=True):
        text = text[:start] + " " + text[end:]
    return text

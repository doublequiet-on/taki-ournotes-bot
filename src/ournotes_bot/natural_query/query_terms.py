# L3
# Input: 待判断的技能占位词／短语。
# Output: is_skill_placeholder 返回 bool。
# Pos: Query / Natural 的共享占位词判定，供本地解析与模型结果校验使用；见 L2-2.md。
# Effects/Dependencies: 纯字符串判定，无外部 I/O 或状态写入。

"""Small dependency-free predicates shared by natural-query parsers."""

from __future__ import annotations

import unicodedata


SKILL_CLARIFICATION = "请补充技能名称、效果关键词，或指定队长／Live／激奏技能。"

_SKILL_PLACEHOLDERS = frozenset({
    "哪些", "有哪些", "什么", "有什么", "有啥", "有什么样", "有什么样的",
})


def is_skill_placeholder(value: str) -> bool:
    """Return true only when the complete skill value is a question placeholder."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(normalized.split()).strip("，,。？?！!、·・“”\"'")
    return normalized in _SKILL_PLACEHOLDERS

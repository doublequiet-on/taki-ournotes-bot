"""Ground and validate model-proposed query parameters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .data import SongRepository, normalize
from .entity_lexicon import find_anchor, resolve_entity
from .structured_query import QuerySpec


UNSUPPORTED = "目前只能查询歌曲、谱面等级与 Note 数、成员卡和支援卡。试试 /查曲、/查谱面、/查卡 或 /查支援卡。"
UNKNOWN_ENTITY = "当前数据中无法确认这个查询对象。请用歌曲、乐队、角色、成员卡或支援卡的原名或 ID 重试。"
AMBIGUOUS_ENTITY = "这句话提到了多个可查询对象，请只保留一个歌曲、乐队、角色、成员卡或支援卡名称。"


class OutcomeCode(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    UNSUPPORTED = "unsupported"
    UNKNOWN_ENTITY = "unknown_entity"
    AMBIGUOUS = "ambiguous"
    INVALID_OUTPUT = "invalid_output"
    INVALID_ARGUMENTS = "invalid_arguments"
    DATA_UNAVAILABLE = "data_unavailable"
    TEMPORARY_FAILURE = "temporary_failure"
    QUOTA_EXHAUSTED = "quota_exhausted"
    QUOTA_UNAVAILABLE = "quota_unavailable"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class ValidationResult:
    spec: QuerySpec | None
    code: OutcomeCode
    invalid_fields: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()
    repairable: bool = False


def validate_legacy_plan(data: dict[str, Any], question: str,
                         repository: SongRepository) -> ValidationResult:
    """Validate the legacy all-capabilities JSON contract without changing it."""
    intent = data.get("intent")
    term = data.get("query")
    difficulty = data.get("difficulty") or ""
    comparison = data.get("level_operator") or ""
    level = data.get("level")
    skill_query = data.get("skill_query") or ""
    skill_kind = data.get("skill_kind") or ""
    if not comparison and level is None and data.get("min_level") is not None:
        comparison, level = "gte", data["min_level"]
    if intent == "unsupported":
        return ValidationResult(None, OutcomeCode.UNSUPPORTED)
    if (intent not in {"song", "chart", "card", "support_card"}
            or not isinstance(term, str) or len(term.strip()) > 50):
        return ValidationResult(None, OutcomeCode.INVALID_OUTPUT)
    if (not isinstance(skill_query, str) or len(skill_query.strip()) > 30
            or skill_kind not in {"", "leader", "live", "gekisou"}):
        return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
    if (skill_query or skill_kind) and intent != "card":
        return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
    normalized_question = normalize(question)
    if skill_query and normalize(skill_query) not in normalized_question:
        return ValidationResult(None, OutcomeCode.UNKNOWN_ENTITY)
    kind_words = {"leader": ("队长", "leader"), "live": ("live",), "gekisou": ("激奏",)}
    if skill_kind and not any(
        normalize(word) in normalized_question for word in kind_words[skill_kind]
    ):
        return ValidationResult(None, OutcomeCode.UNKNOWN_ENTITY)
    if not isinstance(difficulty, str) or difficulty not in {"", "EASY", "NORMAL", "HARD", "EXPERT"}:
        return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
    if intent in {"card", "support_card"} and difficulty:
        return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
    if comparison:
        if (intent != "song" or comparison not in {"gte", "gt", "lte", "lt"}
                or isinstance(level, bool) or not isinstance(level, (int, float))
                or not 1 <= level <= 40):
            return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
    elif level is not None or (intent == "song" and difficulty):
        return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
    anchor = find_anchor(
        intent, question, repository,
        explicit_card_ids=intent == "card" and bool(skill_query or skill_kind),
    )
    if anchor.ambiguous:
        return ValidationResult(None, OutcomeCode.AMBIGUOUS)
    if anchor.entity is None and not (intent == "card" and (skill_query or skill_kind)):
        return ValidationResult(None, OutcomeCode.UNKNOWN_ENTITY)
    if term.strip():
        if resolve_entity(intent, term, repository) is None:
            return ValidationResult(None, OutcomeCode.UNKNOWN_ENTITY)
        proposed = find_anchor(intent, term, repository)
        if proposed.ambiguous or proposed.entity != anchor.entity:
            return ValidationResult(None, OutcomeCode.UNKNOWN_ENTITY)
    if intent in {"chart", "support_card"} and anchor.entity is None:
        return ValidationResult(None, OutcomeCode.UNKNOWN_ENTITY)
    if intent == "card" and anchor.entity is None and not (skill_query or skill_kind):
        return ValidationResult(None, OutcomeCode.UNKNOWN_ENTITY)
    symbol = {"gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}.get(comparison, "")
    display = str(anchor.entity.value) if anchor.entity else ""
    spec = QuerySpec(
        intent, anchor.entity, difficulty, symbol,
        float(level) if comparison else None, display_name=display,
        skill_query=skill_query.strip(), skill_kind=skill_kind,
    )
    return ValidationResult(spec, OutcomeCode.SUCCESS)


def message_for(code: OutcomeCode) -> str:
    if code == OutcomeCode.UNKNOWN_ENTITY:
        return UNKNOWN_ENTITY
    if code == OutcomeCode.AMBIGUOUS:
        return AMBIGUOUS_ENTITY
    return UNSUPPORTED

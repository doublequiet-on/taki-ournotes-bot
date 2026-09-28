"""Ground and validate model-proposed query parameters."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from .commands import _split_page, split_card_rarity
from .data import SongRepository, normalize
from .entity_lexicon import find_anchor, resolve_entity
from .query_capabilities import CAPABILITIES, Capability
from .query_terms import is_skill_placeholder
from .song_conditions import extract_song_conditions
from .structured_query import QuerySpec


UNSUPPORTED = "目前只能查询歌曲、谱面、Haneoka歌曲分数表、成员卡和支援卡。试试 /查曲、/查谱面、/查分数表、/查卡 或 /查支援卡；不支持泛攻略、配队或预测。"
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


@dataclass(frozen=True)
class RouteResult:
    capability_id: str | None
    code: OutcomeCode
    prefetched: dict[str, Any] | None = None


_LEGACY_FIELDS = frozenset({
    "intent", "query", "difficulty", "level_operator", "level", "min_level",
    "skill_query", "skill_kind",
})

_LEVEL_OPERATOR_NAMES = {">=": "gte", ">": "gt", "<=": "lte", "<": "lt"}


def _song_condition_mismatches(arguments: dict[str, Any], question: str) -> tuple[str, ...]:
    evidence = extract_song_conditions(question)
    invalid = []
    if evidence.level_conflict:
        invalid.extend(("level_operator", "level"))
    elif evidence.has_level:
        if arguments.get("level_operator", "") != _LEVEL_OPERATOR_NAMES[evidence.comparison]:
            invalid.append("level_operator")
        proposed_level = arguments.get("level")
        if (isinstance(proposed_level, bool) or not isinstance(proposed_level, (int, float))
                or float(proposed_level) != evidence.level):
            invalid.append("level")
    if evidence.difficulty_conflict:
        invalid.append("difficulty")
    elif evidence.has_difficulty:
        if arguments.get("difficulty", "") != evidence.difficulty:
            invalid.append("difficulty")
    elif evidence.has_level and arguments.get("difficulty", ""):
        invalid.append("difficulty")
    return tuple(dict.fromkeys(invalid))


def validate_route(data: dict[str, Any]) -> RouteResult:
    """Validate the router's deliberately tiny action schema."""
    if set(data) != {"action", "capability"} or data.get("action") != "route":
        return RouteResult(None, OutcomeCode.INVALID_OUTPUT)
    capability_id = data.get("capability")
    if capability_id == "unsupported":
        return RouteResult(None, OutcomeCode.UNSUPPORTED)
    if not isinstance(capability_id, str) or capability_id not in CAPABILITIES:
        return RouteResult(None, OutcomeCode.INVALID_OUTPUT)
    return RouteResult(capability_id, OutcomeCode.SUCCESS)


def validate_capability_action(data: dict[str, Any], capability: Capability, question: str,
                               repository: SongRepository, *,
                               allow_empty_subject: bool = False) -> ValidationResult:
    """Validate a capability-scoped action and apply catalog grounding."""
    if capability.intent == "efficiency":
        from .efficiency_query import parse_efficiency
        spec = parse_efficiency(question, repository)
        if not isinstance(spec, QuerySpec):
            return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
        expected = {"query": str(spec.subject.value) if spec.subject else "",
                    "difficulty": spec.difficulty, "level_operator": {">=": "gte", ">": "gt", "<=": "lte", "<": "lt"}.get(spec.comparison, ""),
                    "level": spec.level, "page": spec.page, "metric": spec.metric, "order": spec.order}
        if data != {"action": "call_tool", "capability": "song.meta", "arguments": expected}:
            return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
        return ValidationResult(spec, OutcomeCode.SUCCESS)
    if "intent" in data:
        if any(key not in _LEGACY_FIELDS for key in data):
            return ValidationResult(None, OutcomeCode.INVALID_OUTPUT)
        if data.get("intent") == "unsupported":
            return ValidationResult(None, OutcomeCode.UNSUPPORTED)
        if data.get("intent") != capability.intent:
            return ValidationResult(None, OutcomeCode.INVALID_OUTPUT)
        return validate_legacy_plan(
            data, question, repository,
            allow_empty_subject=allow_empty_subject and capability.allow_empty_subject,
        )
    if data == {"action": "reject", "reason": "unsupported"}:
        return ValidationResult(None, OutcomeCode.UNSUPPORTED)
    if (set(data) != {"action", "capability", "arguments"}
            or data.get("action") != "call_tool"
            or data.get("capability") != capability.id):
        return ValidationResult(None, OutcomeCode.INVALID_OUTPUT)
    arguments = data.get("arguments")
    invalid = capability.invalid_fields(arguments)
    if invalid:
        return ValidationResult(
            None, OutcomeCode.INVALID_ARGUMENTS, invalid,
            capability.allowed_parameters, repairable=True,
        )
    assert isinstance(arguments, dict)
    # The model must preserve explicit page/rarity filters and cannot invent
    # either one. Both are parsed from the user's own question, independently
    # of the model's proposed arguments.
    expected_page = _split_page(question)[1]
    if arguments.get("page", 1) != expected_page:
        return ValidationResult(
            None, OutcomeCode.INVALID_ARGUMENTS, ("page",),
            capability.allowed_parameters, repairable=True,
        )
    if capability.allow_rarity:
        try:
            expected_rarity = split_card_rarity(question)[1]
        except ValueError:
            return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
        if arguments.get("rarity") != expected_rarity:
            return ValidationResult(
                None, OutcomeCode.INVALID_ARGUMENTS, ("rarity",),
                capability.allowed_parameters, repairable=True,
            )
    if capability.id == "song.search":
        invalid = _song_condition_mismatches(arguments, question)
        if invalid:
            return ValidationResult(
                None, OutcomeCode.INVALID_ARGUMENTS, invalid,
                capability.allowed_parameters, repairable=True,
            )
    legacy = {"intent": capability.intent, "query": arguments.get("query", "")}
    for name in ("difficulty", "level_operator", "level", "skill_query", "skill_kind"):
        if name in arguments:
            legacy[name] = arguments[name]
    validated = validate_legacy_plan(
        legacy, question, repository,
        allow_empty_subject=allow_empty_subject and capability.allow_empty_subject,
    )
    if validated.code == OutcomeCode.INVALID_ARGUMENTS:
        validated = replace(
            validated,
            invalid_fields=("arguments",),
            allowed=capability.allowed_parameters,
            repairable=True,
        )
    if validated.spec is not None:
        validated = replace(validated, spec=replace(
            validated.spec,
            page=int(arguments.get("page", 1)),
            rarity=arguments.get("rarity"),
        ))
    return validated


def validate_legacy_plan(data: dict[str, Any], question: str,
                         repository: SongRepository, *,
                         allow_empty_subject: bool = False) -> ValidationResult:
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
            or is_skill_placeholder(skill_query)
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
    if anchor.entity is None and not allow_empty_subject:
        return ValidationResult(None, OutcomeCode.UNKNOWN_ENTITY)
    if (intent == "card" and anchor.entity is None and "技能" in question
            and not (skill_query or skill_kind)):
        # A structurally valid "which cards' skill ..." question cannot be
        # turned into an unfiltered catalog listing by omitting its predicate.
        return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
    if term.strip():
        if resolve_entity(intent, term, repository) is None:
            return ValidationResult(None, OutcomeCode.UNKNOWN_ENTITY)
        proposed = find_anchor(intent, term, repository)
        if proposed.ambiguous or proposed.entity != anchor.entity:
            return ValidationResult(None, OutcomeCode.UNKNOWN_ENTITY)
    # Ground the proposed entity first. Quoted examples in the user's message
    # may contain level tokens, but cannot authorize an unrelated model query.
    condition_arguments = {
        **data,
        "difficulty": difficulty,
        "level_operator": comparison,
        "level": level,
    }
    if intent == "song" and _song_condition_mismatches(condition_arguments, question):
        return ValidationResult(None, OutcomeCode.INVALID_ARGUMENTS)
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

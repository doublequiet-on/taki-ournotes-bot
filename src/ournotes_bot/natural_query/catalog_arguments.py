# L3
# Input: Schema-checked model parameters, original question, repository identities.
# Output: A grounded QuerySpec or named mismatches; None means legacy fields only.
# Pos: Natural Query condition validation; see L2-2.md.
# Effects: Reads local aliases; never executes model-supplied command/filter strings.
"""Compare typed model dimensions with independently parsed ON evidence."""
from ..data import normalize, character_identity
from ..query.card_catalog import parse_card_request
from ..query.entity_lexicon import find_anchor, resolve_entity
from ..query.song_query import parse_filter
from ..structured_query import QuerySpec
from .on_query import parse_on_query

EXTENDED = ("colors", "mission_mode", "missions", "integer_level", "rarities",
            "live_categories", "gekisou_categories", "tags", "characters", "bands")


def grounded_catalog(arguments, intent, question, repository):
    parsed = parse_on_query(question, repository, include_legacy=True)
    active = any(arguments.get(key) for key in EXTENDED) or arguments.get("level_operator") == "eq" or arguments.get("difficulty") == "ALL"
    if isinstance(parsed, str):
        return None, ("conditions",)
    if not isinstance(parsed, QuerySpec) or (parsed.song_query is None and parsed.card_query is None):
        return (None, ("conditions",)) if active else (None, None)
    if parsed.intent != intent:
        return None, ("capability",)
    invalid = []
    if arguments.get("page", 1) != parsed.page:
        invalid.append("page")
    query = arguments.get("query", "").strip()
    if parsed.song_query is not None:
        req = parse_filter(parsed.song_query, repository, force=True)
        expected = {"colors": list(req.colors), "mission_mode": req.mode, "missions": list(req.missions),
                    "difficulty": req.difficulty, "level": req.level, "integer_level": req.integer_level,
                    "level_operator": {"=": "eq", ">=": "gte", ">": "gt", "<=": "lte", "<": "lt"}.get(req.comparison, "")}
        defaults = {"colors": [], "mission_mode": "", "missions": [], "difficulty": "", "level": None,
                    "integer_level": False, "level_operator": ""}
        for key, value in expected.items():
            proposed = arguments.get(key, defaults[key])
            # OR sets have no ordering; exact mission sequences keep duplicates.
            if key == "colors" or key == "missions" and req.mode != "exact":
                proposed, value = set(proposed), set(value)
            if proposed != value:
                invalid.append(key)
        if req.band:
            # The model contract still has one song/band subject; explicit
            # simultaneous song+band conditions must be handled locally.
            invalid.append("query")
        elif bool(query) != bool(req.term):
            invalid.append("query")
        elif query:
            expected_subject = find_anchor("song", req.term, repository)
            proposed_subject = find_anchor("song", query, repository)
            if (resolve_entity("song", query, repository) is None or expected_subject.ambiguous
                    or proposed_subject.ambiguous or proposed_subject.entity != expected_subject.entity):
                invalid.append("query")
    else:
        req = parse_card_request(parsed.card_query, repository, support=intent == "support_card")
        filters = req.filters
        if (not active and arguments.get("rarity") is not None
                and set(filters) <= {"rarity", "character", "band", "title_ids"}):
            return None, None  # Keep the established single-rarity QuerySpec contract.
        proposed = {}
        # query is an entity only, never a raw command or catalog field string.
        if query:
            if resolve_entity(intent, query, repository) is None:
                invalid.append("query")
            else:
                try:
                    entity = parse_card_request(query, repository, support=req.support)
                    proposed.update(entity.filters)
                    if entity.card_id is not None:
                        proposed["title_ids"] = (entity.card_id,)
                except ValueError:
                    invalid.append("query")
        for field, key in (("characters", "character"), ("bands", "band")):
            if arguments.get(field):
                try:
                    values = []
                    for name in arguments[field]:
                        selector = parse_card_request(("角色=" if key == "character" else "乐队=") + name, repository, support=req.support)
                        if set(selector.filters) != {key} or len(selector.filters[key]) != 1:
                            raise ValueError("not a single entity")
                        values.extend(selector.filters[key])
                    proposed[key] = (*proposed.get(key, ()), *values)
                except ValueError:
                    invalid.append(field)
        if req.card_id is not None:
            filters = {"title_ids": (req.card_id,)}
        for field, key in (("colors", "type"), ("rarities", "rarity"), ("live_categories", "live"),
                           ("gekisou_categories", "gekisou"), ("tags", "tags")):
            values = arguments.get(field, [])
            if field == "rarities" and arguments.get("rarity") is not None:
                if values:
                    invalid.extend(("rarity", "rarities"))
                values = [arguments["rarity"]]
            if values:
                proposed[key] = values
        if arguments.get("skill_query") or arguments.get("skill_kind"):
            invalid.extend(("skill_query", "skill_kind"))
        keys = set(proposed) | set(filters)
        for key in keys:
            expected_values = set(filters.get(key, ()))
            actual_values = set(proposed.get(key, ()))
            if key in {"character", "band"}:
                identity = character_identity if key == "character" else lambda v: v
                expected_values = {normalize(identity(v)) for v in expected_values}
                actual_values = {normalize(identity(v)) for v in actual_values}
            if actual_values != expected_values:
                invalid.append({"type": "colors", "rarity": "rarities", "live": "live_categories",
                                "gekisou": "gekisou_categories", "title_ids": "query"}.get(key, key))
    return (None, tuple(dict.fromkeys(invalid))) if invalid else (parsed, ())

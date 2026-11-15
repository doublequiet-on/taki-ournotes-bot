# L3
# Input: Same-release member LIVE effects and condition/target references.
# Output: Verified Lv.5 score bands and a compact Chinese summary.
# Pos: Sources / Haneoka Catalog conversion.
# Effects: Pure projection. Unrecognised score mechanics remain unconfirmed.
"""Project displayed score bands, including the conditional sum in LIVE skill 9."""
import math
import re

from .catalog_text import clean


LIVE_TYPES = {2000: "score", 2004: "score", 3001: "life", 12006: "judgement", 15000: "duration"}
_SUM = re.compile(r"\(effects\[0\]\.value\s*\+\s*effects\[1\]\.value\)\s*/\s*100(?=:F[012]|\})")


def _one_condition(refs, group):
    sets = [row["raw"]["_conditionIds"] for row in refs["conditionSets"].values()
            if row["raw"]["_group"] == group]
    if len(sets) == 1 and len(sets[0]) == 1:
        return refs["conditions"][sets[0][0]]["raw"]
    raise ValueError("unsupported LIVE condition")


def _judgements(refs, ids):
    targets = [refs["targets"][ident]["raw"] for ident in ids]
    if any(target["_skillTargetType"] != 4 for target in targets):
        raise ValueError("unsupported LIVE target")
    return {target["_judgement"] for target in targets}


def life_threshold(refs, group):
    row = _one_condition(refs, group)
    values = row["_conditionValues"]
    if (row["_conditionType"] == 2001 and not row["_conditionTargetIDs"]
            and type(row["_isPositive"]) is bool and len(values) == 1
            and type(values[0]) is int and values[0] >= 0):
        return values[0], row["_isPositive"]
    raise ValueError("unsupported LIFE threshold")


def _until_good_or_lower(refs, group):
    condition = _one_condition(refs, group)
    return (condition["_conditionType"] == 1020 and condition["_isPositive"] is True
            and not condition["_conditionValues"]
            and _judgements(refs, condition["_conditionTargetIDs"]) == {3})


def _score_band(value, condition=""):
    return {"value": value, "condition": condition}


def _percent(value):
    return f"+{value / 100:g}%"


def _project_score(rows, refs, template):
    # These rows describe fixed gains with a shared duration. Other caps,
    # triggers or formulas require a reviewed projection.
    times = {row.get("activationTimeSecond") for row in rows}
    kinds = {row.get("effectType") for row in rows}
    if len(times) != 1 or len(kinds) != 1 or any(
        row.get(key) for row in rows for key in (
            "triggerConditionGroup", "cumulativeConditionId", "executeLimitResetConditionGroup",
            "maxEffectValue", "effectLimitCount", "executeLimitCount")
    ):
        raise ValueError("unsupported score shape")
    duration = times.pop()
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise ValueError("invalid LIVE duration")
    values = [row["effectValue"] for row in rows]
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("invalid score gain")
    prefix = f"{duration:g}秒 · "
    if kinds == {2000} and all(not row.get("targetIds") and not row.get("releaseConditionGroup") for row in rows):
        if len(rows) == 1 and not rows[0].get("conditionGroup"):
            return [_score_band(values[0])], prefix + "得分" + _percent(values[0])
        if len(rows) == 2 and re.search(r"発動時に(?:LIFE|ライフ)が", template):
            first, second = [life_threshold(refs, row["conditionGroup"]) for row in rows]
            if first[0] == second[0] and first[1] != second[1]:
                threshold = first[0]
                low, high = values[::-1] if first[1] else values
                text = prefix + f"得分{_percent(low)}；发动时 LIFE≥{threshold} 则{_percent(high)}"
                return [_score_band(value, "life") for value in (low, high)], text
    if kinds == {2004} and all(not row.get("conditionGroup")
                               and _judgements(refs, row.get("targetIds", ())) == {5, 6} for row in rows):
        if len(rows) == 1 and not rows[0].get("releaseConditionGroup"):
            return [_score_band(values[0], "perfect")], prefix + f"PERFECT及以上得分{_percent(values[0])}"
        if (len(rows) == 2 and not rows[0].get("releaseConditionGroup")
                and rows[1].get("releaseConditionGroup")
                and _until_good_or_lower(refs, rows[1]["releaseConditionGroup"])
                and "GOOD以下を出すまで" in template
                and _SUM.search(template)):
            high = sum(values)
            text = prefix + (f"PERFECT及以上得分{_percent(values[0])}；"
                             f"出现 GOOD 或更低判定前{_percent(high)}")
            return [_score_band(value, "perfect") for value in (values[0], high)], text
    raise ValueError("unconfirmed score bands")


def member_live_projection(skill, refs):
    rows = [row for row in skill.get("effects", ()) if row.get("level") == 5]
    if not rows or len({row.get("effectId") for row in rows}) != len(rows):
        return [None], None, ""
    categories = list(dict.fromkeys(LIVE_TYPES.get(row.get("effectType")) for row in rows))
    if None in categories:
        return categories, None, ""
    score_rows = [row for row in rows if LIVE_TYPES[row["effectType"]] == "score"]
    if not score_rows:
        return categories, [], ""
    try:
        template = clean((skill.get("description") or [""])[0])
        bands, summary = _project_score(score_rows, refs, template if score_rows == rows else "")
    except (KeyError, ValueError, TypeError):
        return categories, None, ""
    return categories, bands, summary if score_rows == rows else ""

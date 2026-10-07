# L3
# Input: Haneoka localised text arrays, skill effects and same-release references.
# Output: Verified language fallback and bounded Lv.5 template expansion; unknown parameters stay explicit.
# Pos: Sources / Haneoka Catalog text conversion; see ../L2-2.md.
# Effects/Dependencies: Pure conversion, no network, eval, platform calls or upstream implementation.

from __future__ import annotations

import json
import math
import re

LANGUAGES = {"ja": 0, "en": 1, "zh": 3}
UNKNOWN = "参数未确认"


def clean(value):
    return re.sub(r"</?color(?:=[^>]*)?>", "", value, flags=re.I).replace("\r\n", "\n")


def original(value):
    from ...data import normalize
    return normalize(clean(value[0])) if isinstance(value, list) and value and isinstance(value[0], str) else ""


def texts(value):
    if not isinstance(value, list) or len(value) > 10 or any(not isinstance(v, str) or len(v) > 16000 for v in value):
        raise ValueError("invalid localized text")
    return value


def translated(jp, other=None, *, matched=True):
    jp = texts(jp)
    if not matched or not other or not original(jp) or original(jp) != original(other):
        return list(jp)
    other = texts(other)
    # JP already supplied text always wins. Only text slots may cross releases.
    return [jp[i] if i < len(jp) and jp[i].strip() else other[i] if i < len(other) else ""
            for i in range(max(len(jp), len(other)))]


def language(value, locale="zh"):
    value = texts(value)
    for i in (LANGUAGES.get(locale, 3), 0, 1):
        if i < len(value) and value[i].strip():
            return value[i].strip()
    return ""


def localized(value):
    return {locale: language(value, locale) for locale in LANGUAGES}


def references(document):
    result = {}
    for name in ("conditions", "conditionSets", "cumulativeConditions", "targets"):
        rows = document.get(name)
        if not isinstance(rows, list) or len(rows) > 20000:
            raise ValueError("missing skill reference table")
        result[name] = {}
        for row in rows:
            raw = row["raw"]
            key = raw["_id"]
            if type(key) is not int or key <= 0 or key in result[name]:
                raise ValueError("invalid skill reference ID")
            result[name][key] = row
    return result


def mechanics(skill, refs):
    """Include referenced mechanics, not only IDs shared by two releases."""
    effects = skill.get("effects")
    if not isinstance(effects, list) or len(effects) > 100:
        raise ValueError("invalid effects")
    groups, condition_ids, target_ids, cumulative_ids = set(), set(), set(), set()
    for effect in effects:
        for field in ("conditionGroup", "triggerConditionGroup", "releaseConditionGroup", "executeLimitResetConditionGroup"):
            if effect.get(field):
                groups.add(effect[field])
        target_ids.update(effect.get("targetIds", effect.get("raw", {}).get("_skillTargetIDs", [])))
        if effect.get("cumulativeConditionId"):
            cumulative_ids.add(effect["cumulativeConditionId"])
    sets = [row["raw"] for row in refs["conditionSets"].values() if row["raw"]["_group"] in groups]
    if {row["_group"] for row in sets} != groups:
        raise ValueError("missing condition group")
    for row in sets:
        condition_ids.update(row["_conditionIds"])
    conditions = [refs["conditions"][key]["raw"] for key in sorted(condition_ids)]
    cumulative = [refs["cumulativeConditions"][key]["raw"] for key in sorted(cumulative_ids)]
    for row in conditions + cumulative:
        target_ids.update(row.get("_conditionTargetIDs", []))
    targets = [refs["targets"][key]["raw"] for key in sorted(target_ids)]
    return {"effects": effects, "sets": sets, "conditions": conditions, "cumulative": cumulative,
            "targets": targets, "categories": skill.get("categories"),
            "mission": skill.get("gekisouMissionType"), "timing": skill.get("gekisouSupportSkillExecTiming")}


def effect_context(skill, refs, locale):
    def condition(row):
        return {"values": row["_conditionValues"],
                "targets": [{"name": language(refs["targets"][key].get("name", []), locale)}
                            for key in row.get("_conditionTargetIDs", [])]}

    def group(key):
        if not key:
            return []
        sets = [row["raw"] for row in refs["conditionSets"].values() if row["raw"]["_group"] == key]
        if not sets:
            raise ValueError("missing condition group")
        return [[condition(refs["conditions"][key]["raw"]) for key in row["_conditionIds"]] for row in sets]

    rows = [effect for effect in skill["effects"] if effect.get("level") == 5]
    if not rows or len(rows) > 16 or len({r["effectId"] for r in rows}) != len(rows):
        raise ValueError("missing or duplicate Lv.5 effects")
    result = []
    for row in rows:
        fields = {"value": "effectValue", "time": "activationTimeSecond", "maxValue": "maxEffectValue",
                  "limitCount": "effectLimitCount", "EffectExecuteLimitCount": "executeLimitCount"}
        item = {name: row[key] for name, key in fields.items() if key in row}
        item.update(con=group(row.get("conditionGroup")), tCon=group(row.get("triggerConditionGroup")))
        item["cCon"] = condition(refs["cumulativeConditions"][row["cumulativeConditionId"]]["raw"]) if row.get("cumulativeConditionId") else {}
        result.append(item)
    return {"effects": result}


def expression(value, context, depth=0):
    """Only the published arithmetic, field paths, concatenation and time conditional."""
    if depth > 5 or len(value) > 1000:
        raise ValueError("template limit")
    value = value.strip()
    conditional = re.fullmatch(r'0\s*<\s*(effects\[\d+\]\.time)\s*\?\s*(.*?)\s*:\s*(.*)', value)
    if conditional:
        path, yes, no = conditional.groups()
        return expression(yes if expression(path, context, depth + 1) > 0 else no, context, depth + 1)
    # Split only separators outside a JSON quoted string.
    parts = re.split(r'~(?=(?:[^"\\]*(?:\\.[^"\\]*)*"[^"\\]*(?:\\.[^"\\]*)*")*[^"\\]*$)', value)
    if len(parts) > 1:
        return "".join(render_number(expression(part, context, depth + 1)) for part in parts)
    if value.startswith('"'):
        result = json.loads(value)
        if not isinstance(result, str):
            raise ValueError("invalid template literal")
        return result
    fmt = re.fullmatch(r"(.+):F([012])", value)
    if fmt:
        number = expression(fmt[1], context, depth + 1)
        if type(number) not in (int, float) or not math.isfinite(number):
            raise ValueError("invalid format value")
        return f"{number:.{fmt[2]}f}"
    divide = re.fullmatch(r"(.+)/(100|1000)", value)
    if divide:
        return expression(divide[1], context, depth + 1) / int(divide[2])
    if not re.fullmatch(r"effects\[\d{1,2}\](?:\.(?:value|time|maxValue|limitCount|EffectExecuteLimitCount|con|tCon|cCon|values|targets|name)|\[\d{1,2}\])+", value):
        raise ValueError("unknown skill parameter")
    current = context
    for name, index in re.findall(r"([A-Za-z]+)|\[(\d+)\]", value):
        current = current[int(index)] if index else current[name]
    if type(current) not in (int, float, str) or (type(current) in (int, float) and not math.isfinite(current)):
        raise ValueError("invalid skill parameter value")
    return current


def render_number(value):
    return f"{value:g}" if type(value) in (int, float) else str(value)


def description(template, context):
    complete = True
    def replace(match):
        nonlocal complete
        try:
            value = expression(match[1], context)
            if value == "" and ".name" in match[1]:
                raise ValueError("missing target name")
            return render_number(value)
        except (ValueError, KeyError, TypeError, IndexError, OverflowError, ZeroDivisionError):
            complete = False
            return UNKNOWN
    rendered = re.sub(r"\{([^{}]+)\}", replace, clean(template))
    if "{" in rendered or "}" in rendered:
        return UNKNOWN, False
    return "\n".join(" ".join(line.split()) for line in rendered.splitlines() if line.strip()), complete

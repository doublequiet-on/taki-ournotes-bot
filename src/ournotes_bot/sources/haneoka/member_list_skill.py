# L3
# Input: Same-release role-card effects, term references and already verified LIVE bands.
# Output: User-approved Chinese list wording and leader type for recognised Lv.5 mechanics.
# Pos: Sources / Haneoka Catalog conversion.
# Effects: Pure projection. Missing or new mechanics use the existing source summary.
"""Bind the approved /查卡 list copy to mechanics rather than individual skills."""

from .catalog_text import clean
from .leader_summary import TARGET_DEFAULTS
from .live_score import life_threshold

COPY = {
    "演01": "+{加分}%",
    "演02": "+{加分}%P",
    "演03": "+{达标加分}% LIFE≥{LIFE门槛}",
    "演04": "+{条件加分}%P 无{失效判定}",
    "队01": "同队全+{加成}%",
    "队02": "同队{能力}+{加成}%",
    "队03": "同队{能力}+{乐队加成}%，同色{能力}+{属性加成}%",
    "队04": "同队{能力}+{乐队加成}%，同技{能力}+{演出类型加成}%",
    "队05": "同队{能力}+{乐队加成}%，同激{能力}+{激奏类型加成}%",
    "队06": "同队{能力}+{乐队加成}%\n{人数上限}名非{排除属性} 全体{能力}+{加成上限}%",
    "激01": "JUST+{增加量}",
    "激02": "JUST累+{累计上限}",
    "激03": "JUST+{达标增量} LIFE≥{LIFE门槛}",
    "激04": "JUST+{增加量} {减损判定}扣血−{减损比例}%",
    "激05": "COMBO+{增加量}",
    "激06": "COMBO+{增加量} 连击≥{连击门槛}",
    "激07": "COMBO+{达标增量} 连击中 LIFE≥{LIFE门槛}",
    "激08": "COMBO+{增加量} {减损判定}扣血−{减损比例}%",
    "激09": "累积量+{提升比例}%",
    "激10": "LUCK+{LUCK点数}pt",
    "激11": "开场充槽+{抽选条累积量}%\n触发{基础概率}%｜LIFE≥{LIFE门槛}时{达标概率}%",
    "激12": "抽选条累积量+{提升比例}% {减损判定}扣血−{减损比例}%",
}

_ATTRIBUTES = {1000: "全", 1001: "技巧", 1002: "形象", 1003: "表演"}
_COLORS = {1: "红赤", 2: "绀碧", 3: "翡翠", 4: "琉金", 5: "紫苑"}
_STACK_ATTRIBUTES = {1501: 1001, 1502: 1002, 1503: 1003}
_LEADER_FIELDS = {"_id", "_leaderSkillID", "_level", "_skillEffectType", "_effectValue", "_icon",
                  "_skillConditionGroup", "_skillCumulativeConditionID", "_skillTargetIDs",
                  "_effectExecuteLimitCount", "_effectExecuteLimitResetConditionGroup"}
_GEKISOU_FIELDS = (_LEADER_FIELDS - {"_leaderSkillID"}) | {
    "_gekisouSkillID", "_activationTimeSecond", "_skillTriggerType", "_skillTriggerConditionGroup",
    "_skillReleaseConditionGroup", "_maxEffectValue", "_effectLimitCount",
}
_COLOR_TARGET = "_cardType"
_LIVE_TARGET = "_liveSkillCategories"
_GEKISOU_TARGET = "_gekisouMissionType"
_SEGMENT_END = ((7013, True, (), ()),)


def _uint(value):
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ValueError("invalid skill parameter")
    return value


def _pct(value):
    integer, decimal = divmod(value, 100)
    return f"{integer}.{decimal:02}".rstrip("0") if decimal else str(integer)


def _selector(refs, ident, field, *, target_type=3):
    """Read a single-axis target and reject additional restrictions."""
    raw = refs["targets"][ident]["raw"]
    value = raw[field]
    expected = {**TARGET_DEFAULTS, "_id": ident, "_skillTargetType": target_type, field: value}
    if raw != expected or any(type(raw[key]) is not type(v) for key, v in expected.items()):
        raise ValueError("unsupported target")
    if field == _LIVE_TARGET:
        if len(value) != 1:
            raise ValueError("unsupported LIVE category target")
        value = value[0]
    if not _uint(value):
        raise ValueError("empty target selector")
    return value


def _condition_group(refs, group):
    if not group:
        return ()
    sets = [row["raw"]["_conditionIds"] for row in refs["conditionSets"].values()
            if row["raw"]["_group"] == group]
    if len(sets) != 1 or not sets[0]:
        raise ValueError("unsupported condition group")
    conditions = []
    for key in sets[0]:
        raw = refs["conditions"][key]["raw"]
        if set(raw) != {"_id", "_conditionType", "_isPositive", "_conditionValues", "_conditionTargetIDs"}:
            raise ValueError("unknown condition fields")
        if type(raw["_isPositive"]) is not bool:
            raise ValueError("invalid condition polarity")
        conditions.append((_uint(raw["_conditionType"]), raw["_isPositive"],
                           tuple(map(_uint, raw["_conditionValues"])), tuple(map(_uint, raw["_conditionTargetIDs"]))))
    return tuple(conditions)


def _cumulative(refs, ident, kind):
    raw = refs["cumulativeConditions"][ident]["raw"]
    if (set(raw) != {"_id", "_skillCumulativeConditionType", "_conditionValues",
                     "_maxCumulativeCount", "_conditionTargetIDs"}
            or _uint(raw["_skillCumulativeConditionType"]) != kind):
        raise ValueError("unsupported cumulative condition")
    (step,) = map(_uint, raw["_conditionValues"])
    count = _uint(raw["_maxCumulativeCount"])
    if not step or not count:
        raise ValueError("invalid cumulative limit")
    return step, count, tuple(map(_uint, raw["_conditionTargetIDs"]))


def _leader(rows, refs, card, band_ids):
    if len(rows) not in (1, 2) or len(band_ids) != 1:
        return None
    by_target = {}
    stack = None
    for row in rows:
        targets = row["raw"]["_skillTargetIDs"]
        effect = _uint(row["effectType"])
        if row["executeLimitCount"] or row["executeLimitResetConditionGroup"]:
            return None
        if effect in _STACK_ATTRIBUTES and not targets and stack is None:
            stack = row
            continue
        if effect not in _ATTRIBUTES or row["conditionGroup"] or row["cumulativeConditionId"]:
            return None
        (ident,) = row["targetIds"]
        if targets != [ident]:
            return None
        raw = refs["targets"][ident]["raw"]
        fields = [key for key in ("_bandID", _COLOR_TARGET, _LIVE_TARGET, _GEKISOU_TARGET) if raw[key]]
        (field,) = fields
        if field in by_target:
            return None
        by_target[field] = (_selector(refs, ident, field), effect, _uint(row["effectValue"]))
    band, stat, value = by_target.pop("_bandID")
    if band != band_ids[0]:
        return None
    ability, band_bonus = _ATTRIBUTES[stat], _pct(value)
    if len(rows) == 1:
        return "", COPY["队01" if stat == 1000 else "队02"].format(能力=ability, 加成=band_bonus)
    if stack is not None:
        if by_target or _STACK_ATTRIBUTES[stack["effectType"]] != stat:
            return None
        step, count, ids = _cumulative(refs, stack["cumulativeConditionId"], 3000)
        colors = {_selector(refs, ident, _COLOR_TARGET) for ident in ids}
        excluded = _COLORS.keys() - colors
        if (step != 1 or len(ids) != len(colors) or not colors <= _COLORS.keys() or len(excluded) != 1
                or _condition_group(refs, stack["conditionGroup"]) != ((3000, True, (), ids),)):
            return None
        limit = _uint(stack["effectValue"]) * count
        return "异色", COPY["队06"].format(能力=ability, 乐队加成=band_bonus, 人数上限=count,
                                         排除属性=_COLORS[excluded.pop()], 加成上限=_pct(limit))
    (field, (target, second_stat, bonus)), = by_target.items()
    skills = card["resolvedSkills"]
    if second_stat != stat or stat == 1000:
        return None
    if field == _COLOR_TARGET and target == card["cardType"] and target in _COLORS:
        return "同色", COPY["队03"].format(能力=ability, 乐队加成=band_bonus, 属性加成=_pct(bonus))
    if field == _LIVE_TARGET and skills["live"]["categories"] == [target]:
        return "同技", COPY["队04"].format(能力=ability, 乐队加成=band_bonus, 演出类型加成=_pct(bonus))
    if field == _GEKISOU_TARGET and skills["gekisou"]["gekisouMissionType"] == target:
        return "同激", COPY["队05"].format(能力=ability, 乐队加成=band_bonus, 激奏类型加成=_pct(bonus))
    return None


def _live(rows, refs, bands):
    # These are the same bands already validated for numeric filtering and full replies.
    if not bands or len(rows) != len(bands):
        return ""
    family = bands[0]["condition"]
    shown = _pct(_uint(bands[-1]["value"]))
    if len(bands) == 1:
        key = {"": "演01", "perfect": "演02"}.get(family)
        return COPY[key].format(加分=shown) if key else ""
    if len(bands) == 2 and family == "life":
        threshold, _ = life_threshold(refs, rows[0]["conditionGroup"])
        return COPY["演03"].format(达标加分=shown, LIFE门槛=threshold)
    if len(bands) == 2 and family == "perfect":
        return COPY["演04"].format(条件加分=shown, 失效判定="GOOD及以下")
    return ""


def _gekisou_shape(row, refs, *, trigger_type, trigger, time=0, condition=(), release=(),
                   reset=(), execute=0, cumulative=False):
    return (row["activationTimeSecond"] == time and row["triggerType"] == trigger_type
            and not row["raw"]["_skillTargetIDs"] and not row["maxEffectValue"] and not row["effectLimitCount"]
            and row["executeLimitCount"] == execute and bool(row["cumulativeConditionId"]) == cumulative
            and _condition_group(refs, row["triggerConditionGroup"]) == trigger
            and _condition_group(refs, row["releaseConditionGroup"]) == release
            and _condition_group(refs, row["executeLimitResetConditionGroup"]) == reset
            and _condition_group(refs, row["conditionGroup"]) == condition)


def _mission_trigger(refs, row, kind, mission):
    trigger = _condition_group(refs, row["triggerConditionGroup"])
    ((code, positive, values, (target,)),) = trigger
    if code != kind or not positive or values or _selector(refs, target, _GEKISOU_TARGET, target_type=5) != mission:
        raise ValueError("unsupported mission trigger")
    return trigger


def _one_value(conditions, kind, positive=True):
    matches = [values for code, sign, values, ids in conditions if code == kind and sign == positive and not ids]
    ((value,),) = matches
    return value


def _life_pair(groups):
    """The two tiers cover complementary LIFE ranges at the same threshold."""
    threshold = _one_value(groups[1], 2001)
    if threshold != _one_value(groups[0], 2001, False):
        raise ValueError("inconsistent LIFE tiers")
    return threshold


def _gekisou(skill, rows, refs):
    mission = _uint(skill["gekisouMissionType"])
    values = [_uint(row["effectValue"]) for row in rows]
    types = tuple(_uint(row["effectType"]) for row in rows)
    kind, basic, reduced = {1: (12000, "激05", "激08"), 2: (11001, "激09", "激12"),
                           3: (13000, "激01", "激04")}[mission]
    first = rows[0]
    if types in ((kind,), (kind, 3004)):
        group = _condition_group(refs, first["triggerConditionGroup"])
        if mission == 1 and len(rows) == 1 and len(group) == 1 and group[0][0] == 7005:
            threshold = _one_value(group, 7005)
            if threshold and _gekisou_shape(first, refs, trigger_type=2, trigger=group):
                return COPY["激06"].format(增加量=values[0], 连击门槛=threshold)
        trigger = _mission_trigger(refs, first, 7020, mission)
        params = {"增加量": values[0], "提升比例": _pct(values[0])}
        if len(rows) == 1 and _gekisou_shape(first, refs, trigger_type=2, trigger=trigger):
            return COPY[basic].format(**params)
        if (len(rows) == 2 and "BAD以下のライフ減少" in clean(skill["description"][0])
                and all(_gekisou_shape(row, refs, trigger_type=2, trigger=trigger, release=_SEGMENT_END)
                        for row in rows)):
            return COPY[reduced].format(**params, 减损判定="BAD及以下", 减损比例=_pct(values[1]))
    if mission == 2 and types == (11002,) and _gekisou_shape(
            first, refs, trigger_type=1, trigger=((7000, True, (3,), ()),)):
        return COPY["激10"].format(LUCK点数=values[0])
    if mission == 3 and types == (13002,):
        trigger = _mission_trigger(refs, first, 7010, mission)
        step, limit, ids = _cumulative(refs, first["cumulativeConditionId"], 1000)
        (target,) = ids
        if (step and _selector(refs, target, "_judgement", target_type=4) == 6
                and _gekisou_shape(first, refs, trigger_type=1, trigger=trigger, time=9999,
                                   release=_SEGMENT_END, cumulative=True)):
            return COPY["激02"].format(累计上限=values[0] * limit)
    if mission == 3 and types == (13000, 13000):
        trigger = _mission_trigger(refs, first, 7010, mission)
        groups = [_condition_group(refs, row["conditionGroup"]) for row in rows]
        life = _life_pair(groups)
        if groups == [((2001, False, (life,), ()),), ((2001, True, (life,), ()),)] and all(
                _gekisou_shape(row, refs, trigger_type=1, trigger=trigger, time=99999, condition=group,
                               release=_SEGMENT_END, reset=_SEGMENT_END, execute=1)
                for row, group in zip(rows, groups)):
            return COPY["激03"].format(达标增量=values[1], LIFE门槛=life)
    if mission == 1 and types == (12000, 12000):
        groups = [_condition_group(refs, row["triggerConditionGroup"]) for row in rows]
        life = _life_pair(groups)
        if groups == [((7005, True, (1,), ()), (2001, positive, (life,), ())) for positive in (False, True)] and all(
                _gekisou_shape(row, refs, trigger_type=2, trigger=group) for row, group in zip(rows, groups)):
            return COPY["激07"].format(达标增量=values[1], LIFE门槛=life)
    if mission == 2 and types == (11003, 11003) and values[0] == values[1]:
        trigger = _mission_trigger(refs, first, 7010, mission)
        groups = [_condition_group(refs, row["conditionGroup"]) for row in rows]
        life = _life_pair(groups)
        chances = [_one_value(group, 4011) for group in groups]
        if (chances[0] <= chances[1] <= 100 and groups == [
                ((2001, pos, (life,), ()), (4011, True, (chance,), ()))
                for pos, chance in zip((False, True), chances)] and all(
                _gekisou_shape(row, refs, trigger_type=1, trigger=trigger, condition=group, release=_SEGMENT_END)
                for row, group in zip(rows, groups))):
            return COPY["激11"].format(抽选条累积量=_pct(values[0]), 基础概率=chances[0],
                                       LIFE门槛=life, 达标概率=chances[1])
    return ""


def _level_five_rows(skill):
    rows = [row for row in skill["effects"] if row["level"] == 5]
    if not rows or len({row["effectId"] for row in rows}) != len(rows):
        raise ValueError("missing or duplicate Lv.5 skill effects")
    return rows


def member_leader_projection(skill, refs, card, band_ids):
    """Return (type, list text), with an empty type for verified band-only skills."""
    try:
        rows = _level_five_rows(skill)
        if all(set(row["raw"]) == _LEADER_FIELDS for row in rows):
            return _leader(rows, refs, card, band_ids)
    except (KeyError, ValueError, TypeError, IndexError):
        pass
    return None


def member_list_skill_copy(kind, skill, refs, live_bands):
    """Return an empty string for unsupported mechanics so existing summaries win."""
    try:
        rows = _level_five_rows(skill)
        if kind == "liveSkill":
            return _live(rows, refs, live_bands)
        if kind == "gekisouSkill" and all(set(row["raw"]) == _GEKISOU_FIELDS for row in rows):
            return _gekisou(skill, rows, refs)
    except (KeyError, ValueError, TypeError, IndexError):
        return ""
    return ""

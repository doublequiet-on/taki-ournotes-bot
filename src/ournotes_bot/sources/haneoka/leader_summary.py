# L3
# Input: One JP leader skill, same-release target references and band identities.
# Output: Bounded attribute/target summary for verified parameterized mechanics, or None.
# Pos: Data / Sources Haneoka member summaries; see ../L2-2.md.
# Effects/Dependencies: Pure validation and formatting; no I/O, model or dynamic code.

"""Render known leader mechanics without registering each new card's hash.

Both raw mechanics and the complete Japanese template must agree. Values, band
identities and skill IDs are data; a new condition/effect shape is not approved.
"""
from __future__ import annotations

from .catalog_text import clean

# Japanese tokens verified in the JP leader templates (not guessed official CN names).
ATTRIBUTES = {1000: ("全パラメータ", "全属性"), 1001: ("テクニック", "技巧值"),
              1002: ("ビジュアル", "表现值"), 1003: ("パフォーマンス", "表演值")}
COLORS = {1: ("紅赤", "红色"), 2: ("紺碧", "蓝色"), 5: ("紫苑", "紫色")}
MISSIONS = {1: "COMBO", 2: "LUCK", 3: "JUST"}
TARGET_DEFAULTS = {"_bandID": 0, "_cardType": 0, "_characterID": 0,
                   "_gekisouMissionType": 0, "_gekisouSkillCategories": [],
                   "_judgement": -1, "_liveMusicType": 0, "_liveSkillCategories": [],
                   "_skillTargetType": 3, "_tagID": 0}


def target(row, index):
    ids = row["targetIds"]
    if not isinstance(ids, list) or len(ids) != 1 or type(ids[0]) is not int:
        raise ValueError("unsupported leader target set")
    raw = index["targets"][ids[0]]
    changed = {key: value for key, value in raw.items()
               if key != "_id" and value != TARGET_DEFAULTS.get(key)}
    if len(changed) != 1:
        raise ValueError("unsupported combined leader target")
    field, value = next(iter(changed.items()))
    if type(value) is not int or value <= 0:
        raise ValueError("invalid leader target")
    expected = {**TARGET_DEFAULTS, "_id": ids[0], field: value}
    # Strict types also reject True where an integer enum or zero is required.
    if raw != expected or any(type(raw[k]) is not type(v) for k, v in expected.items()):
        raise ValueError("unknown leader target mechanics")
    if field == "_bandID":
        name = index["bands"][value]
        if name != index["target_names"].get(ids[0]):
            raise ValueError("leader band name/identity mismatch")
        return "band", name + "メンバーの", name
    if field == "_cardType":
        jp, zh = COLORS[value]
        return "color", jp + "のメンバーは", zh
    if field == "_gekisouMissionType":
        name = MISSIONS[value]
        return "gekisou", "撃奏スキル【" + name + "】のメンバーは", name + "激奏"
    raise ValueError("unknown leader target selector")


def summarize(skill, rows, index):
    from .haneoka_members import number, fmt
    try:
        if not index.get("bands") or len(rows) not in (1, 2) or type(skill["id"]) is not int:
            return None
        effect_type = rows[0]["effectType"]
        if type(effect_type) is not int:
            return None
        jp_attribute, attribute = ATTRIBUTES[effect_type]
        targets, values = [], []
        for row in rows:
            effect_id, value = row["effectId"], number(row["effectValue"])
            if type(effect_id) is not int or effect_id <= 0:
                return None
            expected_raw = {"_effectExecuteLimitCount": 0, "_effectExecuteLimitResetConditionGroup": 0,
                            "_effectValue": value, "_icon": "", "_id": effect_id,
                            "_leaderSkillID": skill["id"], "_level": 5, "_skillConditionGroup": 0,
                            "_skillCumulativeConditionID": 0, "_skillEffectType": effect_type,
                            "_skillTargetIDs": row["targetIds"]}
            expected = dict(conditionGroup=0, cumulativeConditionId=0, effectId=effect_id,
                            effectType=effect_type, effectValue=value, executeLimitCount=0,
                            executeLimitResetConditionGroup=0, level=5, raw=expected_raw,
                            sourceTable="MasterLeaderSkillEffect", targetIds=row["targetIds"])
            if row != expected:
                return None
            for actual, template in ((row, expected), (row["raw"], expected_raw)):
                if any(type(actual[k]) is not type(v) for k, v in template.items() if k != "effectValue" and k != "_effectValue"):
                    return None
            targets.append(target(row, index))
            values.append(fmt(value / 100))
        kinds = tuple(t[0] for t in targets)
        if kinds not in {("band",), ("color", "band"), ("band", "color"), ("band", "gekisou")}:
            return None
        # Exact full templates prevent an unhandled condition from being dropped.
        text = clean(skill["description"][0]).strip()
        valid = {" さらに".join(t[1] + jp_attribute + particle +
                               "{effects[" + str(i) + "].value/100:F1}%UP"
                               for i, t in enumerate(targets)) for particle in ("", "が")}
        if text not in valid:
            return None
        if len(rows) == 1:
            return f"{attribute} +{values[0]}%", targets[0][2] + "成员"
        return attribute + "提升", "\n".join(f"{t[2]}成员 +{v}%" for t, v in zip(targets, values))
    except (KeyError, ValueError, TypeError, IndexError):
        return None

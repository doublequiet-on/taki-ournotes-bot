# L3
# Input: Verified Chinese Lv.5 SNAP skill text and its card's single verified band.
# Output: Approved list copy, or empty to keep the current source-text fallback.
# Pos: Sources / Haneoka Catalog conversion.
# Effects: Pure projection; no network or per-card overrides.
"""Match complete known source wording before applying the compact list copy."""

import re
from string import Formatter


def _without_spaces(text):
    return re.sub(r"\s+", "", text)


def _rule(source, summary):
    pattern = []
    for literal, field, _, _ in Formatter().parse(_without_spaces(source)):
        pattern.append(re.escape(literal))
        if field is not None:
            value = r"[^」]+" if field == "增强乐队" else r"[0-9]+(?:\.[0-9]+)?"
            pattern.append(rf"(?P<{field}>{value})")
    return re.compile("".join(pattern)), summary


_RULES = {
    "supportSkill": (
        _rule("装配此技能的成员的演出技能发动时间延长{基础秒数}秒"
              "若为「{增强乐队}」成员，则演出技能发动时间延长{增强秒数}秒",
              "演出延长+{基础秒数}s\n同队+{增强秒数}s"),
        _rule("装配此技能的成员发动演出技能期间将GREAT转化为PERFECT（最多{次数上限}次）"
              "若为「{增强乐队}」成员，对GOOD也生效",
              "GREAT转P×{次数上限}\n同队含GOOD"),
        _rule("装配此技能的成员发动演出技能时，LIFE回复{基础回复量}"
              "若为「{增强乐队}」成员则LIFE回复{增强回复量}",
              "LIFE+{基础回复量}\n同队+{增强回复量}"),
        _rule("装配此技能的成员的演出技能发动时间延长{延长秒数}秒",
              "演出延长+{延长秒数}s"),
        _rule("装配此技能的成员发动演出技能时，LIFE回复{回复量}",
              "LIFE+{回复量}"),
        _rule("装配此技能的成员发动演出技能时，{持续秒数}秒内将GREAT转化为PERFECT（最多{次数上限}次）",
              "GREAT转P×{次数上限} {持续秒数}s"),
        _rule("将BAD及以下判定转换为GOOD（最多{基础次数}次）"
              "若为「{增强乐队}」成员，最多可触发{增强次数}次",
              "BAD及以下转GOOD×{基础次数}\n同队×{增强次数}"),
        _rule("所设置成员的演出技能发动期间，每{基础PERFECT次数}次PERFECT得分提升{每次加分}%（最高{基础上限}%）"
              "若为「{增强乐队}」成员每{增强PERFECT次数}次PERFECT（最高{增强上限}%）",
              "每{基础PERFECT次数}P +{每次加分}% 上限{基础上限}%\n同队每{增强PERFECT次数}P 上限{增强上限}%"),
    ),
    "gekisouSupportSkill": (
        _rule("LUCKY RUSH中，得分提升{基础加分}%若为「{增强乐队}」成员则得分提升{增强加分}%",
              "RUSH+{基础加分}%\n同队+{增强加分}%"),
        _rule("JUST激奏期间，每达成{JUST间隔}次JUST判定得分提升{基础每次加分}%（最高{累计上限}%）"
              "若为「{增强乐队}」成员则得分提升{增强每次加分}%",
              "每{JUST间隔}次JUST +{基础每次加分}% 上限{累计上限}%\n同队+{增强每次加分}%"),
        _rule("在COMBO激奏中，每达成{COMBO间隔}次激奏COMBO得分提升{基础每次加分}%（最高{累计上限}%）"
              "若为「{增强乐队}」成员则得分提升{增强每次加分}%",
              "每{COMBO间隔}连击 +{基础每次加分}% 上限{累计上限}%\n同队+{增强每次加分}%"),
        _rule("COMBO激奏期间，将GREAT转化为PERFECT（最多{次数上限}次）若为「{增强乐队}」成员对GOOD也生效",
              "COMBO GREAT转P×{次数上限}\n同队含GOOD"),
        _rule("JUST激奏期间，每达成{基础PERFECT次数}次PERFECT，将下一次PERFECT转化为JUST（最多{次数上限}次）"
              "若为「{增强乐队}」成员则每{增强PERFECT次数}次发动",
              "每{基础PERFECT次数}P 下个P转JUST×{次数上限}\n同队每{增强PERFECT次数}P"),
        _rule("JUST激奏期间JUST判定范围扩大{基础扩大比例}%若为「{增强乐队}」成员则JUST判定范围扩大{增强扩大比例}%",
              "JUST范围+{基础扩大比例}% JUST激奏期间\n同队+{增强扩大比例}%"),
        _rule("COMBO激奏期间即使出现MISS也不会重置激奏COMBO（最多{次数上限}次）"
              "若为「{增强乐队}」成员对BAD也生效",
              "MISS保连×{次数上限} COMBO激奏期间\n同队含BAD"),
        _rule("LUCK激奏开始时有{发动概率}%的概率发动，使抽选结果保底为LUCKY（最多{次数上限}次）"
              "若为「{增强乐队}」成员结果保底为SUPER LUCKY",
              "开场{发动概率}%保LUCKY×{次数上限}\n同队保SUPER LUCKY"),
        _rule("触发NO LUCK时，抽选条累积{基础充槽}%（最多{次数上限}次）"
              "若为「{增强乐队}」成员抽选条累积{增强充槽}%",
              "NO LUCK 充槽+{基础充槽}%×{次数上限}\n同队+{增强充槽}%"),
    ),
}


def support_list_skill_copy(kind, description, band):
    text = _without_spaces(description)
    for pattern, summary in _RULES.get(kind, ()):
        match = pattern.fullmatch(text)
        if match is None:
            continue
        values = match.groupdict()
        target = values.pop("增强乐队", None)
        if target is not None and target != _without_spaces(band):
            return ""
        numbers = {key: value.rstrip("0").rstrip(".") if "." in value else value
                   for key, value in values.items()}
        return summary.format(**numbers)
    return ""

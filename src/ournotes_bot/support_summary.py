"""Conservative Lv.5 summaries of the existing Project Yume Chinese effects.

Only complete, observed templates are accepted. New wording falls back to detail,
not a number guessed from an arbitrary percentage inside the description.
"""
import re

NUMBER = r"(\d+(?:\.\d+)?)"


def summarize(skill):
    text = skill.localized.get("description", {}).get("zh") or skill.description
    text = re.sub(r"\s+", "", text)
    n = NUMBER
    # Basic effect is shown explicitly; the conditional improvement stays in detail.
    templates = [
        ("supportSkill", rf"装配此技能的成员的演出技能发动时间延长{n}秒(?:若为「[^」]+」成员，则演出技能发动时间延长{n}秒)?",
         lambda m: (f"延长 +{float(m[1]):g}秒", "")),
        ("supportSkill", rf"装配此技能的成员发动演出技能时，LIFE回复{n}(?:若为「[^」]+」成员则LIFE回复{n})?",
         lambda m: (f"LIFE回复 +{float(m[1]):g}", "")),
        ("supportSkill", rf"装配此技能的成员发动演出技能期间将GREAT转化为PERFECT（最多{n}次）若为「[^」]+」成员，对GOOD也生效",
         lambda m: (f"判定强化 · {float(m[1]):g}次", "GREAT → PERFECT")),
        ("gekisouSupportSkill", rf"LUCKYRUSH中，得分提升{n}%若为「[^」]+」成员则得分提升{n}%",
         lambda m: (f"LUCK得分 +{float(m[1]):g}%", "LUCKY RUSH期间")),
        ("gekisouSupportSkill", rf"JUST激奏期间，每达成{n}次JUST判定得分提升{n}%（最高{n}%）若为「[^」]+」成员则得分提升{n}%",
         lambda m: (f"JUST得分 +{float(m[2]):g}%", f"每{float(m[1]):g}次JUST · 上限{float(m[3]):g}%")),
        ("gekisouSupportSkill", rf"在COMBO激奏中，每达成{n}次激奏COMBO得分提升{n}%（最高{n}%）若为「[^」]+」成员则得分提升{n}%",
         lambda m: (f"COMBO得分 +{float(m[2]):g}%", f"每{float(m[1]):g}连击 · 上限{float(m[3]):g}%")),
        ("gekisouSupportSkill", rf"COMBO激奏期间，将GREAT转化为PERFECT（最多{n}次）若为「[^」]+」成员对GOOD也生效",
         lambda m: (f"COMBO判强 · {float(m[1]):g}次", "GREAT → PERFECT")),
        ("gekisouSupportSkill", rf"JUST激奏期间，每达成{n}次PERFECT，将下一次PERFECT转化为JUST（最多{n}次）若为「[^」]+」成员则每{n}次发动",
         lambda m: (f"JUST转换 · {float(m[2]):g}次", f"每{float(m[1]):g}次PERFECT触发")),
        ("gekisouSupportSkill", rf"JUST激奏期间JUST判定范围扩大{n}%若为「[^」]+」成员则JUST判定范围扩大{n}%",
         lambda m: (f"JUST判定 +{float(m[1]):g}%", "判定范围扩大")),
        ("gekisouSupportSkill", rf"COMBO激奏期间即使出现MISS也不会重置激奏COMBO（最多{n}次）若为「[^」]+」成员对BAD也生效",
         lambda m: (f"COMBO保护 · {float(m[1]):g}次", "MISS不重置激奏COMBO")),
        ("gekisouSupportSkill", rf"LUCK激奏开始时有{n}%的概率发动，使抽选结果保底为LUCKY（最多{n}次）若为「[^」]+」成员结果保底为SUPERLUCKY",
         lambda m: (f"LUCK保底 · 概率{float(m[1]):g}%", f"开始时保底LUCKY · {float(m[2]):g}次")),
        ("gekisouSupportSkill", rf"触发NOLUCKY时，抽选条累积{n}%（最多{n}次）若为「[^」]+」成员抽选条累积{n}%",
         lambda m: (f"LUCK抽选条 +{float(m[1]):g}%", f"NO LUCKY时 · 最多{float(m[2]):g}次")),
    ]
    for kind, pattern, output in templates:
        if skill.kind == kind and (match := re.fullmatch(pattern, text)):
            # Reject absurd/nonfinite values before formatting a public summary.
            if any(float(v) > 1000000 for v in match.groups() if v is not None):
                break
            return output(match)
    return "摘要未确认", "请按 ID 查看完整效果"


def entries(card):
    result = []
    for kind, label in (("supportSkill", "演出"), ("gekisouSupportSkill", "激奏")):
        skills = [s for s in card.skills if s.kind == kind]
        result.extend((label, *summarize(skill)) for skill in skills)
        if not skills:
            na = kind == "gekisouSupportSkill" and card.catalog.get("categories", {}).get("gekisou") == ["not_applicable"]
            result.append((label, "不适用" if na else "详情未获取", ""))
    return result

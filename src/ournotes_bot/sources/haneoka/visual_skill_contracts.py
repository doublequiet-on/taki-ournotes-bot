# L3
# Input: A pinned skill and its same-release referenced mechanics.
# Output: Exact reviewed summaries or conservative automatic source-text summaries.
# Pos: Data / Sources Haneoka; approved visual-summary contracts.
# Effects: Reads one bundled JSON contract; no network, inference or model calls.
"""Do not reuse a short summary merely because a skill ID stayed the same."""
from functools import lru_cache
import hashlib
import json
import re
from pathlib import Path

from .catalog_text import mechanics, original


def fingerprint(skill, refs):
    def normalized(value):
        if isinstance(value, dict):
            return {k: normalized(v) for k,v in value.items()}
        if isinstance(value, list):
            return [normalized(v) for v in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    evidence={'mechanics':mechanics(skill,refs),'template':original(skill.get('description'))}
    raw=json.dumps(normalized(evidence),sort_keys=True,ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(raw.encode()).hexdigest()


@lru_cache(maxsize=1)
def contracts():
    return json.loads(Path(__file__).with_name('visual_skill_contracts.json').read_text(encoding='utf-8'))['skills']


def summary(skill, refs):
    try:
        return contracts().get(fingerprint(skill,refs))
    except (ValueError,KeyError,TypeError):
        return None


def automatic_summary(levels):
    """Shorten resolved source text without inventing mechanics or dropping clauses.

    Each entry is a (Skill, complete, source_language) result from skill_record.
    Unknown templates stay readable verbatim; only unambiguous Chinese phrases
    are abbreviated. Values and every condition remain from this release.
    """
    result = {}
    for level, (skill, complete, language) in enumerate(levels, 1):
        if not complete:
            continue
        text = skill.localized['description']['zh']
        if language == 'zh':
            text = text.replace('装配此技能的成员的', '装配者')
            text = text.replace('装配此技能的成员', '装配者')
            text = text.replace('演出技能发动时间延长', '演出延长')
            if skill.kind in {'supportSkill', 'gekisouSupportSkill'}:
                text = re.sub(r'^若为「([^」]+)」\s*成员[，,]?\s*(?:则)?',
                              r'\1装配时：', text, flags=re.M)
            text = re.sub(r'在COMBO激奏中，每达成(\d+)次激奏COMBO', r'COMBO激奏每\1连击：', text)
            text = re.sub(r'(得分|获得量|获取量|回复量|全参数|表演值|技巧值|表现值)提升(?=\d)', r'\1+', text)
            text = re.sub(r'(\d+(?:\.\d+)?)秒', r'\1s', text)
            text = re.sub(r'(\d+)\.(\d+)(?=%|s|次|pt)',
                          lambda m: m[1] + ('.' + m[2].rstrip('0') if m[2].rstrip('0') else ''), text)
        # Preserve punctuation, names, probability, limits and conditional scope.
        result[str(level)] = text
    return {'automatic': True, 'levels': result} if result else None

# L3
# Input: A pinned skill and its same-release referenced mechanics.
# Output: Reviewed Lv.1–5 summaries only for an exact mechanics/template match.
# Pos: Data / Sources Haneoka; approved visual-summary contracts.
# Effects: Reads one bundled JSON contract; no network, inference or model calls.
"""Do not reuse a short summary merely because a skill ID stayed the same."""
from functools import lru_cache
import hashlib
import json
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

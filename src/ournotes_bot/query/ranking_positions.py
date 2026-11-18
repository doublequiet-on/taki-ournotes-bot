# L3: Pure response-position syntax shared by the two cutoff queries.
import re

KEY_POSITIONS = (1, 2, 3, 10, 100)
_NUMBER = r'[tT]?[+-]?\d+(?:\.\d+)?'
RANK_PART = re.compile(rf'(?<!\S)({_NUMBER})(?:\s*[-—–~～到]\s*({_NUMBER}))?(?!\S)')


def nearby_window(position: int, limit: int) -> tuple[int, int]:
    return max(1, position - 10), min(limit, position + 10)

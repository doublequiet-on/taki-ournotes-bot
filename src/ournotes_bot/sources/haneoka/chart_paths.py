# L3
# Input: Haneoka JP chart file path and verified release label.
# Output: Release-pinned URL for the chart-only MusicScore subtree, or ChartDataError.
# Pos: Data / Sources concrete chart permission, shared by Catalog conversion; see ../L2-2.md.
# Effects/Dependencies: Pure resource-specific validation; no HTTP, cache or chart execution.
"""Chart file permissions stay separate from the general public transport."""
import re

from ..errors import ChartDataError
from .transport import ORIGIN

ASSET_PREFIX = "/assets/jp/Assets/AddressableResources/Live/MusicScore/"


def chart_asset_url(path, release):
    if (not isinstance(path, str) or len(path) > 1024 or not path.startswith(ASSET_PREFIX)
            or any(segment in {".", "..", ""} or not re.fullmatch(r"[A-Za-z0-9_.-]+", segment)
                   for segment in path[1:].split("/"))):
        raise ChartDataError("Invalid Haneoka chart asset path")
    return ORIGIN + path + "?release=" + release

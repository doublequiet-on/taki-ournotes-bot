# L3
# Input: Expected resource server or game region, release JSON and response headers.
# Output: Validated release identity, exact header match or explicit rejection.
# Pos: Data / Sources Haneoka public identity rules; see ../L2-2.md.
# Effects/Dependencies: Pure checks, explicit independent static/live allowlists; no adapters.
"""Resource servers and live game regions are separate identity namespaces."""
import re

from ..errors import ChartDataError

RESOURCE_SERVERS = frozenset({"jp", "intl"})
# The user-facing hk alias is normalized to tw before reaching source DTOs.
GAME_REGIONS = frozenset({"jp", "tw", "kr", "en"})


def release_identity(value, server):
    if (not isinstance(server, str) or server not in RESOURCE_SERVERS or not isinstance(value, dict)
            or value.get("schema") != "haneoka-resource-release-identity-v1"
            or value.get("server") != server
            or not isinstance(value.get("releaseId"), str)
            or not re.fullmatch(r"r-[a-f0-9]{20}", value["releaseId"])
            or not isinstance(value.get("sourceId"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", value["sourceId"])):
        raise ChartDataError("Invalid Haneoka release identity")
    return value


def check_headers(headers, identity):
    if (headers.get("x-haneoka-release-id") != identity["releaseId"]
            or headers.get("x-haneoka-source-id") != identity["sourceId"]):
        raise ChartDataError("Haneoka response identity mismatch")


def resource_identity(value, server):
    """Catalog/static metadata retain ValueError for a foreign server envelope."""
    if (not isinstance(server, str) or server not in RESOURCE_SERVERS or not isinstance(value, dict)
            or value.get("server") != server):
        raise ValueError("Catalog server mismatch")
    return release_identity(value, server)


def matches_game_region(region, expected):
    return (isinstance(region, str) and isinstance(expected, str)
            and region in GAME_REGIONS and expected in GAME_REGIONS and region == expected)

# L3
# Input: Catalog asset paths and a verified JP release identity, or a pinned image URL.
# Output: Allowlisted pinned image URLs and bounded, release-checked image bytes.
# Pos: Sources / Haneoka Catalog assets; see ../L2-2.md.
# Effects/Dependencies: Anonymous Haneoka GET only; no redirects or other-source retry.

from urllib.parse import parse_qs, urlsplit
import re

from .chart_data import ORIGIN, public_get

PREFIX = "/assets/jp/Assets/AddressableResources/"
IMAGE_PATH = re.compile(re.escape(PREFIX) + r"(?:Image/Jacket/[A-Za-z0-9_-]+|MemberCard/[0-9]+/member_(?:full|thumbnail)|SupportCard/[0-9]+/snap_(?:full|thumbnail))\.(?:png|webp)")


def asset_url(path, release):
    if not isinstance(path, str) or not IMAGE_PATH.fullmatch(path) or not re.fullmatch(r"r-[a-f0-9]{20}", release):
        raise ValueError("unverified Haneoka Catalog asset")
    return ORIGIN + path + "?release=" + release


def is_catalog_asset(url):
    try:
        parts = urlsplit(url)
        query = parse_qs(parts.query, keep_blank_values=True)
        return (parts.scheme == "https" and parts.netloc == "haneoka.org" and not parts.fragment
                and set(query) == {"release"} and len(query["release"]) == 1
                and asset_url(parts.path, query["release"][0]) == url)
    except (ValueError, TypeError):
        return False


def download_asset(url):
    if not is_catalog_asset(url):
        raise ValueError("unverified Haneoka image URL")
    raw, headers = public_get(url, 6_000_000, 12)
    release = parse_qs(urlsplit(url).query)["release"][0]
    if headers.get("x-haneoka-release-id") != release or not headers.get("x-haneoka-source-id"):
        raise ValueError("Haneoka image release mismatch")
    return raw

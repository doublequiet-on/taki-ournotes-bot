# L3
# Input: Catalog/static UI paths and a verified JP/intl release identity, or a pinned image URL.
# Output: Allowlisted pinned image URLs and bounded, release-checked image bytes.
# Pos: Sources / Haneoka Catalog assets; see ../L2-2.md.
# Effects/Dependencies: Anonymous Haneoka GET only; no redirects or other-source retry.

from urllib.parse import parse_qs, urlsplit
import re

from .chart_data import ORIGIN, public_get

PREFIX = "/assets/jp/Assets/AddressableResources/"
IMAGE_PATH = re.compile(r"/assets/(?:jp|intl)/Assets/AddressableResources/"
    r"(?:Image/Jacket/(?:small/)?[A-Za-z0-9_-]+|MemberCard/[0-9]+/member_(?:full|thumbnail)|"
    r"SupportCard/[0-9]+/snap_(?:full|thumbnail)|Story/Banner/Chapter/[A-Za-z0-9_-]+)\.(?:png|webp)")
ICON_PATH = re.compile(r"/runtime/jp/unity/Assets/AddressableResources/UI/Atlas/"
    r"FixUiSpriteAtlas\.spriteatlasv2/CardType-(?:Red|Blue|Green|Yellow|Purple)--Sprite--?[0-9]+\.png")


def attribute_url(marks, color, release):
    name = {1: "Red", 2: "Blue", 3: "Green", 4: "Yellow", 5: "Purple"}.get(color)
    path = marks.get(f"CardType-{name}.png") if isinstance(marks, dict) else None
    if not isinstance(path, str) or not path.startswith("runtime/"):
        return ""
    try:
        return asset_url("/runtime/jp/" + path.removeprefix("runtime/"), release)
    except ValueError:
        return ""


def record_attribute_url(record):
    """Captured source art only; a missing Haneoka mark stays a text/color fallback."""
    catalog = getattr(record, "catalog", {})
    if catalog.get("source") == "haneoka":
        return catalog.get("attribute_icon", "")
    if hasattr(record, "jacket_url"):
        if is_catalog_asset(record.jacket_url):
            return getattr(record.traits, "attribute_icon", "")
        color = record.traits.color if record.traits else None
    else:
        color = record.card_type
    return f"https://bdon.yatta.moe/images/CardType{color}.webp" if color in range(1, 6) else ""


def asset_url(path, release):
    if (not isinstance(path, str) or not (IMAGE_PATH.fullmatch(path) or ICON_PATH.fullmatch(path))
            or not re.fullmatch(r"r-[a-f0-9]{20}", release)):
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

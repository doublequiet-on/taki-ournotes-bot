# L3
# Input: Captured activity, gacha and character facts prepared by query modules.
# Output: Pure immutable views shared by complete text and image replies.
# Pos: Query / Contracts; no source, rendering or platform dependencies.
# Effects: None.
"""Small display contract for the three information queries."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class InfoSchedule:
    identity: str
    status: str
    start: str
    end: str
    zone: str = '日本时间 UTC+9'


@dataclass(frozen=True)
class InfoRow:
    title: str
    body: tuple[str, ...] = ()
    image_url: str = ""
    command: str = ""
    label: str = ""
    rarity: int | None = None
    image_kind: Literal["square", "wide", "portrait"] = "square"
    group: str = ""
    kind: Literal["row", "fact", "prose", "links", "card", "song", "schedule", "banner"] = "row"
    attribute_url: str = ""
    schedule: InfoSchedule | None = None
    display_title: str = ""


@dataclass(frozen=True)
class InfoView:
    title: str
    subtitle: str = ""
    hero_url: str = ""
    rows: tuple[InfoRow, ...] = ()
    footer: tuple[str, ...] = ()
    layout: Literal["list", "detail", "character", "directory"] = "list"
    presentation: Literal["", "activity", "gacha"] = ""
    visual_footer: tuple[str, ...] | None = None
    hero_kind: Literal['banner', 'event'] = 'banner'
    hero_logo_url: str = ''


@dataclass(frozen=True)
class InfoAnswer:
    text: str
    status: str = "success"
    views: tuple[InfoView, ...] = ()


def row_text(row: InfoRow) -> str:
    return "\n".join(value for value in (row.group, row.title, row.label, *row.body, row.command) if value)


def view_text(view: InfoView) -> str:
    return "\n".join(value for value in (view.title, view.subtitle,
        *(row_text(row) for row in view.rows), *view.footer) if value)


def info_text(views: tuple[InfoView, ...]) -> str:
    return "\n\n".join(view_text(view) for view in views)

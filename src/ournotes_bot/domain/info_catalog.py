# L3
# Input: Validated same-release character, recruitment and event facts.
# Output: Pure immutable record contracts for deterministic information queries.
# Pos: Domain / information catalog; see ../../L2-Data.md.
# Effects/Dependencies: Standard library only; mappings are borrowed and never mutated.

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class CardRef:
    resource_type: int  # 2: member card; 3: support card. IDs are scoped by type.
    id: int


@dataclass(frozen=True)
class CharacterInfo:
    id: int
    name: str
    names: dict[str, str]
    band_id: int | None = None
    band: str = ""
    band_names: dict[str, str] = field(default_factory=dict)
    order: int = 0
    image_url: str = ""
    face_url: str = ""
    color: str = ""
    fields: dict[str, dict[str, str]] = field(default_factory=dict)
    birthday: tuple[int, int] | None = None
    release: str = ""
    source_id: str = ""
    fetched_at: str = ""


@dataclass(frozen=True)
class GachaInfo:
    id: int
    title: str
    names: dict[str, str]
    image_url: str = ""
    start_at: float | None = None
    end_at: float | None = None
    display_condition: int | None = None  # Raw condition; not account eligibility.
    category: str = ""
    limited: bool | None = None  # Source flag, not a promise of pool availability.
    pickup_cards: tuple[CardRef, ...] = ()
    featured_cards: tuple[CardRef, ...] = ()
    available_relations: frozenset[str] = frozenset()
    description: dict[str, str] = field(default_factory=dict)
    release: str = ""
    source_id: str = ""
    fetched_at: str = ""


@dataclass(frozen=True)
class ActivityFact:
    title: str
    lines: tuple[str, ...] = ()
    image_url: str = ''
    preview: bool = False
    table: tuple[tuple[str, ...], ...] = ()
    image_kind: str = 'square'
    group: str = ''
    reference: tuple[int, int] | None = None
    bonus_ranges: tuple[tuple[str, float, float], ...] = ()


@dataclass(frozen=True)
class ActivitySection:
    name: str
    facts: tuple[ActivityFact, ...] = ()
    complete: bool = False


@dataclass(frozen=True)
class ActivityInfo:
    id: int
    title: str
    names: dict[str, str]
    image_url: str = ""
    start_at: float | None = None
    end_at: float | None = None
    display_end_at: float | None = None
    bonus_cards: tuple[CardRef, ...] = ()
    reward_cards: tuple[CardRef, ...] = ()
    pickup_cards: tuple[CardRef, ...] = ()
    song_ids: tuple[int, ...] = ()  # Associated songs, not a challenge-song claim.
    available_relations: frozenset[str] = frozenset()
    description: dict[str, str] = field(default_factory=dict)
    release: str = ""
    source_id: str = ""
    fetched_at: str = ""
    detail_image_url: str = ""
    logo_url: str = ""
    details: tuple[ActivitySection, ...] = ()
    event_type_id: int | None = None
    ranking_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class InfoCatalog:
    characters: tuple[CharacterInfo, ...] = ()
    gachas: tuple[GachaInfo, ...] = ()
    activities: tuple[ActivityInfo, ...] = ()
    available: frozenset[str] = frozenset()
    activity_calendars: dict[str, ActivityCalendar] = field(default_factory=dict)


@dataclass(frozen=True)
class ActivityCalendar:
    records: tuple[ActivityInfo, ...]
    release: str
    source_id: str
    fetched_at: str


@dataclass(frozen=True)
class AnnouncedActivity:
    title: str
    start_at: float
    url: str
    observed_at: str
    expires_at: float | None = None

# L3
# Input: Canonical card identities and validated selection dimensions.
# Output: Immutable filters and requests, including resolved member title matches.
# Pos: Domain / pure card selection contract, shared by direct and natural queries.
# Effects: None; no parsing, repository, alias, network or rendering access.
"""Canonical card selection, independent of command grammar and storage."""
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class CardFilters:
    rarity: tuple[int, ...] = ()
    type: tuple[int, ...] = ()
    character: tuple[str, ...] = ()
    band: tuple[str, ...] = ()
    live: tuple[str, ...] = ()
    gekisou: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    title_ids: tuple[int, ...] = ()
    leader: tuple[str, ...] = ()
    character_ids: tuple[int, ...] = ()
    acquisition: tuple[str, ...] = ()

    def __post_init__(self):
        for key, values in self.conditions(include_empty=True):
            expected = int if key in {"rarity", "type", "title_ids", "character_ids"} else str
            if not isinstance(values, tuple) or any(type(v) is not expected for v in values):
                raise TypeError(f"{key} requires an immutable tuple of {expected.__name__}")

    def conditions(self, *, include_empty=False):
        """Enumerate only the declared dimensions; no arbitrary mapping keys."""
        for key in ("rarity", "type", "character", "band", "live", "gekisou", "tags", "title_ids", "leader", "character_ids", "acquisition"):
            values = getattr(self, key)
            if values or include_empty:
                yield key, values


@dataclass(frozen=True)
class CardRequest:
    support: bool = False
    mode: Literal["list", "detail", "art"] = "list"
    card_id: int | None = None
    page: int = 1
    query: str = ""
    filters: CardFilters = field(default_factory=CardFilters)
    # None means no name supplied. An empty tuple is a name search with no matches.
    matched_title_ids: tuple[int, ...] | None = None
    live_conditions: tuple[str, ...] = ()
    # Inclusive percent bounds, in hundredths of a percent. None is an open end.
    live_score_ranges: tuple[tuple[int | None, int | None], ...] = ()

    def __post_init__(self):
        if not isinstance(self.filters, CardFilters):
            raise TypeError("filters must be CardFilters")
        if self.mode not in {"list", "detail", "art"}:
            raise ValueError("unknown card mode")
        if self.card_id is not None and any(self.filters.conditions()):
            raise ValueError("filtered IDs belong in title_ids, not the ID shortcut")
        if self.matched_title_ids is not None and self.card_id is not None:
            raise ValueError("only a filtered card request can carry title matches")
        if self.filters.leader and self.support:
            raise ValueError("only member cards have leader skills")
        if (self.live_conditions or self.live_score_ranges) and (self.support or self.card_id is not None):
            raise ValueError("only a filtered member request can carry LIVE score conditions")
        if self.mode == "art" and self.card_id is None:
            raise ValueError("art requires one explicit ID")

    @property
    def command(self):
        if self.mode == "art":
            return "/查留影卡面" if self.support else "/查卡面"
        return "/查留影卡" if self.support else "/查卡"

    @property
    def has_live_details(self):
        return bool(self.live_conditions or self.live_score_ranges)

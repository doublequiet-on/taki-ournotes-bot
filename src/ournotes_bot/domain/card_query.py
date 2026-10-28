# L3
# Input: Canonical card identities and validated selection dimensions.
# Output: Immutable CardFilters and CardRequest; query is a display label only.
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

    def __post_init__(self):
        for key, values in self.conditions(include_empty=True):
            expected = int if key in {"rarity", "type", "title_ids"} else str
            if not isinstance(values, tuple) or any(type(v) is not expected for v in values):
                raise TypeError(f"{key} requires an immutable tuple of {expected.__name__}")

    def conditions(self, *, include_empty=False):
        """Enumerate only the declared dimensions; no arbitrary mapping keys."""
        for key in ("rarity", "type", "character", "band", "live", "gekisou", "tags", "title_ids"):
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

    def __post_init__(self):
        if not isinstance(self.filters, CardFilters):
            raise TypeError("filters must be CardFilters")
        if self.mode not in {"list", "detail", "art"}:
            raise ValueError("unknown card mode")
        if self.card_id is not None and any(self.filters.conditions()):
            raise ValueError("filtered IDs belong in title_ids, not the ID shortcut")
        if self.mode == "art" and self.card_id is None:
            raise ValueError("art requires one explicit ID")

    @property
    def command(self):
        if self.mode == "art":
            return "/查留影卡面" if self.support else "/查卡面"
        return "/查留影卡" if self.support else "/查卡"

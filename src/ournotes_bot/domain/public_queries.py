# L3
# Input: Explicit public query arguments and captured JSON facts.
# Output: Immutable requests and source snapshots; shared source/query failures.
# Pos: Domain / public Arena and player queries.
# Effects: None.
from dataclasses import dataclass


class PublicQueryError(ValueError):
    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


@dataclass(frozen=True)
class ArenaRequest:
    region: str = 'jp'
    band: int | None = None
    mode: str = '总览'
    season: int | None = None
    first: int = 1
    last: int = 20


@dataclass(frozen=True)
class PlayerRequest:
    profile_id: str
    region: str = 'jp'
    namecard: bool = False
    page: int | None = None


@dataclass(frozen=True)
class PublicSnapshot:
    """JSON bytes keep source facts immutable across cache and rendering users."""
    body: bytes
    fetched_at: float
    resource: str
    release: str = ''

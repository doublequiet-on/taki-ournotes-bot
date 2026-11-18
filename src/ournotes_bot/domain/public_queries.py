# L3
# Input: Explicit public query arguments and captured JSON facts.
# Output: Immutable requests and source snapshots; shared source/query failures.
# Pos: Domain / public Arena queries.
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
    positions: tuple[int, ...] = ()
    numeric_only: bool = False
    population_details: bool = False


@dataclass(frozen=True)
class PublicSnapshot:
    """JSON bytes keep source facts immutable across cache and rendering users."""
    body: bytes
    fetched_at: float
    resource: str
    release: str = ''
    provider: str | None = None
    game_region: str | None = None
    observed_ms: int | None = None
    server_ms: int | None = None
    snapshot_id: str | None = None
    normalization_version: str = 'public/2'
    freshness: str = 'unknown'
    age_seconds: float | None = None
    resource_server: str | None = None
    source_id: str | None = None
    source: str = 'haneoka'
    time_kind: str = 'unknown'

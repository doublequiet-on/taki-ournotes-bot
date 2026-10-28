# L3
# Input: Normalized total-ranking values captured by an explicit source adapter.
# Output: Independent same-position total scores and public player identities.
# Pos: Domain / Total rankings; see sources/L2-2.md.
# Effects: None; no source, storage or platform imports.
from dataclasses import dataclass


@dataclass(frozen=True)
class TotalRankingSnapshot:
    event_key: str
    source: str
    scores: tuple[int | None, ...]
    fetched_ms: int
    server_ms: int
    received_ms: int = 0
    player_ids: tuple[str | None, ...] = ()
    player_names: tuple[str | None, ...] = ()
    stale: bool = False
    contract: str = 'taki:total-ranking/1'
    quality: str = 'valid'
    status: str = '综合榜当前观测'
    notes: tuple[str, ...] = ()
    observed_scores: tuple[int | None, ...] | None = None
    http_date_ms: int | None = None
    player_details: tuple[dict | None, ...] = ()

    def score(self, rank):
        return self.scores[rank-1] if 1 <= rank <= len(self.scores) else None

    def player_id(self, rank):
        return self.player_ids[rank-1] if self.score(rank) is not None and rank <= len(self.player_ids) else None

    def player_name(self, rank):
        return self.player_names[rank-1] if self.score(rank) is not None and rank <= len(self.player_names) else None

    def player_detail(self, rank):
        return self.player_details[rank-1] if self.score(rank) is not None and rank <= len(self.player_details) else None

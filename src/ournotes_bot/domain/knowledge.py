# L3: Immutable knowledge documents, scoped evidence and generation leases; no IO or platform types.
# Pos: Data contracts; see ../../L2-Data.md.
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, Sequence

GAME = 'bang-dream-our-notes'


class KnowledgeError(ValueError):
    """Stable diagnostic code; never include source prose or player questions."""


@dataclass(frozen=True)
class KnowledgeScope:
    game: str = GAME
    server: str = 'jp'
    version: str | None = None
    date: str | None = None

    def __post_init__(self):
        if self.game != GAME or self.server not in ('jp', 'intl'):
            raise KnowledgeError('scope_game_or_server')
        if self.version is not None and (not isinstance(self.version, str) or not self.version.strip()):
            raise KnowledgeError('scope_version')
        if self.date is not None:
            try:
                if date.fromisoformat(self.date).isoformat() != self.date:
                    raise ValueError
            except (TypeError, ValueError):
                raise KnowledgeError('scope_date') from None


@dataclass(frozen=True)
class Applicability:
    mode: str
    game_version: str | None
    valid_from: str | None
    valid_to: str | None
    scope_note: str

    def matches(self, scope: KnowledgeScope, today: str) -> bool:
        # Null is unknown, not a wildcard over explicitly requested versions
        # or past/future dates. A closed date range can identify historical
        # applicability without inferring a version from the question.
        dated = bool(scope.date and self.valid_from and self.valid_to)
        versioned = bool(scope.version and self.game_version == scope.version)
        if scope.version is not None and not versioned:
            return False
        if self.game_version is not None and scope.version is None and not dated:
            return False
        if scope.date is not None and scope.date != today and not dated:
            return False
        if self.mode == 'historical' and not (versioned or dated):
            return False
        if self.mode == 'historical' and versioned and scope.date is None:
            return True
        day = scope.date or today
        return not (self.valid_from and day < self.valid_from or self.valid_to and day > self.valid_to)


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    kind: str
    uri: str
    locator: str
    accessed_at: str


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    title: str
    keywords: tuple[str, ...]
    aliases: tuple[str, ...]
    game: str
    servers: tuple[str, ...]
    applicability: Applicability
    sources: tuple[SourceRef, ...]
    evidence_type: str
    review_status: str
    reviewed_by: str | None
    reviewed_at: str | None
    limitations: tuple[str, ...]
    content_sha256: str
    body: str
    file: str


@dataclass(frozen=True)
class Approval:
    id: str
    content_sha256: str
    approval_revision: int
    approved_by: str
    approved_at: str
    confirmation_ref: str

    @property
    def key(self) -> tuple[str, str, int]:
        return self.id, self.content_sha256, self.approval_revision


@dataclass(frozen=True)
class Revocation:
    id: str
    content_sha256: str | None
    revocation_revision: int
    revoked_at: str
    reason: str


@dataclass(frozen=True)
class ApprovalManifest:
    revision: int
    approved: tuple[Approval, ...]
    revoked: tuple[Revocation, ...]
    digest: str


@dataclass(frozen=True)
class EvidenceChunkV1:
    doc_id: str
    content_sha256: str
    approval_revision: int
    chunk_id: str
    chunk_sha256: str
    text: str
    source_refs: tuple[SourceRef, ...]
    applicability: Applicability
    evidence_type: str
    limitations: tuple[str, ...]
    servers: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str, int]:
        return self.doc_id, self.content_sha256, self.approval_revision


@dataclass(frozen=True)
class EvidenceLease:
    """Carry this intact through queue, prompt, generation and cached context."""
    approved_set_revision: str
    scope: KnowledgeScope
    chunks: tuple[EvidenceChunkV1, ...]


@dataclass(frozen=True)
class Retrieval:
    code: str
    lease: EvidenceLease

    @property
    def message(self) -> str:
        return {'empty': '尚无已审核资料。', 'missing': '暂时没有适用于这个问题的已审核资料。',
                'unavailable': '资料暂时不可用，请稍后再试。',
                'stale': '资料版本已变化，请重新查询。', 'ok': ''}[self.code]


class SemanticEncoder(Protocol):
    identity: str
    dimensions: int

    def encode(self, texts: Sequence[str], *, query: bool = False) -> tuple[tuple[float, ...], ...]: ...

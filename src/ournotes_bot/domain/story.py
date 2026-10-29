# L3: Immutable story sources, ordered scenes and reviewed derived material; no IO.
from __future__ import annotations

from dataclasses import dataclass


class StoryError(ValueError):
    """Finite diagnostic code, not source prose."""


def require(condition,code):
    if not condition: raise StoryError(code)


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    work: str
    medium: str
    language: str
    uri: str
    acquisition: str
    source_kind: str
    version: str
    content_sha256: str | None
    coverage: str
    published_at: str | None
    acquired_at: str | None
    use_scope: str
    coverage_note: str


@dataclass(frozen=True)
class Utterance:
    order: int
    speaker: str
    text_ja: str
    translation: str | None
    locator: str


@dataclass(frozen=True)
class Scene:
    scene_id: str
    source_id: str
    title: str
    chapter: str
    locator: str
    participants: tuple[str,...]
    story_time: str | None
    previous_scene: str | None
    next_scene: str | None
    utterances: tuple[Utterance,...]


@dataclass(frozen=True)
class DerivedClaim:
    claim_id: str
    text: str
    kind: str
    source_scene_ids: tuple[str,...]
    review_status: str
    reviewed_by: str | None
    reviewed_at: str | None
    confirmation_ref: str | None


@dataclass(frozen=True)
class PersonaRelease:
    persona_id: str
    prompt: str
    source_claim_ids: tuple[str,...]
    covers_until: str | None
    review_status: str
    reviewed_by: str | None
    reviewed_at: str | None
    confirmation_ref: str | None


@dataclass(frozen=True)
class StoryBundle:
    revision: str
    sources: tuple[SourceDocument,...]
    scenes: tuple[Scene,...]
    claims: tuple[DerivedClaim,...]
    personas: tuple[PersonaRelease,...]
    aliases: tuple[tuple[str,str],...]

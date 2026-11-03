# L3: Immutable ACK-bound query evidence/context value; no runtime or writer.
# Optional dialogue fields retain the personal API without importing chat implementation.
from __future__ import annotations
from dataclasses import dataclass
from .knowledge import KnowledgeScope


@dataclass(frozen=True)
class ReadOnlyContext:
    text: str
    origin: str = 'direct'
    evidence: tuple = ()
    history: tuple = ()
    facts: str = ''
    # Last grounded knowledge request; volatile, ACK-bound and never a fact source.
    topic: str = ''
    knowledge_scope: KnowledgeScope | None = None
    focus: str = ''
    chat_topic: str = ''

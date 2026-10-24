# L3: Deterministic bounded chunks, lexical/semantic ranking and evidence serialization.
# Pos: Query knowledge retrieval. Encoder/cache are injected; no source or network imports.
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
import unicodedata

from ..domain.knowledge import EvidenceChunkV1, KnowledgeError, SemanticEncoder

CHUNK_CHARS = 360
MAX_CHUNKS = 2_000
# Candidate retrieval threshold measured on isolated Chinese paraphrases;
# this is not a factual-confidence score. Real content is evaluated in package 5.
SEMANTIC_FLOOR = 0.50


def normalized(text):
    return unicodedata.normalize('NFKC', text).casefold()


def tokens(text):
    words = []
    for part in re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]+', normalized(text)):
        if '\u3400' <= part[0] <= '\u9fff':
            words.extend(part[i:i+2] for i in range(max(1, len(part)-1)))
        else:
            words.append(part)
    return Counter(words)


@dataclass(frozen=True)
class IndexRow:
    chunk: EvidenceChunkV1
    search_text: str
    terms: tuple[str, ...]
    vector: tuple[float, ...]


def split_document(doc, approval):
    # Exact slices keep source text (including spaces and injection strings)
    # as data. Sources, scope and limitations accompany every slice.
    start, ordinal = 0, 0
    while start < len(doc.body):
        end = min(start+CHUNK_CHARS, len(doc.body))
        if end < len(doc.body):
            boundary = max(doc.body.rfind('\n', start+CHUNK_CHARS//2, end),
                           doc.body.rfind('。', start+CHUNK_CHARS//2, end))
            if boundary >= 0:
                end = boundary+1
        text = doc.body[start:end]
        start = end
        if not text.strip():
            continue
        ordinal += 1
        chunk_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
        yield EvidenceChunkV1(doc.id, doc.content_sha256, approval.approval_revision,
            f'{doc.id}:{ordinal}:{chunk_hash[:16]}', chunk_hash, text, doc.sources,
            doc.applicability, doc.evidence_type, doc.limitations)


def checked_vector(value, dimensions):
    if not isinstance(value, (tuple, list)) or len(value) != dimensions:
        raise KnowledgeError('semantic_vector_shape')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        raise KnowledgeError('semantic_vector_value')
    norm = math.sqrt(sum(v*v for v in value))
    if not 0.99 <= norm <= 1.01:
        raise KnowledgeError('semantic_vector_norm')
    return tuple(float(v) for v in value)


def build_index(documents, encoder: SemanticEncoder, cached):
    rows, missing, updates, pending = [], [], {}, set()
    for doc, approval in sorted(documents, key=lambda pair: pair[0].id):
        for chunk in split_document(doc, approval):
            search_text = '\n'.join((doc.title, ' '.join(doc.keywords+doc.aliases), chunk.text))
            embedding_text = doc.title[:64]+'\n'+' '.join(doc.keywords+doc.aliases)[:64]+'\n'+chunk.text
            key = hashlib.sha256((encoder.identity+'\0'+embedding_text).encode('utf-8')).hexdigest()
            vector = cached.get(key)
            rows.append((chunk, search_text, doc.keywords+doc.aliases+(doc.title,), key))
            if vector is None and key not in pending:
                missing.append((key, embedding_text))
                pending.add(key)
            if len(rows) > MAX_CHUNKS:
                raise KnowledgeError('knowledge_chunk_limit')
    for offset in range(0, len(missing), 16):
        batch = missing[offset:offset+16]
        vectors = encoder.encode(tuple(t for _, t in batch))
        if len(vectors) != len(batch):
            raise KnowledgeError('semantic_batch_shape')
        updates.update((key, checked_vector(v, encoder.dimensions)) for (key, _), v in zip(batch, vectors))
    available = {**cached, **updates}
    index = tuple(IndexRow(chunk, text, terms, checked_vector(available[key], encoder.dimensions))
                  for chunk, text, terms, key in rows)
    # Only vectors used by this generation survive. No persistent questions.
    return index, {key: available[key] for _, _, _, key in rows}, len(missing)


def rank(query, rows, query_vector):
    if not rows:
        return ()
    query_terms = tokens(query)
    bags = [tokens(row.search_text) for row in rows]
    average = sum(sum(bag.values()) for bag in bags)/len(bags) or 1
    frequency = Counter(t for bag in bags for t in bag)
    lexical, semantic = {}, {}
    for i, (row, bag) in enumerate(zip(rows, bags)):
        length = sum(bag.values())
        score = sum(math.log(1+(len(rows)-frequency[t]+0.5)/(frequency[t]+0.5)) *
                    (bag[t]*2.2)/(bag[t]+1.2*(0.25+0.75*length/average))
                    for t in query_terms if bag[t])
        score += 3*sum(1 for term in row.terms if normalized(term) in normalized(query))
        if score > 0:
            lexical[i] = score
        similarity = sum(a*b for a, b in zip(row.vector, query_vector))
        if similarity >= SEMANTIC_FLOOR:
            semantic[i] = similarity
    fused = {}
    for scores in (lexical, semantic):
        ordered = sorted(scores, key=lambda i: (-scores[i], rows[i].chunk.chunk_id))
        for position, i in enumerate(ordered, 1):
            fused[i] = fused.get(i, 0)+1/(60+position)
    return tuple(rows[i].chunk for i in sorted(fused, key=lambda i: (-fused[i], rows[i].chunk.chunk_id)))


def evidence_json(chunks) -> str:
    """Untrusted tool evidence, not a system message; size includes metadata."""
    return json.dumps([asdict(c) for c in chunks], ensure_ascii=False, separators=(',', ':'))


def bounded(chunks, limit=6, max_chars=6000):
    if type(limit) is not int or not 1 <= limit <= 6 or type(max_chars) is not int or max_chars < 0:
        raise KnowledgeError('retrieval_budget')
    max_chars = min(max_chars, 6000)
    result = []
    for chunk in chunks:
        if len(evidence_json(result+[chunk])) <= max_chars:
            result.append(chunk)
        if len(result) == limit:
            break
    return tuple(result)

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
    for part in re.findall(r'[a-z_]+|[0-9]+|[\u3400-\u9fff]+', normalized(text)):
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


def labelled_tables(body):
    """Repeat source column labels for simple Markdown tables; no new facts.

    Ambiguous/escaped pipes are kept verbatim. Source hashes and approval are
    still bound to the original document; lease validation repeats this transform.
    """
    lines=body.splitlines(keepends=True)
    output=[];i=0
    def cells(line): return [cell.strip() for cell in line.strip().strip('|').split('|')]
    while i<len(lines):
        if (i+1<len(lines) and lines[i].lstrip().startswith('|') and '\\|' not in lines[i]
                and re.fullmatch(r'[\s|:\-]+',lines[i+1]) and '-' in lines[i+1]):
            headers=cells(lines[i]);end=i+2;rows=[]
            while end<len(lines) and lines[end].lstrip().startswith('|'):
                rows.append(cells(lines[end]));end+=1
            if (all(headers) and rows and all(len(row)==len(headers) for row in rows)
                    and not any('\\|' in line for line in lines[i:end])):
                output.extend('；'.join(f'{key}：{value}' for key,value in zip(headers,row))+'\n' for row in rows)
                i=end;continue
        output.append(lines[i]);i+=1
    return ''.join(output)


def split_document(doc, approval):
    # Section identity permits bounded completion without joining unrelated
    # chapters. Heading context counts toward the encoder's existing size cap.
    sections = re.split(r'(?=^#{1,4} )', labelled_tables(doc.body), flags=re.M)
    parents = {}
    for section, body in enumerate(sections):
        heading = re.match(r'^(#{1,4}) (.+)\n?', body)
        if heading:
            level = len(heading[1])
            parents = {k: v for k, v in parents.items() if k < level}
            parents[level] = heading[0].strip()
            body = body[heading.end():]
        prefix = '\n'.join(parents.values())[:120]
        prefix = prefix+'\n' if prefix else ''
        size = CHUNK_CHARS-len(prefix)
        start = 0
        part = 0
        while start < len(body):
            end = min(start+size, len(body))
            if end < len(body):
                boundary = max(body.rfind('\n\n', start+size//2, end),
                               body.rfind('\n', start+size//2, end),
                               body.rfind('。', start+size//2, end))
                if boundary >= 0:
                    end = boundary+1
            text = prefix+body[start:end]
            start = end
            if not text[len(prefix):].strip():
                continue
            part += 1
            chunk_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
            yield EvidenceChunkV1(doc.id, doc.content_sha256, approval.approval_revision,
                f'{doc.id}:s{section}:p{part}:{chunk_hash[:16]}', chunk_hash, text, doc.sources,
                doc.applicability, doc.evidence_type, doc.limitations, doc.servers)


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
    # Explicit Latin names (LUCK, COMBO, SSR) must be present in the
    # passage, not merely repeated in every passage's document keywords.
    entities = set(re.findall(r'[a-z_]+', normalized(query)))-{'lv','jp','intl','id'}
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
    return tuple(rows[i].chunk for i in sorted(fused, key=lambda i: (
        bool(re.search(r'^## 依据与边界', rows[i].chunk.text, re.M)),
        -len(entities.intersection(tokens(rows[i].chunk.text))), -fused[i], rows[i].chunk.chunk_id)))


def complete_sections(ranked, rows):
    """Complete at most three adjacent passages from the first matching section.

    Remaining ranked sections still compete for the same six-chunk/6000-char
    budget. Companions come only from the already scope-filtered index.
    """
    seen = set()
    for chunk in ranked:
        if chunk.chunk_id in seen:
            continue
        section = chunk.chunk_id.rsplit(':', 2)[0]
        siblings = [row.chunk for row in rows if row.chunk.key == chunk.key
                    and row.chunk.chunk_id.rsplit(':', 2)[0] == section]
        position = siblings.index(chunk)
        companions = siblings[max(0, position-1):max(0, position-1)+3]
        # Relevance first, then source order; no speculative distant completion.
        for item in (chunk, *companions):
            if item.chunk_id not in seen:
                seen.add(item.chunk_id)
                yield item


def evidence_json(chunks) -> str:
    """Untrusted tool evidence, not a system message; size includes metadata."""
    documents, passages = {}, []
    for chunk in chunks:
        value = asdict(chunk)
        passage = {key: value.pop(key) for key in ('chunk_id', 'chunk_sha256', 'text')}
        key = '\u0000'.join(map(str, chunk.key))
        if key not in documents:
            documents[key] = value
        passage['document'] = list(documents).index(key)
        passages.append(passage)
    return json.dumps({'documents': list(documents.values()), 'passages': passages},
                      ensure_ascii=False, separators=(',', ':'))


def bounded(chunks, limit=6, max_chars=6000):
    if type(limit) is not int or not 1 <= limit <= 6 or type(max_chars) is not int or max_chars < 0:
        raise KnowledgeError('retrieval_budget')
    max_chars = min(max_chars, 6000)
    result, counts, deferred = [], Counter(), []
    candidates = []
    for chunk in chunks:
        # Repeated sections of one article must not hide other relevant sources.
        (candidates if counts[chunk.key] < 2 else deferred).append(chunk)
        counts[chunk.key] += 1
    for chunk in (*candidates, *deferred):
        if len(evidence_json(result+[chunk])) <= max_chars:
            result.append(chunk)
        if len(result) == limit:
            break
    return tuple(result)

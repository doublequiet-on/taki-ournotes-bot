# L3: UTF-8 JSON-frontmatter and approval validation, bounded local directory scan.
# Pos: Knowledge source boundary; see ../../L2-Data.md. No source URLs are fetched.
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re

from ..domain.knowledge import (GAME, KnowledgeError, Applicability, SourceRef,
    KnowledgeDocument, Approval, Revocation, ApprovalManifest)

MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 32_000_000
MAX_DOCUMENTS = 1000
ID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')


def canonical(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise KnowledgeError('invalid_json') from None


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def require(condition, code):
    if not condition:
        raise KnowledgeError(code)


def object_value(value, fields, code):
    require(isinstance(value, dict) and set(fields) <= value.keys(), code)
    return value


def string(value, code, *, maximum=2000):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and not any(ord(c) < 32 for c in value), code)
    return value


def strings(value, code):
    require(isinstance(value, list) and len(value) <= 128, code)
    result = tuple(string(v, code) for v in value)
    require(len(result) == len(set(result)), code)
    return result


def identifier(value):
    require(isinstance(value, str) and len(value) <= 100 and ID.fullmatch(value), 'document_id')
    return value


def sha256(value):
    require(isinstance(value, str) and HASH.fullmatch(value), 'content_sha256')
    return value


def revision(value):
    require(type(value) is int and 0 <= value <= 2**53-1, 'approval_revision')
    return value


def timestamp(value):
    string(value, 'timestamp')
    try:
        if len(value) == 10:
            require(date.fromisoformat(value).isoformat() == value, 'timestamp')
        else:
            require(datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None, 'timestamp')
    except ValueError:
        raise KnowledgeError('timestamp') from None
    return value


def optional_date(value):
    if value is not None:
        require(isinstance(value, str) and len(value) == 10, 'applicability_date')
        timestamp(value)
    return value


def decode_json(text):
    def number(value):
        result = float(value)
        require(math.isfinite(result), 'nonfinite_json')
        return result
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_float=number,
                          parse_constant=lambda value: require(False, 'nonfinite_json'))
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, KnowledgeError):
            raise
        raise KnowledgeError('invalid_json') from None


def parse_document(text: str, file: str = '') -> KnowledgeDocument:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    require(text.startswith('---\n'), 'frontmatter_open')
    boundary = text.find('\n---\n', 4)
    require(boundary >= 0, 'frontmatter_close')
    meta = object_value(decode_json(text[4:boundary]), (
        'schema', 'id', 'title', 'keywords', 'aliases', 'game', 'servers', 'applicability',
        'sources', 'evidence_type', 'review_status', 'reviewed_by', 'reviewed_at', 'limitations'), 'metadata_fields')
    require(meta['schema'] == 'taki-knowledge/1' and meta['game'] == GAME, 'knowledge_schema_or_game')
    doc_id = identifier(meta['id'])
    title = string(meta['title'], 'title', maximum=200)
    keywords, aliases = strings(meta['keywords'], 'keywords'), strings(meta['aliases'], 'aliases')
    servers = strings(meta['servers'], 'servers')
    require(bool(servers) and set(servers) <= {'jp', 'intl'}, 'servers')
    app = object_value(meta['applicability'], ('mode', 'game_version', 'valid_from', 'valid_to', 'scope_note'), 'applicability_fields')
    require(app['mode'] in ('current', 'historical'), 'applicability_mode')
    if app['game_version'] is not None:
        string(app['game_version'], 'game_version', maximum=100)
    start, end = optional_date(app['valid_from']), optional_date(app['valid_to'])
    require(not (start and end and start > end), 'applicability_interval')
    applicability = Applicability(app['mode'], app['game_version'], start, end,
                                 string(app['scope_note'], 'scope_note'))
    sources = []
    require(isinstance(meta['sources'], list) and len(meta['sources']) <= 64, 'sources')
    for value in meta['sources']:
        value = object_value(value, ('source_id', 'kind', 'uri', 'locator', 'accessed_at'), 'source_fields')
        sources.append(SourceRef(identifier(value['source_id']), string(value['kind'], 'source_kind'),
                                 string(value['uri'], 'source_uri'), string(value['locator'], 'source_locator'),
                                 timestamp(value['accessed_at'])))
    require(len({s.source_id for s in sources}) == len(sources), 'duplicate_source_id')
    require(meta['evidence_type'] in ('official', 'source_data', 'verified_observation', 'reviewed_analysis'), 'evidence_type')
    require(meta['review_status'] in ('draft', 'approved'), 'review_status')
    if meta['review_status'] == 'approved':
        require(bool(sources), 'approved_sources_missing')
        string(meta['reviewed_by'], 'reviewed_by')
        timestamp(meta['reviewed_at'])
    else:
        require(meta['reviewed_by'] is None and meta['reviewed_at'] is None, 'draft_review_fields')
    limitations = strings(meta['limitations'], 'limitations')
    body = text[boundary+5:].rstrip('\n') + '\n'
    require(bool(body.strip()) and '\x00' not in body, 'document_body')
    refs = set(re.findall(r'\[source:([^\]\n]+)\]', body))
    require(refs <= {s.source_id for s in sources}, 'unknown_source_reference')
    hashed = {k: v for k, v in meta.items() if k not in ('review_status', 'reviewed_by', 'reviewed_at')}
    return KnowledgeDocument(doc_id, title, keywords, aliases, GAME, servers, applicability, tuple(sources),
        meta['evidence_type'], meta['review_status'], meta['reviewed_by'], meta['reviewed_at'], limitations,
        digest(canonical(hashed)+'\n'+body), body, file)


def parse_approvals(value) -> ApprovalManifest:
    object_value(value, ('schema', 'revision', 'approved', 'revoked'), 'approval_fields')
    require(value['schema'] == 'taki-knowledge-approvals/1', 'approval_schema')
    version = revision(value['revision'])
    approved, revoked = [], []
    require(isinstance(value['approved'], list) and isinstance(value['revoked'], list), 'approval_lists')
    for item in value['approved']:
        object_value(item, ('id', 'content_sha256', 'approval_revision', 'approved_by', 'approved_at', 'confirmation_ref'), 'approval_entry')
        number = revision(item['approval_revision'])
        require(0 < number <= version, 'approval_revision')
        approved.append(Approval(identifier(item['id']), sha256(item['content_sha256']), number,
            string(item['approved_by'], 'approved_by'), timestamp(item['approved_at']),
            string(item['confirmation_ref'], 'confirmation_ref')))
    require(len({a.id for a in approved}) == len(approved), 'duplicate_approval_id')
    for item in value['revoked']:
        object_value(item, ('id', 'content_sha256', 'revocation_revision', 'revoked_at', 'reason'), 'revocation_entry')
        number = revision(item['revocation_revision'])
        require(0 < number <= version, 'revocation_revision')
        revoked.append(Revocation(identifier(item['id']), sha256(item['content_sha256']) if item['content_sha256'] is not None else None,
            number, timestamp(item['revoked_at']), string(item['reason'], 'revocation_reason')))
    require(len(set(revoked)) == len(revoked), 'duplicate_revocation')
    return ApprovalManifest(version, tuple(approved), tuple(revoked), digest(canonical(value)))


def safe_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    require(bool(relative) and bool(parts.parts) and '\\' not in relative and ':' not in relative and not parts.is_absolute()
            and '..' not in parts.parts and '.' not in parts.parts, 'knowledge_path')
    path = root.joinpath(*parts.parts)
    require(path.resolve().is_relative_to(root.resolve()), 'knowledge_path_escape')
    return path


def read_text(path: Path) -> str:
    try:
        with path.open('rb') as stream:
            data = stream.read(MAX_FILE_BYTES+1)
        require(len(data) <= MAX_FILE_BYTES, 'knowledge_file_size')
        return data.decode('utf-8')
    except UnicodeError:
        raise KnowledgeError('knowledge_encoding') from None


@dataclass(frozen=True)
class KnowledgeScan:
    manifest: ApprovalManifest
    manifest_text: str
    documents: tuple[KnowledgeDocument, ...]
    files: tuple[tuple[str, str], ...]
    issues: tuple[tuple[str, str], ...]
    fingerprint: str


def scan_directory(root: Path) -> KnowledgeScan:
    root = root.resolve()
    files, documents, issues, seen = [], [], [], set()
    manifest_path = safe_path(root, 'approvals.json')
    manifest_text = read_text(manifest_path) if manifest_path.exists() else canonical({
        'schema': 'taki-knowledge-approvals/1', 'revision': 0, 'approved': [], 'revoked': []})
    manifest = parse_approvals(decode_json(manifest_text))
    total = 0
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=lambda exc: (_ for _ in ()).throw(exc)):
        for name in dirs:
            safe_path(root, (Path(directory)/name).relative_to(root).as_posix())
        for name in sorted(names):
            if Path(name).suffix.lower() != '.md':
                continue
            relative = (Path(directory)/name).relative_to(root).as_posix()
            path = safe_path(root, relative)
            try:
                text = read_text(path)
                total += len(text.encode('utf-8'))
                files.append((relative, text))
                require(len(files) <= MAX_DOCUMENTS and total <= MAX_TOTAL_BYTES, 'knowledge_library_size')
                doc = parse_document(text, relative)
                require(doc.id not in seen, 'duplicate_document_id')
                seen.add(doc.id)
                documents.append(doc)
            except (OSError, KnowledgeError) as exc:
                if isinstance(exc, KnowledgeError) and str(exc) in ('duplicate_document_id', 'knowledge_library_size'):
                    raise
                issues.append((relative, str(exc) if isinstance(exc, KnowledgeError) else 'knowledge_file_read'))
    # Re-read the manifest so a concurrent approval replacement never labels a
    # half-read file set with a newer revision. Files are revalidated on use.
    current = read_text(manifest_path) if manifest_path.exists() else manifest_text
    require(current == manifest_text, 'approval_changed_during_scan')
    fingerprint = digest(canonical([manifest.digest, [(p, digest(t)) for p, t in sorted(files)], sorted(issues)]))
    return KnowledgeScan(manifest, manifest_text, tuple(documents), tuple(sorted(files)), tuple(sorted(issues)), fingerprint)


def eligible(scan: KnowledgeScan) -> dict[str, tuple[KnowledgeDocument, Approval]]:
    documents = {doc.id: doc for doc in scan.documents}
    result = {}
    for approval in scan.manifest.approved:
        doc = documents.get(approval.id)
        if (doc is None or doc.review_status != 'approved' or doc.content_sha256 != approval.content_sha256
                or doc.reviewed_by != approval.approved_by or doc.reviewed_at != approval.approved_at):
            continue
        if any(r.id == doc.id and (r.content_sha256 is None or r.content_sha256 == approval.content_sha256)
               and r.revocation_revision >= approval.approval_revision for r in scan.manifest.revoked):
            continue
        result[doc.id] = doc, approval
    return result


def validate_delivery(scan: KnowledgeScan, delivery: dict):
    object_value(delivery, ('schema', 'delivery_id', 'prepared_by', 'items', 'missing_materials'), 'delivery_fields')
    require(delivery['schema'] == 'taki-knowledge-delivery/1', 'delivery_schema')
    string(delivery['delivery_id'], 'delivery_id')
    string(delivery['prepared_by'], 'delivery_prepared_by')
    require(not scan.issues and isinstance(delivery['items'], list), 'delivery_invalid_documents')
    strings(delivery['missing_materials'], 'delivery_missing_materials')
    docs, approvals = {d.id: d for d in scan.documents}, {a.id: a for a in scan.manifest.approved}
    seen = set()
    for item in delivery['items']:
        object_value(item, ('id', 'file', 'content_sha256', 'game', 'servers', 'applicability',
            'source_locators', 'limitations', 'confirmation_ref', 'status'), 'delivery_item')
        require(item['id'] in docs and item['id'] not in seen, 'delivery_document_id')
        seen.add(item['id'])
        doc = docs[item['id']]
        require(item['file'] == doc.file and item['content_sha256'] == doc.content_sha256
                and item['game'] == doc.game and item['servers'] == list(doc.servers)
                and item['applicability'] == {k: getattr(doc.applicability, k) for k in
                    ('mode', 'game_version', 'valid_from', 'valid_to', 'scope_note')}
                and item['source_locators'] == [s.locator for s in doc.sources]
                and item['limitations'] == list(doc.limitations), 'delivery_metadata_mismatch')
        if doc.review_status == 'approved':
            approval = approvals.get(doc.id)
            require(item['status'] == 'confirmed' and approval is not None
                    and item['confirmation_ref'] == approval.confirmation_ref
                    and doc.content_sha256 == approval.content_sha256
                    and doc.reviewed_by == approval.approved_by and doc.reviewed_at == approval.approved_at,
                    'delivery_confirmation_missing')
        else:
            require(item['status'] == 'draft' and item['confirmation_ref'] is None, 'delivery_draft_status')
    require(seen == docs.keys() and approvals.keys() <= docs.keys(), 'delivery_inventory_mismatch')

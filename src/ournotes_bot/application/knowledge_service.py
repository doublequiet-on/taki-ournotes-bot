# L3: Knowledge authority, durable revocation floors, incremental candidate indexes and guarded evidence.
# Pos: Application knowledge lifecycle; see ../../L2-Application.md.
# Effects: Local Markdown/approval reads, single-owner SQLite and explicit confirmed imports; no network/QQ/model generation.
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from ..domain.knowledge import EvidenceLease, KnowledgeError, KnowledgeScope, Retrieval, SemanticEncoder
from ..query.knowledge_retrieval import (bounded, build_index, checked_vector, evidence_json, rank, split_document)
from ..sources.knowledge_files import (canonical, decode_json, digest, eligible, parse_approvals,
    require, safe_path, scan_directory)

logger = logging.getLogger(__name__)


class KnowledgeService:
    def __init__(self, root: Path, state: Path, encoder: SemanticEncoder, *, clock=time.time):
        self.root, self.state_path, self.encoder, self.clock = root.resolve(), state.resolve(), encoder, clock
        self.lock = threading.RLock()
        self.build_lock = threading.Lock()
        self.owner = None
        self.state_owner = None
        self.db = None
        self.authority = {'schema': 1, 'manifest': None, 'history': {}, 'blocked': {}}
        self.allowed = {}
        self.index = ()
        self.index_revision = None
        self.approved_set_revision = ''
        self.available = False
        self.observed_at = None
        self.issues = ()
        self.error = None
        self.index_error = None
        self.last_build = None
        self.signature = None
        self.pending_authority = None
        try:
            require(not self.state_path.is_relative_to(self.root), 'knowledge_state_inside_content')
            self.root.mkdir(parents=True, exist_ok=True)
            self.owner = claim_file(self.root / '.knowledge.lock')
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_owner = claim_file(self.state_path.with_suffix('.lock'))
            self.db = sqlite3.connect(self.state_path, check_same_thread=False)
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('CREATE TABLE IF NOT EXISTS authority (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS index_state (id INTEGER PRIMARY KEY CHECK(id=1), revision TEXT NOT NULL)')
            row = self.db.execute('SELECT value FROM authority WHERE id=1').fetchone()
            if row:
                value = decode_json(row[0])
                require(isinstance(value, dict) and value.get('schema') == 1
                        and isinstance(value.get('history'), dict) and isinstance(value.get('blocked'), dict)
                        and 'manifest' in value, 'knowledge_authority_schema')
                if value['manifest'] is not None:
                    parse_approvals(value['manifest'])
                for doc_id, entry in value['history'].items():
                    parsed = parse_approvals({'schema': 'taki-knowledge-approvals/1',
                        'revision': entry['approval_revision'], 'approved': [entry], 'revoked': []})
                    require(parsed.approved[0].id == doc_id, 'knowledge_authority_id')
                for doc_id, floor in value['blocked'].items():
                    require(doc_id in value['history'] and type(floor) is int and floor >= 0, 'knowledge_authority_floor')
                self.authority = value
            self.db.commit()
        except (OSError, sqlite3.Error, KnowledgeError, KeyError, TypeError):
            self.close()
            self.error = 'knowledge_state_unavailable'
            logger.error('Knowledge unavailable code=%s', self.error)

    def close(self):
        with self.lock:
            self.available = False
            if self.db is not None:
                self.db.close()
                self.db = None
            if self.owner is not None:
                self.owner.close()
                self.owner = None
            if self.state_owner is not None:
                self.state_owner.close()
                self.state_owner = None

    def persist(self, authority):
        require(self.db is not None, 'knowledge_state_unavailable')
        # State is committed before publishing the new allow set in memory.
        try:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO authority VALUES (1, ?)', (canonical(authority),))
        except sqlite3.Error:
            self.pending_authority = authority
            self.available = False
            raise
        self.authority = authority
        self.pending_authority = None

    def transition(self, scan):
        current = scan.manifest
        previous = parse_approvals(self.authority['manifest']) if self.authority['manifest'] is not None else None
        if previous is not None:
            require(current.revision >= previous.revision, 'approval_revision_rollback')
            require(current.revision != previous.revision or current.digest == previous.digest, 'approval_same_revision_changed')
            require(set(previous.revoked) <= set(current.revoked), 'revocation_tombstone_removed')
        history = dict(self.authority['history'])
        for approval in current.approved:
            old = history.get(approval.id)
            if previous is not None and old != asdict(approval):
                require(approval.approval_revision > previous.revision, 'approval_requires_new_revision')
            if old is not None and old != asdict(approval):
                require(approval.approval_revision > old['approval_revision']
                        and approval.confirmation_ref != old['confirmation_ref'], 'approval_requires_new_confirmation')
            history[approval.id] = asdict(approval)
        return {'schema': 1, 'manifest': decode_json(scan.manifest_text),
                'history': history, 'blocked': dict(self.authority['blocked'])}

    def publish_allowed(self, allowed):
        self.allowed = allowed
        self.approved_set_revision = str(self.authority['manifest']['revision'])+':'+digest(canonical(
            [asdict(a) for _, a in sorted(allowed.values(), key=lambda pair: pair[0].id)]))
        self.available = True
        self.error = None
        self.observed_at = self.clock()

    def observe(self):
        with self.lock:
            if self.db is None:
                return self.status()
            try:
                if self.pending_authority is not None:
                    self.persist(self.pending_authority)
                scan = scan_directory(self.root)
                authority = self.transition(scan)
                selected = eligible(scan)
                # All previously known approvals that disappeared or stopped
                # matching become durable floors, including file edits/deletes.
                for doc_id, entry in authority['history'].items():
                    pair = selected.get(doc_id)
                    if pair is None:
                        authority['blocked'][doc_id] = max(authority['blocked'].get(doc_id, 0), entry['approval_revision'])
                selected = {k: pair for k, pair in selected.items()
                            if pair[1].approval_revision > authority['blocked'].get(k, 0)}
                if authority != self.authority:
                    self.persist(authority)
                self.publish_allowed(selected)
                self.issues = scan.issues
            except (OSError, sqlite3.Error, KnowledgeError) as exc:
                self.available = False
                self.error = str(exc) if isinstance(exc, KnowledgeError) else 'knowledge_observation_failed'
                logger.error('Knowledge unavailable code=%s', self.error)
            return self.status()

    def watch(self):
        """One-second stat polling; refresh also scans bytes every 30 seconds."""
        try:
            signature = []
            for directory, dirs, names in os.walk(self.root, followlinks=False):
                for name in dirs:
                    safe_path(self.root, (Path(directory)/name).relative_to(self.root).as_posix())
                for name in names:
                    if name == 'approvals.json' or name.lower().endswith('.md'):
                        relative = (Path(directory)/name).relative_to(self.root).as_posix()
                        path = safe_path(self.root, relative)
                        stat = path.stat()
                        signature.append((relative, stat.st_mtime_ns, stat.st_size))
                        require(len(signature) <= 1001, 'knowledge_library_size')
            signature = tuple(sorted(signature))
        except (OSError, KnowledgeError):
            return self.observe()
        if signature != self.signature:
            self.signature = signature
            return self.observe()
        return self.status()

    def refresh(self):
        self.observe()
        self.rebuild()
        return self.status()

    def rebuild(self):
        with self.build_lock:
            with self.lock:
                if not self.available or self.index_revision == self.approved_set_revision:
                    return self.status()
                version, selected = self.approved_set_revision, tuple(self.allowed.values())
                try:
                    cached = {k: decode_json(v) for k, v in self.db.execute('SELECT key, value FROM vectors')}
                except (sqlite3.Error, KnowledgeError):
                    self.index_error = 'knowledge_vector_cache_invalid'
                    return self.status()
            try:
                index, vectors, count = build_index(selected, self.encoder, cached)
                with self.lock:
                    self.observe()
                    require(self.available and self.approved_set_revision == version, 'knowledge_candidate_superseded')
                    with self.db:
                        self.db.execute('DELETE FROM vectors')
                        self.db.executemany('INSERT INTO vectors VALUES (?, ?)', [(k, canonical(v)) for k, v in vectors.items()])
                        self.db.execute('INSERT OR REPLACE INTO index_state VALUES (1, ?)', (version,))
                    self.index, self.index_revision = index, version
                    self.index_error = None
                    self.last_build = {'at': self.clock(), 'encoded_chunks': count, 'chunks': len(index), 'revision': version}
            except Exception as exc:
                # Encoder/native-runtime failures must not interrupt ordinary
                # queries. Old index remains subject to the current allow set.
                self.index_error = str(exc) if isinstance(exc, KnowledgeError) else 'knowledge_index_build_failed'
                logger.error('Knowledge index unavailable code=%s', self.index_error)
            return self.status()

    def status(self):
        with self.lock:
            return {'code': ('unavailable' if not self.available else 'empty' if not self.allowed else
                             'ready' if self.index_revision == self.approved_set_revision else 'index_pending'),
                    'approved_set_revision': self.approved_set_revision, 'index_revision': self.index_revision,
                    'observed_at': self.observed_at, 'approved_documents': len(self.allowed) if self.available else 0,
                    'indexed_chunks': len(self.index), 'error': self.error, 'index_error': self.index_error,
                    'issues': self.issues, 'last_build': self.last_build, 'encoder': self.encoder.identity}

    def retrieve(self, query: str, scope: KnowledgeScope, approved_set_revision: str, limit=6, *, max_chars=6000) -> Retrieval:
        require(isinstance(query, str) and 0 < len(query.strip()) <= 500, 'knowledge_query_size')
        bounded((), limit, max_chars)
        self.observe()
        with self.lock:
            version = self.approved_set_revision
            def response(code, chunks=()):
                return Retrieval(code, EvidenceLease(version, scope, chunks))
            if not self.available:
                return response('unavailable')
            if approved_set_revision != version:
                return response('stale')
            if not self.allowed:
                return response('empty')
            today = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
            allowed = {a.key for doc, a in self.allowed.values() if doc.game == scope.game and scope.server in doc.servers
                       and doc.applicability.matches(scope, today)}
            rows = tuple(row for row in self.index if row.chunk.key in allowed)
            if not rows:
                return response('unavailable' if self.index_revision != version and allowed else 'missing')
        try:
            values = self.encoder.encode((query,), query=True)
            require(len(values) == 1, 'semantic_query_shape')
            vector = checked_vector(values[0], self.encoder.dimensions)
            ranked = rank(query, rows, vector)
            chunks = bounded(ranked, limit, max_chars)
            if ranked and not chunks:
                return response('unavailable')
        except Exception:
            logger.error('Knowledge retrieval unavailable code=semantic_query_failed')
            return response('unavailable')
        lease = EvidenceLease(version, scope, chunks)
        if not self.valid(lease):
            return response('unavailable')
        return Retrieval('ok' if chunks else 'missing', lease)

    def valid(self, lease: EvidenceLease) -> bool:
        """Required before generation, before send, and before context publication.

        A revoked member invalidates the WHOLE answer. No pruning or model retry.
        The caller owns usage settlement even if this returns False.
        """
        if not isinstance(lease, EvidenceLease):
            return False
        self.observe()
        with self.lock:
            if not self.available:
                return False
            today = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
            for chunk in lease.chunks:
                pair = self.allowed.get(chunk.doc_id)
                if pair is None:
                    return False
                doc, approval = pair
                if (chunk.key != approval.key or doc.game != lease.scope.game or lease.scope.server not in doc.servers
                        or not doc.applicability.matches(lease.scope, today)
                        or chunk not in tuple(split_document(doc, approval))):
                    return False
            return True

    def prepare_evidence(self, lease):
        if not lease.chunks or not self.valid(lease):
            raise KnowledgeError('knowledge_evidence_unavailable')
        text = evidence_json(lease.chunks)
        require(len(lease.chunks) <= 6 and len(text) <= 6000, 'retrieval_budget')
        return text

    def import_confirmed(self, staging: Path, delivery: dict):
        """Full snapshot import; only call with user-supplied confirmed material.

        Owning the content directory excludes another live process. Live control
        integrations must call THIS instance, never a second CLI/SQLite owner.
        No approval or prose is generated by this operation.
        """
        from ..sources.knowledge_files import validate_delivery
        staging = staging.resolve()
        require(not staging.is_relative_to(self.root) and not self.root.is_relative_to(staging), 'knowledge_staging_overlap')
        scan = scan_directory(staging)
        validate_delivery(scan, delivery)
        with self.build_lock:
            self.observe()
            with self.lock:
                require(self.available, 'knowledge_state_unavailable')
                self.transition(scan)
                baseline = scan_directory(self.root).fingerprint
                candidate = eligible(scan)
                require(all(a.approval_revision > self.authority['blocked'].get(d.id, 0)
                            for d, a in candidate.values()), 'approval_requires_new_confirmation')
                old = dict(self.allowed)
                authority = {**self.authority, 'blocked': dict(self.authority['blocked'])}
                for doc_id, (_, approval) in old.items():
                    replacement = candidate.get(doc_id)
                    if replacement is None or replacement[1].key != approval.key:
                        authority['blocked'][doc_id] = max(authority['blocked'].get(doc_id, 0), approval.approval_revision)
                try:
                    self.persist(authority)
                except sqlite3.Error:
                    self.available = False
                    raise KnowledgeError('knowledge_state_unavailable') from None
                self.publish_allowed({k: pair for k, pair in old.items()
                                      if pair[1].approval_revision > authority['blocked'].get(k, 0)})
                # Revocations/deletions above survive a failed candidate build.
                cached = {k: decode_json(v) for k, v in self.db.execute('SELECT key, value FROM vectors')}
            try:
                index, vectors, count = build_index(tuple(candidate.values()), self.encoder, cached)
            except Exception as exc:
                self.index_error = str(exc) if isinstance(exc, KnowledgeError) else 'knowledge_index_build_failed'
                raise KnowledgeError(self.index_error) from None
            require(scan_directory(staging).fingerprint == scan.fingerprint, 'knowledge_staging_changed')
            with self.lock:
                current = scan_directory(self.root)
                # Refuse to overwrite external edits made during candidate work.
                require(current.fingerprint == baseline, 'knowledge_live_content_changed')
                try:
                    names = {name for name, _ in scan.files}
                    for name, text in scan.files:
                        atomic_text(safe_path(self.root, name), text)
                    for name, _ in current.files:
                        if name not in names:
                            safe_path(self.root, name).unlink()
                    atomic_text(safe_path(self.root, 'approvals.json'), scan.manifest_text)
                    self.observe()
                    require(self.available and {a.key for _, a in self.allowed.values()} == {a.key for _, a in candidate.values()},
                            'knowledge_import_not_observed')
                    with self.db:
                        self.db.execute('DELETE FROM vectors')
                        self.db.executemany('INSERT INTO vectors VALUES (?, ?)', [(k, canonical(v)) for k, v in vectors.items()])
                        self.db.execute('INSERT OR REPLACE INTO index_state VALUES (1, ?)', (self.approved_set_revision,))
                    self.index, self.index_revision = index, self.approved_set_revision
                    self.last_build = {'at': self.clock(), 'encoded_chunks': count, 'chunks': len(index), 'revision': self.index_revision}
                    self.index_error = None
                except (OSError, sqlite3.Error, KnowledgeError):
                    self.observe()
                    raise
                return self.status()


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text.replace('\r\n', '\n').replace('\r', '\n'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def claim_file(path):
    handle = path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BaseException:
        handle.close()
        raise

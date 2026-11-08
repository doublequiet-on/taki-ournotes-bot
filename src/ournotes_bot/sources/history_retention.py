# L3
# Input: Explicit offline copies, caller-selected cutoff, external current authority.
# Output: Dry-run inventory/projection plan and C6 allow/reject/manual decisions.
# Pos: Sources / history maintenance; see docs/HISTORY_DATA_CONTRACT.md.
# Effects: Read-only local inspection. No production policy, deletion or activation.
"""Retention planning is not approval; restored journals are not authority."""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from . import history_archive as archive
from . import history_compression as compressed

CONTRACT_VERSION = 1
PROTECTED = ('unknown', 'delivery_facts', 'deletion_journal', 'authority')


def inventory(paths):
    """Explicit replica label -> file path. Includes SQLite sidecars, never values.

    The caller owns completeness (including backups/off-host exports); no implicit
    scan can claim all copies discovered. Missing/unreadable is not empty/success.
    """
    result = []
    for label, filename in paths.items():
        path = Path(filename)
        for suffix in ('', '-wal', '-shm', '-journal'):
            item = Path(str(path) + suffix)
            row = {'replica': label, 'component': suffix or 'main', 'path': str(item), 'state': 'missing'}
            try:
                if item.is_symlink():
                    row['state'] = 'symlink_rejected'
                elif item.is_file():
                    row.update(state='present', bytes=item.stat().st_size)
            except OSError:
                row['state'] = 'unreadable'
            result.append(row)
    return {'contract_version': CONTRACT_VERSION, 'dry_run': True,
            'discovery_complete': False, 'files': result, 'protected': PROTECTED}


def inspect_history(source, *, before_ms=None):
    """Count proposed identity projections; cutoff is explicit, never a default TTL.

    Only valid observations are candidates. Scores/zero/unknown/provenance and
    facts remain. This is NOT anonymity: remaining data may be identifying.
    """
    if before_ms is not None and (type(before_ms) is not int or before_ms <= 0):
        raise ValueError('before_ms must be explicit positive epoch milliseconds')
    source = Path(source).resolve()
    with closing(archive.ro(source)) as header:
        version = header.execute('PRAGMA user_version').fetchone()[0]
    legacy = archive.legacy_version(source) if version == compressed.VERSION else version
    if legacy not in (1, 2, 3, 4):
        raise ValueError('unsupported history schema')
    db = compressed.open_store(source, legacy_version=legacy) if version == compressed.VERSION else archive.ro(source)
    with closing(db):
        db.execute('BEGIN')
        archive.checked(db)
        if version == compressed.VERSION:
            for ident, kind in db.execute('SELECT id,kind FROM payloads'):
                compressed.get(db, ident, kind)
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        total, unknown = db.execute("SELECT count(*),coalesce(sum(quality<>'valid'),0) FROM observations").fetchone()
        eligible = 0 if before_ms is None else db.execute(
            "SELECT count(*) FROM observations WHERE quality='valid' AND received_ms<?", (before_ms,)).fetchone()[0]
        counts = {}
        for name in ('observation_players', 'observation_player_details'):
            counts[name] = (db.execute('SELECT count(*) FROM ' + name +
                " WHERE observation_id IN (SELECT id FROM observations WHERE quality='valid' AND received_ms<?)",
                (before_ms,)).fetchone()[0] if before_ms is not None and name in names else 0)
        pages = db.execute('PRAGMA page_count').fetchone()[0]
        free = db.execute('PRAGMA freelist_count').fetchone()[0]
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
    return {'contract_version': CONTRACT_VERSION, 'dry_run': True, 'schema': version, 'logical_schema': legacy,
            'before_ms': before_ms, 'policy_selected': False, 'observations': total, 'protected_nonvalid': unknown,
            'eligible_observations': eligible, 'identity_rows': counts, 'anonymous': False,
            'allocated_bytes': pages * page_size, 'freelist_bytes': free * page_size,
            'physical_bytes': source.stat().st_size,
            'requires': ['approved_field_scope', 'current_deletion_authority', 'complete_replica_inventory',
                         'quiesced_writers', 'dictionary_payload_rebuild', 'wal_checkpoint', 'backup_responsibility']}


def journal_digest(records):
    """Hash exact ordered deletion records; identifiers should be opaque, not PII."""
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def read_current_authority(path, *, excluded_roots, read_approved_sha256):
    """Read a manually approved external record, never bootstrap an authority.

    The trusted host supplies ALL restore/backup roots and a fresh independent
    approval reader. Its return is the SHA256 of exact approved file bytes, or
    None when current approval cannot be established. Neither input may come
    from the candidate. Host ownership, revocation and locking remain with 10.
    Invalid/unapproved records return None; IO failures propagate to C6 manual.
    """
    if (not callable(read_approved_sha256) or not isinstance(excluded_roots, (list, tuple))
            or not excluded_roots):
        return None
    if not Path(path).is_absolute() or any(not Path(root).is_absolute() for root in excluded_roots):
        return None
    approved = read_approved_sha256()
    if not isinstance(approved, str) or not re.fullmatch('[a-f0-9]{64}', approved):
        return None
    source = Path(path)
    if source.is_symlink():
        return None
    source = source.resolve(strict=True)
    if not source.is_file():
        return None
    for root in excluded_roots:
        if source.is_relative_to(Path(root).resolve()):
            return None
    with source.open('rb') as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != approved:
        return None

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate authority field')
            result[key] = value
        return result

    try:
        record = json.loads(raw, object_pairs_hook=unique_pairs)
        if not isinstance(record, dict) or set(record) != {'authority', 'journal'}:
            return None
        authority = record['authority']
        if not _valid_evidence(authority) or not isinstance(record['journal'], list):
            return None
        if journal_digest(record['journal']) != authority['journal_sha256']:
            return None
    except (ValueError, UnicodeError, RecursionError):
        return None
    if read_approved_sha256() != approved:
        return None
    return authority


def _valid_evidence(value, candidate=False):
    keys = {'contract_version', 'binding', 'policy_generation', 'deletion_generation',
            'journal_sha256', 'replicas', 'evidence_version'}
    if not isinstance(value, dict) or set(value) != keys:
        return False
    if type(value['contract_version']) is not int or value['contract_version'] != CONTRACT_VERSION:
        return False
    if any(not isinstance(value[k], str) or not value[k] for k in ('binding', 'evidence_version')):
        return False
    if any(type(value[k]) is not int or value[k] < 0 for k in ('policy_generation', 'deletion_generation')):
        return False
    if not isinstance(value['journal_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', value['journal_sha256']):
        return False
    replicas = value['replicas']
    if not isinstance(replicas, dict) or not replicas:
        return False
    for name, row in replicas.items():
        if not isinstance(name, str) or not name or not isinstance(row, dict):
            return False
        expected = {'generation', 'sha256', 'state'} if candidate else {'generation', 'sha256'}
        if set(row) != expected or type(row['generation']) is not int or row['generation'] < 0:
            return False
        if not isinstance(row['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', row['sha256']):
            return False
        if candidate and (not isinstance(row['state'], str) or row['state'] not in ('verified', 'pending', 'unavailable')):
            return False
    return True


def check_recovery(candidate, current_authority):
    """C6 only. current_authority() MUST query outside the candidate/backup tree.

    10 supplies measured candidate coverage, after quiescing writers. 09 owns
    identity; binding is its opaque verified instance token, not a new identity
    scheme. read_current_authority supports an externally approved record;
    it does not provision a production authority or confer approval itself.
    """
    def result(status, reason, evidence=None):
        return {'status': status, 'reason': reason, 'contract_version': CONTRACT_VERSION,
                'evidence': evidence}
    if current_authority is None:
        return result('manual', 'current_deletion_authority_missing')
    if not callable(current_authority):
        return result('manual', 'current_deletion_authority_invalid')
    try:
        authority = current_authority()
    except (OSError, TimeoutError):
        return result('manual', 'current_deletion_authority_unavailable')
    if not _valid_evidence(authority):
        return result('manual', 'current_deletion_authority_invalid')
    evidence = authority['evidence_version']
    if not _valid_evidence(candidate, True):
        return result('reject', 'malformed_deletion_evidence', evidence)
    if candidate['binding'] != authority['binding']:
        return result('reject', 'deletion_binding_mismatch', evidence)
    if candidate['evidence_version'] != evidence:
        return result('reject', 'deletion_evidence_version_mismatch', evidence)
    for field in ('policy_generation', 'deletion_generation', 'journal_sha256'):
        if candidate[field] != authority[field]:
            return result('reject', 'deletion_authority_mismatch', evidence)
    if set(candidate['replicas']) != set(authority['replicas']):
        return result('manual', 'replica_coverage_incomplete', evidence)
    for name, expected in authority['replicas'].items():
        observed = candidate['replicas'][name]
        if observed['state'] != 'verified':
            return result('manual', 'replica_not_verified', evidence)
        if any(observed[k] != expected[k] for k in ('generation', 'sha256')):
            return result('reject', 'replica_deletion_not_applied', evidence)
        if observed['generation'] != authority['deletion_generation']:
            return result('manual', 'authority_propagation_incomplete', evidence)
    return result('allow', 'deletion_state_verified', evidence)

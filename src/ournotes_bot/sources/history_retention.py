# L3
# Input: Explicit offline copies, caller-selected cutoff, external current authority.
# Output: Dry-run inventory/projection plan and C6 allow/reject/manual decisions.
# Pos: Sources / history maintenance; see docs/HISTORY_DATA_CONTRACT.md.
# Effects: Read-only local inspection. No production policy, deletion or activation.
"""Retention planning is not approval; restored journals are not authority."""
import hashlib
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
    import json
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def check_recovery(candidate, current_authority):
    """C6 only. current_authority() MUST query outside the candidate/backup tree.

    10 supplies measured candidate coverage, after quiescing writers. 09 owns
    identity; binding is its opaque verified instance token, not a new identity
    scheme. No current authority implementation is selected by task07.
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
    keys = {'contract_version', 'binding', 'policy_generation', 'deletion_generation',
            'journal_sha256', 'replicas', 'evidence_version'}
    def valid(value, candidate=False):
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
    if not valid(authority):
        return result('manual', 'current_deletion_authority_invalid')
    evidence = authority['evidence_version']
    if not valid(candidate, True):
        return result('reject', 'malformed_deletion_evidence', evidence)
    if candidate['binding'] != authority['binding']:
        return result('reject', 'deletion_binding_mismatch', evidence)
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

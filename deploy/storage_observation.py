"""Read-only physical/logical history observations for task09's schema mapping.

Input: external replica inventory and explicitly isolated, checkpointed copies.
Output: per-replica observations, complete schema projection or manual; no permit.
Pos: deploy/INSTANCE_RECOVERY.md. Effects: directory/stat/hash reads, SQLite
mode=ro&immutable=1 queries only. Never opens application stores or migrators.
Live/WAL copies are refused, not silently inspected without their sidecars.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import stat

try:
    from .instance_preflight import digest, read_document
    from .recovery_preflight import measure_components
except ImportError:
    from instance_preflight import digest, read_document
    from recovery_preflight import measure_components


class ObservationError(ValueError):
    pass


def _metadata_json(value):
    if not isinstance(value, str):
        raise ObservationError('history_metadata_invalid')
    return json.loads(value)


def _scan(roots):
    found = set()
    for root in roots:
        if root.is_symlink() or not root.is_dir() or root.resolve() != root:
            raise ObservationError('storage_root_invalid')
        def visit(directory):
            for child in directory.iterdir():
                info = child.lstat()
                if stat.S_ISDIR(info.st_mode):
                    visit(child)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    if child in found:
                        raise ObservationError('storage_roots_overlap')
                    found.add(child)
                else:
                    raise ObservationError('storage_alias_or_special_file')
        visit(root)
    return found


def _columns(db, name, required):
    # Only fixed identifiers supplied below, never external SQL identifiers.
    names = {r[1] for r in db.execute('PRAGMA table_info(' + name + ')')}
    if not required <= names:
        raise ObservationError('history_columns_missing')


def _inspect(path, expected_partition):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True,
                                 timeout=0.15)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('BEGIN')
        # A finite VM instruction budget makes malformed/huge copies diagnosable.
        budget = 10000
        def progress():
            nonlocal budget
            budget -= 1
            return budget <= 0
        db.set_progress_handler(progress, 1000)
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ObservationError('sqlite_integrity_failed')
        physical = db.execute('PRAGMA user_version').fetchone()[0]
        if physical not in (1, 2, 3, 4, 107):
            raise ObservationError('history_format_unknown')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        logical = physical
        if physical == 107:
            rows = db.execute('SELECT key,value FROM store_meta').fetchall()
            meta = dict(rows)
            if len(meta) != len(rows) or meta.get('legacy_version') not in ('1', '2', '3', '4'):
                raise ObservationError('logical_version_missing_or_invalid')
            if meta.get('state') != 'complete':
                raise ObservationError('compressed_copy_incomplete')
            if meta.get('players_codec') != 'dictionary':
                raise ObservationError('compressed_codec_unknown')
            if (not isinstance(meta.get('source_sha'), str)
                    or not re.fullmatch('[a-f0-9]{64}', meta['source_sha'])
                    or not isinstance(_metadata_json(meta.get('schema', 'null')), list)):
                raise ObservationError('compressed_metadata_missing')
            logical = int(meta['legacy_version'])
            if not {'payloads', 'player_dictionary', 'store_meta', 'observations_data'} <= tables:
                raise ObservationError('compressed_tables_missing')
        required = {'id', 'server', 'event', 'challenge', 'music', 'source_ms',
                    'received_ms', 'scores', 'digest', 'quality'}
        if logical >= 2:
            required |= {'source', 'time_kind', 'contract_version'}
        _columns(db, 'observations_data' if physical == 107 else 'observations', required)
        if not {'boards', 'gaps', 'health'} <= tables:
            raise ObservationError('history_tables_missing')
        partition = None
        if logical == 4:
            if expected_partition is None:
                raise ObservationError('provider_partition_not_registered')
            rows = db.execute('SELECT singleton,identity FROM partition_identity').fetchall()
            if len(rows) != 1 or rows[0][0] != 1:
                raise ObservationError('provider_partition_missing')
            partition = _metadata_json(rows[0][1])
            if partition != expected_partition:
                raise ObservationError('provider_partition_mismatch')
            if 'observation_provenance' not in tables:
                raise ObservationError('provider_provenance_missing')
            physical_table = 'observations_data' if physical == 107 else 'observations'
            for (payload,) in db.execute('SELECT p.payload FROM ' + physical_table +
                    ' o LEFT JOIN observation_provenance p ON p.observation_id=o.id'):
                if payload is None:
                    raise ObservationError('provider_provenance_missing')
                provenance = _metadata_json(payload)
                if (not isinstance(provenance, dict) or
                        [provenance.get(k) for k in ('provider', 'normalization_version', 'time_kind')]
                        != expected_partition):
                    raise ObservationError('provider_provenance_mismatch')
        elif expected_partition is not None or 'partition_identity' in tables:
            raise ObservationError('legacy_provider_not_unknown')
        return {'physical': physical, 'logical': logical, 'partition': partition}


def observe_history(inventory, *, isolated=False):
    """Observe all files in explicitly dedicated history roots, including extras.

    Inventory shape: {version:1, roots:[absolute directory], replicas:{opaque_id:
    {path:absolute file, partition:null|[provider,normalization,time_kind]}}}.
    A null partition denotes unknown legacy, never an invented provider. Roots
    and replica bindings must be supplied outside the recovery tree. Enumeration
    covers only these roots; it cannot discover omitted hosts/backups/exports.
    """
    result = {'status': 'manual', 'activation_permitted': False, 'schemas': {},
              'replicas': {}, 'coverage_complete': False}
    if isolated is not True:
        return {**result, 'reason': 'isolated_checkpointed_copy_required'}
    if not (isinstance(inventory, dict) and set(inventory) == {'version', 'roots', 'replicas'}
            and type(inventory['version']) is int and inventory['version'] == 1
            and isinstance(inventory['roots'], list) and inventory['roots']
            and isinstance(inventory['replicas'], dict) and inventory['replicas']):
        return {**result, 'reason': 'storage_inventory_invalid'}
    try:
        roots = []
        for raw in inventory['roots']:
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise ObservationError('storage_root_invalid')
            roots.append(Path(raw))
        paths = {}
        for ident, row in inventory['replicas'].items():
            if not (isinstance(ident, str) and re.fullmatch('[a-zA-Z0-9_-]{1,64}', ident)
                    and isinstance(row, dict) and set(row) == {'path', 'partition'}
                    and isinstance(row['path'], str) and Path(row['path']).is_absolute()):
                raise ObservationError('storage_replica_invalid')
            partition = row['partition']
            if partition is not None and not (isinstance(partition, list) and len(partition) == 3
                    and all(isinstance(v, str) and v for v in partition)):
                raise ObservationError('storage_partition_invalid')
            path = Path(row['path'])
            if path in paths.values() or path.resolve() != path:
                raise ObservationError('storage_replica_alias')
            if not any(path.is_relative_to(root) for root in roots):
                raise ObservationError('storage_replica_outside_roots')
            paths[ident] = path
        before = _scan(roots)
        if before != set(paths.values()):
            # Includes unlisted DBs, sidecars, missing replicas and unexpected files.
            raise ObservationError('replica_coverage_or_sidecars_mismatch')
        hashes = measure_components({k: str(p) for k, p in paths.items()})
        schemas = {}
        for ident, path in paths.items():
            try:
                row = _inspect(path, inventory['replicas'][ident]['partition'])
            except (sqlite3.Error, ValueError, OSError) as exc:
                reason = str(exc) if isinstance(exc, ObservationError) else 'storage_read_failed'
                result['replicas'][ident] = {'status': 'manual', 'reason': reason}
                continue
            result['replicas'][ident] = {'status': 'observed', **row, 'sha256': hashes[ident]}
            for kind in ('physical', 'logical'):
                schemas[f'cutoff_history.{ident}.{kind}'] = row[kind]
        if (_scan(roots) != before or hashes != measure_components({k: str(p) for k, p in paths.items()})):
            raise ObservationError('storage_changed_during_observation')
        if any(row['status'] != 'observed' for row in result['replicas'].values()):
            return {**result, 'reason': 'storage_observation_incomplete'}
        return {**result, 'status': 'observed', 'reason': 'history_formats_observed',
                'coverage_complete': True, 'schemas': schemas,
                'inventory_sha256': digest(inventory),
                'evidence': digest({'inventory': inventory, 'replicas': result['replicas']})}
    except (OSError, ValueError, RuntimeError) as exc:
        reason = str(exc) if isinstance(exc, ObservationError) else 'storage_read_failed'
        return {**result, 'reason': reason}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--isolated-checkpointed-copy', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = observe_history(read_document(args.inventory), isolated=args.isolated_checkpointed_copy)
    except (OSError, ValueError):
        result = {'status': 'manual', 'reason': 'storage_inventory_unreadable',
                  'activation_permitted': False, 'schemas': {}, 'coverage_complete': False}
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0 if result['status'] == 'observed' else 2


if __name__ == '__main__':
    raise SystemExit(main())

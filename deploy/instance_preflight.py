"""Task10 deployment planning, separate from the mutating Linux updater.

Input: explicit non-secret instance inventory, optional local path observations.
Output: reviewable conflicts and pending gates; NEVER a start permit.
Pos: deploy/L2.md; contract and examples in INSTANCE_RECOVERY.md.
Effects: plan_instances is pure. CLI reads JSON and, with --local-paths, Linux
filesystem metadata only. No backend construction, subprocess, lock, mkdir,
network, credential content, service control or application import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

PATHS = frozenset(('base', 'config', 'environment', 'data', 'console_state',
                  'ledger', 'history', 'controller', 'controller_state',
                  'runtime', 'combinations', 'backups', 'recovery', 'authority'))
WRITABLE = frozenset(('data', 'console_state', 'ledger', 'history'))
TRUSTED = frozenset(('controller', 'controller_state', 'runtime', 'authority', 'config', 'environment'))
PENDING = ('host_inventory_and_ownership', 'actual_product_candidate',
           'independent_scope_approval_policy', 'current_deletion_authority_policy',
           'isolated_linux_acceptance', 'activation_not_implemented')


def read_document(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('nonfinite_json')
    return json.loads(Path(path).read_text(encoding='utf-8'),
                      object_pairs_hook=pairs, parse_constant=constant)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _path(value):
    return (isinstance(value, str) and value.startswith('/') and not value.startswith('//')
            and str(PurePosixPath(value)) == value and value != '/'
            and '..' not in PurePosixPath(value).parts
            and not any(ord(c) < 32 for c in value) and '\\' not in value)


def _overlap(a, b):
    a, b = PurePosixPath(a), PurePosixPath(b)
    return a == b or a in b.parents or b in a.parents


def validate_inventory(document):
    if not (isinstance(document, dict) and set(document) == {'version', 'host', 'instances'}
            and type(document['version']) is int and document['version'] == 1
            and isinstance(document['host'], str) and document['host'].strip()
            and isinstance(document['instances'], list) and document['instances']):
        raise ValueError('instance_inventory_invalid')
    for row in document['instances']:
        if not (isinstance(row, dict) and set(row) == {
                'instance', 'product', 'app_id', 'channel', 'user', 'uid',
                'controller_uid', 'paths', 'units', 'ports'}):
            raise ValueError('instance_fields_invalid')
        if any(not isinstance(row[k], str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', row[k])
               for k in ('instance', 'product', 'channel', 'user')):
            raise ValueError('instance_name_invalid')
        if not isinstance(row['app_id'], str) or not re.fullmatch(r'[0-9]{1,32}', row['app_id']):
            raise ValueError('app_id_invalid')
        if not (type(row['uid']) is int and row['uid'] > 0
                and type(row['controller_uid']) is int and row['controller_uid'] >= 0
                and row['controller_uid'] != row['uid']):
            raise ValueError('controller_user_not_isolated')
        if not (isinstance(row['paths'], dict) and set(row['paths']) == PATHS
                and all(_path(p) for p in row['paths'].values())):
            raise ValueError('instance_paths_invalid')
        units = row['units']
        if not isinstance(units, dict) or set(units) != {'service', 'timer', 'socket'}:
            raise ValueError('instance_units_invalid')
        for kind, names in units.items():
            if not (isinstance(names, list) and (names or kind == 'socket')
                    and all(isinstance(n, str) and re.fullmatch(
                        r'[a-zA-Z0-9_.@-]+\.' + kind, n) for n in names)):
                raise ValueError('instance_units_invalid')
        if not (isinstance(row['ports'], dict) and row['ports']
                and all(isinstance(k, str) and k and type(p) is int and 1 <= p <= 65535
                        for k, p in row['ports'].items())):
            raise ValueError('instance_ports_invalid')


def inspect_local_paths(document):
    """Read Linux realpaths and inode ancestry, including missing-leaf aliases.

    Mount/hardlink aliases can share inode identity without sharing path spelling.
    This is a point-in-time observation, not a lock or a complete host audit.
    """
    validate_inventory(document)
    if os.name != 'posix':
        raise ValueError('linux_path_observation_required')
    result = {}
    for row in document['instances']:
        for raw in row['paths'].values():
            path = Path(raw).resolve(strict=False)
            chain = []
            for ancestor in (path, *path.parents):
                try:
                    st = ancestor.stat()
                except FileNotFoundError:
                    continue
                chain.append({'device': st.st_dev, 'inode': st.st_ino,
                              'suffix': str(path.relative_to(ancestor)),
                              'uid': st.st_uid, 'mode': st.st_mode})
            result[raw] = {'resolved': str(path), 'ancestry': chain}
    return result


def _aliases(a, b):
    if _overlap(a['resolved'], b['resolved']):
        return True
    return any(x['device'] == y['device'] and x['inode'] == y['inode']
               and _overlap('/' + x['suffix'], '/' + y['suffix'])
               for x in a['ancestry'] for y in b['ancestry'])


def plan_instances(document, observations=None):
    """Compare explicit instances, never infer the host inventory is complete."""
    validate_inventory(document)
    conflicts = []
    rows = document['instances']
    paths = {p for row in rows for p in row['paths'].values()}
    if observations is not None:
        if not isinstance(observations, dict) or set(observations) != paths:
            raise ValueError('path_observations_incomplete')
        for observation in observations.values():
            if not (isinstance(observation, dict) and set(observation) == {'resolved', 'ancestry'}
                    and _path(observation['resolved']) and isinstance(observation['ancestry'], list)
                    and observation['ancestry']):
                raise ValueError('path_observation_invalid')
            for node in observation['ancestry']:
                if not (isinstance(node, dict) and set(node) == {'device', 'inode', 'suffix', 'uid', 'mode'}
                        and all(type(node[k]) is int and node[k] >= 0 for k in ('device', 'inode', 'uid', 'mode'))
                        and isinstance(node['suffix'], str)
                        and (node['suffix'] == '.' or _path('/' + node['suffix']))):
                    raise ValueError('path_observation_invalid')
    def add(code, *resources):
        conflicts.append({'reason': code, 'resources': list(resources)})
    def overlap(a, b):
        return _overlap(a, b) or (observations is not None and _aliases(observations[a], observations[b]))
    seen = {k: {} for k in ('instance', 'app_id', 'user', 'uid', 'unit', 'port')}
    for row in rows:
        name = row['instance']
        # Product semantics remain C1's responsibility; this is declaration sanity.
        if {'taki-personal': 'personal', 'haneoka-custom': 'custom'}.get(row['product']) != row['channel']:
            add('product_channel_mismatch', name)
        resources = [(k, row[k]) for k in ('instance', 'app_id', 'user', 'uid')]
        resources += [('unit', n) for names in row['units'].values() for n in names]
        resources += [('port', p) for p in row['ports'].values()]
        for kind, value in resources:
            if value in seen[kind]:
                add(kind + '_collision', seen[kind][value], name)
            seen[kind][value] = name
        for protected in TRUSTED:
            for writable in WRITABLE | {'backups', 'recovery', 'combinations'}:
                if overlap(row['paths'][protected], row['paths'][writable]):
                    add('authority_or_controller_overlap', name, protected, writable)
        independent = ('console_state', 'ledger', 'history')
        for i, left in enumerate(independent):
            for right in independent[i + 1:]:
                if overlap(row['paths'][left], row['paths'][right]):
                    add('state_roles_overlap', name, left, right)
        if observations is not None:
            for role, raw in row['paths'].items():
                observation = observations[raw]
                if observation['resolved'] != raw:
                    add('path_alias_requires_canonical_plan', name, role)
                if role in TRUSTED:
                    for node in observation['ancestry']:
                        if node['uid'] not in (0, row['controller_uid']) or node['mode'] & 0o022:
                            add('controller_path_not_trusted', name, role)
                            break
                elif role in WRITABLE:
                    leaf = next((n for n in observation['ancestry'] if n['suffix'] == '.'), None)
                    if leaf is not None and leaf['uid'] != row['uid']:
                        add('data_owner_mismatch', name, role)
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            if left['uid'] == right['controller_uid'] or right['uid'] == left['controller_uid']:
                add('cross_instance_controller_user', left['instance'], right['instance'])
            for lk, lp in left['paths'].items():
                for rk, rp in right['paths'].items():
                    if overlap(lp, rp):
                        add('cross_instance_path_overlap', left['instance'], lk, right['instance'], rk)
    return {'contract_version': 1, 'kind': 'deployment_plan',
            'status': 'reject' if conflicts else 'manual', 'activation_permitted': False,
            'inventory_sha256': digest(document),
            'observations_sha256': digest(observations) if observations is not None else None,
            'conflicts': conflicts,
            'pending': list(PENDING) + ([] if observations is not None else ['local_path_observation']),
            'operations': []}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--local-paths', action='store_true', help='read Linux path metadata; never install')
    args = parser.parse_args(argv)
    try:
        document = read_document(args.inventory)
        result = plan_instances(document, inspect_local_paths(document) if args.local_paths else None)
    except (OSError, ValueError, RuntimeError) as exc:
        # Only our ValueError codes are public; filesystem errors may contain paths.
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch('[a-z_]+', str(exc)) else 'input_unreadable'
        result = {'status': 'reject', 'reason': reason, 'activation_permitted': False, 'operations': []}
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 1 if result['status'] == 'reject' else 2  # manual is never shell success


if __name__ == '__main__':
    raise SystemExit(main())

"""C1/C6 pure preflight; no activation, configuration writes or payload execution.

Input: candidate, external expected authority and independently observed digests.
Output: structured verdict; see docs/PRODUCT_RELEASE.md. Pos: deploy/L2.md.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def read_json(path: Path) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError('nonfinite_json')

    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                       parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError('expected_json_object')
    return value


def _keys(value, keys):
    return isinstance(value, dict) and set(value) == set(keys.split())


def _number(value, minimum=0):
    return type(value) is int and value >= minimum


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 128 and not any(ord(c) < 32 for c in value)


def _hash(value, size=64):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(size) + '}', value) is not None


def _caps(value):
    return isinstance(value, list) and all(_text(x) for x in value) and len(set(value)) == len(value)


def _components(value):
    return isinstance(value, dict) and bool(value) and all(_text(k) and _hash(v) for k, v in value.items())


def _range(value):
    return (isinstance(value, list) and len(value) == 2
            and all(_number(x) for x in value) and value[0] <= value[1])


def _schemas(value):
    return isinstance(value, dict) and bool(value) and all(
        _text(k) and _keys(v, 'read write') and _range(v['read']) and _range(v['write'])
        for k, v in value.items())


def _identity(value):
    return (all(_text(value[k]) for k in ('product', 'instance', 'channel'))
            and isinstance(value['app_id'], str)
            and re.fullmatch('[0-9]{1,32}', value['app_id']) is not None)


def check_identity(candidate, expected, observed):
    """Allow only the identity check, never the overall activation or restore."""
    def result(reason, status='reject'):
        return {'status': status, 'reason': reason, 'contract_version': 1}

    if not (_keys(candidate, 'contract_version product instance app_id channel release_seq source_commit '
                            'capabilities components protocol schemas min_controller inventory_sha256')
            and _keys(expected, 'contract_version product instance app_id channel capability_ceiling '
                                'manifest_sha256 minimum_release_seq protocol schemas')
            and _keys(observed, 'manifest_sha256 components controller')):
        return result('malformed_contract')
    c, e, o = candidate, expected, observed
    if not (type(c['contract_version']) is int and c['contract_version'] == 1
            and type(e['contract_version']) is int and e['contract_version'] == 1
            and _identity(c) and _identity(e)
            and _number(c['release_seq']) and _number(e['minimum_release_seq'])
            and _hash(c['source_commit'], 40) and _hash(c['inventory_sha256'])
            and _hash(e['manifest_sha256']) and _hash(o['manifest_sha256'])
            and _caps(c['capabilities']) and _caps(e['capability_ceiling'])
            and _components(c['components']) and _components(o['components'])
            and _number(c['min_controller'], 1) and _number(o['controller'], 1)
            and _keys(c['protocol'], 'major minor')
            and all(_number(v) for v in c['protocol'].values())
            and _keys(e['protocol'], 'major min_minor max_minor')
            and all(_number(v) for v in e['protocol'].values())
            and e['protocol']['min_minor'] <= e['protocol']['max_minor']
            and _schemas(c['schemas']) and isinstance(e['schemas'], dict) and bool(e['schemas'])
            and all(_text(k) and _number(v) for k, v in e['schemas'].items())):
        return result('malformed_contract')
    channels = {'taki-personal': 'personal', 'haneoka-custom': 'custom'}
    if (channels.get(c['product']) != c['channel']
            or any(c[k] != e[k] for k in ('product', 'instance', 'app_id', 'channel'))):
        return result('identity_mismatch')
    if e['manifest_sha256'] != o['manifest_sha256']:
        return result('unapproved_manifest')
    caps = set(c['capabilities'])
    if (not caps <= set(e['capability_ceiling'])
            or (c['product'] == 'haneoka-custom' and caps & {'chat.free', 'chat.proactive'})):
        return result('capability_exceeded')
    if c['components'] != o['components']:
        return result('component_mismatch')
    if (c['protocol']['major'] != e['protocol']['major']
            or not e['protocol']['min_minor'] <= c['protocol']['minor'] <= e['protocol']['max_minor']):
        return result('protocol_incompatible')
    if o['controller'] < c['min_controller']:
        return result('controller_too_old')
    if c['release_seq'] < e['minimum_release_seq']:
        return result('rollback_denied')
    if (set(c['schemas']) != set(e['schemas']) or any(
            not all(c['schemas'][k][mode][0] <= version <= c['schemas'][k][mode][1]
                    for mode in ('read', 'write')) for k, version in e['schemas'].items())):
        return result('schema_incompatible', 'manual')
    return result('identity_verified', 'allow')

"""Read-only checksum and graph checks for the bounded Rollup source delta."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import tomllib
import zipfile


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def check(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive member')
        for name in names:
            parts = PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts:
                raise ValueError('Unsafe archive member')
        index = json.loads(archive.read('index.json'))
        lock_bytes = archive.read('evidence/Cargo.lock')
        if digest(lock_bytes) != index['cargo_lock_sha256']:
            raise ValueError('Lock digest mismatch')
        if digest(archive.read('evidence/cargo-metadata.json')) != index['cargo_metadata_sha256']:
            raise ValueError('Cargo metadata digest mismatch')
        lock = {(row['name'], row['version']): row for row in
                tomllib.loads(lock_bytes.decode())['package']}
        rows = index['selected']
        ids = {row['id'] for row in rows}
        if len(ids) != len(rows) or index['root'] not in ids:
            raise ValueError('Invalid selected package identities')
        metadata = json.loads(archive.read('evidence/cargo-metadata.json'))
        nodes = {row['id']: row for row in metadata['resolve']['nodes']}
        packages = {row['id']: row for row in metadata['packages']}
        reachable, pending, wanted_edges = set(), [index['root']], []
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            reachable.add(key)
            for edge in nodes[key]['deps']:
                kinds = [item for item in edge['dep_kinds'] if item['kind'] != 'dev']
                if kinds:
                    wanted_edges.append({'parent': key, 'child': edge['pkg'],
                                         'name': edge['name'], 'kinds': kinds})
                    pending.append(edge['pkg'])
        if ids != reachable or sorted(index['edges'], key=str) != sorted(wanted_edges, key=str):
            raise ValueError('Indexed closure differs from Cargo normal/build graph')
        if 'cargo_tree_sha256' in index:
            features = {}
            for line in archive.read('evidence/cargo-tree-summary.txt').decode().splitlines():
                if '|' not in line:
                    continue
                label, flags = line.split('|', 1)
                key = tuple(re.match(r'([^ ]+) v([^ |]+)', label).groups())
                flags = tuple(sorted(set(flags.removesuffix(' (*)').split(',')) - {''}))
                features.setdefault(key, set()).add(flags)
            if set(features) != {(row['name'], row['version']) for row in rows}:
                raise ValueError('Target tree package set differs')
            for row in rows:
                selected = features[(row['name'], row['version'])]
                if (sorted(map(tuple, row['resolved_feature_sets'])) != sorted(selected)
                        or row['features'] != sorted({flag for group in selected for flag in group})):
                    raise ValueError('Target feature selection differs: ' + row['name'])
                package = packages[row['id']]
                if package['name'] != row['name'] or package['version'] != row['version']:
                    raise ValueError('Package identity differs from Cargo metadata')
        for edge in index['edges']:
            if edge['parent'] not in ids or edge['child'] not in ids:
                raise ValueError('Edge escapes selected closure')
            if any(kind['kind'] == 'dev' for kind in edge['kinds']):
                raise ValueError('Development edge in selected closure')
        expected = {'index.json', 'evidence/Cargo.lock', 'evidence/cargo-metadata.json'}
        if 'cargo_tree_sha256' in index:
            expected.add('evidence/cargo-tree-summary.txt')
            if digest(archive.read('evidence/cargo-tree-summary.txt')) != index['cargo_tree_sha256']:
                raise ValueError('Target feature summary digest mismatch')
        if index['identity'].endswith('.2'):
            expected.add('evidence/upstream-license-fetches.json')
        for row in rows:
            if 'archive_path' not in row:
                continue
            data = archive.read(row['archive_path'])
            wanted = lock[(row['name'], row['version'])]['checksum']
            if digest(data) != wanted or row['archive_sha256'] != wanted:
                raise ValueError('Crate checksum mismatch: ' + row['name'])
            expected.add(row['archive_path'])
            for license_row in row['license_materials']:
                if digest(archive.read(license_row['path'])) != license_row['sha256']:
                    raise ValueError('Original text checksum mismatch')
                expected.add(license_row['path'])
            for evidence in row.get('fixed_upstream', {}).get('evidence', []):
                if 'path' in evidence:
                    if digest(archive.read(evidence['path'])) != evidence['sha256']:
                        raise ValueError('Fixed upstream evidence digest mismatch')
                    expected.add(evidence['path'])
        if expected != set(names):
            raise ValueError('Unindexed material members')
    return {'path': str(path), 'size': path.stat().st_size,
            'sha256': digest(path.read_bytes()), 'selected': len(rows),
            'collected': sum('archive_path' in row for row in rows),
            'reused': sum(row['status'] == 'reused_checked_material' for row in rows),
            'license_texts': sum(len(row.get('license_materials', [])) for row in rows),
            'missing_original_license_text': [row['id'] for row in rows
                                               if row['status'] == 'missing_original_license_text'],
            'complete_corresponding_source': False, 'product_release': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    print(json.dumps(check(parser.parse_args().archive), indent=2))

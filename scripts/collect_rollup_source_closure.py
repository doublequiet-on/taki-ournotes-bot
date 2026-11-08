"""Collect the fixed Linux Cargo normal/build/proc-macro material difference.

Consumes Cargo metadata --locked --filter-platform x86_64-unknown-linux-gnu
and the same invocation's cache. Does not compile crates or approve distribution.
Previously checked archives are referenced by digest, not repackaged.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from pathlib import Path, PurePosixPath
import tarfile
import tomllib
import zipfile


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(metadata_path: Path, lock_path: Path, cache: Path,
            reused_paths: list[Path], output: Path, tree_path: Path) -> None:
    if output.exists():
        raise ValueError('Refusing to overwrite evidence')
    metadata_bytes = metadata_path.read_bytes()
    metadata = json.loads(metadata_bytes)
    packages = {row['id']: row for row in metadata['packages']}
    nodes = {row['id']: row for row in metadata['resolve']['nodes']}
    tree_features = {}
    for line in tree_path.read_text(encoding='utf-8').splitlines():
        if '|' not in line:
            continue
        package, features = line.split('|', 1)
        match = re.match(r'([^ ]+) v([^ |]+)', package)
        if not match:
            raise ValueError('Unrecognized Cargo tree package')
        key = tuple(match.groups())
        features = set(features.removesuffix(' (*)').split(',')) - {''}
        tree_features.setdefault(key, set()).add(tuple(sorted(features)))
    roots = [key for key, row in packages.items() if row['name'] == 'bindings_napi']
    if len(roots) != 1:
        raise ValueError('Expected one native entry')
    selected, pending, edges = set(), roots[:], []
    while pending:
        key = pending.pop()
        if key in selected:
            continue
        selected.add(key)
        for edge in nodes[key]['deps']:
            kinds = [row for row in edge['dep_kinds'] if row['kind'] != 'dev']
            if kinds:
                edges.append({'parent': key, 'child': edge['pkg'], 'name': edge['name'],
                              'kinds': kinds})
                pending.append(edge['pkg'])
    if {(packages[key]['name'], packages[key]['version']) for key in selected} != set(tree_features):
        raise ValueError('Metadata reachable packages differ from native-entry Cargo tree')
    locked = {(row['name'], row['version']): row
              for row in tomllib.loads(lock_path.read_text(encoding='utf-8'))['package']}
    reused, prior = {}, []
    for path in reused_paths:
        binding = {'path': path.name, 'size': path.stat().st_size,
                   'sha256': sha(path.read_bytes())}
        prior.append(binding)
        with zipfile.ZipFile(path) as archive:
            for row in json.loads(archive.read('index.json'))['selected']:
                reused[(row['name'], row['version'])] = binding
    members = {'evidence/cargo-metadata.json': metadata_bytes,
               'evidence/Cargo.lock': lock_path.read_bytes(),
               'evidence/cargo-tree-summary.txt': tree_path.read_bytes()}
    rows = []
    for key in sorted(selected):
        package = packages[key]
        name, version = package['name'], package['version']
        row = {'id': key, 'name': name, 'version': version,
               'features': sorted({feature for group in tree_features[(name, version)] for feature in group}),
               'resolved_feature_sets': sorted(tree_features[(name, version)]),
               'workspace_metadata_union_features': nodes[key]['features'],
               'proc_macro': any('proc-macro' in target['kind'] for target in package['targets']),
               'source': package['source']}
        if package['source'] is None:
            row['status'] = 'local_source_in_fixed_rollup_archive'
        elif (name, version) in reused:
            row['status'] = 'reused_checked_material'
            row['material'] = reused[(name, version)]
        else:
            expected = locked[(name, version)]['checksum']
            crate = cache / f'{name}-{version}.crate'
            if not crate.is_file():
                raise ValueError('Selected crate missing from Cargo cache: ' + crate.name)
            data = crate.read_bytes()
            if sha(data) != expected:
                raise ValueError('Cargo.lock checksum mismatch: ' + crate.name)
            archive_path = 'crates/' + crate.name
            members[archive_path] = data
            licenses = []
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as source:
                for entry in source:
                    path = PurePosixPath(entry.name)
                    if path.is_absolute() or '..' in path.parts:
                        raise ValueError('Unsafe crate path')
                    if not entry.isfile():
                        continue
                    leaf = path.name.upper()
                    relative = '/'.join(path.parts[1:])
                    if (leaf.startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE'))
                            or relative == package.get('license_file')):
                        raw = source.extractfile(entry).read()
                        dest = f'licenses/{name}-{version}/{relative}'
                        members[dest] = raw
                        licenses.append({'path': dest, 'sha256': sha(raw)})
            row.update(status='collected' if licenses else 'missing_original_license_text',
                       archive_path=archive_path, archive_sha256=sha(data),
                       cargo_lock_checksum=expected, license_expression=package['license'],
                       repository=package['repository'], license_materials=licenses,
                       download_url=f'https://static.crates.io/crates/{name}/{name}-{version}.crate')
        rows.append(row)
    index = {'identity': 'rollup-4.67.4-linux-x64-glibc-remaining-closure.1',
             'target': 'x86_64-unknown-linux-gnu', 'host': 'x86_64-unknown-linux-gnu',
             'root': roots[0], 'cargo_metadata_sha256': sha(metadata_bytes),
             'cargo_lock_sha256': sha(lock_path.read_bytes()),
             'cargo_tree_sha256': sha(tree_path.read_bytes()),
             'selection': 'reachable normal/build edges; proc-macro marked from Cargo target kind; dev edges excluded',
             'prior_materials': prior, 'selected': rows, 'edges': edges,
             'not_reachable_from_native_entry': sorted(set(packages) - selected),
             'binary_rebuilt': False, 'complete_corresponding_source': False,
             'product_release': False}
    members['index.json'] = (json.dumps(index, indent=2) + '\n').encode()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in sorted(members.items()):
            archive.writestr(zipfile.ZipInfo(name, (2026, 11, 4, 0, 0, 0)), data,
                             compress_type=zipfile.ZIP_DEFLATED)
    print(json.dumps({'path': str(output), 'size': output.stat().st_size,
                      'sha256': sha(output.read_bytes()), 'selected': len(rows),
                      'collected': sum(row['status'] == 'collected' for row in rows),
                      'missing_license_text': [row['id'] for row in rows
                                               if row['status'] == 'missing_original_license_text']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--reuse', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tree', type=Path, required=True)
    args = parser.parse_args()
    collect(args.metadata, args.lock, args.cache, args.reuse, args.output, args.tree)

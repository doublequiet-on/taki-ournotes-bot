"""Collect the bounded Rollup Linux x64 glibc native transitive materials.

The selected set closes the three transitive branches identified by the Task11
audit.  It verifies official crates.io archive bytes against the fixed Rollup
Cargo.lock and retains original license/notice files.  It does not compile the
crates or claim the upstream native binary is reproducible.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import tomllib
import urllib.request
import zipfile


SELECTED = {
    'mimalloc-safe': 'parent: Linux glibc local_dynamic_tls allocator selection',
    'libmimalloc-sys2': 'normal: bundled mimalloc C source and build script',
    'cc': 'build: libmimalloc-sys2 C compiler driver',
    'cmake': 'build: libmimalloc-sys2 CMake driver',
    'find-msvc-tools': 'build dependency of pinned cc; retained although MSVC discovery is inactive on Linux',
    'shlex': 'build dependency of pinned cc',
    'napi': 'parent: napi4 with default features disabled',
    'napi-sys': 'normal: Node-API FFI selected by napi',
    'libloading': 'normal: napi-sys dynamic loader',
    'parking_lot': 'parent: swc_common parking_lot feature and parse_ast direct dependency',
    'parking_lot_core': 'normal: parking_lot Linux implementation',
    'lock_api': 'normal: parking_lot locking API',
    'scopeguard': 'normal: lock_api dependency',
    'smallvec': 'normal: parking_lot_core dependency',
    'libc': 'normal: mimalloc, napi and parking_lot Linux libc bindings',
    'cfg-if': 'normal: Linux cfg selection for libloading and parking_lot_core',
}

EXCLUDED = {
    'redox_syscall': 'target_os=redox; excluded for x86_64-unknown-linux-gnu',
    'windows-link': 'cfg(windows); excluded for x86_64-unknown-linux-gnu',
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def locked(lock: Path) -> dict[str, dict]:
    packages = tomllib.loads(lock.read_text(encoding='utf-8'))['package']
    by_name = {row['name']: row for row in packages if row.get('source', '').startswith('registry+')}
    missing = (set(SELECTED) | set(EXCLUDED)) - set(by_name)
    if missing:
        raise ValueError('Fixed Cargo.lock is missing: ' + ', '.join(sorted(missing)))
    return by_name


def collect(lock: Path, output: Path) -> None:
    if output.exists():
        raise ValueError('Refusing to overwrite material archive')
    packages = locked(lock)
    members: dict[str, bytes] = {}
    rows = []
    for name, role in SELECTED.items():
        package = packages[name]
        version = package['version']
        url = f'https://static.crates.io/crates/{name}/{name}-{version}.crate'
        with urllib.request.urlopen(url, timeout=60) as response:
            archive = response.read()
        if digest(archive) != package['checksum']:
            raise ValueError(f'{name} archive differs from Cargo.lock checksum')
        archive_path = f'crates/{name}-{version}.crate'
        members[archive_path] = archive
        licenses = []
        cargo_toml = None
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as source:
            for entry in source:
                path = PurePosixPath(entry.name)
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError('Unsafe crate member: ' + entry.name)
                if not entry.isfile():
                    continue
                leaf = path.name.upper()
                if path.name == 'Cargo.toml':
                    cargo_toml = source.extractfile(entry).read()
                if leaf.startswith(('LICENSE', 'COPYING', 'NOTICE')):
                    data = source.extractfile(entry).read()
                    relative = '/'.join(path.parts[1:])
                    material_path = f'licenses/{name}-{version}/{relative}'
                    members[material_path] = data
                    licenses.append({'path': material_path, 'sha256': digest(data)})
        if cargo_toml is None:
            raise ValueError(name + ' has no Cargo.toml')
        manifest = tomllib.loads(cargo_toml.decode('utf-8'))
        license_expression = manifest.get('package', {}).get('license')
        license_file = manifest.get('package', {}).get('license-file')
        if not license_expression and not license_file:
            raise ValueError(name + ' has no Cargo license declaration')
        if license_file and not any(item['path'].endswith('/' + license_file) for item in licenses):
            raise ValueError(name + ' declared license-file is missing')
        rows.append({
            'name': name,
            'version': version,
            'role': role,
            'source': package['source'],
            'cargo_lock_checksum': package['checksum'],
            'download_url': url,
            'archive_path': archive_path,
            'archive_sha256': digest(archive),
            'license_expression': license_expression,
            'license_file': license_file,
            'license_materials': licenses,
        })
    excluded = [{
        'name': name,
        'version': packages[name]['version'],
        'cargo_lock_checksum': packages[name]['checksum'],
        'reason': reason,
    } for name, reason in EXCLUDED.items()]
    index = {
        'identity': 'rollup-4.67.4-linux-x64-glibc-transitive-materials.1',
        'target': 'x86_64-unknown-linux-gnu',
        'rollup_source_commit': '18b5cb60e55926f6d917cc1aeb17fd81d0da5cc6',
        'scope': 'three Task11 open transitive branches; no native binary rebuild',
        'selected': rows,
        'excluded_target_packages': excluded,
        'all_archives_match_fixed_cargo_lock': True,
        'binary_rebuilt': False,
        'complete_corresponding_source': False,
        'product_release': False,
    }
    members['index.json'] = (json.dumps(index, indent=2) + '\n').encode()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(members.items()):
            archive.writestr(zipfile.ZipInfo(name, (2026, 11, 4, 0, 0, 0)), data,
                             compress_type=zipfile.ZIP_DEFLATED)
    print(json.dumps({'path': str(output), 'size': output.stat().st_size,
                      'sha256': digest(output.read_bytes()), 'members': len(members),
                      'selected_packages': len(rows)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    collect(args.lock, args.output)

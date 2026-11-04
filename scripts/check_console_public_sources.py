"""Verify the bounded candidate.3 corresponding-source input and rebuild.

This checker does not approve a product release.  It proves that the reduced
build input excludes cache-content-type 1.0.1 and preserves candidate.3 runtime
bytes; identity metadata is expected to bind the new input separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha(path)
        for path in sorted(root.rglob('*')) if path.is_file()
    }


def main(inputs: Path, frozen: Path, rebuilt: Path, output: Path) -> None:
    with zipfile.ZipFile(inputs) as archive:
        manifest = json.loads(archive.read('input-manifest.json'))
        expected = {row['path']: row for row in manifest['files']}
        if set(archive.namelist()) != set(expected) | {'input-manifest.json'}:
            raise ValueError('Public-source input members differ from manifest')
        for name, row in expected.items():
            data = archive.read(name)
            if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise ValueError('Public-source input checksum differs: ' + name)
        lock = archive.read('source/yarn.lock').decode()
        names = set(archive.namelist())
    if 'cache-content-type@npm:1.0.1' in lock or 'cache-content-type@npm:^1.0.0' in lock:
        raise ValueError('Old cache-content-type lock entry remains')
    if any('cache-content-type-npm-1.0.1-' in name for name in names):
        raise ValueError('Old cache-content-type cache remains')
    required = {
        'source/taki-tools/cache-content-type/index.cjs',
        'source/taki-tools/cache-content-type/LICENSE',
        'source/taki-tools/cache-content-type-upstream/src/index.ts',
        'source/taki-tools/cache-content-type-upstream/LICENSE',
        'source/taki-tools/ylru/src/index.ts',
        'source/taki-tools/ylru/LICENSE',
    }
    if not required.issubset(names):
        raise ValueError('Replacement source material missing')
    frozen_files = files(frozen)
    rebuilt_files = files(rebuilt)
    if set(frozen_files) != set(rebuilt_files):
        raise ValueError('Rebuilt package member set changed')
    changed = sorted(name for name in frozen_files if frozen_files[name] != rebuilt_files[name])
    if changed != ['TAKI-CONSOLE.json']:
        raise ValueError('Unexpected rebuilt package changes: ' + ', '.join(changed))
    runtime = [name for name in frozen_files if name.startswith(('lib/', 'dist/', 'src/'))]
    if len(runtime) != 26 or any(frozen_files[name] != rebuilt_files[name] for name in runtime):
        raise ValueError('Candidate.3 runtime bytes changed')
    frozen_binding = json.loads((frozen / 'TAKI-CONSOLE.json').read_text(encoding='utf-8'))
    rebuilt_binding = json.loads((rebuilt / 'TAKI-CONSOLE.json').read_text(encoding='utf-8'))
    stable = set(frozen_binding) - {'inputs_sha256'}
    if any(frozen_binding[key] != rebuilt_binding[key] for key in stable):
        raise ValueError('Candidate.3 binding changed beyond input digest')
    result = {
        'inputs': {'path': str(inputs), 'size': inputs.stat().st_size, 'sha256': sha(inputs)},
        'input_members': len(expected) + 1,
        'old_cache_content_type_1_0_1': {'lock': False, 'cache': False, 'installed_source': False},
        'replacement_source_paths': sorted(required),
        'package_members': len(frozen_files),
        'runtime_files': len(runtime),
        'runtime_bytes_equal_frozen_candidate3': True,
        'changed_package_files': changed,
        'frozen_inputs_sha256': frozen_binding['inputs_sha256'],
        'rebuilt_inputs_sha256': rebuilt_binding['inputs_sha256'],
        'complete_corresponding_source': False,
        'product_release': False,
    }
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--frozen-package', type=Path, required=True)
    parser.add_argument('--rebuilt-package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    main(args.inputs, args.frozen_package, args.rebuilt_package, args.output)

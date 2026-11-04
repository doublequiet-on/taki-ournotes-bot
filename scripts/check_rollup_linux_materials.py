"""Verify the bounded Rollup Linux x64 glibc transitive material archive."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(materials: Path) -> None:
    with zipfile.ZipFile(materials) as archive:
        member_count = len(archive.namelist())
        if member_count != len(set(archive.namelist())):
            raise ValueError('Duplicate material member')
        index = json.loads(archive.read('index.json'))
        for row in index['selected']:
            data = archive.read(row['archive_path'])
            if sha(data) != row['archive_sha256'] or sha(data) != row['cargo_lock_checksum']:
                raise ValueError(row['name'] + ' archive checksum differs')
            for license_row in row['license_materials']:
                if sha(archive.read(license_row['path'])) != license_row['sha256']:
                    raise ValueError(row['name'] + ' license checksum differs')
        excluded = {row['name'] for row in index['excluded_target_packages']}
        if excluded != {'redox_syscall', 'windows-link'}:
            raise ValueError('Unexpected target exclusion set')
    print(json.dumps({
        'path': str(materials),
        'size': materials.stat().st_size,
        'sha256': sha(materials.read_bytes()),
        'members': member_count,
        'selected_packages': len(index['selected']),
        'license_materials': sum(len(row['license_materials']) for row in index['selected']),
        'target': index['target'],
        'binary_rebuilt': index['binary_rebuilt'],
    }, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--materials', type=Path, required=True)
    args = parser.parse_args()
    main(args.materials)

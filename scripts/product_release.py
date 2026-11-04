"""Offline byte inventory and exact allowlist staging, not semantic trim certification.

Input: explicitly selected tree/archive, reviewed inventory, or fixed Git source list.
Output: JSON inventory/verdict or new staged tree. Never executes candidate code.
Pos: scripts/L2.md; contract and limitations: docs/PRODUCT_RELEASE.md.
"""
from __future__ import annotations

import argparse
import bz2
import gzip
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from product_identity import check_identity, read_json

MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 100000
MAX_DEPTH = 8


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_name(name):
    # Portable paths: no aliases, ADS, drives, separators, reserved device names.
    if not isinstance(name, str) or not name or any(c in name for c in '\\:!*?"<>|'):
        raise ValueError('unsafe_member_path')
    parts = name.split('/')
    if any(p in ('', '.', '..') or p.endswith((' ', '.')) or any(ord(c) < 32 for c in p)
           or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p)
           for p in parts):
        raise ValueError('unsafe_member_path')
    return name


def inventory(path):
    """Recursively hash files, nested ZIP/tar/gzip by magic; never extract to disk."""
    rows = {}
    total = 0

    def member_path(name, is_dir, seen, regular):
        key = safe_name(name).casefold()
        parents = ['/'.join(key.split('/')[:i]) for i in range(1, len(key.split('/')))]
        if key in seen or any(p in regular for p in parents) or (
                not is_dir and any(p.startswith(key + '/') for p in seen)):
            raise ValueError('duplicate_or_conflicting_member')
        seen.add(key)
        if not is_dir:
            regular.add(key)
        if len(seen) > MAX_ENTRIES:
            raise ValueError('inventory_limit')

    def record(name, raw, parent, depth):
        nonlocal total
        total += len(raw)
        if depth > MAX_DEPTH or len(raw) > MAX_FILE or total > MAX_TOTAL or len(rows) >= MAX_ENTRIES:
            raise ValueError('inventory_limit')
        rows[name] = {'size': len(raw), 'sha256': digest(raw), 'parent_sha256': parent}
        # Bound decompression before handing data to tarfile (including tar headers).
        compression = (gzip.GzipFile if raw.startswith(b'\x1f\x8b') else
                       bz2.BZ2File if raw.startswith(b'BZh') else
                       lzma.LZMAFile if raw.startswith(b'\xfd7zXZ\x00') else None)
        if compression:
            stream = (gzip.GzipFile(fileobj=io.BytesIO(raw)) if compression is gzip.GzipFile
                      else compression(io.BytesIO(raw)))
            with stream:
                child = stream.read(MAX_FILE + 1)
            record(name + '!compressed-content', child, digest(raw), depth + 1)
            return
        buf = io.BytesIO(raw)
        if raw.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')) and zipfile.is_zipfile(buf):
            with zipfile.ZipFile(buf) as archive:
                seen, regular = set(), set()
                for item in archive.infolist():
                    safe_name(item.orig_filename.rstrip('/') if item.is_dir() else item.orig_filename)
                    member = safe_name(item.filename.rstrip('/') if item.is_dir() else item.filename)
                    member_path(member, item.is_dir(), seen, regular)
                    mode = item.external_attr >> 16
                    if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR) or item.flag_bits & 1:
                        raise ValueError('unsupported_member')
                    if not item.is_dir():
                        if item.file_size > MAX_FILE or total + item.file_size > MAX_TOTAL:
                            raise ValueError('inventory_limit')
                        with archive.open(item) as stream:
                            child = stream.read(MAX_FILE + 1)
                        record(name + '!' + member, child, digest(raw), depth + 1)
            return
        # Only uncompressed tar; compressed streams were bounded above.
        try:
            archive = tarfile.open(fileobj=io.BytesIO(raw), mode='r:')
        except tarfile.ReadError:
            archive = None
        if archive is not None:
            with archive:
                seen, regular = set(), set()
                for item in archive:
                    member = safe_name(item.name.rstrip('/') if item.isdir() else item.name)
                    member_path(member, item.isdir(), seen, regular)
                    if not (item.isfile() or item.isdir()):
                        raise ValueError('unsupported_member')
                    if item.isfile():
                        if item.size > MAX_FILE or total + item.size > MAX_TOTAL:
                            raise ValueError('inventory_limit')
                        with archive.extractfile(item) as stream:
                            child = stream.read(MAX_FILE + 1)
                        if len(child) != item.size:
                            raise ValueError('truncated_tar_member')
                        record(name + '!' + member, child, digest(raw), depth + 1)
            return
        if name.lower().endswith(('.zip', '.whl', '.tar', '.tgz', '.tar.gz', '.gz', '.tar.xz', '.tar.bz2', '.7z', '.rar')):
            raise ValueError('unreadable_or_unsupported_archive')

    def read_file(p, name):
        info = p.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
            raise ValueError('unsupported_file_or_size')
        record(name, p.read_bytes(), None, 0)

    path = Path(path)
    if path.is_symlink():
        raise ValueError('symlink_root')
    if path.is_dir():
        seen = set()
        for folder, dirs, files in os.walk(path, followlinks=False):
            for leaf in sorted(dirs + files):
                p = Path(folder) / leaf
                name = safe_name(p.relative_to(path).as_posix())
                if name.casefold() in seen or p.is_symlink() or getattr(p.lstat(), 'st_file_attributes', 0) & 0x400:
                    raise ValueError('alias_or_link')
                seen.add(name.casefold())
                if leaf in files:
                    read_file(p, name)
    else:
        read_file(path, safe_name(path.name))
    return {'inventory_version': 1, 'files': dict(sorted(rows.items()))}


def stage(checkout, selection, output):
    """Copy exact committed blobs only; selection is reviewed, not filename heuristics."""
    if set(selection) != {'source_commit', 'files'} or not re.fullmatch('[0-9a-f]{40}', selection['source_commit']):
        raise ValueError('invalid_source_selection')
    files = selection['files']
    if not isinstance(files, dict) or not files:
        raise ValueError('empty_source_selection')
    commit = selection['source_commit']
    tree = subprocess.check_output(['git', 'ls-tree', '-r', '-z', commit], cwd=checkout)
    blobs = {}
    for entry in tree.split(b'\0'):
        if entry:
            meta, name = entry.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            blobs[name.decode('utf-8')] = (mode, kind, oid)
    output = Path(output).absolute()
    if output.exists():
        raise ValueError('stage_output_exists')
    seen = set()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.product-stage-') as temporary:
        root = Path(temporary) / 'payload'
        root.mkdir()
        for name, expected in sorted(files.items()):
            safe_name(name)
            if name.casefold() in seen or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
                raise ValueError('invalid_source_selection')
            seen.add(name.casefold())
            mode, kind, oid = blobs.get(name, ('', '', ''))
            if kind != 'blob' or mode not in ('100644', '100755'):
                raise ValueError('nonregular_or_missing_git_blob')
            raw = subprocess.check_output(['git', 'cat-file', 'blob', oid], cwd=checkout)
            if digest(raw) != expected:
                raise ValueError('source_digest_mismatch')
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(int(mode, 8) & 0o777)
        root.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('inventory')
    scan.add_argument('path', type=Path)
    scan.add_argument('--output', type=Path, required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('path', type=Path)
    verify.add_argument('--inventory', type=Path, required=True)
    verify.add_argument('--inventory-sha256', required=True)
    identity = commands.add_parser('identity')
    for name in ('candidate', 'expected', 'observed'):
        identity.add_argument('--' + name, type=Path, required=True)
    staging = commands.add_parser('stage')
    staging.add_argument('--checkout', type=Path, required=True)
    staging.add_argument('--selection', type=Path, required=True)
    staging.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'inventory':
        result = inventory(args.path)
        # Refuse overwrite, and do not write inventory inside a tree being checked.
        if args.path.is_dir() and args.output.resolve().is_relative_to(args.path.resolve()):
            raise ValueError('inventory_inside_payload')
        with args.output.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        print('inventory_written; semantic_trim_not_certified')
    elif args.command == 'verify':
        if digest(args.inventory.read_bytes()) != args.inventory_sha256:
            raise ValueError('unapproved_inventory')
        if read_json(args.inventory) != inventory(args.path):
            raise ValueError('inventory_mismatch')
        print('byte_inventory_verified; semantic_trim_not_certified')
    elif args.command == 'identity':
        candidate = read_json(args.candidate)
        observed = read_json(args.observed)
        observed['manifest_sha256'] = digest(args.candidate.read_bytes())
        result = check_identity(candidate, read_json(args.expected), observed)
        print(json.dumps(result))
        return 0 if result['status'] == 'allow' else 2
    else:
        stage(args.checkout, read_json(args.selection), args.output)
        print('selected_git_blobs_staged; complete_product_not_certified')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

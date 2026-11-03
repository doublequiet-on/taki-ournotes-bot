"""Derive build-only Console inputs from the frozen reduced kit; never edits it.

Run prepare on Linux, review lock.diff and rollup-metadata.json, then pack.
Only the local Rollup package metadata removes lzma; executable files stay exact.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import zipfile
import difflib

BASE_SHA = '7440ab10e2df8556ec065c17d1aac9f5b46fde199b18bf57a37c1673e1d1a5ef'
ROOT = Path(__file__).resolve().parents[1]

def sha(b):
    return hashlib.sha256(b).hexdigest()

def dump(p, value):
    p.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')

def prepare(kit, work):
    if os.name != 'posix' or work.exists() or not work.is_absolute():
        raise ValueError('Fresh absolute Linux work directory required')
    if sha(kit.read_bytes()) != BASE_SHA:
        raise ValueError('Unexpected base kit')
    work.mkdir(parents=True)
    with zipfile.ZipFile(kit) as z:
        # Exact approved archive; immutable bytes also bind its confined paths.
        z.extractall(work)
    source = work / 'source'
    cache = source / '.yarn/cache'
    rollup = list(cache.glob('rollup-npm-4.67.4-*.zip'))
    if len(rollup) != 1:
        raise ValueError('Expected one Rollup cache')
    target = source / 'taki-tools/rollup'
    target.mkdir(parents=True)
    files = {}
    with zipfile.ZipFile(rollup[0]) as z:
        for n in z.namelist():
            if n.endswith('/'):
                continue
            rel = n.removeprefix('node_modules/rollup/')
            if rel == n or '..' in Path(rel).parts:
                raise ValueError('Unexpected Rollup member')
            data = z.read(n)
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            files[rel] = sha(data)
    p = target / 'package.json'
    modified = json.loads(p.read_text())
    if modified['optionalDependencies'].pop('@napi-rs/lzma-linux-x64-gnu') != '1.5.1':
        raise ValueError('Unexpected lzma edge')
    # No name/version/executable/platform alteration; downstream identity is the
    # portal locator plus before/after hash, not an upstream publication claim.
    dump(p, modified)
    dump(work / 'rollup-metadata.json', dict(identity='taki-rollup-build-metadata.1',
        original_cache=rollup[0].name, original_files=files,
        modified_package_sha256=sha(p.read_bytes()),
        only_semantic_change='remove optionalDependencies.@napi-rs/lzma-linux-x64-gnu=1.5.1'))
    root = source / 'package.json'
    data = json.loads(root.read_text())
    data['resolutions']['rollup'] = 'portal:./taki-tools/rollup'
    dump(root, data)
    with tarfile.open(work / 'tools/node-v24.19.0-linux-x64.tar.xz') as t:
        t.extractall(work, filter='data')
    env = dict(os.environ, PATH=str(work / 'node-v24.19.0-linux-x64/bin') + ':/usr/bin:/bin',
        HOME=str(work / 'home'), YARN_ENABLE_NETWORK='0', YARN_ENABLE_GLOBAL_CACHE='false',
        YARN_ENABLE_TELEMETRY='0', YARN_ENABLE_SCRIPTS='false', YARN_ENABLE_IMMUTABLE_INSTALLS='false')
    before = (source / 'yarn.lock').read_text()
    command = ['node', str(work / 'tools/yarn-4.10.3.cjs'), 'install', '--mode=skip-build']
    with (work / 'relock.log').open('wb') as log:
        result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
    (work / 'relock.exit').write_text(str(result.returncode))
    if result.returncode:
        raise RuntimeError('Relock failed; evidence retained')
    after = (source / 'yarn.lock').read_text()
    (work / 'lock.diff').write_text(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), 'before', 'after')))
    if 'lzma' in after or list(cache.glob('*lzma*')):
        raise ValueError('lzma still in resolved inputs')
    for rel, digest in files.items():
        if rel != 'package.json' and sha((target / rel).read_bytes()) != digest:
            raise ValueError('Rollup executable/license changed')
    # Preferred marked-vue tree, independent from npm's prebuilt module.
    archive = work / 'materials/actual-sources/shigma_marked-vue-86f4fa602bc4ad588661bbddcc0fee5126c295b7.tar.gz'
    with tarfile.open(archive) as t:
        for entry in t:
            if entry.isdir():
                continue
            if not entry.isfile():
                raise ValueError('Nonregular marked-vue source member')
            rel = Path(*Path(entry.name).parts[1:])
            if '..' in rel.parts:
                raise ValueError('Unsafe marked-vue path')
            p = source / 'taki-sources/marked-vue' / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(t.extractfile(entry).read())
    print('Relocked offline; lzma absent; Rollup non-metadata bytes unchanged')

def pack(work, output, candidate):
    if output.exists():
        raise ValueError('Never overwrite frozen material')
    old = json.loads((work / 'manifest.json').read_text())
    members = {}
    for name in old['files']:
        p = work / name
        if p.is_symlink():
            raise ValueError('Linked source input')
        if p.is_file():
            members[name] = p.read_bytes()
        elif not (name.startswith('source/.yarn/cache/') and Path(name).name.startswith(('rollup-npm-4.67.4-', '@napi-rs-lzma-linux-x64-gnu-npm-1.5.1-'))):
            raise ValueError('Missing retained source input: ' + name)
    for area in ('source/taki-tools', 'source/taki-sources/marked-vue', 'source/.yarn/cache'):
        for p in (work / area).rglob('*'):
            if p.is_symlink():
                raise ValueError('Linked build input')
            if p.is_file():
                members[p.relative_to(work).as_posix()] = p.read_bytes()
    # The old cache inventory includes removed entries; the new member manifest
    # indexes every actual retained cache byte instead.
    members.pop('build-cache-index.json', None)
    for name in ('rollup-metadata.json', 'lock.diff', 'relock.log', 'relock.exit'):
        members['materials/build-repair/' + name] = (work / name).read_bytes()
    if candidate:
        members['recipe/downstream-build.cjs'] = (ROOT / 'deploy/licenses/koishi-console/downstream-build.cjs').read_bytes()
    manifest = dict(base_inputs_sha256=BASE_SHA, expected_code=old['expected_code'],
        component_sha256=old['component_sha256'], complete_corresponding_source=False, product_release=False)
    entries = {n: dict(size=len(b), sha256=sha(b)) for n, b in sorted(members.items())}
    manifest['files'] = [dict(path=n, **v) for n, v in entries.items()] if candidate else entries
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, b in sorted(members.items()):
            z.writestr(zipfile.ZipInfo(n, (2026, 11, 4, 0, 0, 0)), b, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('input-manifest.json' if candidate else 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(path=str(output), size=output.stat().st_size, sha256=sha(output.read_bytes()))))

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=['prepare', 'pack'])
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--inputs', type=Path)
    p.add_argument('--output', type=Path)
    p.add_argument('--candidate', action='store_true')
    a = p.parse_args()
    if a.phase == 'prepare' and a.inputs is None:
        p.error('prepare requires --inputs')
    if a.phase == 'pack' and a.output is None:
        p.error('pack requires --output')
    if a.phase == 'prepare':
        prepare(a.inputs, a.work)
    else:
        pack(a.work, a.output, a.candidate)

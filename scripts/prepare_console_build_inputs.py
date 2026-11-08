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
CANDIDATE3_SHA = 'c6261043446d4d717af86a3466733f3f284021dbfd996082464ec202b2a68e0b'
CANDIDATE3_COMBINATION_SHA = '4ce3e43013199145e19e3a13caa089dd7b826fd431c22182192ece182bf9ca32'
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
        for name in ('downstream-build.cjs', 'marked-vue-href.patch'):
            members['recipe/' + name] = (ROOT / 'deploy/licenses/koishi-console' / name).read_bytes()
    manifest = dict(base_inputs_sha256=BASE_SHA, expected_code=old['expected_code'],
        component_sha256=old['component_sha256'], complete_corresponding_source=False, product_release=False)
    entries = {n: dict(size=len(b), sha256=sha(b)) for n, b in sorted(members.items())}
    manifest['files'] = [dict(path=n, **v) for n, v in entries.items()] if candidate else entries
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for n, b in sorted(members.items()):
            z.writestr(zipfile.ZipInfo(n, (2026, 11, 4, 0, 0, 0)), b, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('input-manifest.json' if candidate else 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(path=str(output), size=output.stat().st_size, sha256=sha(output.read_bytes()))))

def public(kit, combination, work, output):
    """Replace the unused 1.0.1 build edge with the reviewed local adapter.

    This does not alter the frozen candidate.3 component.  It derives a new
    corresponding-source candidate whose runtime outputs must be compared with
    candidate.3 before it can be referenced by a release index.
    """
    if os.name != 'posix' or work.exists() or output.exists() or not work.is_absolute():
        raise ValueError('Fresh absolute Linux work/output paths required')
    if sha(kit.read_bytes()) != CANDIDATE3_SHA:
        raise ValueError('Unexpected candidate.3 inputs')
    if sha(combination.read_bytes()) != CANDIDATE3_COMBINATION_SHA:
        raise ValueError('Unexpected candidate.3 combination')
    work.mkdir(parents=True)
    with zipfile.ZipFile(kit) as archive:
        manifest = json.loads(archive.read('input-manifest.json'))
        expected = {row['path']: row for row in manifest['files']}
        if set(archive.namelist()) != set(expected) | {'input-manifest.json'}:
            raise ValueError('Candidate.3 input members differ')
        for name, row in expected.items():
            data = archive.read(name)
            if len(data) != row['size'] or sha(data) != row['sha256']:
                raise ValueError('Candidate.3 input checksum differs: ' + name)
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    source = work / 'source'
    tool_root = source / 'taki-tools'
    adapter = tool_root / 'cache-content-type'
    adapter.mkdir()
    for name in ('index.cjs', 'package.json', 'README.md', 'LICENSE', 'upstream-materials.json'):
        data = (ROOT / 'koishi/compat/cache-content-type' / name).read_bytes()
        (adapter / name).write_bytes(data)
    extracted = {}
    with zipfile.ZipFile(combination) as archive:
        for package in ('cache-content-type-upstream', 'ylru'):
            prefix = 'gateway/node_modules/' + package + '/'
            target = tool_root / package
            for info in archive.infolist():
                if not info.filename.startswith(prefix) or info.is_dir():
                    continue
                rel = Path(info.filename.removeprefix(prefix))
                if '..' in rel.parts:
                    raise ValueError('Unsafe combination member')
                data = archive.read(info)
                path = target / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                extracted[f'{package}/{rel.as_posix()}'] = sha(data)
    for package in ('cache-content-type-upstream', 'ylru'):
        if not (tool_root / package / 'package.json').is_file():
            raise ValueError('Missing installed upstream package: ' + package)
    root = source / 'package.json'
    old_cache = list((source / '.yarn/cache').glob('cache-content-type-npm-1.0.1-*'))
    if len(old_cache) != 1:
        raise ValueError('Expected one frozen 1.0.1 cache before relock')
    old_cache_name = old_cache[0].name
    old_cache_sha = sha(old_cache[0].read_bytes())
    data = json.loads(root.read_text(encoding='utf-8'))
    data['resolutions'].update({
        'cache-content-type': 'portal:./taki-tools/cache-content-type',
        'cache-content-type-upstream': 'portal:./taki-tools/cache-content-type-upstream',
        'mime-types': 'npm:2.1.35',
        'ylru': 'portal:./taki-tools/ylru',
    })
    dump(root, data)
    with tarfile.open(work / 'tools/node-v24.19.0-linux-x64.tar.xz') as archive:
        archive.extractall(work, filter='data')
    env = dict(os.environ, PATH=str(work / 'node-v24.19.0-linux-x64/bin') + ':/usr/bin:/bin',
        HOME=str(work / 'home'), YARN_ENABLE_NETWORK='0', YARN_ENABLE_GLOBAL_CACHE='false',
        YARN_ENABLE_TELEMETRY='0', YARN_ENABLE_SCRIPTS='false', YARN_ENABLE_IMMUTABLE_INSTALLS='false')
    before = (source / 'yarn.lock').read_text(encoding='utf-8')
    command = ['node', str(work / 'tools/yarn-4.10.3.cjs'), 'install', '--mode=skip-build']
    with (work / 'public-relock.log').open('wb') as log:
        result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
    (work / 'public-relock.exit').write_text(str(result.returncode), encoding='ascii')
    if result.returncode:
        raise RuntimeError('Public-source relock failed; evidence retained')
    after = (source / 'yarn.lock').read_text(encoding='utf-8')
    if 'cache-content-type@npm:1.0.1' in after or 'cache-content-type@npm:^1.0.0' in after:
        raise ValueError('Old cache-content-type remains in lock')
    remaining_old_cache = list((source / '.yarn/cache').glob('cache-content-type-npm-1.0.1-*'))
    if len(remaining_old_cache) > 1:
        raise ValueError('Unexpected duplicate 1.0.1 cache after relock')
    if remaining_old_cache:
        remaining_old_cache[0].unlink()
    installed = source / 'node_modules/cache-content-type'
    if not installed.exists() or sha((installed / 'index.cjs').read_bytes()) != sha((adapter / 'index.cjs').read_bytes()):
        raise ValueError('Local adapter was not selected')
    if list((source / 'node_modules').glob('cache-content-type-1*')):
        raise ValueError('Old cache-content-type installation remains')
    (work / 'public-lock.diff').write_text(''.join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), 'candidate3', 'public-source-v2')), encoding='utf-8')
    dump(work / 'public-source-metadata.json', {
        'identity': 'taki-console-candidate3-public-source-v2',
        'candidate3_inputs_sha256': CANDIDATE3_SHA,
        'candidate3_combination_sha256': CANDIDATE3_COMBINATION_SHA,
        'removed_cache': {'name': old_cache_name, 'sha256': old_cache_sha,
                          'removed_by': 'Yarn relock' if not remaining_old_cache else 'bounded pack preparation'},
        'replacement': {
            'adapter_commit_path': 'koishi/compat/cache-content-type',
            'adapter_files': {p.name: sha(p.read_bytes()) for p in sorted(adapter.iterdir()) if p.is_file()},
            'installed_upstream_files': extracted,
        },
        'network': 'disabled for Yarn relock',
        'component_changed': False,
        'complete_corresponding_source': False,
        'product_release': False,
    })
    members = {}
    excluded = 'source/.yarn/cache/' + old_cache_name
    for row in manifest['files']:
        name = row['path']
        if name == excluded:
            continue
        path = work / name
        if path.is_file():
            members[name] = path.read_bytes()
    for area in ('source/taki-tools/cache-content-type', 'source/taki-tools/cache-content-type-upstream',
                 'source/taki-tools/ylru'):
        for path in (work / area).rglob('*'):
            if path.is_file():
                members[path.relative_to(work).as_posix()] = path.read_bytes()
    members['source/package.json'] = root.read_bytes()
    members['source/yarn.lock'] = (source / 'yarn.lock').read_bytes()
    for name in ('public-relock.log', 'public-relock.exit', 'public-lock.diff', 'public-source-metadata.json'):
        members['materials/public-source-v2/' + name] = (work / name).read_bytes()
    new_manifest = dict(
        base_inputs_sha256=CANDIDATE3_SHA,
        component='taki-console',
        component_version='0.1.0-candidate.3',
        purpose='candidate.3 corresponding-source candidate; does not replace frozen component identity',
        complete_corresponding_source=False,
        product_release=False,
        files=[dict(path=name, size=len(blob), sha256=sha(blob)) for name, blob in sorted(members.items())],
    )
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, blob in sorted(members.items()):
            archive.writestr(zipfile.ZipInfo(name, (2026, 11, 4, 0, 0, 0)), blob,
                             compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr('input-manifest.json', json.dumps(new_manifest, indent=2) + '\n')
    print(json.dumps({'path': str(output), 'size': output.stat().st_size,
                      'sha256': sha(output.read_bytes()), 'members': len(members) + 1}))

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=['prepare', 'pack', 'public'])
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--inputs', type=Path)
    p.add_argument('--combination', type=Path)
    p.add_argument('--output', type=Path)
    p.add_argument('--candidate', action='store_true')
    a = p.parse_args()
    if a.phase in ('prepare', 'public') and a.inputs is None:
        p.error(a.phase + ' requires --inputs')
    if a.phase == 'pack' and a.output is None:
        p.error('pack requires --output')
    if a.phase == 'public' and (a.combination is None or a.output is None):
        p.error('public requires --combination and --output')
    if a.phase == 'prepare':
        prepare(a.inputs, a.work)
    elif a.phase == 'pack':
        pack(a.work, a.output, a.candidate)
    else:
        public(a.inputs, a.combination, a.work, a.output)

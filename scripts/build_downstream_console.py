"""Build a pinned, independently identified Console candidate in a new Linux directory.

Input: reviewed input ZIP and adjacent downstream recipe/config/patch in deploy.
Output: deterministic component tarball, source bindings, module graph and logs.
Effects: extracts verified inputs and executes their locked tools; no network,
product installation, service startup or legacy byte patch. See CONSOLE_DOWNSTREAM.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / 'deploy/licenses/koishi-console'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build(inputs: Path, work: Path, output: Path, spec: Path | None = None) -> None:
    config = json.loads((spec or RECIPES / 'downstream-inputs.json').read_text(encoding='utf-8'))
    native = config.get('rollup_native')
    if spec is not None and (
        config['version'] != '0.1.0-candidate.4'
        or config['package_version'] != '5.30.10-taki.4'
        or not isinstance(native, dict)
        or not native
    ):
        raise ValueError('The alternate specification must bind the candidate.4 native tool')
    if os.name != 'posix' or not work.is_absolute() or work.exists() or output.exists():
        raise ValueError('Linux and new absolute work/output directories required')
    if inputs.stat().st_size != config['inputs_size'] or sha(inputs.read_bytes()) != config['inputs_sha256']:
        raise ValueError('Input archive differs from pinned downstream input')
    output.mkdir(parents=True)
    work.mkdir(parents=True)
    logs = output / 'logs'
    logs.mkdir()
    with zipfile.ZipFile(inputs) as archive:
        manifest = json.loads(archive.read('input-manifest.json'))
        expected = {x['path']: x for x in manifest['files']}
        if len(archive.namelist()) != len(expected) + 1 or set(archive.namelist()) != set(expected) | {'input-manifest.json'}:
            raise ValueError('Input member set differs from manifest')
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in info.filename or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe input member')
            data = archive.read(info)
            if info.filename != 'input-manifest.json':
                row = expected[info.filename]
                if len(data) != row['size'] or sha(data) != row['sha256']:
                    raise ValueError('Input file checksum differs')
            path = work.joinpath(*name.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    for recipe in ('downstream-build.cjs', 'taki-reconnect-source.patch', 'marked-vue-href.patch'):
        if (work / 'recipe' / recipe).read_bytes() != (RECIPES / recipe).read_bytes():
            raise ValueError('Recipe differs from pinned build inputs')
    if native:
        if json.loads((work / 'materials/rollup-native-rebuild.json').read_bytes()) != native:
            raise ValueError('Rollup native provenance differs from the specification')
        for recipe, digest in (
            ('rollup-xxhash-base.patch', native['patch_sha256']),
            ('rebuild-rollup-native.sh', native['native_recipe_sha256']),
        ):
            if sha((RECIPES / recipe).read_bytes()) != digest or sha((work / 'recipe' / recipe).read_bytes()) != digest:
                raise ValueError('Rollup native recipe differs from the specification')
    if '@napi-rs/lzma' in (work / 'source/yarn.lock').read_text(encoding='utf-8'):
        raise ValueError('Excluded build-only lzma dependency returned')
    # The pinned official Node tool archive has internal bin/npm links. Python's
    # data filter confines them to this isolated tools directory, not a product.
    with tarfile.open(work / 'tools/node-v24.19.0-linux-x64.tar.xz') as archive:
        archive.extractall(work, filter='data')
    home = work / 'home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home), PATH=str(work / 'node-v24.19.0-linux-x64/bin') + ':/usr/bin:/bin',
               YARN_ENABLE_NETWORK='0', YARN_ENABLE_GLOBAL_CACHE='false', YARN_ENABLE_TELEMETRY='0',
               YARN_ENABLE_IMMUTABLE_INSTALLS='true', YARN_ENABLE_SCRIPTS='false')
    source = work / 'source'
    yarn = ['node', str(work / 'tools/yarn-4.10.3.cjs')]

    def run(label: str, args: list[str]) -> None:
        with (logs / (label + '.log')).open('wb') as handle:
            result = subprocess.run(args, cwd=source, env=env, stdout=handle, stderr=subprocess.STDOUT)
        (logs / (label + '.exit')).write_text(str(result.returncode), encoding='ascii')
        if result.returncode:
            raise RuntimeError(f'{label} failed; logs retained at {logs}')

    run('node-version', ['node', '--version'])
    run('yarn-version', yarn + ['--version'])
    dump(logs / 'environment.json', dict(system=list(os.uname()), node=config['node'], yarn=config['yarn'],
                                       network='Yarn disabled; not OS network isolation'))
    run('install', yarn + ['install', '--immutable', '--immutable-cache', '--mode=skip-build'])
    if list((source / 'node_modules/@napi-rs').glob('lzma*')):
        raise ValueError('Excluded lzma installed')
    if native:
        module = 'node_modules/@rollup/rollup-linux-x64-gnu/rollup.linux-x64-gnu.node'
        data = (source / module).read_bytes()
        if native['installed_binary'] != dict(path=module, size=len(data), sha256=sha(data)):
            raise ValueError('Installed Rollup native tool differs from the specification')
        run('rollup-native-probe', ['node', '-e',
            "require('rollup'); const expected = require('node:fs').realpathSync(process.argv[1]); "
            "if (!require.cache[expected]) throw Error('Rollup did not load the bound native tool'); "
            "console.log(expected);", module])
    # Stable component build stages; do not invoke the upstream broken client dispatcher.
    run('types', yarn + ['yakumo', '--import', 'tsx', 'tsc'])
    run('lib', yarn + ['yakumo', '--import', 'tsx', 'esbuild'])
    reconnect_source = source / 'packages/client/client/data.ts'
    before = sha(reconnect_source.read_bytes())
    if before != '084f9b14d322cddb6e4cfbefe84a7d251e04bb0ccfc8970afab816acb89eb376':
        raise ValueError('Unexpected reconnect source; refusing repeated patch')
    patch = RECIPES / 'taki-reconnect-source.patch'
    run('reconnect-source-patch', ['git', '-c', 'core.autocrlf=false', 'apply', '--unidiff-zero', str(patch)])
    if sha(reconnect_source.read_bytes()) != 'cf21a5e49c75deeda40a064e9dba2f65933a97c9357beda743337dee4be60e49':
        raise ValueError('Reconnect source result differs')
    shutil.copyfile(RECIPES / 'downstream-build.cjs', source / 'taki-build.cjs')
    run('client', ['node', 'taki-build.cjs'])
    shutil.copytree(source / 'taki-evidence', output / 'module-evidence')
    shutil.copytree(source / 'taki-generated', output / 'generated-sources')
    emitted = []
    for graph in (source / 'taki-evidence').glob('graph-*.json'):
        for item in json.loads(graph.read_text(encoding='utf-8'))['outputs'].values():
            emitted.extend(item.get('modules', []))
    if any('node_modules/lodash-unified/' in name for name in emitted):
        raise ValueError('Original lodash-unified wrapper entered emitted chunks')
    payload = output / 'package'
    payload.mkdir()
    plugin = source / 'plugins/console'
    for directory in ('lib', 'dist', 'src'):
        shutil.copytree(plugin / directory, payload / directory)
    package = json.loads((plugin / 'package.json').read_text(encoding='utf-8'))
    package['version'] = config['package_version']
    package['description'] += ' — Taki downstream candidate (not upstream npm release)'
    package.pop('devDependencies', None)
    package['files'] = ['lib', 'dist', 'src', 'licenses', 'LICENSE', 'TAKI-CONSOLE.json', 'TAKI-MODIFICATIONS.txt']
    dump(payload / 'package.json', package)
    shutil.copyfile(RECIPES / 'LICENSE', payload / 'LICENSE')
    licenses = payload / 'licenses'
    licenses.mkdir()
    shutil.copyfile(work / 'materials/Taki-LICENSE', licenses / 'Taki-LICENSE')
    materials = json.loads((work / 'materials/component-materials.json').read_text(encoding='utf-8'))
    license_index = []
    for component in materials:
        key = component['name'].replace('/', '_') + '-' + component['version']
        for number, entry in enumerate(component['licenses']):
            data = base64.b64decode(entry['content_base64'])
            if sha(data) != entry['sha256']:
                raise ValueError('License text checksum differs')
            name = key + '/' + str(number) + '-' + PurePosixPath(entry['path']).name
            dest = licenses / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            license_index.append(dict(component=component['name'], version=component['version'],
                                      origin=entry['origin'], original_path=entry['path'], path=name, sha256=sha(data)))
    for label in ('element-plus', 'devtools', 'marked-vue'):
        data = (source / 'taki-sources' / label / 'LICENSE').read_bytes()
        dest = licenses / (label + '-source-LICENSE')
        dest.write_bytes(data)
        license_index.append(dict(component=label, origin='pinned source root', path=dest.name, sha256=sha(data)))
    dump(licenses / 'index.json', license_index)
    (licenses / 'NOTICE.txt').write_text(
        'Original license and notice texts are retained with their original paths in index.json.\n'
        'Root/source-tree notices do not relicense every nested component. Source archives and package metadata accompany the build inputs.\n'
        'lodash-unified implementation is excluded from emitted chunks. The generated Taki adapter uses lodash-es named exports.\n'
        'This candidate has external server dependencies; their final installation tree requires separate product review.\n', encoding='utf-8')
    binding = dict(component=config['component'], version=config['version'], package_version=config['package_version'],
                   upstream_commit=config['upstream_commit'], inputs_sha256=config['inputs_sha256'],
                   recipe_sha256=sha((RECIPES / 'downstream-build.cjs').read_bytes()),
                   builder_sha256=sha(Path(__file__).read_bytes()), patch_sha256=sha(patch.read_bytes()),
                   markdown_security_patch_sha256=sha((RECIPES / 'marked-vue-href.patch').read_bytes()),
                   reconnect_source_before=before, reconnect_source_after=sha(reconnect_source.read_bytes()),
                   legacy_byte_patch='forbidden; source patch already applied',
                   source_download={'relative_path': 'sources/{component_sha256}/source-materials.zip', 'published': False},
                   complete_corresponding_source=False, product_release=False)
    if native:
        binding['rollup_native'] = native
    dump(payload / 'TAKI-CONSOLE.json', binding)
    rollup_note = (
        'Build-only Rollup excludes unused lzma and uses the bound downstream Linux native tool.'
        if native else 'Build-only Rollup metadata excludes unused lzma; executable tool bytes are unchanged.'
    )
    (payload / 'TAKI-MODIFICATIONS.txt').write_text(
        'Taki downstream Console candidate; 2026-11-04. See TAKI-CONSOLE.json.\n'
        'Reconnect URL preservation is compiled from source exactly once.\n'
        'Cordis uses npm TS; devtools/Element Plus use pinned source; lodash-unified browser imports use a new Taki adapter generated from lodash-es public exports.\n'
        'marked-vue fixed TS and exact npm reggol 1.7.1 TS generate ESM with explicit externals, keepNames and no type edits.\n'
        f'{rollup_note}\n'
        'marked-vue href: decode HTML attribute once, validate unchanged protocols, encode ampersands/quotes before HTML output.\n'
        'Do not apply the upstream-npm byte patch. Not a final product release.\n', encoding='utf-8')
    files = [dict(path=p.relative_to(payload).as_posix(), size=p.stat().st_size, sha256=sha(p.read_bytes()))
             for p in sorted(payload.rglob('*')) if p.is_file()]
    dump(output / 'component-manifest.json', dict(**binding, files=files))
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for row in files:
            info = tarfile.TarInfo('package/' + row['path'])
            info.size = row['size']
            info.mode = 0o644
            archive.addfile(info, io.BytesIO((payload / row['path']).read_bytes()))
    artifact = output / f"taki-console-{config['version']}.tgz"
    artifact.write_bytes(gzip.compress(raw.getvalue(), mtime=0))
    dump(output / 'receipt.json', dict(file=artifact.name, size=artifact.stat().st_size, sha256=sha(artifact.read_bytes()),
                                      manifest_sha256=sha((output / 'component-manifest.json').read_bytes())))
    print((output / 'receipt.json').read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--spec', type=Path, help='fixed alternate candidate.4 input specification')
    args = parser.parse_args()
    build(args.inputs.resolve(), args.work, args.output.resolve(), args.spec)

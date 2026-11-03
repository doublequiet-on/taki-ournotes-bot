"""Rebuild lib/dist from the reduced source kit; does not mint a component identity.

Requires an externally approved kit SHA256. Checks every member, installs from
the frozen Yarn cache, runs types/lib/source-patch/client, then compares code.
The original component's TAKI-CONSOLE identity is never rewritten by this tool.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rebuild(kit, expected_sha, work, output):
    if os.name != 'posix' or not work.is_absolute() or work.exists() or output.exists():
        raise ValueError('New absolute Linux work and output directories required')
    if sha(kit.read_bytes()) != expected_sha:
        raise ValueError('Unapproved source kit')
    with zipfile.ZipFile(kit) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        files = manifest['files']
        if len(archive.namelist()) != len(files) + 1 or set(archive.namelist()) != set(files) | {'manifest.json'}:
            raise ValueError('Source kit member set')
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe source kit member')
            data = archive.read(info)
            if info.filename != 'manifest.json' and (sha(data) != files[info.filename]['sha256'] or len(data) != files[info.filename]['size']):
                raise ValueError('Source kit byte mismatch')
        work.mkdir(parents=True)
        archive.extractall(work)
    output.mkdir(parents=True)
    with tarfile.open(work / 'tools/node-v24.19.0-linux-x64.tar.xz') as archive:
        archive.extractall(work, filter='data')  # only tool's confined internal npm links
    home = work / 'home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home), PATH=str(work / 'node-v24.19.0-linux-x64/bin') + ':/usr/bin:/bin',
               YARN_ENABLE_NETWORK='0', YARN_ENABLE_GLOBAL_CACHE='false', YARN_ENABLE_TELEMETRY='0',
               YARN_ENABLE_IMMUTABLE_INSTALLS='true', YARN_ENABLE_SCRIPTS='false')
    source = work / 'source'
    yarn = ['node', str(work / 'tools/yarn-4.10.3.cjs')]
    shutil.copyfile(work / 'recipe/downstream-build.cjs', source / 'taki-build.cjs')
    commands = [
        ('install', yarn + ['install', '--immutable', '--immutable-cache', '--mode=skip-build']),
        ('types', yarn + ['yakumo', '--import', 'tsx', 'tsc']),
        ('lib', yarn + ['yakumo', '--import', 'tsx', 'esbuild']),
        ('patch', ['git', '-c', 'core.autocrlf=false', 'apply', '--unidiff-zero', str(work / 'recipe/taki-reconnect-source.patch')]),
        ('client', ['node', 'taki-build.cjs']),
    ]
    for label, args in commands:
        with (output / (label + '.log')).open('wb') as handle:
            result = subprocess.run(args, cwd=source, env=env, stdout=handle, stderr=subprocess.STDOUT)
        (output / (label + '.exit')).write_text(str(result.returncode))
        if result.returncode:
            raise RuntimeError(label + ' failed; log retained')
    plugin = source / 'plugins/console'
    actual = {p.relative_to(plugin).as_posix(): sha(p.read_bytes())
              for area in ('lib', 'dist') for p in (plugin / area).rglob('*') if p.is_file()}
    result = dict(component_sha256=manifest['component_sha256'], inputs_sha256=expected_sha,
                  code_equal=actual == manifest['expected_code'], files=actual,
                  component_repackaged=False, complete_corresponding_source=False, product_release=False)
    (output / 'comparison.json').write_text(json.dumps(result, indent=2) + '\n')
    shutil.copytree(source / 'taki-evidence', output / 'generation-evidence')
    if not result['code_equal']:
        raise ValueError('Rebuilt code differs; new component requires separate integration acceptance')
    print(json.dumps(result))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs', type=Path, required=True)
    p.add_argument('--inputs-sha256', required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    rebuild(a.inputs.resolve(), a.inputs_sha256, a.work, a.output.resolve())

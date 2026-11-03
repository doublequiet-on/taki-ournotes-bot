"""Verify an extracted combination offline, including the installed gateway probe."""
import argparse
import hashlib
import json
import runpy
from pathlib import Path
import subprocess
import sys

try:
    from scripts.build_koishi_release import LICENSE_SHA256
    from scripts.console_release import verify_downstream
except ModuleNotFoundError:
    from build_koishi_release import LICENSE_SHA256
    from console_release import verify_downstream


def verify(root, expected_commit):
    root=root.resolve()
    manifest=json.loads((root/'combination.json').read_text(encoding='utf-8'))
    if manifest['schema']!=1 or manifest['commit']!=expected_commit: raise ValueError('Combination identity')
    for name, expected in LICENSE_SHA256.items():
        if hashlib.sha256((root / 'licenses' / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Release license mismatch')
    # The same strict pairing checked by the trusted controller, before runtime probes.
    # Do not import from the payload: normal imports can create an unlisted .pyc.
    controller=runpy.run_path(str(root/'controller/koishi_control.py'))
    controller['verify_combination'](root,expected_commit)
    listed=set(manifest['files'])
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix()!='combination.json'}
    if actual!=listed or any(p.is_symlink() for p in root.rglob('*')): raise ValueError('Unlisted or missing payload')
    for name,digest in manifest['files'].items():
        p=root/name
        if p.is_symlink() or not p.resolve().is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise ValueError('Combination payload mismatch')
    artifact=root/manifest['python_artifact']
    component=manifest.get('console_component')
    package_path=root/'gateway/node_modules/@koishijs/plugin-console/package.json'
    package_version=json.loads(package_path.read_text())['version'] if package_path.exists() else None
    if package_version and package_version not in ('5.30.10-taki.1','5.30.11'):
        raise ValueError('Unknown installed Console version')
    if package_version=='5.30.10-taki.1' or (component and component.get('kind')=='taki-downstream'):
        if component!=verify_downstream(root/'gateway'):
            raise ValueError('Console component identity')
        materials=json.loads((root/'licenses/npm-installation.json').read_text())
        notices=json.loads((root/'licenses/koishi-console/gateway-notices.json').read_text())
        if notices['lock_canonical_sha256']!=component['lock_canonical_sha256']:
            raise ValueError('Gateway notice lock mismatch')
        for package in notices['packages']:
            for notice in package.get('original_texts',[]):
                if hashlib.sha256(notice['text'].encode()).hexdigest()!=notice['sha256']:
                    raise ValueError('Gateway original notice mismatch')
        for package in materials['packages']:
            for name,digest in package['files'].items():
                if hashlib.sha256((root/'gateway'/package['location']/name).read_bytes()).hexdigest()!=digest:
                    raise ValueError('Installed npm material mismatch')
    if hashlib.sha256(artifact.read_bytes()).hexdigest()!=manifest['python_sha256']: raise ValueError('Python digest')
    import zipfile
    with zipfile.ZipFile(artifact) as archive:
        if json.loads(archive.read('manifest.json'))['commit']!=expected_commit: raise ValueError('Python commit')
    node=root/'bin'/('node.exe' if manifest['node']['platform']=='win32' else 'node')
    identity=json.loads(subprocess.check_output([str(node),'-e','console.log(JSON.stringify({version:process.version,platform:process.platform,arch:process.arch}))'],text=True))
    if identity!=manifest['node']: raise ValueError('Runtime identity')
    subprocess.run([str(node),'scripts/probe.cjs'],cwd=root/'gateway',check=True,stdout=subprocess.DEVNULL)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('commit')
    args=p.parse_args();verify(args.root,args.commit);print('Offline combination verified')

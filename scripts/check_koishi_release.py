"""Verify an extracted combination offline, including the installed gateway probe."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def verify(root, expected_commit):
    root=root.resolve()
    manifest=json.loads((root/'combination.json').read_text(encoding='utf-8'))
    if manifest['schema']!=1 or manifest['commit']!=expected_commit: raise ValueError('Combination identity')
    listed=set(manifest['files'])
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix()!='combination.json'}
    if actual!=listed or any(p.is_symlink() for p in root.rglob('*')): raise ValueError('Unlisted or missing payload')
    for name,digest in manifest['files'].items():
        p=root/name
        if p.is_symlink() or not p.resolve().is_relative_to(root) or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise ValueError('Combination payload mismatch')
    artifact=root/manifest['python_artifact']
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

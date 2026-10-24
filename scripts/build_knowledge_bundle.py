"""L3: Package reviewed Linux knowledge wheels without network or installation.

Input: application commit, exact hash lock, existing wheels. Output: a deterministic
commit-bound sidecar. Does not contain knowledge content, credentials or state.
"""
from pathlib import Path
import argparse
import json
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.offline_bundle import dependency_lock, digest, inspect_dependency_bundle, TARGET


def build(commit: str, lock: Path, wheelhouse: Path, output: Path):
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('full_application_commit_required')
    entries = dependency_lock(lock.read_text(encoding='utf-8'))
    found = {}
    for p in sorted(wheelhouse.glob('*.whl')):
        if p.is_symlink():
            raise ValueError('wheel_symlink')
        parts = p.name.split('-'); key = re.sub(r'[-_.]+','-',parts[0]).lower()
        if key in entries and len(parts)>=5 and (parts[1],digest(p)) == entries[key]:
            if key in found:
                raise ValueError('duplicate_locked_wheel')
            found[key] = p
    if set(found) != set(entries):
        raise ValueError('locked_wheel_missing')
    files = {'requirements.lock':lock}
    files.update({'wheelhouse/'+p.name:p for p in found.values()})
    manifest = {'version':1,'profile':'knowledge','commit':commit,'target':TARGET,
                'roots':{k:entries[k][0] for k in ('onnxruntime','tokenizers','numpy')},
                'files':{name:digest(p) for name,p in files.items()}}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as stream, zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
        contents={name:p.read_bytes() for name,p in files.items()}
        contents['manifest.json']=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
        for name,raw in sorted(contents.items()):
            item=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));item.external_attr=0o100644<<16
            item.compress_type=zipfile.ZIP_DEFLATED;z.writestr(item,raw)
    sha=digest(output)
    inspect_dependency_bundle(output,commit,sha)
    output.with_suffix('.sha256').write_text(sha+'\n',encoding='ascii')
    return {'commit':commit,'path':str(output),'sha256':sha,'wheels':len(found)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--commit',required=True)
    p.add_argument('--lock',type=Path,default=ROOT/'deploy/linux-knowledge-cp312.lock')
    p.add_argument('--wheelhouse',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(build(a.commit,a.lock,a.wheelhouse,a.output)))

"""Build a pinned, offline two-process combination; never installs or starts services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
KOISHI_CLIENT_SHA256 = '55a68e89eda7e3fe59305b09e6c4de19481a8d7aa5df97579822c819ebf3ccaf'
KOISHI_RELOAD_OLD = b'p2(e,t).then(location.reload,'
KOISHI_RELOAD_NEW = b'p2(e,t).then(()=>location.reload(),'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_koishi_client(path):
    """Bind Koishi 5.30.11's reconnect reload; reject unknown upstream assets."""
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != KOISHI_CLIENT_SHA256 or data.count(KOISHI_RELOAD_OLD) != 1:
        raise ValueError('Koishi Console client differs from reviewed 5.30.11 asset')
    path.write_bytes(data.replace(KOISHI_RELOAD_OLD, KOISHI_RELOAD_NEW, 1))


def build(python_artifact, node, output):
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT):
        raise ValueError('Combination requires committed tracked source')
    if subprocess.check_output(['git','ls-files','--others','--exclude-standard','--','koishi/src','koishi/scripts','koishi/console'],cwd=ROOT):
        raise ValueError('Untracked gateway sources')
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if python_artifact.suffix == '.zip':
        with zipfile.ZipFile(python_artifact) as archive:
            if json.loads(archive.read('manifest.json'))['commit'] != commit:
                raise ValueError('Python bundle commit mismatch')
    else:
        raise ValueError('Use the existing verified Python offline zip')
    identity = json.loads(subprocess.check_output([str(node),'-e',
        'console.log(JSON.stringify({version:process.version,platform:process.platform,arch:process.arch}))'],text=True))
    if identity['version'] != 'v24.19.0': raise ValueError('Unexpected Node version')
    with tempfile.TemporaryDirectory(prefix='taki-combination-') as folder:
        stage=Path(folder)
        gateway=stage/'gateway';gateway.mkdir()
        for name in ('scripts','node_modules','console'):
            shutil.copytree(ROOT/'koishi'/name,gateway/name,ignore=shutil.ignore_patterns('.bin','*.tsbuildinfo'))
        patch_koishi_client(gateway/'node_modules/@koishijs/plugin-console/dist/client.js')
        for name in ('package.json','package-lock.json','run.cjs','console.cjs','health.cjs','operation-journal.cjs','ops-host.cjs','ops-controls.cjs','trusted-actions.cjs'):
            shutil.copy2(ROOT/'koishi'/name,gateway/name)
        subprocess.run([str(node),'node_modules/typescript/bin/tsc','-p','tsconfig.json','--outDir',str(gateway/'lib')],cwd=ROOT/'koishi',check=True)
        subprocess.run([str(node),'scripts/probe.cjs'],cwd=gateway,check=True,stdout=subprocess.DEVNULL)
        binary=stage/'bin'/('node.exe' if identity['platform']=='win32' else 'node')
        binary.parent.mkdir();shutil.copy2(node,binary)
        python=stage/'python'/python_artifact.name;python.parent.mkdir();shutil.copy2(python_artifact,python)
        recovery=stage/'controller';recovery.mkdir()
        for name in ('koishi_control.py','koishi_launcher.py','group_policy_guard.py','linux_backend.py','linux_updater.py','offline_bundle.py','ops_snapshot.py','trusted_actions.py'):
            shutil.copy2(ROOT/'deploy'/name,recovery/name)
        shutil.copytree(ROOT/'deploy/koishi',recovery/'service-templates')
        shutil.copy2(ROOT/'docs/KOISHI_MIGRATION_M4.md',stage/'RUNBOOK.md')
        rollback=stage/'controller-rollback';rollback.mkdir()
        rollback_ref='0359a8d515db5cd32a66617852edb29b25862580'
        for name in ('linux_backend.py','linux_updater.py','offline_bundle.py'):
            (rollback/name).write_bytes(subprocess.check_output(['git','show',rollback_ref+':deploy/'+name],cwd=ROOT))
        files={p.relative_to(stage).as_posix():sha(p) for p in sorted(stage.rglob('*')) if p.is_file()}
        application=ROOT/'src/ournotes_bot/application'
        import runpy
        inventory=runpy.run_path(str(application/'backend_sources.py'))
        code_files=inventory['CODE_FILES']
        manifest={'schema':1,'commit':commit,'controller_rollback_commit':rollback_ref,'node':identity,'lock_sha256':sha(gateway/'package-lock.json'),
                  'python_artifact':python.relative_to(stage).as_posix(),'python_sha256':sha(python),
                  'backend_code_digest':hashlib.sha256(b''.join((application/n).read_bytes() for n in code_files)).hexdigest(),
                  'protocol':inventory['TRANSPORT_PROTOCOL'],'capabilities':['group_policies_v1','query_chat_contract_v1'],
                  'data_schema':1,'rollback':'code-only; freeze settings before unsupported policy runtimes',
                  'files':files}
        (stage/'combination.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for p in sorted(stage.rglob('*')):
                if p.is_symlink(): raise ValueError('Symlinks are not release payloads')
                if p.is_file():
                    info=zipfile.ZipInfo(p.relative_to(stage).as_posix(),date_time=(2026,1,1,0,0,0))
                    info.external_attr=(0o100755 if p==binary else 0o100644)<<16
                    archive.writestr(info,p.read_bytes(),compress_type=zipfile.ZIP_DEFLATED)
        output.with_suffix(output.suffix+'.sha256').write_text(sha(output)+'\n')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-artifact',required=True,type=Path)
    parser.add_argument('--node',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps({k:v for k,v in build(args.python_artifact,args.node,args.output).items() if k!='files'}))

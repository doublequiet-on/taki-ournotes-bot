"""Build a pinned, offline two-process combination; never installs or starts services."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import runpy
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

try:
    from scripts.console_release import (PACKAGE_VERSION, SOURCE_METADATA, VENDOR_FILES,
                                         installed_npm_materials, verify_downstream, verify_downstream_materials,
                                         verify_source_vendor)
except ModuleNotFoundError:
    from console_release import (PACKAGE_VERSION, SOURCE_METADATA, VENDOR_FILES,
                                 installed_npm_materials, verify_downstream, verify_downstream_materials,
                                 verify_source_vendor)

ROOT = Path(__file__).resolve().parents[1]
APPLICATION = ROOT / 'src/ournotes_bot/application'
RECOVERY_PROTOCOL_SOURCE = 'a6e102a5b94be6af65652985fe0063491352cbf5'
RECOVERY_HOST_SOURCE = '29c4230f07364899b11a55ad03d07654238e1731'
RECOVERY_HOST_SOURCE_ONLY = ('host_authority.py', 'isolated_runtime.py', 'recovery_matrix.py',
                             'recovery_preflight.py', 'recovery_session.py', 'recovery_session.cjs',
                             'storage_observation.py')
RECOVERY_LAUNCHER_FILES = ('koishi_launcher.py', 'instance_install.py', 'instance_preflight.py')
RECOVERY_AUTHORITY_FILES = ('product_identity.py', 'recovery_authority.py')
KOISHI_CLIENT_SHA256 = '55a68e89eda7e3fe59305b09e6c4de19481a8d7aa5df97579822c819ebf3ccaf'
LICENSE_SHA256 = {
    'node/LICENSE': '148eacf7863ef4329224a29398623077200a27194aa075569faf4a0a85566ca5',
    'koishi-console/LICENSE': '8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef',
}
KOISHI_RECONNECT_PATCHES = (
    (b'function p2(e,t){', b'function p2(e,t,takiReconnectUrl){'),
    (b'o=()=>{cr.value=null;for(const a in ot)ot[a]=void 0;',
     b'o=()=>{takiReconnectUrl??=location.href;cr.value=null;for(const a in ot)ot[a]=void 0;'),
    (b'p2(e,t).then(location.reload,',
     b'p2(e,t,takiReconnectUrl).then(()=>location.replace(takiReconnectUrl),'),
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure_recovery_entries(python_bundle, gateway, commit):
    """Measure actual release entries and the installed CODE_FILES inventory."""
    inventory = runpy.run_path(str(APPLICATION / 'backend_sources.py'))
    code_files = inventory['CODE_FILES']
    python_entry = 'recovery_entry.py'
    if python_entry not in code_files:
        raise ValueError('Recovery Python entry missing from CODE_FILES')
    wheel_names = [posixpath.normpath('ournotes_bot/application/' + name) for name in code_files]
    source = {}
    for name in ('run.cjs', 'recovery.cjs'):
        path = gateway / name
        if not path.is_file():
            raise ValueError('Missing packaged gateway recovery entry: ' + name)
        source['koishi/' + name] = path.read_bytes()
    controller = gateway.parent / 'controller'
    for name in (*RECOVERY_AUTHORITY_FILES, *RECOVERY_LAUNCHER_FILES):
        path = controller / name
        if not path.is_file():
            raise ValueError('Missing packaged controller entry: ' + name)
        data = path.read_bytes()
        if data != (ROOT / 'deploy' / name).read_bytes():
            raise ValueError('Different packaged controller entry: ' + name)
        source['deploy/' + name] = data
    for name in RECOVERY_HOST_SOURCE_ONLY:
        if (controller / name).exists():
            raise ValueError('Unexpected recovery host in controller payload: ' + name)
        source['deploy/' + name] = (ROOT / 'deploy' / name).read_bytes()
    compiled = gateway / 'lib/recovery-scope.js'
    if not compiled.is_file():
        raise ValueError('Missing compiled recovery scope entry')
    with zipfile.ZipFile(python_bundle) as bundle:
        wheels = [n for n in bundle.namelist()
                  if n.startswith('wheelhouse/taki_ournotes_bot-') and n.endswith('.whl')]
        if len(wheels) != 1:
            raise ValueError('Expected one paired application wheel')
        with zipfile.ZipFile(io.BytesIO(bundle.read(wheels[0]))) as wheel:
            members = wheel.namelist()
            if any(members.count(name) != 1 for name in wheel_names):
                raise ValueError('Missing or duplicate installed CODE_FILES member')
            installed = [wheel.read(name) for name in wheel_names]
        python_bytes = installed[code_files.index(python_entry)]
        source['src/ournotes_bot/application/' + python_entry] = python_bytes
        prefix = 'taki-ournotes-bot-' + commit + '/'
        expected_source = {prefix + name: data for name, data in source.items()}
        seen = set()
        with bundle.open('source.tar.gz') as stream, tarfile.open(fileobj=stream, mode='r|gz') as archive:
            for member in archive:
                if member.name in expected_source:
                    if (member.name in seen or not member.isfile()
                            or archive.extractfile(member).read() != expected_source[member.name]):
                        raise ValueError('Recovery entry differs from paired source: ' + member.name)
                    seen.add(member.name)
        if seen != expected_source.keys():
            raise ValueError('Recovery entry missing in paired source archive')
    backend_digest = hashlib.sha256(b''.join(installed)).hexdigest()
    if backend_digest != hashlib.sha256(b''.join((APPLICATION / n).read_bytes() for n in code_files)).hexdigest():
        raise ValueError('Installed CODE_FILES bytes differ from fixed backend source')
    return backend_digest, {
        'gateway_scope': {'path': 'gateway/lib/recovery-scope.js', 'export': 'createRecoveryScope',
                          'sha256': sha(compiled)},
        'authority_files': {'controller/' + name: hashlib.sha256(source['deploy/' + name]).hexdigest()
                            for name in RECOVERY_AUTHORITY_FILES},
        'gateway': {'path': 'gateway/run.cjs', 'export': 'startRecovery',
                    'sha256': hashlib.sha256(source['koishi/run.cjs']).hexdigest()},
        'gateway_lifecycle': {'path': 'gateway/recovery.cjs', 'export': 'serveRecoveryGateway',
                              'sha256': hashlib.sha256(source['koishi/recovery.cjs']).hexdigest()},
        'python': {'wheel_member': 'ournotes_bot/application/recovery_entry.py',
                   'export': 'serve_recovery_backend', 'sha256': hashlib.sha256(python_bytes).hexdigest()},
        'protocol_source_commit': RECOVERY_PROTOCOL_SOURCE,
        'host_source_commit': RECOVERY_HOST_SOURCE,
        'host_source_only': {'deploy/' + name: hashlib.sha256(source['deploy/' + name]).hexdigest()
                             for name in RECOVERY_HOST_SOURCE_ONLY},
        'launcher_files': {'controller/' + name: hashlib.sha256(source['deploy/' + name]).hexdigest()
                           for name in RECOVERY_LAUNCHER_FILES},
        'host_bundled': False,
    }


def patch_koishi_client(path):
    """Keep the first disconnect URL through Koishi's page disposal and retries."""
    data = path.read_bytes()
    if (hashlib.sha256(data).hexdigest() != KOISHI_CLIENT_SHA256
            or any(data.count(old) != 1 for old, _ in KOISHI_RECONNECT_PATCHES)):
        raise ValueError('Koishi Console client differs from reviewed 5.30.11 asset')
    for old, new in KOISHI_RECONNECT_PATCHES:
        data = data.replace(old, new, 1)
    path.write_bytes(data)


def copy_release_materials(stage, downstream=False):
    """Preserve full pinned license texts and the exact downstream patch recipe."""
    for name, expected in LICENSE_SHA256.items():
        source = ROOT / 'deploy/licenses' / name
        if sha(source) != expected:
            raise ValueError('Release license differs from reviewed upstream bytes')
        target = stage / 'licenses' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(ROOT / 'deploy/licenses/SOURCES.md', stage / 'licenses/SOURCES.md')
    shutil.copy2(ROOT / 'docs/RELEASE_MATERIALS.md', stage / 'licenses/RELEASE_MATERIALS.md')
    shutil.copy2(ROOT / 'LICENSE', stage / 'licenses/TAKI-LICENSE')
    shutil.copy2(ROOT / 'THIRD_PARTY.md', stage / 'licenses/THIRD_PARTY.md')
    if downstream:
        sources = ROOT / 'deploy/licenses/koishi-console'
        verify_downstream_materials(sources)
        for name in SOURCE_METADATA:
            shutil.copy2(sources / name, stage / 'licenses/koishi-console' / name)
        shutil.copy2(ROOT / 'docs/CONSOLE_DOWNSTREAM.md', stage / 'licenses/koishi-console/CONSOLE_DOWNSTREAM.md')
        shutil.copy2(ROOT / 'deploy/licenses/koishi-console/gateway-notices.json', stage / 'licenses/koishi-console/gateway-notices.json')
        return
    patch = {'upstream_version': '5.30.11', 'upstream_git_head': '896882b50cc91d16f522a52ded9644045d461c26',
             'referenced_source_package_version': '5.30.10', 'source_match_status': 'unresolved',
             'path': 'gateway/node_modules/@koishijs/plugin-console/dist/client.js',
             'before_sha256': KOISHI_CLIENT_SHA256,
             'modification': 'Taki: preserve initial disconnect URL across reconnect retries',
             'replacements': [{'old': old.decode(), 'new': new.decode()} for old, new in KOISHI_RECONNECT_PATCHES]}
    (stage / 'licenses/koishi-console/taki-reconnect-patch.json').write_text(
        json.dumps(patch, indent=2) + '\n', encoding='utf-8')


def build(python_artifact, node, output):
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT):
        raise ValueError('Combination requires committed tracked source')
    if subprocess.check_output(['git','ls-files','--others','--exclude-standard','--','koishi/src','koishi/scripts','koishi/console','koishi/vendor'],cwd=ROOT):
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
    package=json.loads((ROOT/'koishi/node_modules/@koishijs/plugin-console/package.json').read_text())
    downstream=package['version']==PACKAGE_VERSION
    if not downstream and package['version']!='5.30.11':
        raise ValueError('Unknown Console component version')
    component=verify_downstream(ROOT/'koishi') if downstream else {'kind':'official-npm','version':'5.30.11'}
    if downstream:
        verify_source_vendor(python_artifact)
    npm_materials=installed_npm_materials(ROOT/'koishi') if downstream else None
    with tempfile.TemporaryDirectory(prefix='taki-combination-') as folder:
        stage=Path(folder)
        copy_release_materials(stage, downstream)
        gateway=stage/'gateway';gateway.mkdir()
        for name in ('scripts','node_modules','console','compat'):
            shutil.copytree(ROOT/'koishi'/name,gateway/name,ignore=shutil.ignore_patterns('.bin','*.tsbuildinfo'))
        if downstream:
            (gateway/'vendor').mkdir()
            for name in VENDOR_FILES:
                shutil.copy2(ROOT/'koishi/vendor'/name, gateway/'vendor'/name)
            (stage/'licenses/npm-installation.json').write_text(json.dumps(npm_materials,indent=2)+'\n',encoding='utf-8')
        else:
            patch_koishi_client(gateway/'node_modules/@koishijs/plugin-console/dist/client.js')
        # Sidecar notice sits beside the modified asset; preserve the upstream asset syntax.
        if not downstream:
            (gateway/'node_modules/@koishijs/plugin-console/dist/TAKI-MODIFICATIONS.txt').write_text(
            'Modified by Taki: reconnect URL preservation.\n'
            'Modification documentation added: 2026-11-03; patch predates this material update.\n'
            f'Taki source commit: {commit}\n'
            'Patch and upstream source reference: licenses/koishi-console/taki-reconnect-patch.json '
            'and licenses/SOURCES.md at combination root.\n', encoding='utf-8')
        for name in ('package.json','package-lock.json','run.cjs','recovery.cjs','console.cjs','health.cjs','operation-journal.cjs','ops-host.cjs','ops-controls.cjs','trusted-actions.cjs'):
            shutil.copy2(ROOT/'koishi'/name,gateway/name)
        if downstream:
            verify_downstream(gateway)
        subprocess.run([str(node),'node_modules/typescript/bin/tsc','-p','tsconfig.json','--outDir',str(gateway/'lib')],cwd=ROOT/'koishi',check=True)
        subprocess.run([str(node),'scripts/probe.cjs'],cwd=gateway,check=True,stdout=subprocess.DEVNULL)
        binary=stage/'bin'/('node.exe' if identity['platform']=='win32' else 'node')
        binary.parent.mkdir();shutil.copy2(node,binary)
        python=stage/'python'/python_artifact.name;python.parent.mkdir();shutil.copy2(python_artifact,python)
        recovery=stage/'controller';recovery.mkdir()
        for name in ('koishi_control.py','group_policy_guard.py','linux_backend.py','linux_updater.py','offline_bundle.py','release_retention.py','ops_snapshot.py','trusted_actions.py',
                     *RECOVERY_AUTHORITY_FILES, *RECOVERY_LAUNCHER_FILES):
            shutil.copy2(ROOT/'deploy'/name,recovery/name)
        shutil.copytree(ROOT/'deploy/koishi',recovery/'service-templates')
        shutil.copy2(ROOT/'docs/KOISHI_MIGRATION_M4.md',stage/'RUNBOOK.md')
        rollback=stage/'controller-rollback';rollback.mkdir()
        rollback_ref='0359a8d515db5cd32a66617852edb29b25862580'
        for name in ('linux_backend.py','linux_updater.py','offline_bundle.py'):
            (rollback/name).write_bytes(subprocess.check_output(['git','show',rollback_ref+':deploy/'+name],cwd=ROOT))
        files={p.relative_to(stage).as_posix():sha(p) for p in sorted(stage.rglob('*')) if p.is_file()}
        inventory=runpy.run_path(str(APPLICATION/'backend_sources.py'))
        backend_digest, recovery_entries = measure_recovery_entries(python, gateway, commit)
        manifest={'schema':1,'commit':commit,'controller_rollback_commit':rollback_ref,'node':identity,'lock_sha256':sha(gateway/'package-lock.json'),
                  'python_artifact':python.relative_to(stage).as_posix(),'python_sha256':sha(python),
                  'backend_code_digest':backend_digest,'recovery_entrypoints':recovery_entries,
                  'protocol':inventory['TRANSPORT_PROTOCOL'],'capabilities':['group_policies_v1','query_chat_contract_v1'],
                  'data_schema':1,'rollback':'code-only; freeze settings before unsupported policy runtimes',
                  'files':files,'console_component':component}
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

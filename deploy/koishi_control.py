"""Opt-in trusted-controller patch for a preverified two-process combination.

No installation on import. The existing updater owns transactions, compatibility
review, backups and rollback. Opt-in staging accepts exact-commit public release assets.
"""
import hashlib
import http.client
import json
from pathlib import Path
import re
import os
import shutil
import stat
import time
import tempfile
import zipfile

COMBINATION_ASSET = 'taki-koishi-linux-x64.zip'


def inspect_combination_archive(path, commit, expected):
    """Inspect archive bytes as data; never import or execute its controller."""
    try:
        from .offline_bundle import digest, LIMIT, Rejected
    except ImportError:
        from offline_bundle import digest, LIMIT, Rejected
    path = Path(path)
    if (not re.fullmatch('[a-f0-9]{40}', commit) or not re.fullmatch('[a-f0-9]{64}', expected)
            or path.is_symlink() or path.stat().st_size > LIMIT or digest(path) != expected):
        raise Rejected('combination_archive_digest')
    try:
        with zipfile.ZipFile(path) as archive:
            rows = archive.infolist(); names = [r.filename for r in rows]
            if (len(rows) > 50000 or len(set(names)) != len(names)
                    or sum(r.file_size for r in rows) > 1024**3
                    or 'combination.json' not in names):
                raise ValueError('inventory')
            for row in rows:
                parts = row.filename.split('/')
                if (any(p in ('', '.', '..') for p in parts) or '\\' in row.filename or ':' in row.filename
                        or stat.S_IFMT(row.external_attr >> 16) not in (0, stat.S_IFREG)
                        or row.flag_bits & 1 or row.file_size > 256*1024**2):
                    raise ValueError('member')
            if archive.getinfo('combination.json').file_size > 8*1024**2:
                raise ValueError('manifest size')
            manifest = json.loads(archive.read('combination.json'))
            _profile(manifest, commit)
            if (not isinstance(manifest.get('files'), dict)
                    or set(manifest['files']) != set(names)-{'combination.json'}
                    or not {'bin/node', 'gateway/scripts/probe.cjs', 'gateway/package-lock.json',
                            'python/taki-linux-x86_64-cp312.zip'} <= set(manifest['files'])
                    or manifest.get('python_artifact') != 'python/taki-linux-x86_64-cp312.zip'
                    or manifest.get('python_sha256') != manifest['files'][manifest['python_artifact']]
                    or manifest.get('lock_sha256') != manifest['files']['gateway/package-lock.json']):
                raise ValueError('manifest inventory')
            for name, expected_member in manifest['files'].items():
                if not isinstance(expected_member, str) or not re.fullmatch('[a-f0-9]{64}', expected_member):
                    raise ValueError('member hash')
                h = hashlib.sha256()
                with archive.open(name) as source:
                    for block in iter(lambda: source.read(1024**2), b''): h.update(block)
                if h.hexdigest() != expected_member: raise ValueError('member hash')
            return manifest
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise Rejected('combination_archive_invalid') from exc


def stage_combination(archive, destination, commit, expected, python_hash, *, ownership_dir=None):
    """Publish a complete verified directory with no application-writable files."""
    manifest = inspect_combination_archive(archive, commit, expected)
    _profile(manifest, commit, python_hash)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink(): raise FileExistsError(destination)
    ownership = None
    if ownership_dir is not None:
        try:
            from .linux_updater import atomic_json
            from .release_retention import OWNER, manifest_digest
        except ImportError:
            from linux_updater import atomic_json
            from release_retention import OWNER, manifest_digest
        ownership_dir = Path(ownership_dir)
        ownership_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        ownership = ownership_dir / (commit + '.json')
        if ownership.exists() or ownership.is_symlink(): raise FileExistsError(ownership)
    stage = Path(tempfile.mkdtemp(prefix='.'+commit+'-', dir=destination.parent))
    try:
        if ownership is not None:
            record = dict(owner=OWNER, commit=commit, destination=str(destination), stage=str(stage),
                          phase='staging', python_sha256=python_hash, manifest_sha256=manifest_digest(manifest))
            atomic_json(ownership, record)
        with zipfile.ZipFile(archive) as z:
            for name in ['combination.json', *manifest['files']]:
                target = stage/name
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024**2)
                target.chmod(0o555 if name=='bin/node' else 0o444)
        verify_combination(stage, commit, python_hash)
        for directory in sorted((p for p in stage.rglob('*') if p.is_dir()), reverse=True): directory.chmod(0o555)
        stage.chmod(0o555)
        os.rename(stage, destination)
        if ownership is not None:
            atomic_json(ownership, dict(record, stage=None, phase='published'))
    finally:
        if stage.exists():
            # Only the freshly created, link-free staging directory belongs to us.
            stage.chmod(0o700)
            for p in stage.rglob('*'): p.chmod(0o700 if p.is_dir() else 0o600)
            shutil.rmtree(stage)
            if ownership is not None:
                ownership.unlink(missing_ok=True)
    return manifest


def verify_combination(root, commit, python_hash=None):
    root=Path(root)
    row=json.loads((root/'combination.json').read_text(encoding='utf-8'))
    _profile(row, commit, python_hash)
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix()!='combination.json'}
    if actual!=set(row['files']) or any(p.is_symlink() for p in root.rglob('*')): raise ValueError('combination_inventory')
    for name,digest in row['files'].items():
        path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('combination_payload')
    return row


def _profile(row, commit, python_hash=None):
    if row.get('schema')!=1 or row.get('commit')!=commit:
        raise ValueError('combination_identity')
    profile = row.get('protocol')
    if not isinstance(profile,dict) or set(profile) != {'major','minor'} or any(type(profile[k]) is not int for k in ('major','minor')):
        raise ValueError('combination_protocol')
    if profile == {'major':1,'minor':0}:
        if row.get('capabilities',[]) != []: raise ValueError('combination_capabilities')
    elif profile == {'major':1,'minor':1}:
        if row.get('capabilities') != ['group_policies_v1']: raise ValueError('combination_capabilities')
    elif profile in ({'major':2,'minor':0},{'major':2,'minor':1}):
        if row.get('capabilities') != ['group_policies_v1','query_chat_contract_v1']: raise ValueError('combination_capabilities')
    else:
        raise ValueError('combination_protocol')
    if row.get('node')!={'version':'v24.19.0','platform':'linux','arch':'x64'}:
        raise ValueError('combination_target')
    if python_hash and row.get('python_sha256')!=python_hash: raise ValueError('combination_python_mismatch')


def health_matches(row, release, backend_pid, gateway_pid, digest):
    return bool(row.get('ready') is True and row.get('release_sha')==release
                and row.get('pid')==int(gateway_pid) and row.get('backend_pid')==int(backend_pid)
                and row.get('backend_code_digest')==digest and row.get('qq_online') is True
                and row.get('handshake') is True and row.get('backend_ready') is True)


class KoishiControl:
    def __init__(self, backend):
        self.backend=backend

    def prefetch(self, commit):
        if not self.backend.config.get('koishi_auto_stage', False): return None
        try:
            from .linux_backend import API
            from .offline_bundle import get_bundle
        except ImportError:
            from linux_backend import API
            from offline_bundle import get_bundle
        return get_bundle(self.backend.state/'combination-cache', commit,
            lambda suffix: self.backend._json(API+suffix), asset_name=COMBINATION_ASSET,
            inspector=inspect_combination_archive)

    def prepare(self, release):
        if self.backend.config.get('koishi_auto_stage', False):
            try:
                from .linux_backend import trusted
            except ImportError:
                from linux_backend import trusted
            parent = Path(self.backend.config['koishi_combinations'])
            trusted(parent, directory=True)
            root = parent/release.commit
            if not root.exists() and not root.is_symlink():
                archive, digest = self.prefetch(release.commit)
                stage_combination(archive, root, release.commit, digest,
                                  self.backend._manifest(release)['bundle_sha256'],
                                  ownership_dir=self.backend.state/'combinations')
        return self.combination(release)

    def combination(self, release):
        try:
            from .linux_backend import trusted
        except ImportError:
            from linux_backend import trusted
        root=Path(self.backend.config['koishi_combinations'])/release.commit
        trusted(root,directory=True)
        # The entire staged payload must be administrator-owned and non-writable by the app.
        for path in root.rglob('*'): trusted(path,directory=path.is_dir())
        manifest=self.backend._manifest(release)
        return root,verify_combination(root,release.commit,manifest.get('bundle_sha256'))

    def state(self):
        output=self.backend.run(['/usr/bin/systemctl','show','taki-gateway.service',
            '--property=ActiveState,SubState,MainPID,InvocationID,NRestarts'],timeout=15)
        return dict(line.split('=',1) for line in output.splitlines() if '=' in line)

    def stop(self):
        self.backend.run(['/usr/bin/systemctl','stop','taki-gateway.service'],timeout=90)
        state=self.state()
        if state.get('MainPID')!='0' or state.get('ActiveState') not in ('inactive','failed'):
            raise ValueError('gateway_not_stopped')

    def prepare_start(self):
        try:
            from .group_policy_guard import transition, LEGACY_FREEZE_COMMITS
        except ImportError:
            from group_policy_guard import transition, LEGACY_FREEZE_COMMITS
        _,manifest = self.combination(self.backend.current_release())
        directory = Path(self.backend.config['base'])/'console-state'
        supported = manifest.get('capabilities') in (['group_policies_v1'], ['group_policies_v1','query_chat_contract_v1'])
        permit = transition(directory, self.backend.state/'group-policy-freeze.v1.json', supported)
        if permit['state'] == 'frozen':
            if manifest['commit'] not in LEGACY_FREEZE_COMMITS:
                raise ValueError('policy_guard_legacy_unverified')
            permit['legacy_commit'] = manifest['commit']
        return permit

    def start(self):
        self.combination(self.backend.current_release())
        # An inactive new unit may be unloaded between show and reset-failed.
        # Failed units stay loaded; only they need their start limit reset.
        if self.state().get('ActiveState') == 'failed':
            self.backend.run(['/usr/bin/systemctl','reset-failed','taki-gateway.service'],timeout=30)
        self.backend.run(['/usr/bin/systemctl','start','taki-gateway.service'],timeout=90)

    def healthy(self, release):
        try:
            from .linux_backend import trusted
        except ImportError:
            from linux_backend import trusted
        _,manifest=self.combination(release)
        token_path=Path(self.backend.config['koishi_health_token_file']);trusted(token_path)
        token=token_path.read_text().strip()
        if len(token)<32: return False
        first=self.backend._service(),self.state()
        for state in first:
            if state.get('ActiveState')!='active' or state.get('SubState')!='running' or not re.fullmatch('[1-9][0-9]*',state.get('MainPID','')): return False
        ready_at=None;deadline=self.backend.clock()+self.backend.health_seconds
        while self.backend.clock()<deadline:
            if first!=(self.backend._service(),self.state()) or self.backend.current_release()!=release: return False
            connection=http.client.HTTPConnection('127.0.0.1',self.backend.config.get('koishi_health_port',8766),timeout=12)
            try:
                connection.request('GET','/health',headers={'Authorization':'Bearer '+token})
                response=connection.getresponse();raw=response.read(32769)
                valid=response.status==200 and len(raw)<=32768 and health_matches(json.loads(raw),release.commit,
                    first[0]['MainPID'],first[1]['MainPID'],manifest['backend_code_digest'])
            except (OSError,ValueError,http.client.HTTPException): valid=False
            finally: connection.close()
            if valid:
                if ready_at is None: ready_at=self.backend.clock()
                if self.backend.clock()-ready_at>=self.backend.stable_seconds: return True
            else: ready_at=None
            self.backend.sleep(2)
        return False


    def publish_notice(self, state):
        import difflib
        try:
            from .linux_updater import atomic_json
        except ImportError:
            from linux_updater import atomic_json
        valid=state and not state.get('transaction') and not state.get('paused') and state.get('previous')
        row={'rejected':True}
        if valid:
            active,previous=state['current'],state['previous']
            # Only a successfully updated current deployment may generate a report; rollback is silent.
            latest=next((e for e in reversed(state.get('events',[])) if e.get('status') in ('updated','operator_rollback','rolled_back')),None)
            if latest and latest.get('status')=='updated' and latest.get('commit')==active['commit']:
                def changelog(release):
                    path=Path(release['code_dir'])/'更新日志.md'
                    return path.read_text(encoding='utf-8').splitlines() if path.is_file() else []
                added=[line[2:] for line in difflib.ndiff(changelog(previous),changelog(active)) if line.startswith('+ ')]
                row={'active':{'sha':active['commit'],'source':active['code_dir']},
                     'previous':{'sha':previous['commit']},'rejected':None,'transaction':None,
                     'facts':json.dumps({'更新日志新增内容':'\n'.join(added)[:8000]},ensure_ascii=False)}
        target=self.backend.runtime_directory()/'notice.json'
        atomic_json(target,row);target.chmod(0o644)

"""Opt-in trusted-controller patch for a preverified two-process combination.

No installation on import. The existing updater still owns transactions, compatibility
review, backups and rollback. Combination staging is a separate administrator action.
"""
import hashlib
import http.client
import json
from pathlib import Path
import re
import time


def verify_combination(root, commit, python_hash=None):
    root=Path(root)
    row=json.loads((root/'combination.json').read_text(encoding='utf-8'))
    if row.get('schema')!=1 or row.get('commit')!=commit or row.get('protocol')!={'major':1,'minor':0}:
        raise ValueError('combination_identity')
    if row.get('node')!={'version':'v24.19.0','platform':'linux','arch':'x64'}:
        raise ValueError('combination_target')
    if python_hash and row.get('python_sha256')!=python_hash: raise ValueError('combination_python_mismatch')
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix()!='combination.json'}
    if actual!=set(row['files']) or any(p.is_symlink() for p in root.rglob('*')): raise ValueError('combination_inventory')
    for name,digest in row['files'].items():
        path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('combination_payload')
    return row


def health_matches(row, release, backend_pid, gateway_pid, digest):
    return bool(row.get('ready') is True and row.get('release_sha')==release
                and row.get('pid')==int(gateway_pid) and row.get('backend_pid')==int(backend_pid)
                and row.get('backend_code_digest')==digest and row.get('qq_online') is True
                and row.get('handshake') is True and row.get('backend_ready') is True)


class KoishiControl:
    def __init__(self, backend):
        self.backend=backend

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

    def start(self):
        self.combination(self.backend.current_release())
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
        target=Path('/run/taki-updater/notice.json')
        atomic_json(target,row);target.chmod(0o644)

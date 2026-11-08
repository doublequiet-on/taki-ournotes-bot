"""Independent first-install preparation; never installs units or starts services.

Input: trusted instance settings and a pinned combination archive. Output: unit
drafts and a durable previous=null staging record. Reuses the existing archive
verifier, staging transaction and lock. See INSTANCE_RECOVERY.md.
"""
from pathlib import Path, PurePosixPath
import os
import re
import stat
import subprocess
import sys

try:
    from .instance_preflight import digest, read_document
    from .koishi_control import inspect_combination_archive, stage_combination
    from .linux_updater import FileLock, atomic_json
except ImportError:
    from instance_preflight import digest, read_document
    from koishi_control import inspect_combination_archive, stage_combination
    from linux_updater import FileLock, atomic_json


def layout(settings):
    """Pure, explicit Linux configuration. No product capabilities are selected."""
    if not isinstance(settings, dict) or set(settings) != {
        'version', 'instance', 'parent', 'user', 'backend_port', 'console_port', 'health_port'
    } or type(settings['version']) is not int or settings['version'] != 1:
        raise ValueError('instance_settings_invalid')
    for key in ('instance', 'user'):
        if not isinstance(settings[key], str) or not re.fullmatch('[a-z][a-z0-9-]{0,31}', settings[key]):
            raise ValueError('instance_name_invalid')
    if settings['user'] == 'root':
        raise ValueError('application_user_must_not_be_root')
    parent = settings['parent']
    if (not isinstance(parent, str) or not re.fullmatch('/[A-Za-z0-9_./-]+', parent)
            or '..' in PurePosixPath(parent).parts or str(PurePosixPath(parent)) != parent
            or parent == '/' or parent.startswith('//')):
        raise ValueError('instance_parent_invalid')
    ports = [settings[k] for k in ('backend_port', 'console_port', 'health_port')]
    if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != 3:
        raise ValueError('instance_ports_invalid')
    base = parent + '/' + settings['instance']
    return dict(base=base, state=base+'/controller-state', runtime=base+'/runtime',
                controller=base+'/controller', combinations=base+'/combinations',
                console_state=base+'/console-state', config=base+'/instance.json',
                backend_unit='taki-'+settings['instance']+'.service',
                gateway_unit='taki-'+settings['instance']+'-gateway.service')


def unit_drafts(settings):
    """Return reviewable systemd text; the new launcher gate deliberately fails closed."""
    p = layout(settings)
    units = {}
    for role, unit in [('backend', p['backend_unit']), ('gateway', p['gateway_unit'])]:
        dependency = (f"Requires={p['backend_unit']}\nAfter={p['backend_unit']}\n"
                      f"BindsTo={p['backend_unit']}\nPartOf={p['backend_unit']}\n") if role == 'gateway' else ''
        units[unit] = (
            '[Unit]\nDescription=Taki independent '+role+'\n'+dependency+
            '[Service]\nType=simple\nUser='+settings['user']+'\nGroup='+settings['user']+
            '\nWorkingDirectory='+p['base']+'\nEnvironmentFile='+p['base']+'/config/runtime.env\n'
            'ExecStart=/usr/bin/python3 '+p['controller']+'/koishi_launcher.py --instance-config '+p['config']+' '+role+
            '\nRestart=no\nTimeoutStopSec=90\nKillMode=control-group\nNoNewPrivileges=true\nProtectSystem=strict\n'
            'ReadWritePaths='+p['base']+'/data '+p['console_state']+'\n')
    return units


def _trusted_directory(path):
    """Require an existing controller-owned, link-free directory and ancestors."""
    path = Path(path)
    if os.name != 'posix' or not path.is_absolute() or path.resolve() != path:
        raise ValueError('controller_directory_untrusted')
    for part in [path, *path.parents]:
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('controller_directory_untrusted')


def prepare_first_install(settings, archive, commit, archive_sha256, python_sha256):
    """Stage only. Interrupted intent is retained, never inferred as an installed baseline.

    The parent must already be dedicated to this controller. No user creation,
    environment installation, unit writes, systemctl, data restore or permit.
    """
    paths = layout(settings)
    _trusted_directory(settings['parent'])
    manifest = inspect_combination_archive(archive, commit, archive_sha256)
    if manifest['python_sha256'] != python_sha256:
        raise ValueError('combination_python_mismatch')
    base = Path(paths['base'])
    if base.exists() and (base.is_symlink() or any(base.iterdir())):
        raise ValueError('first_install_requires_empty_instance')
    base.mkdir(mode=0o755, exist_ok=True)
    _trusted_directory(base)
    state = Path(paths['state'])
    state.mkdir(mode=0o700, exist_ok=True)
    _trusted_directory(state)
    with FileLock(state/'update.lock'):
        record = state/'first-install.json'
        if record.exists() or record.is_symlink() or (state/'state.json').exists() or (base/'current').exists() or (base/'current').is_symlink():
            raise ValueError('first_install_requires_empty_instance')
        combinations = Path(paths['combinations'])
        combinations.mkdir(mode=0o755, exist_ok=True)
        _trusted_directory(combinations)
        if any(combinations.iterdir()):
            raise ValueError('first_install_requires_empty_combinations')
        value = dict(kind='taki-first-install/1', previous=None, current=None,
                     phase='staging', instance=settings['instance'], settings_sha256=digest(settings),
                     commit=commit, archive_sha256=archive_sha256, python_sha256=python_sha256,
                     activation_permitted=False)
        atomic_json(record, value)
        stage_combination(archive, combinations/commit, commit, archive_sha256,
                          python_sha256, ownership_dir=state/'combinations')
        value['phase'] = 'staged_pending_manual_review'
        atomic_json(record, value)
        return value


def inspect_first_install(settings):
    """Read-only. A staged directory never implies completion or recovery permission."""
    paths = layout(settings)
    value = read_document(Path(paths['state'])/'first-install.json')
    if (value.get('kind') != 'taki-first-install/1' or value.get('previous') is not None
            or value.get('current') is not None or value.get('activation_permitted') is not False
            or value.get('settings_sha256') != digest(settings)
            or value.get('phase') not in ('staging', 'staged_pending_manual_review')):
        raise ValueError('first_install_record_invalid')
    return dict(status='manual', reason=value['phase'], record=value, activation_permitted=False)


def require_loopback():
    """Check the actual caller namespace, not the systemctl client's intent."""
    import json
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('isolated_controller_requires_linux_root')
    interfaces = json.loads(subprocess.check_output(['/usr/sbin/ip', '-j', 'addr'], text=True))
    routes = json.loads(subprocess.check_output(['/usr/sbin/ip', '-j', 'route'], text=True))
    routes6 = json.loads(subprocess.check_output(['/usr/sbin/ip', '-6', '-j', 'route'], text=True))
    if not interfaces or any(i['ifname'] != 'lo' for i in interfaces) or routes or routes6:
        raise ValueError('loopback_namespace_required')


def install_runtime(settings):
    """Complete offline runtime preparation, never activate or resume a failure.

    Uses the existing bundle/extraction/environment seal conventions. The installed
    record denotes code and dependencies only, not approved product activation.
    """
    try:
        from .offline_bundle import require_target, unpack_bundle
        from .linux_backend import extract_archive, freeze_release, environment_digest, tree_digest
        from .koishi_control import verify_combination
    except ImportError:
        from offline_bundle import require_target, unpack_bundle
        from linux_backend import extract_archive, freeze_release, environment_digest, tree_digest
        from koishi_control import verify_combination
    require_loopback()
    require_target()
    p = layout(settings)
    _trusted_directory(p['state'])
    with FileLock(Path(p['state'])/'update.lock'):
        staged = inspect_first_install(settings)['record']
        if staged['phase'] != 'staged_pending_manual_review':
            raise ValueError('first_install_not_staged')
        record = Path(p['state'])/'installed.json'
        root = Path(p['base'])/'application'
        if record.exists() or record.is_symlink() or root.exists() or root.is_symlink():
            raise ValueError('runtime_install_requires_manual_reconcile')
        combo = Path(p['combinations'])/staged['commit']
        verify_combination(combo, staged['commit'], staged['python_sha256'])
        value = dict(kind='taki-independent-runtime/1', phase='installing',
                     settings_sha256=digest(settings), commit=staged['commit'],
                     python_sha256=staged['python_sha256'], previous=None,
                     activation_permitted=False)
        atomic_json(record, value)
        root.mkdir(mode=0o755)
        payload = root/'payload'
        unpack_bundle(combo/'python/taki-linux-x86_64-cp312.zip', payload,
                      staged['commit'], staged['python_sha256'])
        extract_archive(payload/'source.tar.gz', root/'code', staged['commit'])
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(root), 'LANG': 'C.UTF-8',
               'PYTHONDONTWRITEBYTECODE': '1', 'PIP_CONFIG_FILE': '/dev/null',
               'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PIP_NO_CACHE_DIR': '1'}
        with (root/'install.log').open('xb') as log:
            def run(args):
                subprocess.run([str(a) for a in args], env=env, cwd=root, check=True,
                               stdout=log, stderr=subprocess.STDOUT, timeout=240)
            run([sys.executable, '-m', 'venv', '--copies', root/'venv'])
            python = root/'venv/bin/python'
            run([python, '-m', 'pip', 'install', '--no-index', '--no-compile',
                 '--find-links', payload/'wheelhouse', '--require-hashes',
                 '-r', payload/'requirements.lock'])
            run([python, '-m', 'pip', 'check'])
        freeze_release(root)
        value.update(phase='installed', code_digest=tree_digest(root/'code'),
                     environment_digest=environment_digest(root/'venv'))
        atomic_json(record, value)
        return value

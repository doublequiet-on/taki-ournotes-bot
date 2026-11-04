"""Task10 controller-owned, loopback-only execution of reviewed fixture entries.

Input: installed instance, trusted recovery callbacks and controller fixture files.
Effects: holds the existing instance FileLock, starts unprivileged children and
stops them before releasing it. No RPC, persistent permit or production activation.
See INSTANCE_RECOVERY.md for the outstanding standalone launcher authority bridge.
"""
import copy
import os
from pathlib import Path
import pwd
import signal
import subprocess
import threading
import time

try:
    from .instance_install import layout, require_loopback
    from .instance_preflight import digest, read_document
    from .linux_backend import trusted, environment_digest, tree_digest
    from .linux_updater import FileLock, atomic_json
    from .koishi_control import verify_combination
    from .recovery_preflight import ManualRecoveryReview, measure_components
except ImportError:
    from instance_install import layout, require_loopback
    from instance_preflight import digest, read_document
    from linux_backend import trusted, environment_digest, tree_digest
    from linux_updater import FileLock, atomic_json
    from koishi_control import verify_combination
    from recovery_preflight import ManualRecoveryReview, measure_components


class IsolatedRuntime:
    """One locked controller session; inputs are trusted Python composition, not JSON.

    stop_writers must stop every registered writer/trigger, then check_current must
    freshly run recovery_preflight with actual isolation and current 05/07/09 inputs.
    Entry files are explicitly reviewed synthetic harnesses under base/controller;
    this class does not select a product, launch QQ or authorize a production start.
    """
    def __init__(self, settings, *, check_current, stop_writers, entries):
        self.settings = copy.deepcopy(settings)
        self.paths = layout(self.settings)
        if not callable(check_current) or not callable(stop_writers):
            raise ValueError('trusted_recovery_and_stop_callbacks_required')
        if set(entries) != {'backend', 'gateway'}:
            raise ValueError('isolated_entry_pair_required')
        self.entries = {k: str(v) for k, v in entries.items()}
        self.check = check_current
        self.stop_writers = stop_writers
        self.review = ManualRecoveryReview(check_current)
        self.guard = threading.Lock()
        self.generation = 0
        self.permits = {}
        self.children = []
        self.lock = None
        self.used = False
        self.owner_thread = None

    def invalidate(self):
        with self.guard:
            self.generation += 1
            self.permits.clear()
            self.review.invalidate()

    def __enter__(self):
        require_loopback()
        if self.used:
            raise ValueError('new_controller_session_required')
        self.used = True
        self.owner_thread = threading.get_ident()
        trusted(Path(self.paths['state']), directory=True)
        self.lock = FileLock(Path(self.paths['state'])/'update.lock')
        self.lock.__enter__()
        try:
            self.stop_writers()
            self.installed = self._measure()
        except BaseException:
            self.lock.__exit__()
            self.lock = None
            raise
        return self

    def _measure(self):
        require_loopback()
        if threading.get_ident() != self.owner_thread:
            raise ValueError('controller_session_thread_mismatch')
        if self.lock is None or self.lock.fd is None:
            raise ValueError('controller_lock_not_held')
        current = os.stat(self.lock.path, follow_symlinks=False)
        held = os.fstat(self.lock.fd)
        if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise ValueError('controller_lock_replaced')
        record_path = Path(self.paths['state'])/'installed.json'
        trusted(record_path)
        row = read_document(record_path)
        if (row.get('kind') != 'taki-independent-runtime/1' or row.get('phase') != 'installed'
                or row.get('settings_sha256') != digest(self.settings)
                or row.get('activation_permitted') is not False):
            raise ValueError('runtime_not_installed')
        root = Path(self.paths['base'])/'application'
        combo = Path(self.paths['combinations'])/row['commit']
        for directory in (root, combo):
            trusted(directory, directory=True)
            for path in directory.rglob('*'):
                # venv's lib64 link is recorded by environment_digest; never use
                # a symlink entry as an executable or controller authority.
                if not path.is_symlink():
                    trusted(path, directory=path.is_dir())
        verify_combination(combo, row['commit'], row['python_sha256'])
        if (environment_digest(root/'venv') != row['environment_digest']
                or tree_digest(root/'code') != row['code_digest']):
            raise ValueError('installed_runtime_changed')
        for entry in self.entries.values():
            path = Path(entry)
            if not path.is_relative_to(Path(self.paths['controller'])) or path.resolve() != path:
                raise ValueError('entry_outside_trusted_controller')
            trusted(path)
        executables = {'python': str(root/'venv/bin/python'), 'node': str(combo/'bin/node')}
        return {'record': row, 'entries': measure_components(self.entries),
                'executables': measure_components(executables)}

    def approve(self, confirm_current):
        """Ask a trusted local human callback after displaying the fresh report.

        Callback returns exactly the two explicit confirmations. No defaults and
        no remote booleans. Returns per-role opaque tickets valid for 30 seconds.
        """
        if not callable(confirm_current):
            raise ValueError('trusted_human_confirmation_required')
        with self.guard:
            self.generation += 1
            self.permits.clear()
            self.review.invalidate()
            generation = self.generation
        if self._measure() != self.installed:
            self.invalidate()
            raise ValueError('installed_runtime_changed')
        with self.guard:
            if generation != self.generation:
                return {'status': 'manual', 'reason': 'session_invalidated', 'activation_permitted': False}, {}
        ticket, snapshot = self.review.begin()
        if ticket is None:
            return snapshot, {}
        confirmation = confirm_current(copy.deepcopy(snapshot))
        if not isinstance(confirmation, dict) or set(confirmation) != {
                'current_permissions_confirmed', 'current_deletions_confirmed'}:
            self.invalidate()
            return {'status': 'reject', 'reason': 'human_confirmation_required', 'activation_permitted': False}, {}
        result = self.review.confirm(ticket, **confirmation)
        if result['status'] != 'reviewed':
            return result, {}
        measured = self._measure()
        if measured != self.installed:
            self.invalidate()
            raise ValueError('installed_runtime_changed')
        with self.guard:
            if generation != self.generation:
                return {'status': 'manual', 'reason': 'session_invalidated', 'activation_permitted': False}, {}
            expires = time.monotonic() + 30
            tickets = {role: object() for role in self.entries}
            self.permits = {t: (role, generation, expires, copy.deepcopy(snapshot)) for role, t in tickets.items()}
            return result, tickets

    def start(self, role, ticket):
        """Consume once, recheck under FileLock, then run the measured fixture as app UID."""
        with self.guard:
            permit = self.permits.pop(ticket, None)
            if permit is None or permit[0] != role or permit[1] != self.generation:
                raise ValueError('isolated_permit_invalid')
        try:
            measured = self._measure()
            current = self.check()
            if measured != self.installed or current != permit[3]:
                raise ValueError('isolated_evidence_changed')
            user = pwd.getpwnam(self.settings['user'])
            if user.pw_uid == 0:
                raise ValueError('application_user_must_not_be_root')
            root = Path(self.paths['base'])/'application'
            combo = Path(self.paths['combinations'])/self.installed['record']['commit']
            env = {'PATH': '/usr/bin:/bin', 'HOME': self.paths['base'], 'LANG': 'C.UTF-8',
                   'PYTHONDONTWRITEBYTECODE': '1', 'TAKI_INSTANCE_ID': self.settings['instance'],
                   'TAKI_BACKEND_PORT': str(self.settings['backend_port']),
                   'TAKI_CONSOLE_PORT': str(self.settings['console_port']),
                   'TAKI_HEALTH_PORT': str(self.settings['health_port']),
                   'TAKI_CONSOLE_STATE_DIR': self.paths['console_state'],
                   'TAKI_COMBINATION': str(combo), 'TAKI_TEST_MODE': 'synthetic-offline'}
            args = ([str(root/'venv/bin/python'), '-I', '-B'] if role == 'backend'
                    else [str(combo/'bin/node')]) + [self.entries[role]]
            with self.guard:
                if permit[1] != self.generation or time.monotonic() >= permit[2]:
                    raise ValueError('isolated_permit_expired_or_invalidated')
                # No untrusted preexec callback; Popen drops all supplementary groups.
                child = subprocess.Popen(args, env=env, cwd=self.paths['base'],
                                         user=user.pw_uid, group=user.pw_gid, extra_groups=[],
                                         start_new_session=True)
                self.children.append(child)
                return child
        except BaseException:
            self.invalidate()
            raise

    def after_restore(self):
        """Existing restore executor calls this while still isolated; no old approval survives."""
        if threading.get_ident() != self.owner_thread:
            raise ValueError('controller_session_thread_mismatch')
        self.invalidate()
        self._stop_children()
        self.stop_writers()
        self._measure()
        return self.check()  # Caller must obtain a new manual review before any start.

    def _stop_children(self):
        for child in self.children:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)

    def __exit__(self, *_):
        self.invalidate()
        # Do not release the writer lock if termination cannot be established.
        # The owning systemd control group is the final process-lifetime boundary.
        self._stop_children()
        try:
            atomic_json(Path(self.paths['state'])/'isolated-session.json',
                        dict(status='stopped', activation_permitted=False))
        finally:
            self.lock.__exit__()
            self.lock = None

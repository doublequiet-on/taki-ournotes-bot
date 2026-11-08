"""10 host-owned inputs for 08 recovery-scope; no RPC or persisted allow.

Only a trusted host entry may supply authenticated operator/identity callbacks.
Files and the existing instance FileLock stay root-owned outside recovery trees.
See INSTANCE_RECOVERY.md. Config/record provisioning is deliberately separate.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading

from .instance_preflight import digest
from .linux_updater import FileLock, atomic_json

MAX_GENERATION = 9007199254740991
HASH = re.compile(r'[a-f0-9]{64}')


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 256


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_host_field')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('nonfinite_host_field')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _path(value, *, directory=False, private=True):
    if os.name != 'posix' or os.geteuid() != 0 or not isinstance(value, str):
        raise ValueError('root_linux_host_required')
    path = Path(value)
    if not path.is_absolute() or str(path) != value or '..' in path.parts or path.resolve(strict=True) != path:
        raise ValueError('host_path_alias')
    for ancestor in path.parents:
        info = ancestor.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('host_ancestor_untrusted')
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
        raise ValueError('host_path_untrusted')
    if private and stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise ValueError('host_mode_invalid')
    if not directory and info.st_nlink != 1:
        raise ValueError('host_hardlink_rejected')
    return path


def _excluded(value):
    # These are untrusted application/backup trees, not authority directories.
    # Their ownership need not be root; pin their identities and exclude aliases.
    if not isinstance(value, str):
        raise ValueError('excluded_root_invalid')
    path = Path(value)
    if (not path.is_absolute() or str(path) != value or '..' in path.parts
            or path.resolve(strict=True) != path or not stat.S_ISDIR(path.lstat().st_mode)):
        raise ValueError('excluded_root_invalid')
    return path


def _read(value, limit=131072):
    path = _path(value)
    before = _signature(path.lstat())
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if _signature(os.fstat(stream.fileno())) != before:
            raise ValueError('host_file_changed')
        raw = stream.read(limit + 1)
        if len(raw) > limit or _signature(os.fstat(stream.fileno())) != before:
            raise ValueError('host_file_changed_or_oversized')
    if _signature(path.lstat()) != before:
        raise ValueError('host_file_changed')
    return raw, before


@dataclass(frozen=True)
class AuthenticatedOperator:
    """Trusted authentication callback result, never deserialized request JSON.

    The host entry authenticates issuer/subject/session before constructing this.
    Root or a Console login does not implicitly grant product administrator rights.
    """
    issuer: str
    subject: str
    session: str
    product: str
    instance: str


def principal(issuer, subject):
    """08 accepts one opaque subject string; namespace it without delimiter ambiguity."""
    return 'principal:' + digest(dict(issuer=issuer, subject=subject))


class HostAuthority:
    def __init__(self, config_path, runtime, *, read_operator, read_identity,
                 invalidate_scope, read_deletion_review):
        if not all(callable(v) for v in (read_operator, read_identity, invalidate_scope, read_deletion_review)):
            raise ValueError('trusted_host_callbacks_required')
        self.runtime = runtime
        self.read_authenticated = read_operator
        self.read_identity = read_identity
        self.invalidate_scope = invalidate_scope
        self.deletion_review = read_deletion_review
        self.guard = threading.RLock()
        self.blocked = None
        self.epoch = 0
        self.pending = self.approval = self.context = None
        self.session = secrets.token_hex(32)
        self.config_path = config_path
        self.config_raw, self.config_stamp = _read(config_path)
        self.config = _json(self.config_raw)
        c = self.config
        keys = {'schema', 'instance', 'product', 'authority_root', 'deletion_record',
                'revocation_record', 'excluded_roots', 'administrators'}
        if (not isinstance(c, dict) or set(c) != keys or c['schema'] != 'task10-host-control/1'
                or c['instance'] != runtime.settings['instance'] or c['product'] not in ('personal', 'custom')
                or not isinstance(c['administrators'], list) or not c['administrators']):
            raise ValueError('host_authority_unconfigured')
        admins = c['administrators']
        if any(not isinstance(a, dict) or set(a) != {'issuer', 'subject'}
               or not all(_text(v) for v in a.values()) for a in admins):
            raise ValueError('host_administrators_invalid')
        if len({principal(**a) for a in admins}) != len(admins):
            raise ValueError('duplicate_host_administrator')
        self.root = _path(c['authority_root'], directory=True)
        self.root_stamp = _signature(self.root.stat())[:6]
        _path(str(Path(config_path).parent), directory=True)
        if not isinstance(c['excluded_roots'], list) or not c['excluded_roots']:
            raise ValueError('excluded_roots_required')
        exclusions = [_excluded(v) for v in c['excluded_roots']]
        base = Path(runtime.paths['base']).resolve(strict=True)
        if not any(base.is_relative_to(p) for p in exclusions):
            raise ValueError('application_tree_not_excluded')
        self.trusted_paths = [Path(config_path), self.root]
        for field in ('deletion_record', 'revocation_record'):
            p = _path(c[field])
            if not p.is_relative_to(self.root):
                raise ValueError('authority_record_outside_control_root')
            _path(str(p.parent), directory=True)
            self.trusted_paths.append(p)
        if len({(p.stat().st_dev, p.stat().st_ino) for p in self.trusted_paths}) != len(self.trusted_paths):
            raise ValueError('authority_path_overlap')
        # Reject containment and mount aliases through any trusted path ancestor.
        for p in self.trusted_paths:
            for excluded in exclusions:
                if p.is_relative_to(excluded) or excluded.is_relative_to(p):
                    raise ValueError('authority_inside_excluded_tree')
                if any(os.path.samestat(a.stat(), excluded.stat()) for a in (p, *p.parents)):
                    raise ValueError('authority_mount_alias')
        self.path_stamps = {str(p): _signature(p.stat()) for p in exclusions}
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.config_sha256 = hashlib.sha256(self.config_raw).hexdigest()
        self.rev_raw, self.rev_stamp = _read(c['revocation_record'])
        self.rev = self._revocation(self.rev_raw)
        self._lock()
        previous = getattr(runtime, '_host_authority', None)
        if previous is not None:
            raise ValueError('host_authority_already_bound')
        runtime._host_authority = self

    def _revocation(self, raw):
        value = _json(raw)
        if (not isinstance(value, dict) or set(value) != {'schema', 'instance', 'generation', 'event_id'}
                or value['schema'] != 'task10-revocation/1' or value['instance'] != self.config['instance']
                or type(value['generation']) is not int or not 1 <= value['generation'] <= MAX_GENERATION
                or not isinstance(value['event_id'], str) or not HASH.fullmatch(value['event_id'])):
            raise ValueError('host_revocation_invalid')
        return value

    def _lock(self):
        lock = self.runtime.lock
        if not isinstance(lock, FileLock) or lock.fd is None:
            raise ValueError('host_lock_required')
        p = _path(str(lock.path))
        if p != Path(self.runtime.paths['state']) / 'update.lock':
            raise ValueError('host_lock_wrong_instance')
        if not os.path.samestat(os.fstat(lock.fd), p.stat()):
            raise ValueError('host_lock_replaced')

    def _invalidate(self, reason):
        self.epoch += 1
        self.pending = self.approval = self.context = None
        self.runtime.invalidate()
        session = self.runtime.recovery_session
        if session is not None:
            session.revoke(reason)
        self.invalidate_scope()

    def _fail(self, reason):
        self.blocked = reason
        self._invalidate(reason)

    def _current(self):
        if self.blocked:
            raise ValueError(self.blocked)
        epoch = self.epoch
        self._lock()
        if _read(self.config_path) != (self.config_raw, self.config_stamp):
            raise ValueError('host_config_changed')
        if _signature(_path(str(self.root), directory=True).stat())[:6] != self.root_stamp:
            raise ValueError('authority_root_replaced')
        for path, stamp in self.path_stamps.items():
            p = _excluded(path)
            # Directory content changes are ordinary; identity/permissions must not.
            if _signature(p.stat())[:6] != stamp[:6]:
                raise ValueError('excluded_root_changed')
        if _read(self.config['revocation_record']) != (self.rev_raw, self.rev_stamp):
            raise ValueError('host_revocation_external_change')
        operator = self.read_authenticated()
        if not isinstance(operator, AuthenticatedOperator) or not all(_text(v) for v in vars(operator).values()):
            raise ValueError('authenticated_operator_required')
        if (operator.product != self.config['product'] or operator.instance != self.config['instance']
                or dict(issuer=operator.issuer, subject=operator.subject) not in self.config['administrators']):
            raise ValueError('operator_not_designated')
        identity = self.read_identity()
        if (not isinstance(identity, dict) or set(identity) != {'product', 'instance', 'identity_evidence'}
                or identity['product'] != operator.product or identity['instance'] != operator.instance
                or not isinstance(identity['identity_evidence'], str) or not HASH.fullmatch(identity['identity_evidence'])):
            raise ValueError('current_identity_required')
        if self.blocked or self.epoch != epoch:
            raise ValueError('host_read_invalidated')
        context = dict(operator=vars(operator).copy(), identity=copy.deepcopy(identity))
        if self.context is not None and self.context != context:
            raise ValueError('host_authentication_or_identity_changed')
        if self.approval is not None and self.approval != self._review(context):
            raise ValueError('approved_deletion_record_changed')
        self.context = context
        return context

    def _reading(self, fn):
        with self.guard:
            if self.blocked:
                return None
            try:
                context = self._current()
                epoch = self.epoch
                result = fn(context)
                if self.blocked or self.epoch != epoch:
                    raise ValueError('host_read_invalidated')
                return result
            except Exception as exc:
                # Read callbacks fail closed and expose the failure class/reason in
                # status, never preserve an earlier approval after an IO/auth failure.
                try:
                    self._fail(type(exc).__name__ + ':' + str(exc))
                except Exception:
                    self.blocked = 'host_invalidation_failed'
                return None

    def _review(self, context):
        raw, stamp = _read(self.config['deletion_record'], 16 * 1024 * 1024)
        return dict(instance=self.config['instance'], controller_session=self.session, boot_id=self.boot,
                    config_sha256=self.config_sha256, revocation_generation=self.rev['generation'],
                    actor=context['operator'], identity_evidence=context['identity']['identity_evidence'],
                    authority_file_sha256=hashlib.sha256(raw).hexdigest(), file_stamp=stamp)

    def begin_deletion_review(self):
        # A fresh human review first revokes the previous approval durably and
        # tells 08 immediately; polling cannot protect an unobserved ABA change.
        with self.guard:
            if threading.get_ident() != self.runtime.owner_thread:
                raise ValueError('host_writer_thread_mismatch')
            try:
                self.revoke()
            except Exception:
                return None
        def begin(context):
            self.pending = self.approval = None
            ticket = object()
            review = self._review(context)
            self.pending = (ticket, review)
            return ticket, copy.deepcopy(review)
        return self._reading(begin)

    def confirm_deletion_review(self, ticket):
        if threading.get_ident() != self.runtime.owner_thread:
            raise ValueError('host_writer_thread_mismatch')
        def confirm(context):
            pending, self.pending = self.pending, None
            if pending is None or ticket is not pending[0]:
                return False
            if pending[1] != self._review(context):
                self._fail('deletion_review_changed')
                return False
            self.approval = pending[1]
            return True
        return self._reading(confirm) is True

    def read_approved_sha256(self):
        def read(context):
            if self.approval is None:
                return None
            if self.approval != self._review(context):
                raise ValueError('approved_deletion_record_changed')
            return self.approval['authority_file_sha256']
        return self._reading(read)

    def read_administrators(self):
        return self._reading(lambda c: dict(product=self.config['product'], instance=self.config['instance'],
            subjects=[principal(**a) for a in self.config['administrators']],
            revision=digest(dict(config=self.config_sha256, session=self.session, generation=self.rev['generation'])),
            identity_evidence=c['identity']['identity_evidence']))

    def read_operator(self):
        return self._reading(lambda c: dict(subject=principal(c['operator']['issuer'], c['operator']['subject']),
            session=c['operator']['session'], product=self.config['product'], instance=self.config['instance']))

    def read_revocation_generation(self):
        return self._reading(lambda _: self.rev['generation'])

    def read_deletion_review(self):
        def read(context):
            result = self.deletion_review()
            if self._current() != context or self.blocked:
                raise ValueError('deletion_review_invalidated')
            # 08 owns review_sha256 and the complete C6 decision, not this module.
            return copy.deepcopy(result)
        return self._reading(read)

    def read_revision(self):
        """Current host evidence for 10 revisions, never an approval or start token."""
        return self._reading(lambda c: digest(dict(session=self.session, boot=self.boot,
            config=self.config_sha256, revocation=self.rev, context=c, approval=self.approval)))

    def revoke(self):
        """Owner-thread action; clear all approvals BEFORE durable generation write.

        A failed fsync/replace is terminal for this controller even if bytes landed.
        Never retry a partially acknowledged event or recreate a missing record.
        """
        with self.guard:
            if threading.get_ident() != self.runtime.owner_thread:
                raise ValueError('host_writer_thread_mismatch')
            try:
                self._current()
                self._invalidate('administrator_revocation')
                if self.rev['generation'] >= MAX_GENERATION:
                    raise ValueError('host_generation_exhausted')
                value = dict(schema='task10-revocation/1', instance=self.config['instance'],
                             generation=self.rev['generation'] + 1, event_id=secrets.token_hex(32))
                atomic_json(Path(self.config['revocation_record']), value)
                raw, stamp = _read(self.config['revocation_record'])
                if self._revocation(raw) != value:
                    raise ValueError('host_revocation_write_unconfirmed')
                self.rev, self.rev_raw, self.rev_stamp = value, raw, stamp
                return value['generation']
            except BaseException:
                self._fail('host_revocation_failed')
                raise

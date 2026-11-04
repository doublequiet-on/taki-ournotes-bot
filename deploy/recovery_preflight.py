"""Task10 isolated recovery check ordering; no activation implementation.

Input: trusted, read-only callbacks supplied by the deployment composition root.
Output: reject/manual/checked with ordered evidence, never a permit or a lock.
Pos: deploy/L2.md and INSTANCE_RECOVERY.md. Effects: invokes only the explicit
callbacks; does not import or execute candidate code, stop writers or restore data.
"""
from __future__ import annotations

import re
import hashlib
import os
import stat
import copy


def measure_components(files):
    """Hash independently selected ordinary files, never candidate-reported hashes.

    The trusted installer supplies component names/paths from the reviewed 09
    mapping. No directory digest convention is invented here. Writers must already
    be stopped; fstat checks detect ordinary drift, not hostile concurrent writers.
    Symlink components and multiply linked files are rejected.
    """
    if not isinstance(files, dict) or not files:
        raise ValueError('component_mapping_required')
    observed = {}
    for name, path in files.items():
        if not isinstance(name, str) or not name or not isinstance(path, str) or not os.path.isabs(path):
            raise ValueError('component_mapping_invalid')
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError('component_not_regular')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise ValueError('component_not_regular')
            def signature(st):
                # Windows 3.12 stat/fstat expose different ctime semantics;
                # only POSIX ctime is the inode-change clock used by this gate.
                return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
                        st.st_ctime_ns if os.name == 'posix' else None)
            if signature(before) != signature(opened):
                raise ValueError('component_changed')
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            if signature(opened) != signature(os.fstat(stream.fileno())):
                raise ValueError('component_changed')
        if signature(before) != signature(os.lstat(path)):
            raise ValueError('component_changed')
        observed[name] = checksum
    return observed

REVISION_FIELDS = frozenset(('session', 'inventory', 'candidate', 'components',
                            'controller', 'storage', 'expected', 'scope', 'deletion'))


def recovery_preflight(candidate, *, read_isolation=None, check_identity=None,
                       check_scope=None, check_deletion=None, current_authority=None):
    """Identity -> independent scope -> 07 check_recovery(candidate, callback).

    read_isolation() is current controller evidence from outside recovery trees:
    all four isolation facts plus revisions must be measured, never caller JSON
    approval. Re-read before each check and after all checks. A checked result
    expires on any revision/fact change, restart, restoration or loss of ownership.
    The callback contract is a composition boundary, not an isolation mechanism.
    """
    steps = []
    initial = None
    def result(status, reason):
        return {'contract_version': 1, 'status': status, 'reason': reason,
                'activation_permitted': False, 'revisions': initial, 'steps': steps}
    def isolation():
        if not callable(read_isolation):
            return None
        value = read_isolation()
        if not (isinstance(value, dict) and set(value) == {
                'outbound_isolated', 'writers_stopped', 'triggers_inhibited',
                'controller_exclusive', 'revisions'}
                and all(value[k] is True for k in ('outbound_isolated', 'writers_stopped',
                                                  'triggers_inhibited', 'controller_exclusive'))
                and isinstance(value['revisions'], dict) and set(value['revisions']) == REVISION_FIELDS
                and all(isinstance(v, str) and re.fullmatch('[a-f0-9]{64}', v)
                        for v in value['revisions'].values())):
            return None
        return dict(value['revisions'])
    try:
        initial = isolation()
        if initial is None:
            return result('manual', 'isolation_not_proven')
        for stage, checker in (('identity', check_identity), ('scope', check_scope), ('deletion', check_deletion)):
            if isolation() != initial:
                return result('manual', 'preflight_evidence_invalidated')
            if not callable(checker):
                return result('manual', stage + '_interface_missing')
            if stage == 'deletion' and not callable(current_authority):
                return result('manual', 'current_deletion_authority_missing')
            # Crucially pass the no-argument authority callback, not its result.
            decision = checker(candidate, current_authority) if stage == 'deletion' else checker()
            key = 'outcome' if stage == 'scope' else 'status'
            statuses = {'allow': 'allow', 'manual': 'manual', 'deny' if stage == 'scope' else 'reject': 'reject'}
            if not (isinstance(decision, dict) and isinstance(decision.get(key), str)
                    and decision[key] in statuses and isinstance(decision.get('reason'), str)
                    and decision['reason'] and 'evidence' in decision
                    and (isinstance(decision.get('evidence'), str) or decision.get('evidence') is None)
                    and (decision[key] != 'allow' or isinstance(decision.get('evidence'), str)
                         and bool(decision['evidence']))
                    and (stage == 'scope' or type(decision.get('contract_version')) is int
                         and decision['contract_version'] == 1)):
                return result('reject', stage + '_decision_invalid')
            if stage == 'scope' and decision[key] == 'allow':
                if decision.get('identity_evidence') != steps[0]['evidence']:
                    return result('reject', 'scope_identity_evidence_mismatch')
            step = {'stage': stage, **{k: decision[k] for k in (key, 'reason', 'evidence')}}
            if stage == 'scope' and 'identity_evidence' in decision:
                step['identity_evidence'] = decision['identity_evidence']
            steps.append(step)
            if statuses[decision[key]] != 'allow':
                return result(statuses[decision[key]], stage + ':' + decision['reason'])
        if isolation() != initial:
            return result('manual', 'preflight_evidence_invalidated')
    except (OSError, TimeoutError):
        return result('manual', 'current_authority_or_measurement_unavailable')
    return result('checked', 'preflight_only_activation_pending')

class ManualRecoveryReview:
    """One in-memory human review of a fresh, trusted recovery_preflight callback.

    The composition root must obtain current scope/deletion authority outside the
    backup tree. This is not a JSON/RPC approval endpoint, a lock, or a start
    permit. Restart, invalidate, a second begin or any changed evidence revokes
    the pending review. The operator must confirm both current authorities.
    """
    def __init__(self, check_current):
        if not callable(check_current):
            raise ValueError('current_recovery_check_required')
        self._check = check_current
        self.invalidate()

    def invalidate(self):
        self._ticket = None
        self._snapshot = None

    def begin(self):
        self.invalidate()
        try:
            value = self._check()
        except (OSError, TimeoutError):
            return None, dict(status='manual', reason='current_authority_unavailable', activation_permitted=False)
        if (not isinstance(value, dict) or value.get('status') != 'checked'
                or value.get('activation_permitted') is not False
                or value.get('contract_version') != 1 or not isinstance(value.get('revisions'), dict)
                or not isinstance(value.get('steps'), list) or len(value['steps']) != 3):
            if isinstance(value, dict) and value.get('status') in ('manual', 'reject') and value.get('activation_permitted') is False:
                return None, copy.deepcopy(value)
            return None, dict(status='manual', reason='current_recovery_report_invalid', activation_permitted=False)
        self._snapshot = copy.deepcopy(value)
        self._ticket = object()
        return self._ticket, copy.deepcopy(value)

    def confirm(self, ticket, *, current_permissions_confirmed=False, current_deletions_confirmed=False):
        def result(status, reason):
            return dict(status=status, reason=reason, activation_permitted=False)
        if self._ticket is None or ticket is not self._ticket:
            return result('manual', 'manual_review_missing_or_invalidated')
        snapshot = self._snapshot
        self.invalidate()  # Consume before rechecking, including failed confirmations.
        if current_permissions_confirmed is not True or current_deletions_confirmed is not True:
            return result('reject', 'current_permissions_and_deletions_not_confirmed')
        try:
            current = self._check()
        except (OSError, TimeoutError):
            return result('manual', 'current_authority_unavailable')
        if current != snapshot:
            return result('manual', 'manual_review_evidence_changed')
        return result('reviewed', 'manual_review_current_activation_pending')

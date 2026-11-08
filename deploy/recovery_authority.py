"""08 trusted composition of 07 current deletion reads; no host configuration.

The host supplies an independently approved path/digest and ALL excluded roots.
Use current_authority as 10 recovery_preflight's no-argument callback. The
separate review fingerprint binds 05 manual approval to the complete measured
candidate and authority, without changing 07's decision/evidence semantics.
"""
import copy
import hashlib
import json

from ournotes_bot.sources.history_retention import check_recovery, read_current_authority


def check_current_recovery(candidate, *, preflight, read_isolation, check_identity,
                           check_scope, authority_path, excluded_roots,
                           read_approved_sha256):
    """Wire current 05/07 reads into the separately installed fixed 10 checker.

    preflight is 10's trusted recovery_preflight function, never a user-selected
    callable. check_scope MUST synchronously call the live 05 checkScope on each
    invocation; a saved decision is not this interface. No start is performed.
    """
    return preflight(candidate, read_isolation=read_isolation,
                     check_identity=check_identity, check_scope=check_scope,
                     check_deletion=check_recovery,
                     current_authority=current_authority_reader(
                         path=authority_path, excluded_roots=excluded_roots,
                         read_approved_sha256=read_approved_sha256))


def current_authority_reader(*, path, excluded_roots, read_approved_sha256):
    """Missing host configuration stays unknown; successful reads are not cached."""
    def current_authority():
        if path is None or not callable(read_approved_sha256):
            return None
        return read_current_authority(path, excluded_roots=excluded_roots,
                                      read_approved_sha256=read_approved_sha256)
    return current_authority


def deletion_scope_review(candidate, current_authority):
    """Fresh C6 report + SHA256 for 05; never substitutes for 10's own C6 check.

    Host isolation/ownership and monotonic revocation are still mandatory. The
    double read detects ordinary drift, not A->B->A or hostile concurrent writes.
    No fingerprint is issued for manual/reject; the original decision survives.
    """
    if not callable(current_authority):
        return check_recovery(candidate, current_authority)
    snapshot = copy.deepcopy(candidate)
    observed = []

    def read():
        value = current_authority()
        observed.append(copy.deepcopy(value))
        return value

    report = check_recovery(snapshot, read)
    if report['status'] != 'allow':
        return report
    try:
        if current_authority() != observed[-1]:
            return dict(contract_version=1, status='manual',
                        reason='current_deletion_authority_changed', evidence=None)
    except (OSError, TimeoutError):
        return dict(contract_version=1, status='manual',
                    reason='current_deletion_authority_unavailable', evidence=None)
    material = dict(format='taki-scope-deletion-review/1', candidate=snapshot,
                    authority=observed[-1], decision=report)
    fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':'),
                                           ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    return {**report, 'review_sha256': fingerprint}

"""Emit the isolated Linux acceptance matrix; does not execute any scenario.

Input: optional, non-secret reviewed parameter JSON. Output: explicit not_run
rows and missing inputs. Pos: deploy/INSTANCE_RECOVERY.md. Effects: JSON read and
stdout only. Supplying parameters never authorizes installation or activation.
"""
import argparse
import json
try:
    from .instance_preflight import read_document, digest
except ImportError:
    from instance_preflight import read_document, digest


REQUIRED = ('host_image_digest', 'controller_commit', 'controller_sha256',
            'personal_candidate_commit', 'personal_manifest_sha256',
            'custom_candidate_commit', 'custom_manifest_sha256', 'artifact_files',
            'reviewed_component_mapping', 'instance_inventory', 'rollback_manifest_sha256',
            'scope_policy_reference', 'deletion_authority_reference',
            'backup_fixture_inventory', 'isolated_harness_commit')
SCENARIOS = (
    ('two_instances', 'normal', 'independent users, units, ports, paths and control targets; peer hashes unchanged'),
    ('aliases', 'symlink, hardlink and bind mount overlap', 'reject before writes; no peer control'),
    ('wrong_package', 'wrong AppID/product/channel or component hash', '09 reject; scope/deletion not called'),
    ('first_install', 'interrupt before/after every durable phase', 'previous=null; preserve intent; never invent baseline'),
    ('half_started', 'backend starts but gateway fails', 'isolate both; first install stays stopped, upgrade uses transaction previous'),
    ('gateway_stop', 'stop times out or PID remains', 'do not switch current or copy data; preserve transaction'),
    ('migration', 'incompatible schema or forward-only write', 'manual/forward repair; never boot old reader after one-way write'),
    ('backup_missing', 'remove required DB, WAL, journal or authority coverage from fixture', 'reject/manual; no partial successful restore'),
    ('current_switch', 'rename/fsync failure or power loss', 'diagnosable pointer and transaction; explicit recovery'),
    ('low_disk', 'insufficient backup/staging bytes', 'fail before stop; preserve current and existing backups'),
    ('old_scope', 'restore old wide scope and old policy files', 'independent current approval denies; no fallback to personal'),
    ('old_deletions', 'restore old DB plus old deletion journal', '07 compares external authority; reject mismatches, preserve unknown'),
    ('authority_down', 'missing, unavailable or changing current authority', 'manual; evidence invalidated; stay isolated'),
    ('code_rollback', 'approved retained release; upgrade from B to C fails', 'return to B not historical A; data is not restored'),
    ('controller_rollback', 'older controller against new transaction/storage', 'refuse unsupported format; restore reviewed config/code together only'),
    ('restore_recheck', 'modify restored component/schema/config after preflight', 'invalidate then repeat all checks before any activation'),
    ('host_handoff', 'same AppID on old remote host without stop proof', 'manual; no second receiver'),
    ('upstream_down', 'Haneoka serverless service unavailable', '03-defined failure/stale semantics; no shared local DB assumption'),
)


def matrix(parameters):
    if not isinstance(parameters, dict) or set(parameters) - set(REQUIRED):
        raise ValueError('matrix_parameters_invalid')
    missing = [k for k in REQUIRED if not parameters.get(k)]
    return {'kind': 'linux_acceptance_plan', 'status': 'manual',
            'activation_permitted': False, 'parameters_sha256': digest(parameters),
            'missing': missing, 'execution': 'not_implemented_pending_candidate_and_host',
            'scenarios': [{'id': name, 'inject': fault, 'expected': expected, 'result': 'not_run'}
                          for name, fault, expected in SCENARIOS]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parameters', help='optional JSON, no secrets; see REQUIRED keys')
    args = parser.parse_args(argv)
    print(json.dumps(matrix(read_document(args.parameters) if args.parameters else {}),
                     sort_keys=True, ensure_ascii=True))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())

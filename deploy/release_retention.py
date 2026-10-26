"""L3: plan paired-release retention; execute only inside the updater lock.

Reads trusted manifests and process references. Deletion journals retain ownership
through interrupted rmtree; unknown directories are reported, never adopted by name.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

try:
    from .linux_updater import Rejected, SHA_RE, atomic_json
    from .koishi_control import verify_combination
except ImportError:
    from linux_updater import Rejected, SHA_RE, atomic_json
    from koishi_control import verify_combination

OWNER = 'taki-linux-updater-v1'


def manifest_digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_record(path):
    """The installed root controller only accepts its own non-writable records."""
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024**2
            or path.resolve() != path
            or os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o022)):
        raise Rejected('retention_record_untrusted')
    row = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(row, dict):
        raise Rejected('retention_record_invalid')
    return row


def process_paths(proc=Path('/proc')):
    """Only cwd/executable paths are read, never command lines or environments."""
    if not proc.is_dir():
        return set(), False
    paths = set()
    try:
        for process in proc.iterdir():
            if not process.name.isdigit():
                continue
            for name in ('cwd', 'exe'):
                try:
                    value = os.readlink(process / name)
                    paths.add(value.removesuffix(' (deleted)'))
                except FileNotFoundError:
                    # Exited processes and kernel threads have no such link.
                    continue
        return paths, True
    except OSError:
        return paths, False


def _inside(value, root):
    path = Path(value)
    return path == root or root in path.parents


def _tree(path, *, required=False):
    """Count without following links; rmtree also unlinks, never follows them."""
    if path.is_symlink() or path.resolve() != path:
        raise Rejected('cleanup_path_not_owned')
    if not path.exists():
        if required:
            raise Rejected('retention_directory_missing')
        return 0, None
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise Rejected('retention_directory_invalid')
    size = 0
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in files:
            entry = (Path(parent) / name).lstat()
            if stat.S_ISREG(entry.st_mode):
                size += entry.st_size
    return size, [info.st_dev, info.st_ino]


def _layout(config):
    base, state = Path(config['base']), Path(config['state_dir'])
    combo = Path(config['koishi_combinations']) if config.get('runtime_mode') == 'koishi' else None
    return base, state, combo


def plan(config, protected=(), *, references=None):
    """A fresh read-only observation, not an authorization to delete later."""
    base, state, combos = _layout(config)
    refs, complete = process_paths() if references is None else references
    pinned = config.get('retention_pins', [])
    if (not isinstance(pinned, list) or any(not isinstance(p, str) or not SHA_RE.fullmatch(p) for p in pinned)):
        raise Rejected('retention_pins_invalid')
    guards = {}
    def protect(commit, reason):
        guards.setdefault(commit, set()).add(reason)
    for release in protected:
        protect(release.commit, 'supplied')
    protect(config['baseline']['commit'], 'baseline')
    for sha in pinned:
        protect(sha, 'pinned')
    state_file = state / 'state.json'
    state_known = state_file.exists()
    if state_known:
        current = read_record(state_file)
        for key in ('current', 'previous'):
            if current.get(key):
                protect(current[key]['commit'], key)
        txn = current.get('transaction') or {}
        if txn.get('commit'):
            protect(txn['commit'], 'transaction')
        for key in ('previous', 'candidate'):
            if txn.get(key):
                protect(txn[key]['commit'], 'transaction_' + key)
    # A drifted live symlink is protected independently of the recorded state.
    current_path = base / 'current'
    current_target = current_path.resolve() if current_path.exists() else None
    rows, warnings = {}, []
    def row(sha):
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise Rejected('cleanup_path_not_owned')
        return rows.setdefault(sha, dict(commit=sha, paths=[], records=[], reasons=[], bytes=0, identities={}))
    def add(item, path, required=False):
        size, identity = _tree(path, required=required)
        if str(path) not in item['paths']:
            item['paths'].append(str(path)); item['bytes'] += size
            item['identities'][str(path)] = identity
    manifests = sorted((state / 'releases').glob('*.json'), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    # Validate ownership before interpreting any manifest-provided paths.
    retained = 0
    for path in manifests:
        record = read_record(path)
        release = record.get('release', {})
        sha = release.get('commit')
        item = row(sha)
        root = base / 'releases' / sha
        if (record.get('owner') != OWNER or path.stem != sha
                or release != {'commit': sha, 'code_dir': str(root / 'code'), 'venv_dir': str(root / 'venv')}):
            raise Rejected('cleanup_path_not_owned')
        add(item, root)
        item['records'].append(str(path))
        item['python_hash'] = record.get('bundle_sha256')
        if record.get('prepared') is True and retained < 3:
            protect(sha, 'recent')
            retained += 1
    # New staging records remain after rename, proving ownership independently
    # from the Python manifest; older pairs are accepted only after full checking.
    for path in (state / 'combinations').glob('*.json'):
        record = read_record(path)
        sha = record.get('commit'); item = row(sha)
        if (combos is None or record.get('owner') != OWNER or path.stem != sha
                or record.get('destination') != str(combos / sha)):
            raise Rejected('combination_owner_invalid')
        item['records'].append(str(path))
        item['combination_owner'] = record
        stage = record.get('stage')
        if stage:
            stage_path = Path(stage)
            if (stage_path.parent != combos or not stage_path.name.startswith('.' + sha + '-')
                    or record.get('phase') != 'staging'):
                raise Rejected('combination_stage_invalid')
            add(item, stage_path)
        elif record.get('phase') != 'published':
            raise Rejected('combination_owner_invalid')
    # A trusted deletion intent survives partially removed trees/manifests.
    for path in (state / 'cleanup-intents').glob('*.json'):
        intent = read_record(path); sha = intent.get('commit'); item = row(sha)
        if intent.get('owner') != OWNER or path.stem != sha:
            raise Rejected('cleanup_intent_invalid')
        allowed_records = {str(state / 'releases' / (sha + '.json')), str(state / 'combinations' / (sha + '.json'))}
        if set(intent.get('records', [])) - allowed_records:
            raise Rejected('cleanup_intent_invalid')
        for value in intent.get('paths', []):
            target = Path(value)
            allowed = target == base / 'releases' / sha or combos is not None and (
                target == combos / sha or target.parent == combos and target.name.startswith('.' + sha + '-'))
            if not allowed:
                raise Rejected('cleanup_intent_invalid')
            add(item, target)
            identity = item['identities'][value]
            if identity is not None and identity != intent.get('identities', {}).get(value):
                raise Rejected('cleanup_directory_replaced')
        item['records'] = sorted(set(item['records']) | set(intent['records']))
        item['intent'] = str(path)
    for sha, item in rows.items():
        if combos is not None:
            root = combos / sha
            if root.exists() or root.is_symlink():
                if 'intent' not in item:
                    owner = item.get('combination_owner')
                    expected = owner.get('python_sha256') if owner else item.get('python_hash')
                    if not expected:
                        item['reasons'].append('combination_ownership_unproven')
                    else:
                        try:
                            manifest = verify_combination(root, sha, expected)
                            if owner and manifest_digest(manifest) != owner.get('manifest_sha256'):
                                raise ValueError('manifest changed')
                        except (OSError, ValueError, KeyError, TypeError):
                            item['reasons'].append('combination_invalid')
                add(item, root)
        reasons = guards.get(sha, set())
        if current_target is not None and _inside(current_target, base / 'releases' / sha):
            reasons.add('current_path')
        if any(_inside(value, Path(path)) for path in item['paths'] for value in refs):
            reasons.add('active_process')
        if not complete:
            reasons.add('process_references_unknown')
        if not state_known:
            reasons.add('state_unavailable')
        item['reasons'] = sorted(set(item['reasons']) | reasons)
        item['action'] = 'keep' if item['reasons'] else 'delete'
        for key in ('python_hash', 'combination_owner'):
            item.pop(key, None)
    known = {p for item in rows.values() for p in item['paths']}
    for parent in (base / 'releases', combos):
        if parent is not None and parent.exists():
            for path in parent.iterdir():
                baseline_code = Path(config['baseline']['code_dir'])
                if str(path) not in known and path not in (baseline_code, baseline_code.parent):
                    warnings.append({'path': str(path), 'reason': 'unmanaged_directory'})
    entries = sorted(rows.values(), key=lambda item: item['commit'])
    return {'schema': 1, 'read_only': True, 'entries': entries, 'warnings': warnings,
            'baseline': config['baseline'],
            'reclaim_bytes': sum(r['bytes'] for r in entries if r['action'] == 'delete')}


def execute(config, protected=()):
    """Caller holds update.lock. Re-plan before each destructive step."""
    _, state, _ = _layout(config)
    observed = plan(config, protected)
    atomic_json(state / 'cleanup-plan.json', observed)
    for candidate in observed['entries']:
        if candidate['action'] != 'delete':
            continue
        sha = candidate['commit']
        current = next((r for r in plan(config, protected)['entries'] if r['commit'] == sha), None)
        if current is None or current['action'] != 'delete':
            continue
        journal = state / 'cleanup-intents' / (sha + '.json')
        journal.parent.mkdir(mode=0o700, exist_ok=True)
        atomic_json(journal, {key: current[key] for key in ('commit', 'paths', 'records', 'identities')} | {'owner': OWNER})
        for value in reversed(current['paths']):
            refreshed = next(r for r in plan(config, protected)['entries'] if r['commit'] == sha)
            if refreshed['action'] != 'delete':
                break
            target = Path(value)
            if target.exists():
                _tree(target, required=True)
                shutil.rmtree(target)
        else:
            for value in current['records']:
                Path(value).unlink(missing_ok=True)
            for log in (state / 'logs').glob(sha + '-*.log'):
                if log.is_symlink():
                    raise Rejected('cleanup_log_invalid')
                log.unlink()
            journal.unlink()
    return observed

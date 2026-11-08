"""Private inherited-FD recovery session for two measured local child entries.

No listening socket, RPC router or disk grant. The controller holds the instance
FileLock. Waiting children cannot import their business entry until both are ready
and a fresh stopped-state manual review commits the barrier. Running checks use a
separate current-authority reader, never a replay of writers_stopped evidence.
"""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import threading
import time


def _line(sock, raw=None):
    raw = bytearray() if raw is None else raw
    while len(raw) < 4096:
        part = sock.recv(1)
        if not part:
            raise ConnectionError('recovery_controller_disconnected')
        if part == b'\n':
            value = json.loads(raw)
            raw.clear()
            return value
        raw.extend(part)
    raise ValueError('recovery_frame_too_large')


def _send(sock, value):
    sock.sendall(json.dumps(value, separators=(',', ':')).encode() + b'\n')


class RecoverySession:
    """Only the trusted controller may construct this object inside IsolatedRuntime.

    read_running() must freshly return exactly {status:'current', revisions:{the
    nine REVISION_FIELDS hashes}} from current authority/schema/component epochs.
    It must be thread-safe and must not return a recovery_preflight report.
    read_stopped() is invoked only after direct child exit; it must additionally
    prove all host writers/triggers stopped, or return unknown. No default reader.
    """
    def __init__(self, runtime, *, read_running, read_stopped, drain_timeout=10, startup_timeout=120):
        if not callable(read_running) or not callable(read_stopped):
            raise ValueError('running_and_stopped_authority_readers_required')
        if not 0 < drain_timeout <= 60:
            raise ValueError('invalid_drain_timeout')
        if type(startup_timeout) is not int or not 1 <= startup_timeout <= 300:
            raise ValueError('invalid_startup_timeout')
        if runtime._measure() != runtime.installed or runtime.children or runtime.recovery_session is not None:
            raise ValueError('fresh_measured_runtime_required')
        self.runtime = runtime
        self.read_running = read_running
        self.read_stopped = read_stopped
        self.guard = threading.Lock()
        self.phase = 'waiting'
        self.reason = None
        self.roles = {}
        self.session = secrets.token_hex(32)
        self.generation = runtime.generation
        self.baseline = None
        self.startup_timeout = startup_timeout
        self.deadline = time.monotonic() + startup_timeout
        self.drain_timeout = drain_timeout
        self.pending = 0
        self.stop_error = None
        self.forced = False
        self.monitor_started = False
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.wrappers = {role: str(Path(__file__).with_name('recovery_session.' + ext))
                         for role, ext in (('backend', 'py'), ('gateway', 'cjs'))}
        self.wrapper_hashes = self._measure_wrappers()
        runtime.recovery_session = self

    def _measure_wrappers(self):
        from .linux_backend import trusted
        from .recovery_preflight import measure_components
        for path in self.wrappers.values():
            trusted(Path(path))
        return measure_components(self.wrappers)

    def approve_start(self, confirm_current):
        """Measure the session before issuing the existing 30-second start tickets.

        No role exists yet. This is an explicit fresh review, never extension or
        renewal of an old ticket. commit() still reviews again with both waiting.
        """
        with self.guard:
            if self.phase != 'waiting' or self.roles:
                raise ValueError('fresh_waiting_session_required')
            self.phase = 'preparing'
        try:
            result, tickets = self.runtime.approve(confirm_current)
            if result['status'] != 'reviewed':
                self.revoke('initial_review_not_current')
                return result, {}
            with self.guard, self.runtime.guard:
                if (self.phase != 'preparing' or set(tickets) != {'backend', 'gateway'}
                        or any(t not in self.runtime.permits or self.runtime.permits[t][1] != self.runtime.generation
                               for t in tickets.values())):
                    raise ValueError('initial_review_invalidated')
                self.generation, self.phase = self.runtime.generation, 'waiting'
            self._alive()
            return result, tickets
        except BaseException:
            self.revoke('initial_review_failed')
            raise

    def prepare_child(self, role):
        if self._measure_wrappers() != self.wrapper_hashes:
            raise ValueError('recovery_wrapper_changed')
        with self.guard:
            if self.phase != 'waiting' or role in self.roles or role not in ('backend', 'gateway'):
                raise ValueError('recovery_role_not_available')
            parent, child = socket.socketpair()
            parent.settimeout(.1)
            # Remains blocking, including for Node's synchronous fs.readSync.
            child.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, struct.pack('ll', 1, 0))
            child.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, struct.pack('ll', 1, 0))
            binding = dict(version=1, session=self.session, instance=self.runtime.settings['instance'],
                           role=role, generation=self.generation, boot=self.boot,
                           startup_timeout=self.startup_timeout)
            self.roles[role] = dict(parent=parent, child=child, binding=binding,
                                    process=None, ready=False, sequence=0, stage=None)
            return child.fileno(), binding

    def attach(self, role, process):
        with self.guard:
            row = self.roles[role]
            row['process'] = process
            row['child'].close()  # Parent must not keep the peer alive after exec.
            monitor = not self.monitor_started
            self.monitor_started = True
        threading.Thread(target=self._serve, args=(role,), daemon=True).start()
        if monitor:
            threading.Thread(target=self._monitor, daemon=True).start()

    def _monitor(self):
        # Frame delivery must not starve timeout/current-authority observation.
        while True:
            with self.guard:
                if self.phase not in ('waiting', 'preparing', 'active'):
                    return
            try:
                self._alive()
            except Exception as exc:
                self.revoke(type(exc).__name__ + ':' + str(exc))
                return
            time.sleep(.1)

    def revoke(self, reason='recovery_session_revoked'):
        with self.guard:
            if self.phase in ('quiesce', 'drain', 'close', 'stopped'):
                return
            self.phase, self.reason = 'quiesce', reason
        self.runtime.invalidate()
        # Leave inherited channels available for lifecycle receipts. Authorization
        # is already sticky-revoked; a missing/uncooperative peer gets bounded kill.
        threading.Thread(target=self._force_stop, daemon=True).start()

    def _force_stop(self):
        end = time.monotonic() + self.drain_timeout
        while time.monotonic() < end:
            if all(p.poll() is not None for p in self.runtime.children):
                return
            time.sleep(.02)
        try:
            self.forced = True
            self.runtime._stop_children()
        except Exception as exc:
            self.stop_error = type(exc).__name__

    def _running(self):
        try:
            from .recovery_preflight import REVISION_FIELDS
        except ImportError:
            from recovery_preflight import REVISION_FIELDS
        value = self.read_running()
        if (not isinstance(value, dict) or set(value) != {'status', 'revisions'}
                or value['status'] != 'current' or not isinstance(value['revisions'], dict)
                or set(value['revisions']) != REVISION_FIELDS
                or any(not isinstance(v, str) or len(v) != 64 or any(c not in '0123456789abcdef' for c in v)
                       for v in value['revisions'].values())):
            raise ValueError('running_authority_unknown')
        return copy.deepcopy(value)

    def _alive(self):
        lock = self.runtime.lock
        if lock is None or lock.fd is None:
            raise ValueError('recovery_lock_lost')
        held, current = os.fstat(lock.fd), os.stat(lock.path, follow_symlinks=False)
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError('recovery_lock_replaced')
        with self.guard:
            phase = self.phase
            processes = [r['process'] for r in self.roles.values()]
        if phase not in ('waiting', 'preparing', 'active'):
            return
        if phase != 'active' and time.monotonic() >= self.deadline:
            raise ValueError('recovery_barrier_timeout')
        if any(p is not None and p.poll() is not None for p in processes):
            raise ValueError('recovery_role_exited')
        if phase == 'active' and self._running() != self.baseline:
            raise ValueError('running_authority_changed')
        with self.guard, self.runtime.guard:
            if self.phase in ('waiting', 'active') and self.generation != self.runtime.generation:
                raise ValueError('recovery_generation_invalidated')

    def _serve(self, role):
        row = self.roles[role]
        buffer = bytearray()
        try:
            while True:
                try:
                    self._alive()
                except Exception as exc:
                    self.revoke(type(exc).__name__ + ':' + str(exc))
                try:
                    request = _line(row['parent'], buffer)
                except socket.timeout:
                    continue
                if (not isinstance(request, dict) or set(request) != {'binding', 'op', 'sequence', 'facts'}
                        or request['binding'] != row['binding'] or type(request['sequence']) is not int
                        or request['sequence'] != row['sequence'] + 1
                        or request['op'] not in ('wait', 'assert', 'state', 'quiesced', 'drained', 'closed')):
                    raise ValueError('recovery_child_binding_invalid')
                row['sequence'] = request['sequence']
                try:
                    self._alive()
                except Exception as exc:
                    self.revoke(type(exc).__name__ + ':' + str(exc))
                with self.guard:
                    if request['op'] == 'wait':
                        row['ready'] = True
                    self._receipt(role, request['op'], request['facts'])
                    # Never answer active after cancellation while a reader ran.
                    with self.runtime.guard:
                        state = self.phase
                        if state == 'active' and self.generation != self.runtime.generation:
                            state = 'quiesce'
                        _send(row['parent'], dict(sequence=request['sequence'], state=state))
        except Exception:
            self.revoke('recovery_channel_or_authority_lost')
        finally:
            row['parent'].close()

    def _receipt(self, role, op, facts):
        if op in ('wait', 'assert', 'state'):
            if facts is not None:
                raise ValueError('unexpected_recovery_facts')
            return
        expected = {'quiesced': ('backend', 'quiesce'), 'drained': ('gateway', 'drain'),
                    'closed': ('backend', 'close')}
        if expected[op] != (role, self.phase):
            raise ValueError('recovery_lifecycle_out_of_order')
        if op == 'quiesced':
            if (not isinstance(facts, dict) or set(facts) != {'business_writers_stopped', 'receipt_tail_open', 'pending_terminations'}
                    or facts['business_writers_stopped'] is not True or facts['receipt_tail_open'] is not True):
                raise ValueError('backend_not_quiesced')
        elif op == 'closed':
            if not isinstance(facts, dict) or set(facts) != {'stores_closed', 'pending_terminations'} or facts['stores_closed'] is not True:
                raise ValueError('backend_not_closed')
        elif not isinstance(facts, dict) or set(facts) != {'gateway_drained'} or facts['gateway_drained'] is not True:
            raise ValueError('gateway_not_drained')
        if op != 'drained':
            pending = facts['pending_terminations']
            if type(pending) is not int or pending < 0:
                raise ValueError('pending_terminations_unknown')
            self.pending = max(self.pending, pending)
        self.roles[role]['stage'] = op
        self.phase = {'quiesced': 'drain', 'drained': 'close', 'closed': 'stopped'}[op]

    def commit(self, confirm_current):
        """Both children still have no business objects. Re-review, then release both."""
        self._alive()
        with self.guard:
            if self.phase != 'waiting' or set(self.roles) != {'backend', 'gateway'} or not all(
                    r['ready'] and r['process'] is not None for r in self.roles.values()):
                return dict(status='manual', reason='both_roles_not_ready', activation_permitted=False)
            self.phase = 'preparing'
        try:
            result, _ = self.runtime.approve(confirm_current)
            with self.runtime.guard:
                snapshots = [p[3]['revisions'] for p in self.runtime.permits.values()]
                self.runtime.permits.clear()  # Barrier commit never exports extra start tickets.
                generation = self.runtime.generation
            if result['status'] != 'reviewed':
                self.revoke('stopped_review_not_current')
                return result
            baseline = self._running()
            if len(snapshots) != 2 or any(s != baseline['revisions'] for s in snapshots):
                raise ValueError('running_authority_not_bound_to_review')
            self._alive()
            with self.guard, self.runtime.guard:
                if self.phase != 'preparing' or generation != self.runtime.generation:
                    raise ValueError('recovery_barrier_invalidated')
                self.baseline, self.generation, self.phase = baseline, generation, 'active'
            return dict(status='isolated_active', activation_permitted=False)
        except BaseException:
            self.revoke('recovery_commit_failed')
            raise

    def confirm_stopped(self, timeout=None):
        """Revoke first; wait for 08 drain/exit before forced group termination.

        Direct process exit is not host writer proof. A missing/unknown host proof
        stays manual; no restore is authorized by this receipt.
        """
        if threading.get_ident() != self.runtime.owner_thread:
            raise ValueError('controller_session_thread_mismatch')
        self.revoke()
        deadline = time.monotonic() + (self.drain_timeout if timeout is None else timeout)
        processes = [r['process'] for r in self.roles.values() if r['process'] is not None]
        while any(p.poll() is None for p in processes) and time.monotonic() < deadline:
            time.sleep(.02)
        self.forced = self.forced or any(p.poll() is None for p in processes)
        self.runtime._stop_children()
        if any(p.poll() is None for p in processes):
            return dict(status='manual', reason='children_not_stopped', activation_permitted=False)
        try:
            proof = self.read_stopped()
        except Exception:
            proof = None
        known = (isinstance(proof, dict) and set(proof) == {'writers_stopped', 'evidence'}
                 and proof['writers_stopped'] is True and isinstance(proof['evidence'], str) and bool(proof['evidence']))
        known = known and not self.pending and self.stop_error is None
        for row in self.roles.values():
            row['parent'].close()
            row['child'].close()
        return dict(status='stopped' if known else 'manual', forced=self.forced,
                    orderly=self.phase == 'stopped' and not self.forced, pending_terminations=self.pending,
                    stop_error=self.stop_error,
                    reason='writers_stopped' if known else 'host_writer_stop_unknown',
                    evidence=proof.get('evidence') if known else None, activation_permitted=False)


class ChildSession:
    """Sticky authorization failure; lifecycle channel survives an explicit revoke."""
    def __init__(self):
        self.socket = socket.socket(fileno=int(os.environ.pop('TAKI_RECOVERY_FD')))
        self.binding = json.loads(os.environ.pop('TAKI_RECOVERY_BINDING'))
        if type(self.binding.get('startup_timeout')) is not int or not 1 <= self.binding['startup_timeout'] <= 300:
            self.socket.close()
            raise ValueError('invalid_startup_timeout')
        self.sequence = 0
        self.closed = False
        self.revoked = False
        self.guard = threading.Lock()

    def _request(self, op, facts=None):
        with self.guard:
            if self.closed:
                raise RuntimeError('recovery_session_closed')
            try:
                self.sequence += 1
                _send(self.socket, dict(binding=self.binding, op=op, sequence=self.sequence, facts=facts))
                reply = _line(self.socket)
                if set(reply) != {'sequence', 'state'} or reply['sequence'] != self.sequence:
                    raise ValueError('recovery_response_invalid')
                return reply['state']
            except (OSError, ValueError, TypeError):
                self.closed = True
                self.socket.close()
                raise RuntimeError('recovery_session_closed') from None

    def wait(self):
        deadline = time.monotonic() + self.binding['startup_timeout']
        while time.monotonic() < deadline:
            state = self._request('wait')
            if state == 'active':
                return
            if state not in ('waiting', 'preparing'):
                break
            time.sleep(.02)
        self.closed = True
        self.socket.close()
        raise RuntimeError('recovery_barrier_timeout')

    def assert_current(self):
        if self.revoked or self._request('assert') != 'active':
            self.revoked = True
            raise RuntimeError('recovery_session_closed')
        # Exactly None, as required by 08 backend_bootstrap.

    def state(self):
        return self._request('state')

    def report(self, stage, facts):
        return self._request(stage, facts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--entry', required=True)
    args = parser.parse_args()
    client = ChildSession()
    client.wait()
    client.assert_current()
    spec = importlib.util.spec_from_file_location('trusted_recovery_entry', args.entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.start(client)

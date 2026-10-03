"""Durable update transactions; Linux/systemd side effects belong to the backend.

L3 Input: A trusted backend and a controller-owned state directory.
Output: RunResult and atomically replaced state; no application data restoration.
Pos: Linux deployment transaction core; see deploy/L2.md.
Effects: Exclusive file lock, fsync state writes, and explicitly delegated effects.

Backend contract:
* current_release returns canonical, verified code/venv paths, including the local
  hotfix baseline, rather than merely reading the application's Git HEAD.
* resolve_main returns one immutable full Git SHA. check_ci requires successful
  checks for that SHA. Deferred is for network/CI pending; Rejected is permanent.
* prepare installs and tests an isolated candidate as the application account,
  with no live credentials/data, bounded resources, and no QQ/network test calls.
* verify_compatibility must raise unless the forward AND rollback readers/writers
  are compatible with shared data and required production fixes are preserved.
  Passing tests alone is not evidence of data or hotfix compatibility.
* backup preserves the old code/environment and required recovery metadata before
  stopping. Its opaque reference must contain no credentials. It never implies
  that shared data should be restored on rollback.
* stop/activate/start are idempotent. activate must safely handle interruption
  between code/venv pointer changes; stop must confirm the old process has ended.
* healthy checks this release's current PID, fresh QQ-ready evidence, errors and
  restart stability within a bounded timeout, without sending QQ messages.
* cleanup protects every supplied release and enforces disk/retention limits.

All methods must have bounded execution. The controller, backend, lock and state
must be protected from candidate writes. Never execute candidate code as root.
Systemd should recover transactions before starting the application after reboot.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import time
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol


SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
SAFE_PHASES = frozenset({"preparing", "verifying", "backing_up"})
MUTATING_PHASES = frozenset({
    "stopping", "activating", "starting", "checking_health",
    "rollback_stopping", "rollback_activating", "rollback_starting", "rollback_health",
})


class UpdateError(Exception):
    """A public, non-secret machine code; never pass command output here."""

    def __init__(self, code: str):
        if not re.fullmatch(r"[a-z0-9_.-]{1,80}", code):
            raise ValueError("invalid public error code")
        self.code = code
        super().__init__(code)


class Deferred(UpdateError):
    """Transient resolution or CI failure: retry at the next scheduled check."""


class Rejected(UpdateError):
    """Block this immutable commit until an explicit operator retry."""


class StateError(UpdateError):
    """State is unreadable or not durable; do not start further operations."""


class LockBusy(Exception):
    pass


@dataclass(frozen=True)
class Release:
    commit: str
    code_dir: str
    venv_dir: str

    def __post_init__(self):
        if not isinstance(self.commit, str) or not SHA_RE.fullmatch(self.commit):
            raise ValueError("release requires a full immutable commit SHA")
        for value in (self.code_dir, self.venv_dir):
            if not isinstance(value, str) or not value or "\0" in value:
                raise ValueError("release requires canonical code and venv paths")


class Backend(Protocol):
    def current_release(self) -> Release: ...
    def resolve_main(self) -> str: ...
    def check_ci(self, commit: str) -> None: ...
    def prepare(self, commit: str) -> Release: ...
    def verify_compatibility(self, previous: Release, candidate: Release) -> None: ...
    def backup(self, previous: Release, candidate: Release) -> str: ...
    def stop(self) -> None: ...
    def activate(self, release: Release) -> None: ...
    def start(self) -> None: ...
    def healthy(self, release: Release) -> bool: ...
    def cleanup(self, protected: tuple[Release, ...]) -> None: ...


@dataclass(frozen=True)
class RunResult:
    status: str
    commit: str | None = None
    reason: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _code(exc: Exception) -> str:
    # Exceptions/subprocess output can contain secrets; persist type names only.
    return exc.code if isinstance(exc, UpdateError) else type(exc).__name__


def atomic_json(path: Path, value: dict, *, mode: int = 0o600) -> None:
    """Replace a state file only after fsync; fsync its parent on Linux as well."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".state-", delete=False,
        ) as stream:
            temporary = stream.name
            if os.name == "posix":
                os.fchmod(stream.fileno(), mode)
            else:
                os.chmod(temporary, mode)
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        if os.name == "posix":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except (OSError, TypeError, ValueError) as exc:
        raise StateError("state_write_failed") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class FileLock:
    """Nonblocking advisory lock; do not unlink the lock file after release."""

    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def __enter__(self):
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        self.fd = os.open(self.path, flags, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b"\0")
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            self.fd = None
            raise LockBusy() from exc
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Engine:
    """One timer invocation. This class never starts a scheduler or sends notices."""

    def __init__(self, state_dir: str | Path, backend: Backend,
                 lock_factory: Callable = FileLock):
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / "state.json"
        self.backend = backend
        self.lock_factory = lock_factory

    def _ensure_state_directory(self):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
        # The ubuntu launcher needs metadata access even with root's 0077 umask.
        if os.name == "posix":
            self.state_dir.chmod(0o755)

    def load_state(self) -> dict | None:
        try:
            with self.state_file.open(encoding="utf-8") as stream:
                state = json.load(stream)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise StateError("state_read_failed") from exc
        try:
            if state["version"] != 1:
                raise ValueError("version")
            Release(**state["current"])
            if state.get("previous") is not None:
                Release(**state["previous"])
            if not isinstance(state["failed"], dict) or not isinstance(state["events"], list):
                raise ValueError("records")
            for commit in state["failed"]:
                if not SHA_RE.fullmatch(commit):
                    raise ValueError("failed commit")
            retries = state.get("download_retries", {})
            if not isinstance(retries, dict):
                raise ValueError("download retries")
            for sha, row in retries.items():
                if (not SHA_RE.fullmatch(sha) or type(row["attempts"]) is not int
                        or not 1 <= row["attempts"] <= 3
                        or not isinstance(row["after"], (int, float)) or not math.isfinite(row["after"])):
                    raise ValueError("download retries")
            transaction = state.get("transaction")
            if transaction is not None:
                if transaction["phase"] not in SAFE_PHASES | MUTATING_PHASES:
                    raise ValueError("phase")
                if not SHA_RE.fullmatch(transaction["commit"]):
                    raise ValueError("commit")
                if Release(**transaction["previous"]) != Release(**state["current"]):
                    raise ValueError("previous is not known good")
                candidate = transaction.get("candidate")
                if candidate is not None:
                    if Release(**candidate).commit != transaction["commit"]:
                        raise ValueError("candidate commit")
                if transaction["phase"] in MUTATING_PHASES:
                    if candidate is None or transaction.get("compatible") is not True:
                        raise ValueError("candidate was not approved")
                    if not isinstance(transaction.get("backup"), str) or not transaction["backup"]:
                        raise ValueError("missing backup")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise StateError("state_invalid") from exc
        return state

    def _save(self, state: dict) -> None:
        state["updated_at"] = _now()
        # Metadata only: the unprivileged launcher reads this root-owned journal
        # to reject an uncommitted release after reboot. Never put secrets here.
        atomic_json(self.state_file, state, mode=0o644)

    def _event(self, state: dict, status: str, commit=None, reason=None):
        state["events"].append(dict(at=_now(), status=status, commit=commit, reason=reason))
        del state["events"][:-32]

    def _phase(self, state: dict, phase: str):
        state["transaction"]["phase"] = phase
        self._save(state)

    def _failed(self, state: dict, commit: str, reason: str):
        # Keep failures, including superseded SHAs: main can move backwards too.
        state["failed"][commit] = dict(at=_now(), reason=reason)

    def _pause(self, state: dict, reason: str, commit=None) -> RunResult:
        state["paused"] = reason
        self._event(state, "paused", commit, reason)
        self._save(state)
        return RunResult("paused", commit, reason)

    def _cleanup(self, state: dict):
        protected = [Release(**state["current"])]
        if state.get("previous"):
            protected.append(Release(**state["previous"]))
        try:
            self.backend.cleanup(tuple(protected))
        except Exception as exc:
            # A retention failure must not roll back an otherwise healthy release.
            self._event(state, "cleanup_failed", reason=_code(exc))
            self._save(state)

    def _rollback(self, state: dict, reason: str) -> RunResult:
        transaction = state["transaction"]
        commit = transaction["commit"]
        previous = Release(**transaction["previous"])
        self._failed(state, commit, reason)
        try:
            self._phase(state, "rollback_stopping")
            self.backend.stop()
            self._phase(state, "rollback_activating")
            self.backend.activate(previous)
            self._phase(state, "rollback_starting")
            self.backend.start()
            self._phase(state, "rollback_health")
            if not self.backend.healthy(previous):
                raise Rejected("rollback_unhealthy")
        except StateError:
            raise
        except Exception as exc:
            return self._pause(state, "rollback_failed." + _code(exc), commit)
        state["transaction"] = None
        self._event(state, "rolled_back", commit, reason)
        self._save(state)
        self._cleanup(state)
        return RunResult("rolled_back", commit, reason)

    def _run(self, recover_only: bool) -> RunResult:
        state = self.load_state()
        if state is None:
            if recover_only:
                return RunResult("uninitialized")
            baseline = self.backend.current_release()
            if not self.backend.healthy(baseline):
                return RunResult("paused", baseline.commit, "baseline_not_healthy")
            state = dict(version=1, current=asdict(baseline), previous=None,
                         transaction=None, failed={}, events=[], paused=None)
            self._event(state, "initialized", baseline.commit)
            self._save(state)
        if state.get("paused"):
            return RunResult("paused", reason=state["paused"])
        if state.get("transaction"):
            transaction = state["transaction"]
            if transaction["phase"] in SAFE_PHASES:
                # The durable intent to stop is always written before stop. These
                # earlier phases cannot have changed service pointers/processes.
                if self.backend.current_release() != Release(**state["current"]):
                    return self._pause(state, "release_drift_during_preparation")
                self._failed(state, transaction["commit"], "interrupted_preparation")
                self._event(state, "abandoned", transaction["commit"], "interrupted_preparation")
                state["transaction"] = None
                self._save(state)
                self._cleanup(state)
                return RunResult("recovered", transaction["commit"], "interrupted_preparation")
            # Recovery is its own invocation: never fetch another candidate before
            # rollback health is proven and its commit is durably recorded.
            return self._rollback(state, "interrupted_transaction")
        if recover_only:
            return RunResult("no_transaction", state["current"]["commit"])
        previous = Release(**state["current"])
        if self.backend.current_release() != previous:
            return self._pause(state, "release_drift")
        try:
            commit = self.backend.resolve_main()
            if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
                raise Deferred("invalid_remote_commit")
        except Exception as exc:
            return RunResult("deferred", reason=_code(exc))
        if commit == previous.commit:
            return RunResult("up_to_date", commit)
        if commit in state["failed"]:
            return RunResult("blocked", commit, state["failed"][commit]["reason"])
        retry = state.get("download_retries", {}).get(commit)
        if retry and time.time() < retry["after"]:
            return RunResult("deferred", commit, "bundle_retry_backoff")
        try:
            self.backend.check_ci(commit)
        except Deferred as exc:
            return RunResult("deferred", commit, _code(exc))
        except Exception as exc:
            # Network errors should be wrapped by the backend in Deferred.
            self._failed(state, commit, _code(exc))
            self._event(state, "rejected", commit, _code(exc))
            self._save(state)
            return RunResult("rejected", commit, _code(exc))
        state["transaction"] = dict(commit=commit, previous=asdict(previous),
                                    candidate=None, phase="preparing", compatible=False,
                                    backup=None, started_at=_now())
        self._save(state)
        try:
            candidate = self.backend.prepare(commit)
            if not isinstance(candidate, Release) or candidate.commit != commit:
                raise Rejected("prepared_commit_mismatch")
            if candidate.code_dir == previous.code_dir or candidate.venv_dir == previous.venv_dir:
                raise Rejected("candidate_not_isolated")
            state["transaction"]["candidate"] = asdict(candidate)
            self._phase(state, "verifying")
            self.backend.verify_compatibility(previous, candidate)
            state["transaction"]["compatible"] = True
            self._phase(state, "backing_up")
            backup = self.backend.backup(previous, candidate)
            if not isinstance(backup, str) or not backup:
                raise Rejected("missing_backup")
            state["transaction"]["backup"] = backup
        except StateError:
            raise
        except Deferred as exc:
            # Only download preparation is retryable. No service has been stopped.
            if state["transaction"]["phase"] != "preparing":
                self._failed(state, commit, _code(exc))
                status, reason = "rejected", _code(exc)
            else:
                retries = state.setdefault("download_retries", {})
                attempts = retries.get(commit, {}).get("attempts", 0) + 1
                retries[commit] = {"attempts": attempts, "after": time.time() + 300 * 3 ** (attempts - 1)}
                status, reason = "deferred", _code(exc)
                if attempts >= 3:
                    status, reason = "rejected", "bundle_retry_exhausted"
                    self._failed(state, commit, reason)
            state["transaction"] = None
            self._event(state, status, commit, reason)
            self._save(state)
            self._cleanup(state)
            return RunResult(status, commit, reason)
        except Exception as exc:
            self._failed(state, commit, _code(exc))
            state["transaction"] = None
            self._event(state, "rejected", commit, _code(exc))
            self._save(state)
            self._cleanup(state)
            return RunResult("rejected", commit, _code(exc))
        return self._switch(state, previous, candidate)

    def _switch(self, state: dict, previous: Release, candidate: Release,
                *, manual: bool = False) -> RunResult:
        commit = candidate.commit
        try:
            self._phase(state, "stopping")
            self.backend.stop()
            self._phase(state, "activating")
            self.backend.activate(candidate)
            self._phase(state, "starting")
            self.backend.start()
            self._phase(state, "checking_health")
            if not self.backend.healthy(candidate):
                raise Rejected("candidate_unhealthy")
        except StateError:
            raise
        except Exception as exc:
            return self._rollback(state, _code(exc))
        state["previous"] = asdict(previous)
        state["current"] = asdict(candidate)
        state["transaction"] = None
        if manual:
            # A timer that still sees the rejected main SHA must not immediately
            # undo the operator's restore. Only an explicit retry may clear it.
            self._failed(state, previous.commit, "operator_rollback")
        self._event(state, "operator_rollback" if manual else "updated", commit)
        self._save(state)
        self._cleanup(state)
        return RunResult("rolled_back" if manual else "updated", commit,
                         "operator_requested" if manual else None)

    def rollback(self) -> RunResult:
        """Explicitly restore the retained, previously validated release.

        ``previous`` is saved only after a successful transition whose gate
        approved both forward and rollback data compatibility. Reuse that trusted
        approval rather than today's forward CI/policy (which may be unavailable
        during an incident). Backend.activate must revalidate immutable retained
        release paths/content. No shared data is rewound. If restoration fails,
        recover the release that was active before this command instead.
        """
        try:
            self._ensure_state_directory()
            with self.lock_factory(self.state_dir / "update.lock"):
                state = self.load_state()
                if state is None:
                    return RunResult("uninitialized")
                if state.get("paused"):
                    return RunResult("paused", reason=state["paused"])
                if state.get("transaction"):
                    return RunResult("paused", reason="recover_transaction_first")
                if state.get("previous") is None:
                    return RunResult("rejected", reason="no_previous_release")
                current = Release(**state["current"])
                target = Release(**state["previous"])
                if self.backend.current_release() != current:
                    return self._pause(state, "release_drift")
                if target == current:
                    return RunResult("rejected", reason="previous_equals_current")
                state["transaction"] = dict(
                    commit=target.commit, previous=asdict(current), candidate=asdict(target),
                    phase="backing_up", compatible=True, backup=None, started_at=_now(),
                    manual=True,
                )
                self._save(state)
                try:
                    backup = self.backend.backup(current, target)
                    if not isinstance(backup, str) or not backup:
                        raise Rejected("missing_backup")
                    state["transaction"]["backup"] = backup
                except StateError:
                    raise
                except Exception as exc:
                    state["transaction"] = None
                    self._event(state, "manual_rollback_rejected", target.commit, _code(exc))
                    self._save(state)
                    return RunResult("rejected", target.commit, _code(exc))
                return self._switch(state, current, target, manual=True)
        except LockBusy:
            return RunResult("busy", reason="another_controller_holds_lock")
        except StateError as exc:
            return RunResult("state_error", reason=_code(exc))
        except Exception as exc:
            return RunResult("error", reason=_code(exc))

    def run(self, *, recover_only: bool = False) -> RunResult:
        try:
            self._ensure_state_directory()
            with self.lock_factory(self.state_dir / "update.lock"):
                return self._run(recover_only)
        except LockBusy:
            return RunResult("busy", reason="another_controller_holds_lock")
        except StateError as exc:
            return RunResult("state_error", reason=_code(exc))
        except Exception as exc:
            # Do not print exception messages, environment variables or commands.
            return RunResult("error", reason=_code(exc))

    def resume(self, *, retry_commit: str | None = None) -> RunResult:
        """Operator-only action; never call automatically from the timer.

        Resuming a failed rollback keeps its transaction: run(recover_only=True)
        must finish recovery. Explicit retry removes only the named failed SHA.
        """
        if retry_commit is not None and not SHA_RE.fullmatch(retry_commit):
            raise ValueError("retry requires a full immutable commit SHA")
        try:
            self._ensure_state_directory()
            with self.lock_factory(self.state_dir / "update.lock"):
                state = self.load_state()
                if state is None:
                    return RunResult("uninitialized")
                state["paused"] = None
                if retry_commit is not None:
                    state["failed"].pop(retry_commit, None)
                    state.get("download_retries", {}).pop(retry_commit, None)
                self._event(state, "operator_resumed", retry_commit)
                self._save(state)
                return RunResult("resumed", retry_commit)
        except LockBusy:
            return RunResult("busy", reason="another_controller_holds_lock")
        except StateError as exc:
            return RunResult("state_error", reason=_code(exc))
        except Exception as exc:
            return RunResult("error", reason=_code(exc))

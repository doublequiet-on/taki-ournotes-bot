"""Offline update fault injection: no systemd, network, installation, or QQ."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "taki_linux_updater", Path(__file__).resolve().parents[1] / "deploy/linux_updater.py",
)
u = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = u
SPEC.loader.exec_module(u)
A, B, C = (character * 40 for character in "abc")


def release(commit):
    return u.Release(commit, "/releases/" + commit + "/code", "/releases/" + commit + "/venv")


class PowerLoss(BaseException):
    """A killed controller does not execute its ordinary exception handlers."""


class FakeBackend:
    def __init__(self):
        self.current = release(A)
        self.remote = B
        self.running = True
        self.calls = []
        self.failures = {}
        self.health = {A: True, B: True, C: True}
        self.shared_data = {"counter": 8}
        self.state_path = None
        self.observed_phases = []
        self.protected = ()

    def _call(self, name, effect=lambda: None):
        self.calls.append(name)
        if name in self.failures:
            error = self.failures.pop(name)
            raise error
        result = effect()
        if name + ":after" in self.failures:
            raise self.failures.pop(name + ":after")
        return result

    def _phase(self):
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        phase = state["transaction"]["phase"]
        self.observed_phases.append(phase)
        return phase

    def current_release(self):
        return self._call("current", lambda: self.current)

    def resolve_main(self):
        return self._call("resolve", lambda: self.remote)

    def check_ci(self, commit):
        self._call("ci:" + commit)

    def prepare(self, commit):
        self._call("install:" + commit)
        self._call("tests:" + commit)
        return release(commit)

    def verify_compatibility(self, previous, candidate):
        self._call("compatibility")

    def backup(self, previous, candidate):
        return self._call("backup", lambda: "backup-previous-code-env")

    def stop(self):
        self._phase()
        self._call("stop", lambda: setattr(self, "running", False))

    def activate(self, target):
        self._phase()
        def effect():
            if self.running:
                raise AssertionError("activate must follow confirmed stop")
            self.current = target
        self._call("activate:" + target.commit, effect)

    def start(self):
        self._phase()
        def effect():
            self.running = True
            if self.current.commit != A:
                self.shared_data["counter"] += 1
        self._call("start:" + self.current.commit, effect)

    def healthy(self, target):
        return self._call("health:" + target.commit,
                          lambda: self.current == target and self.running and self.health[target.commit])

    def cleanup(self, protected):
        self.protected = protected
        self._call("cleanup")


class LinuxUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.backend = FakeBackend()
        self.engine = u.Engine(self.folder, self.backend)
        self.backend.state_path = self.engine.state_file

    def initialize(self):
        self.backend.remote = A
        self.assertEqual(self.engine.run().status, "up_to_date")
        self.backend.remote = B
        self.backend.calls.clear()

    def test_network_backoff_survives_restarts_and_recovers_after_more_than_three_rounds(self):
        self.initialize()
        for attempt, now in enumerate((1000, 1300, 2200, 4000, 5800), start=1):
            self.backend.failures["install:" + B] = u.Deferred("bundle_network_unavailable")
            with patch.object(u.time, "time", return_value=now):
                result = self.engine.run()
            self.assertEqual(result.status, "deferred")
            self.assertTrue(self.backend.running)
            self.assertEqual(self.backend.current.commit, A)
            self.assertNotIn("stop", self.backend.calls)
            state = self.engine.load_state()
            self.assertIsNone(state["transaction"])
            self.assertNotIn(B, state["failed"])
            self.assertEqual(state["download_retries"][B]["after"], now + (300, 900, 1800)[min(attempt, 3) - 1])
            self.engine = u.Engine(self.folder, self.backend)
            with patch.object(u.time, "time", return_value=now + 1):
                self.assertEqual(self.engine.run().reason, "bundle_retry_backoff")
        with patch.object(u.time, "time", return_value=7600):
            self.assertEqual(self.engine.run().status, "updated")
        self.assertNotIn(B, self.engine.load_state()["download_retries"])

    def test_other_preparation_deferrals_keep_finite_budget(self):
        self.initialize()
        for attempt, now in enumerate((1000, 1300, 2200), start=1):
            self.backend.failures["install:" + B] = u.Deferred("unknown_preparation_failure")
            with patch.object(u.time, "time", return_value=now):
                result = self.engine.run()
            self.assertEqual(result.status, "rejected" if attempt == 3 else "deferred")
            self.assertNotIn("stop", self.backend.calls)
        self.assertEqual(self.engine.run().status, "blocked")
        self.assertEqual(self.engine.load_state()["failed"][B]["reason"], "bundle_retry_exhausted")
        self.engine.resume(retry_commit=B)
        self.assertNotIn(B, self.engine.load_state()["download_retries"])
        self.assertEqual(self.engine.run().status, "updated")

    def test_legacy_failed_records_are_never_cleared_by_network_retry_change(self):
        self.initialize()
        state = self.engine.load_state()
        state["failed"][B] = dict(reason="bundle_retry_exhausted", at="2026-10-04T00:00:00Z")
        state["download_retries"] = {B: dict(attempts=3, after=1000)}
        self.engine._save(state)
        result = self.engine.run()
        self.assertEqual((result.status, result.reason), ("blocked", "bundle_retry_exhausted"))
        self.assertIn(B, self.engine.load_state()["failed"])
        self.assertNotIn("install:" + B, self.backend.calls)

    def test_interrupted_download_recovers_and_retries_without_stopping_old_process(self):
        self.initialize()
        def killed_download(commit):
            state = self.engine.load_state()
            state["transaction"]["preparation_step"] = "downloading"
            self.engine._save(state)
            raise PowerLoss()
        with patch.object(self.backend, "prepare", side_effect=killed_download):
            with self.assertRaises(PowerLoss):
                self.engine.run()
        self.engine = u.Engine(self.folder, self.backend)
        with patch.object(u.time, "time", return_value=1000):
            recovered = self.engine.run()
        self.assertEqual(recovered.reason, "interrupted_download")
        self.assertNotIn(B, self.engine.load_state()["failed"])
        self.assertIsNone(self.engine.load_state()["transaction"])
        self.assertTrue(self.backend.running)
        self.assertNotIn("stop", self.backend.calls)
        with patch.object(u.time, "time", return_value=1001):
            self.assertEqual(self.engine.run().reason, "bundle_retry_backoff")
        with patch.object(u.time, "time", return_value=1300):
            self.assertEqual(self.engine.run().status, "updated")

    def test_interrupted_offline_preparation_still_blocks_the_candidate(self):
        self.initialize()
        def killed_install(commit):
            state = self.engine.load_state()
            state["transaction"]["preparation_step"] = "offline_preparation"
            self.engine._save(state)
            raise PowerLoss()
        with patch.object(self.backend, "prepare", side_effect=killed_install):
            with self.assertRaises(PowerLoss):
                self.engine.run()
        self.assertEqual(self.engine.run().reason, "interrupted_preparation")
        self.assertIn(B, self.engine.load_state()["failed"])
        self.assertEqual(self.engine.run().status, "blocked")
        self.assertNotIn("stop", self.backend.calls)

    def test_missing_bundle_and_metadata_network_failures_keep_retrying(self):
        for reason in ("bundle_not_published", "github_unavailable"):
            with self.subTest(reason=reason):
                self.initialize()
                for now in (1000, 1300, 2200, 4000):
                    self.backend.failures["install:" + B] = u.Deferred(reason)
                    with patch.object(u.time, "time", return_value=now):
                        self.assertEqual(self.engine.run().status, "deferred")
                    self.assertNotIn(B, self.engine.load_state()["failed"])
                    self.assertNotIn("stop", self.backend.calls)
                self.engine.resume(retry_commit=B)

    def test_compatibility_deferred_is_not_a_retryable_download(self):
        self.initialize()
        self.backend.failures["compatibility"] = u.Deferred("compatibility_unavailable")
        self.assertEqual(self.engine.run().status, "rejected")
        self.assertNotIn("stop", self.backend.calls)
        self.assertNotIn(B, self.engine.load_state().get("download_retries", {}))

    def test_success_records_known_good_only_after_health_and_preserves_backup(self):
        result = self.engine.run()
        self.assertEqual(result, u.RunResult("updated", B))
        state = self.engine.load_state()
        self.assertEqual(state["current"]["commit"], B)
        self.assertEqual(state["previous"]["commit"], A)
        self.assertIsNone(state["transaction"])
        self.assertEqual(self.backend.observed_phases, ["stopping", "activating", "starting"])
        self.assertLess(self.backend.calls.index("compatibility"), self.backend.calls.index("backup"))
        self.assertLess(self.backend.calls.index("backup"), self.backend.calls.index("stop"))
        self.assertEqual(set(self.backend.protected), {release(A), release(B)})
        self.assertEqual(self.backend.shared_data["counter"], 9)

    def test_unhealthy_baseline_never_fetches_or_initializes(self):
        self.backend.health[A] = False
        self.assertEqual(self.engine.run().reason, "baseline_not_healthy")
        self.assertFalse(self.engine.state_file.exists())
        self.assertNotIn("resolve", self.backend.calls)

    def test_network_failure_leaves_running_release_unchanged_and_retries_next_check(self):
        self.initialize()
        self.backend.failures["resolve"] = OSError("password=not-for-logs")
        self.assertEqual(self.engine.run().status, "deferred")
        self.assertTrue(self.backend.running)
        self.assertEqual(self.backend.current, release(A))
        self.assertNotIn("stop", self.backend.calls)
        self.assertNotIn("not-for-logs", self.engine.state_file.read_text())
        self.assertEqual(self.engine.run().status, "updated")

    def test_pending_ci_does_not_prepare_or_block_sha(self):
        self.initialize()
        self.backend.failures["ci:" + B] = u.Deferred("ci_pending")
        self.assertEqual(self.engine.run().reason, "ci_pending")
        self.assertFalse(self.engine.load_state()["failed"])
        self.assertNotIn("install:" + B, self.backend.calls)
        self.assertEqual(self.engine.run().status, "updated")

    def test_failed_ci_blocks_sha_without_install_or_service_mutation(self):
        self.initialize()
        self.backend.failures["ci:" + B] = u.Rejected("ci_failed")
        self.assertEqual(self.engine.run().status, "rejected")
        self.assertEqual(self.engine.run().status, "blocked")
        self.assertNotIn("install:" + B, self.backend.calls)
        self.assertNotIn("stop", self.backend.calls)

    def test_install_tests_compatibility_backup_failures_leave_old_process_running(self):
        for stage in ("install:" + B, "tests:" + B, "compatibility", "backup"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                backend = FakeBackend()
                engine = u.Engine(folder, backend)
                backend.state_path = engine.state_file
                backend.failures[stage] = u.Rejected("validation_failed")
                self.assertEqual(engine.run().status, "rejected")
                self.assertEqual(engine.run().status, "blocked")
                self.assertTrue(backend.running)
                self.assertEqual(backend.current, release(A))
                self.assertNotIn("stop", backend.calls)
                self.assertEqual(backend.calls.count("install:" + B), 1)

    def test_compatibility_is_explicit_fail_closed_gate(self):
        self.backend.failures["compatibility"] = u.Rejected("nofever_or_data_unverified")
        self.assertEqual(self.engine.run().reason, "nofever_or_data_unverified")
        self.assertNotIn("backup", self.backend.calls)
        self.assertNotIn("stop", self.backend.calls)

    def test_prepared_sha_mismatch_or_shared_environment_is_rejected(self):
        for candidate in (release(C), u.Release(B, release(A).code_dir, "/isolated/venv"),
                          u.Release(B, "/isolated/code", release(A).venv_dir)):
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as folder:
                backend = FakeBackend()
                engine = u.Engine(folder, backend)
                backend.state_path = engine.state_file
                with patch.object(backend, "prepare", return_value=candidate):
                    self.assertEqual(engine.run().status, "rejected")
                self.assertNotIn("stop", backend.calls)

    def test_stop_activate_start_and_health_failures_roll_back(self):
        for stage in ("stop", "activate:" + B, "start:" + B, "health:" + B):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                backend = FakeBackend()
                engine = u.Engine(folder, backend)
                backend.state_path = engine.state_file
                backend.failures[stage] = RuntimeError("do not retain private output")
                self.assertEqual(engine.run().status, "rolled_back")
                self.assertTrue(backend.running)
                self.assertEqual(backend.current, release(A))
                self.assertIsNone(engine.load_state()["transaction"])
                self.assertEqual(engine.run().status, "blocked")
                self.assertNotIn("private output", engine.state_file.read_text())

    def test_unhealthy_candidate_rolls_back_code_not_shared_persistent_data(self):
        self.backend.health[B] = False
        self.assertEqual(self.engine.run().status, "rolled_back")
        self.assertEqual(self.backend.current, release(A))
        self.assertEqual(self.backend.shared_data["counter"], 9)

    def test_crash_at_every_durable_mutating_phase_recovers_before_any_fetch(self):
        for phase in ("stopping", "activating", "starting", "checking_health"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as folder:
                backend = FakeBackend()
                engine = u.Engine(folder, backend)
                backend.state_path = engine.state_file
                real_phase = engine._phase
                def crash_after_intent(state, value):
                    real_phase(state, value)
                    if value == phase:
                        raise PowerLoss()
                with patch.object(engine, "_phase", side_effect=crash_after_intent):
                    with self.assertRaises(PowerLoss):
                        engine.run()
                backend.calls.clear()
                recovery = u.Engine(folder, backend)
                self.assertEqual(recovery.run().status, "rolled_back")
                self.assertEqual(backend.current, release(A))
                self.assertTrue(backend.running)
                self.assertNotIn("resolve", backend.calls)
                self.assertIsNone(recovery.load_state()["transaction"])

    def test_crash_after_mutation_recovers_from_last_durable_intent(self):
        for stage in ("stop", "activate:" + B, "start:" + B, "health:" + B):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                backend = FakeBackend()
                engine = u.Engine(folder, backend)
                backend.state_path = engine.state_file
                backend.failures[stage + ":after"] = PowerLoss()
                with self.assertRaises(PowerLoss):
                    engine.run()
                backend.calls.clear()
                self.assertEqual(u.Engine(folder, backend).run(recover_only=True).status, "rolled_back")
                self.assertEqual(backend.current, release(A))
                self.assertTrue(backend.running)
                self.assertNotIn("resolve", backend.calls)

    def test_prepare_crash_blocks_candidate_without_restarting_running_baseline(self):
        self.backend.failures["tests:" + B] = PowerLoss()
        with self.assertRaises(PowerLoss):
            self.engine.run()
        self.backend.calls.clear()
        self.assertEqual(u.Engine(self.folder, self.backend).run().status, "recovered")
        self.assertNotIn("resolve", self.backend.calls)
        self.assertNotIn("stop", self.backend.calls)
        self.assertEqual(self.engine.run().status, "blocked")

    def test_crashed_recovery_is_idempotently_retried_before_fetch(self):
        self.backend.health[B] = False
        self.backend.failures["activate:" + A + ":after"] = PowerLoss()
        with self.assertRaises(PowerLoss):
            self.engine.run()
        self.backend.calls.clear()
        self.assertEqual(u.Engine(self.folder, self.backend).run().status, "rolled_back")
        self.assertNotIn("resolve", self.backend.calls)
        self.assertTrue(self.backend.running)

    def test_rollback_failure_pauses_no_infinite_retry_and_operator_can_resume(self):
        self.backend.health[B] = False
        self.backend.failures["start:" + A] = RuntimeError("failed restart")
        result = self.engine.run()
        self.assertEqual(result.status, "paused")
        self.assertTrue(self.engine.load_state()["transaction"])
        self.backend.calls.clear()
        self.assertEqual(self.engine.run().status, "paused")
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.engine.resume().status, "resumed")
        self.assertEqual(self.engine.run(recover_only=True).status, "rolled_back")
        self.assertEqual(self.backend.current, release(A))
        self.assertTrue(self.backend.running)

    def test_only_explicit_operator_retry_unblocks_failed_sha(self):
        self.backend.failures["tests:" + B] = u.Rejected("tests_failed")
        self.assertEqual(self.engine.run().status, "rejected")
        self.assertEqual(self.engine.resume().status, "resumed")
        self.assertEqual(self.engine.run().status, "blocked")
        self.assertEqual(self.engine.resume(retry_commit=B).status, "resumed")
        self.assertEqual(self.engine.run().status, "updated")

    def test_new_main_can_advance_after_old_failed_sha_but_old_sha_stays_blocked(self):
        self.backend.failures["tests:" + B] = u.Rejected("tests_failed")
        self.assertEqual(self.engine.run().status, "rejected")
        self.backend.remote = C
        self.assertEqual(self.engine.run().status, "updated")
        self.backend.remote = B
        self.assertEqual(self.engine.run().status, "blocked")

    def test_corrupt_unknown_or_inconsistent_state_fails_closed(self):
        self.initialize()
        valid = self.engine.load_state()
        invalid = ["{broken", "[]", json.dumps({**valid, "version": 99})]
        invalid.append(json.dumps({**valid, "transaction": dict(
            commit=B, phase="starting", previous=valid["current"],
            candidate=vars(release(B)), compatible=False, backup="backup",
        )}))
        for content in invalid:
            with self.subTest(content=content):
                self.engine.state_file.write_text(content, encoding="utf-8")
                self.backend.calls.clear()
                self.assertEqual(self.engine.run().status, "state_error")
                self.assertEqual(self.backend.calls, [])
                self.assertEqual(self.engine.state_file.read_text(encoding="utf-8"), content)

    def test_real_exclusive_lock_skips_concurrent_controller_and_releases(self):
        with u.FileLock(self.folder / "update.lock"):
            self.assertEqual(self.engine.run().status, "busy")
            self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.engine.run().status, "updated")

    def test_state_write_failure_prevents_stop_and_next_run_recovers_safely(self):
        self.initialize()
        real_write = u.atomic_json
        def fail_stop_intent(path, state, **kwargs):
            if state.get("transaction", {}).get("phase") == "stopping":
                raise u.StateError("state_write_failed")
            return real_write(path, state, **kwargs)
        with patch.object(u, "atomic_json", side_effect=fail_stop_intent):
            self.assertEqual(self.engine.run().status, "state_error")
        self.assertNotIn("stop", self.backend.calls)
        self.assertEqual(self.engine.run().status, "recovered")
        self.assertTrue(self.backend.running)

    def test_commit_state_write_failure_recovers_old_release_on_next_run(self):
        self.initialize()
        real_write = u.atomic_json
        def fail_commit(path, state, **kwargs):
            if state["current"]["commit"] == B:
                raise u.StateError("state_write_failed")
            return real_write(path, state, **kwargs)
        with patch.object(u, "atomic_json", side_effect=fail_commit):
            self.assertEqual(self.engine.run().status, "state_error")
        self.assertEqual(self.backend.current, release(B))
        self.assertEqual(self.engine.run().status, "rolled_back")
        self.assertEqual(self.backend.current, release(A))

    def test_post_replace_commit_fsync_error_keeps_already_health_verified_release(self):
        self.initialize()
        real_write = u.atomic_json
        def fail_after_replace(path, state, **kwargs):
            real_write(path, state, **kwargs)
            if state["current"]["commit"] == B:
                # Model a directory-fsync failure after the replacement became
                # visible. Either this complete state or the old transaction can
                # survive power loss; both have safe next-invocation behavior.
                raise u.StateError("state_write_failed")
        with patch.object(u, "atomic_json", side_effect=fail_after_replace):
            self.assertEqual(self.engine.run().status, "state_error")
        self.assertIn("health:" + B, self.backend.calls)
        self.assertEqual(self.engine.load_state()["current"]["commit"], B)
        self.backend.calls.clear()
        self.assertEqual(self.engine.run().status, "up_to_date")
        self.assertNotIn("stop", self.backend.calls)

    def test_operator_resume_without_existing_directory_does_not_initialize_baseline(self):
        other = u.Engine(self.folder / "absent", self.backend)
        self.assertEqual(other.resume().status, "uninitialized")
        self.assertEqual(self.backend.calls, [])

    def test_atomic_write_failure_retains_previous_complete_document(self):
        target = self.folder / "atomic.json"
        u.atomic_json(target, {"old": True})
        with patch.object(u.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(u.StateError):
                u.atomic_json(target, {"new": True})
        self.assertEqual(json.loads(target.read_text()), {"old": True})
        self.assertFalse(list(self.folder.glob(".state-*")))

    def test_cleanup_failure_does_not_undo_success_and_is_recorded(self):
        self.backend.failures["cleanup"] = OSError("quota")
        self.assertEqual(self.engine.run().status, "updated")
        self.assertEqual(self.backend.current, release(B))
        self.assertEqual(self.engine.load_state()["events"][-1]["status"], "cleanup_failed")

    def test_external_release_drift_pauses_without_touching_external_process(self):
        self.initialize()
        self.backend.current = release(C)
        self.assertEqual(self.engine.run().reason, "release_drift")
        self.assertNotIn("resolve", self.backend.calls)
        self.assertNotIn("stop", self.backend.calls)

    def test_recover_only_has_no_network_or_new_install(self):
        self.assertEqual(self.engine.run(recover_only=True).status, "uninitialized")
        self.assertEqual(self.backend.calls, [])
        self.initialize()
        self.backend.calls.clear()
        self.assertEqual(self.engine.run(recover_only=True).status, "no_transaction")
        self.assertEqual(self.backend.calls, [])

    def test_manual_rollback_restores_previous_and_blocks_automatic_redeployment(self):
        self.assertEqual(self.engine.run().status, "updated")
        self.backend.calls.clear()
        self.assertEqual(self.engine.rollback(), u.RunResult("rolled_back", A, "operator_requested"))
        self.assertEqual(self.backend.current, release(A))
        self.assertTrue(self.backend.running)
        self.assertEqual(self.backend.shared_data["counter"], 9)
        self.assertNotIn("resolve", self.backend.calls)
        self.assertNotIn("install:" + A, self.backend.calls)
        self.assertNotIn("compatibility", self.backend.calls)
        self.assertLess(self.backend.calls.index("backup"), self.backend.calls.index("stop"))
        self.assertEqual(self.engine.load_state()["failed"][B]["reason"], "operator_rollback")
        self.assertEqual(self.engine.run().status, "blocked")

    def test_manual_rollback_health_failure_recovers_precommand_current(self):
        self.assertEqual(self.engine.run().status, "updated")
        self.backend.health[A] = False
        self.assertEqual(self.engine.rollback().reason, "candidate_unhealthy")
        self.assertEqual(self.backend.current, release(B))
        self.assertTrue(self.backend.running)
        self.assertEqual(self.engine.load_state()["current"]["commit"], B)
        self.assertIn(A, self.engine.load_state()["failed"])

    def test_manual_rollback_crash_recovers_precommand_current_before_fetch(self):
        self.assertEqual(self.engine.run().status, "updated")
        self.backend.failures["activate:" + A + ":after"] = PowerLoss()
        with self.assertRaises(PowerLoss):
            self.engine.rollback()
        self.backend.calls.clear()
        self.assertEqual(u.Engine(self.folder, self.backend).run().status, "rolled_back")
        self.assertEqual(self.backend.current, release(B))
        self.assertNotIn("resolve", self.backend.calls)

    def test_manual_rollback_backup_failure_leaves_current_running(self):
        self.assertEqual(self.engine.run().status, "updated")
        self.backend.calls.clear()
        self.backend.failures["backup"] = OSError("disk full")
        self.assertEqual(self.engine.rollback().status, "rejected")
        self.assertEqual(self.backend.current, release(B))
        self.assertTrue(self.backend.running)
        self.assertNotIn("stop", self.backend.calls)
        self.assertIsNone(self.engine.load_state()["transaction"])

    def test_manual_rollback_requires_completed_prior_success(self):
        self.assertEqual(self.engine.rollback().status, "uninitialized")
        self.initialize()
        self.assertEqual(self.engine.rollback().reason, "no_previous_release")
        self.backend.failures["activate:" + B] = PowerLoss()
        with self.assertRaises(PowerLoss):
            self.engine.run()
        self.backend.calls.clear()
        self.assertEqual(self.engine.rollback().reason, "recover_transaction_first")
        self.assertEqual(self.backend.calls, [])

    def test_manual_rollback_obeys_exclusive_lock(self):
        with u.FileLock(self.folder / "update.lock"):
            self.assertEqual(self.engine.rollback().status, "busy")
        self.assertEqual(self.backend.calls, [])

    @unittest.skipUnless(u.os.name == "posix", "POSIX modes; Windows uses ACLs")
    def test_state_metadata_is_readable_but_other_atomic_json_defaults_private(self):
        self.assertEqual(self.engine.run().status, "updated")
        self.assertEqual(self.engine.state_file.stat().st_mode & 0o777, 0o644)
        private = self.folder / "manifest.json"
        u.atomic_json(private, {"metadata": True})
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        new_directory = self.folder / "new-state"
        other = u.Engine(new_directory, self.backend)
        previous_umask = u.os.umask(0o077)
        try:
            self.assertEqual(other.run(recover_only=True).status, "uninitialized")
        finally:
            u.os.umask(previous_umask)
        self.assertEqual(new_directory.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()

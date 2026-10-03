"""Offline Linux backend contracts: no real network, service, or credentials."""
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

from deploy.linux_backend import (GIB, JOBS, LinuxBackend, Rejected, Deferred,
                                 extract_archive, fingerprints, tree_digest)
from deploy.linux_backend import environment_digest, recover_engine, validate_config
from deploy.linux_backend import run_bounded, freeze_release
from deploy.linux_updater import Engine, Release, RunResult, atomic_json


A, B = "a" * 40, "b" * 40


class LinuxBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / "taki"
        self.baseline_code = self.base / "releases" / "baseline-snapshot"
        self.baseline_code.mkdir(parents=True)
        (self.baseline_code / "critical.py").write_text("original")
        self.baseline = Release(A, str(self.baseline_code), str(self.base / "venv"))
        self.config = {"base": str(self.base), "state_dir": str(self.root / "state"),
            "baseline": asdict(self.baseline), "baseline_digest": tree_digest(self.baseline_code),
            "health_seconds": 6, "stable_seconds": 2,
            "credential_paths": [str(self.base / "config")]}
        self.backend = LinuxBackend(self.config)
        self.calls = []
        self.backend.run = self.runner

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return ""

    def candidate(self, sha=B):
        root = self.base / "releases" / sha
        (root / "code").mkdir(parents=True)
        (root / "venv").mkdir()
        (root / "code" / "critical.py").write_text("original")
        release = Release(sha, str(root / "code"), str(root / "venv"))
        atomic_json(self.backend.manifests / (sha + ".json"), {
            "release": asdict(release), "owner": "taki-linux-updater-v1", "prepared": True,
            "digest": tree_digest(root / "code"), "environment_digest": environment_digest(root / "venv")})
        return release

    def ci_rows(self):
        return {"workflow_runs": [{"id": 123, "run_number": 12, "run_attempt": 2,
            "head_sha": B, "head_branch": "main", "event": "push", "name": "Checks",
            "path": ".github/workflows/checks.yml", "status": "completed", "conclusion": "success"}]}, {
            "total_count": 2, "jobs": [{"name": name, "head_sha": B, "run_id": 123,
            "status": "completed", "conclusion": "success"} for name in JOBS]}

    def policy(self):
        paths = ["critical.py", "absent.py"]
        policy = self.root / "policy.json"
        policy.write_text(json.dumps({"version": 2, "critical_paths": paths, "reviews": [{
            "fingerprints": fingerprints(self.baseline_code, paths),
            "forward_and_rollback_data_compatible": True, "schema_contract": "schema1",
            "evidence": "explicit operator review"}]}))
        self.config.update(compatibility_paths=paths, compatibility_policy=str(policy))
        for field in ("compatibility_gate", "compatibility_fixture"):
            path = self.root / field
            path.write_text("frozen")
            self.config[field] = str(path)
            self.config[field + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return policy

    def test_ci_requires_exact_push_main_and_both_python_jobs(self):
        runs, jobs = self.ci_rows()
        with patch.object(self.backend, "_json", side_effect=[runs, jobs]) as api:
            self.backend.check_ci(B)
        self.assertIn("head_sha=" + B, api.call_args_list[0].args[0])
        self.assertIn("/attempts/2/jobs", api.call_args_list[1].args[0])
        jobs["jobs"].pop()
        with patch.object(self.backend, "_json", side_effect=[runs, jobs]):
            with self.assertRaisesRegex(Rejected, "ci_required_jobs_missing"):
                self.backend.check_ci(B)

    def test_ci_rejects_other_sha_even_if_workflow_green(self):
        runs, jobs = self.ci_rows()
        jobs["jobs"][0]["head_sha"] = A
        with patch.object(self.backend, "_json", side_effect=[runs, jobs]):
            with self.assertRaisesRegex(Rejected, "ci_required_job_failed"):
                self.backend.check_ci(B)

    def test_pending_latest_ci_does_not_fall_back_to_old_green(self):
        runs, _ = self.ci_rows()
        newer = dict(runs["workflow_runs"][0], run_attempt=3, status="in_progress")
        runs["workflow_runs"].append(newer)
        with patch.object(self.backend, "_json", return_value=runs):
            with self.assertRaisesRegex(Deferred, "ci_pending"):
                self.backend.check_ci(B)

    def test_pull_request_checks_do_not_qualify(self):
        runs, _ = self.ci_rows()
        runs["workflow_runs"][0]["event"] = "pull_request"
        with patch.object(self.backend, "_json", return_value=runs):
            with self.assertRaisesRegex(Deferred, "ci_missing"):
                self.backend.check_ci(B)

    def test_network_failure_is_transient_without_service_calls(self):
        with patch("deploy.linux_backend.urllib.request.urlopen", side_effect=OSError):
            with self.assertRaisesRegex(Deferred, "github_unavailable"):
                self.backend.resolve_main()
        self.assertFalse(self.calls)

    def test_missing_policy_blocks_even_prepared_candidate(self):
        with self.assertRaisesRegex(Rejected, "compatibility_review_required"):
            self.backend.verify_compatibility(self.baseline, self.candidate())
        self.assertFalse(self.calls)

    def test_review_is_hash_based_and_runs_frozen_gate_offline(self):
        candidate = self.candidate()
        self.policy()
        with patch("deploy.linux_backend.trusted"):
            self.backend.verify_compatibility(self.baseline, candidate)
        command = self.calls[0][0]
        self.assertIn("PrivateNetwork=yes", command)
        self.assertIn("ProtectHome=tmpfs", command)
        self.assertIn(self.config["compatibility_gate"], command)
        self.assertIn("--repo", command)
        self.assertNotIn("--record-only", command)

    def test_changed_critical_source_requires_new_review(self):
        candidate = self.candidate()
        self.policy()
        (Path(candidate.code_dir) / "critical.py").write_text("schema2")
        manifest = self.backend.manifests / (B + ".json")
        record = json.loads(manifest.read_text())
        record["digest"] = tree_digest(Path(candidate.code_dir))
        atomic_json(manifest, record)
        with patch("deploy.linux_backend.trusted"):
            with self.assertRaisesRegex(Rejected, "compatibility_review_required"):
                self.backend.verify_compatibility(self.baseline, candidate)
        self.assertFalse(self.calls)

    def test_gate_digest_changes_block_before_execution(self):
        candidate = self.candidate()
        self.policy()
        Path(self.config["compatibility_gate"]).write_text("unreviewed")
        with patch("deploy.linux_backend.trusted"):
            with self.assertRaisesRegex(Rejected, "compatibility_gate_untrusted"):
                self.backend.verify_compatibility(self.baseline, candidate)
        self.assertFalse(self.calls)

    def test_baseline_drift_prevents_activation(self):
        (self.baseline_code / "critical.py").write_text("changed")
        with self.assertRaisesRegex(Rejected, "baseline_code_drift"):
            self.backend.activate(self.baseline)
        self.assertFalse(self.calls)

    def test_arbitrary_release_paths_not_accepted(self):
        release = Release(B, str(self.root / "outside"), str(self.root / "outside-venv"))
        with self.assertRaisesRegex(Rejected, "release_path_not_allowed"):
            self.backend._manifest(release)

    def test_digest_ignores_runtime_data_but_includes_nested_source_data(self):
        root = self.baseline_code
        before = tree_digest(root)
        (root / "data").mkdir()
        (root / "data" / "cache.json").write_text("private runtime data")
        self.assertEqual(tree_digest(root), before)
        (root / "src" / "data").mkdir(parents=True)
        (root / "src" / "data" / "reader.py").write_text("source")
        self.assertNotEqual(tree_digest(root), before)

    def test_candidate_runner_has_no_live_environment_and_restricted_resources(self):
        release = self.candidate()
        self.backend._sandbox(release, ["/usr/bin/python3.12", "-m", "unittest"], "tests", offline=True)
        command, options = self.calls[0]
        for item in ("User=ubuntu", "NoNewPrivileges=yes", "PrivateNetwork=yes", "MemoryMax=768M",
                     "CPUQuota=50%", "ProtectHome=tmpfs", "QQ_APP_ID=", "QQ_APP_SECRET=", "AI_API_KEY=", "-i"):
            self.assertIn(item, command)
        self.assertIn("InaccessiblePaths=/proc -/run/user -/run/dbus -" + str(self.base / "config"), command)
        self.assertIn("SystemCallFilter=~ptrace process_vm_readv process_vm_writev", command)
        self.assertEqual(len(self.calls), 1)  # Collected successful units need no second stop.
        self.assertEqual(options["budget_root"], Path(release.code_dir).parent)

    def test_failed_sandbox_stops_transient_service_and_keeps_original_failure(self):
        self.backend.run = lambda *args, **kwargs: (_ for _ in ()).throw(Rejected("command_timeout"))
        with self.assertRaisesRegex(Rejected, "command_timeout"):
            self.backend._sandbox(self.candidate(), ["/usr/bin/python3.12"], "tests", offline=True)

    def health_runner(self, *, error=False, ready=True, stale_pid=False, restart=False):
        state = {"ActiveState": "active", "SubState": "running", "MainPID": "42",
                 "InvocationID": "c" * 32, "NRestarts": "0"}
        now = [0]
        self.backend.clock = lambda: now[0]
        self.backend.sleep = lambda delay: now.__setitem__(0, now[0] + delay)
        self.backend.current_release = lambda: self.baseline
        def runner(command, **kwargs):
            self.calls.append((command, kwargs))
            if command[0].endswith("systemctl"):
                row = dict(state)
                if restart and now[0]:
                    row["MainPID"] = "43"
                return "\n".join(f"{key}={value}" for key, value in row.items())
            if any(arg.startswith("--grep=") for arg in command):
                return json.dumps({"_PID": "41" if stale_pid else "42", "_SYSTEMD_INVOCATION_ID": "c" * 32,
                    "MESSAGE": "INFO ournotes_bot.qq: 机器人 Taki 已上线"}) if ready else ""
            return json.dumps({"_PID": "42", "_SYSTEMD_INVOCATION_ID": "c" * 32,
                               "MESSAGE": "ERROR startup failed"}) if error else ""
        self.backend.run = runner

    def test_health_requires_current_process_ready_then_stability(self):
        self.health_runner()
        self.assertTrue(self.backend.healthy(self.baseline))
        journal = [call[0] for call in self.calls if call[0][0].endswith("journalctl")]
        self.assertTrue(all("_PID=42" in command for command in journal))
        self.assertTrue(any(any(arg.startswith("--since=@") for arg in command) for command in journal))

    def test_old_process_ready_does_not_qualify(self):
        self.health_runner(stale_pid=True)
        self.assertFalse(self.backend.healthy(self.baseline))

    def test_missing_ready_is_bounded(self):
        self.health_runner(ready=False)
        self.assertFalse(self.backend.healthy(self.baseline))
        self.assertLessEqual(len(self.calls), 12)

    def test_current_start_error_or_restart_fails_health(self):
        for scenario in ({"error": True}, {"restart": True}):
            self.health_runner(**scenario)
            self.assertFalse(self.backend.healthy(self.baseline))

    def test_stop_confirms_pid_zero_before_activation(self):
        with patch.object(self.backend, "_service", return_value={"MainPID": "42", "ActiveState": "active"}):
            with self.assertRaisesRegex(Rejected, "service_not_stopped"):
                self.backend.stop()
            with self.assertRaisesRegex(Rejected, "activation_requires_stopped_service"):
                self.backend.activate(self.baseline)

    def test_cleanup_does_not_touch_baseline_or_unowned_folders(self):
        unknown = self.base / "releases" / "operator-backup"
        unknown.mkdir()
        releases = [self.candidate(str(number) * 40) for number in range(1, 5)]
        self.backend.cleanup((releases[0],))
        self.assertTrue(self.baseline_code.exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(Path(releases[0].code_dir).exists())

    def test_cleanup_rejects_manifest_path_escape(self):
        release = self.candidate()
        manifest = self.backend.manifests / (B + ".json")
        record = json.loads(manifest.read_text())
        record["release"]["code_dir"] = str(self.root)
        atomic_json(manifest, record)
        with self.assertRaisesRegex(Rejected, "(?:cleanup|preparation)_path_not_owned"):
            self.backend.cleanup(())
        self.assertTrue(self.root.exists())

    def test_environment_changes_are_rejected_before_activation(self):
        release = self.candidate()
        (Path(release.venv_dir) / "injected.py").write_text("changed")
        with self.assertRaisesRegex(Rejected, "release_environment_drift"):
            self.backend.activate(release)
        self.assertFalse(self.calls)

    def state(self, *, previous=None, transaction=None):
        atomic_json(self.backend.state / "state.json", dict(version=1, current=asdict(self.baseline),
                    previous=asdict(previous) if previous else None, transaction=transaction,
                    paused=None, failed={}, events=[]))

    def test_explicit_retry_rebuilds_only_owned_unprotected_candidate(self):
        release = self.candidate()
        self.state()
        self.backend.current_release = lambda: self.baseline
        self.backend._discard_candidate(B)
        self.assertFalse(Path(release.code_dir).parent.exists())
        self.assertTrue(self.baseline_code.exists())

    def test_retry_protects_previous_release_and_unknown_folders(self):
        release = self.candidate()
        self.state(previous=release)
        self.backend.current_release = lambda: self.baseline
        with self.assertRaisesRegex(Rejected, "candidate_is_protected"):
            self.backend._discard_candidate(B)
        self.assertTrue(Path(release.code_dir).exists())
        self.state()
        record = self.backend.manifests / (B + ".json")
        row = json.loads(record.read_text())
        row["owner"] = "operator"
        atomic_json(record, row)
        with self.assertRaisesRegex(Rejected, "candidate_path_not_owned"):
            self.backend._discard_candidate(B)
        self.assertTrue(Path(release.code_dir).exists())

    def test_stopped_data_snapshot_is_separate_and_never_restored(self):
        archive = self.backend.backups / "owned.tar"
        archive.write_bytes(b"code and venv")
        atomic_json(archive.with_suffix('.json'), dict(owner="taki-linux-updater-v1", archive=archive.name))
        (self.base / "data").mkdir()
        (self.base / "data/cache.json").write_text("live data")
        self.state(transaction=dict(phase="stopping", backup=str(archive)))
        def runner(argv, **kwargs):
            self.calls.append((argv, kwargs))
            Path(next(v.removeprefix('--file=') for v in argv if v.startswith('--file='))).write_bytes(b"data snapshot")
        self.backend.run = runner
        self.backend._snapshot_stopped_data()
        row = json.loads(archive.with_suffix('.json').read_text())
        self.assertEqual(row['data_archive'], 'owned-data.tar')
        self.assertFalse(row['shared_data_restore'])
        self.assertEqual((self.base / 'data/cache.json').read_text(), 'live data')
        self.assertNotIn('--extract', self.calls[0][0])
        self.state(transaction=dict(phase="rollback_stopping", backup=str(archive)))
        self.backend._snapshot_stopped_data()
        self.assertEqual(len(self.calls), 1)

    def test_data_snapshot_failure_leaves_original_data_and_no_partial_archive(self):
        archive = self.backend.backups / "owned.tar"
        archive.write_bytes(b"code")
        atomic_json(archive.with_suffix('.json'), dict(owner="taki-linux-updater-v1", archive=archive.name))
        (self.base / "data").mkdir()
        self.state(transaction=dict(phase="stopping", backup=str(archive)))
        def runner(argv, **kwargs):
            Path(next(v.removeprefix('--file=') for v in argv if v.startswith('--file='))).write_bytes(b"partial")
            raise Rejected("backup_disk_budget")
        self.backend.run = runner
        with self.assertRaisesRegex(Rejected, "backup_disk_budget"):
            self.backend._snapshot_stopped_data()
        self.assertFalse((self.backend.backups / 'owned-data.tar').exists())
        self.assertTrue((self.base / "data").exists())

    def test_boot_recovery_restarts_gated_baseline_after_abandoned_preparation(self):
        self.state(transaction=dict(phase="preparing", commit=B, previous=asdict(self.baseline), candidate=None))
        self.backend.current_release = lambda: self.baseline
        self.backend._service = lambda: dict(MainPID="0", ActiveState="failed")
        self.backend.start = Mock()
        self.backend.healthy = Mock(return_value=True)
        engine = Engine(self.backend.state, self.backend)
        result = recover_engine(engine)
        self.assertEqual(result.reason, "known_good_restarted")
        self.backend.start.assert_called_once()
        self.assertIsNone(engine.load_state()['transaction'])
        self.assertIn(B, engine.load_state()['failed'])

    def test_boot_recovery_does_not_restart_active_service_and_pauses_failed_start(self):
        self.state()
        self.backend.current_release = lambda: self.baseline
        self.backend._service = lambda: dict(MainPID="42", ActiveState="active")
        self.backend.start = Mock()
        engine = Engine(self.backend.state, self.backend)
        self.assertEqual(recover_engine(engine).status, 'no_transaction')
        self.backend.start.assert_not_called()
        self.backend._service = lambda: dict(MainPID="0", ActiveState="failed")
        self.backend.start.side_effect = Rejected("command_failed")
        self.assertEqual(recover_engine(engine).status, 'paused')
        self.assertEqual(engine.load_state()['paused'], 'command_failed')

    def test_configuration_cannot_redirect_root_actions(self):
        with self.assertRaisesRegex(Rejected, 'controller_configuration_out_of_scope'):
            validate_config(self.config)

    def test_policy_path_set_cannot_omit_a_configured_shared_reader(self):
        candidate = self.candidate()
        path = self.policy()
        row = json.loads(path.read_text())
        row['critical_paths'].pop()
        path.write_text(json.dumps(row))
        with patch('deploy.linux_backend.trusted'):
            with self.assertRaises(Rejected):
                self.backend.verify_compatibility(self.baseline, candidate)
        self.assertFalse(self.calls)

    def test_candidate_preparation_uses_only_verified_bundle_and_offline_steps(self):
        self.state()
        self.backend.current_release = lambda: self.baseline
        def unpack(archive, payload, commit, expected):
            payload.mkdir()
        def extract(archive, code, commit):
            code.mkdir()
            (code / 'critical.py').write_text('original')
        with patch('deploy.linux_backend.require_target'), \
                patch('deploy.linux_backend.get_bundle', return_value=(self.root / 'bundle.zip', 'f' * 64)), \
                patch('deploy.linux_backend.unpack_bundle', side_effect=unpack), \
                patch('deploy.linux_backend.extract_archive', side_effect=extract), \
                patch('deploy.linux_backend.freeze_release'), \
                patch.object(self.backend, '_check_reviewed_fingerprints'), \
                patch.object(self.backend, '_sandbox') as sandbox:
            release = self.backend.prepare(B)
        self.assertTrue(all(call.kwargs.get('offline') is True for call in sandbox.call_args_list))
        install = next(call.args[1] for call in sandbox.call_args_list if call.args[2] == 'install')
        for option in ('--no-index', '--require-hashes', '--no-deps', '--find-links'):
            self.assertIn(option, install)
        self.assertNotIn('-e', install)
        self.assertEqual(release.commit, B)
        self.assertEqual(json.loads((self.backend.manifests / (B + '.json')).read_text())['bundle_sha256'], 'f' * 64)

    def test_interrupted_transient_units_stop_before_another_candidate(self):
        self.candidate()
        unit = 'taki-prepare-' + B + '-tests.service'
        self.backend.run = Mock(side_effect=[unit + ' loaded active running candidate', ''])
        self.backend._quiesce_preparations()
        self.assertEqual(self.backend.run.call_args_list[1].args[0], ['/usr/bin/systemctl', 'stop', unit])
        self.backend.run = Mock(side_effect=[unit + ' loaded active running candidate', Rejected('command_timeout')])
        with self.assertRaisesRegex(Rejected, 'command_timeout'):
            self.backend.prepare('c' * 40)
        self.assertFalse((self.base / 'releases' / ('c' * 40)).exists())

    def test_quiescing_rejects_unrelated_unit_names(self):
        self.candidate()
        self.backend.run = Mock(return_value='xbot.service loaded active running')
        with self.assertRaisesRegex(Rejected, 'preparation_unit_not_owned'):
            self.backend._quiesce_preparations()
        self.assertEqual(self.backend.run.call_count, 1)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "source.tar.gz"

    def create(self, entries):
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, kind in entries:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.linkname = "/etc/passwd"
                item.size = 1 if kind == tarfile.REGTYPE else 0
                archive.addfile(item, io.BytesIO(b"x") if item.size else None)

    def test_regular_single_root_archive_extracts(self):
        self.create([("repo/src/app.py", tarfile.REGTYPE)])
        extract_archive(self.archive, self.root / "target")
        self.assertEqual((self.root / "target/src/app.py").read_bytes(), b"x")

    def test_unsafe_archives_rejected_before_any_extraction(self):
        scenarios = [
            [("repo/../../escape", tarfile.REGTYPE)], [("/absolute", tarfile.REGTYPE)],
            [("repo/link", tarfile.SYMTYPE)], [("repo/hard", tarfile.LNKTYPE)],
            [("repo/fifo", tarfile.FIFOTYPE)], [("repo/.env", tarfile.REGTYPE)],
            [("repo/file", tarfile.REGTYPE), ("other/file", tarfile.REGTYPE)],
            [("repo/file", tarfile.REGTYPE), ("repo/file", tarfile.REGTYPE)],
        ]
        for entries in scenarios:
            with self.subTest(entries=entries):
                self.create(entries)
                with self.assertRaises(Rejected):
                    extract_archive(self.archive, self.root / "target")
                self.assertFalse((self.root / "target").exists())

    def test_expanded_size_bound(self):
        self.create([("repo/file", tarfile.REGTYPE)])
        with patch("deploy.linux_backend.MAX_EXTRACTED", 0):
            with self.assertRaisesRegex(Rejected, "archive_size_limit"):
                extract_archive(self.archive, self.root / "target")


@unittest.skipUnless(os.name == 'posix', 'Linux process groups and nonblocking pipes require POSIX')
class BoundedProcessTests(unittest.TestCase):
    def test_normal_output_and_nonzero_exit(self):
        self.assertEqual(run_bounded([sys.executable, '-c', "print('bounded')"], timeout=5).strip(), 'bounded')
        with self.assertRaisesRegex(Rejected, 'command_failed'):
            run_bounded([sys.executable, '-c', 'raise SystemExit(2)'], timeout=5)

    def test_output_and_timeout_limits(self):
        with patch('deploy.linux_backend.MAX_OUTPUT', 64):
            with self.assertRaisesRegex(Rejected, 'command_output_limit'):
                run_bounded([sys.executable, '-c', "print('x' * 10000)"], timeout=5)
        with self.assertRaisesRegex(Rejected, 'command_timeout'):
            run_bounded([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=0.1)

    @unittest.skipUnless(getattr(os, 'geteuid', lambda: -1)() == 0, 'Root ownership check is for the installer validation')
    def test_freeze_owns_files_without_following_python_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'code').mkdir()
            (root / 'venv').mkdir()
            source = root / 'code/app.py'
            source.write_text('pass')
            executable = root / 'venv/python'
            executable.symlink_to(sys.executable)
            before = Path(sys.executable).stat()
            freeze_release(root)
            self.assertEqual(source.stat().st_mode & 0o777, 0o644)
            self.assertEqual((root / 'venv').stat().st_mode & 0o777, 0o755)
            self.assertEqual(source.stat().st_uid, 0)
            self.assertEqual(Path(sys.executable).stat().st_uid, before.st_uid)


if __name__ == "__main__":
    unittest.main()

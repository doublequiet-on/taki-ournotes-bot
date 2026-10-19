"""Offline Linux backend contracts: no real network, service, or credentials."""
from dataclasses import asdict
import hashlib
import http.client
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
from deploy.linux_backend import run_bounded, freeze_release, status_document, human_status, main
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

    def test_read_only_status_includes_progress_without_backend_or_state_writes(self):
        self.state(transaction=dict(phase="preparing", commit=B, previous=asdict(self.baseline), candidate=None))
        cache = self.backend.state / "bundle-cache"
        cache.mkdir()
        row = dict(version=1, commit=B, stage="downloading", route="asset_api",
                   received_bytes=1048576, expected_bytes=4194304, at="2026-10-04T00:00:00+00:00")
        atomic_json(cache / (B + ".download.json"), row)
        before = (self.backend.state / "state.json").read_bytes()
        backend = Mock()
        with patch("deploy.linux_backend.trusted"):
            result = status_document(Engine(self.backend.state, backend))
        backend.assert_not_called()
        self.assertFalse(backend.mock_calls)
        self.assertEqual((self.backend.state / "state.json").read_bytes(), before)
        self.assertEqual(result["observed_target"], B)
        self.assertEqual(result["download"]["received_bytes"], 1048576)
        output = human_status(result)
        self.assertIn("1.00 MB / 4.00 MB", output)
        self.assertIn("官方资产 API", output)
        self.assertIn("准备候选版本", output)
        self.assertIn("实际进程版本和 QQ 功能另行核验", output)

    def test_status_explains_waiting_for_retry_and_preserves_legacy_failure(self):
        self.state(download_retries={B: dict(attempts=3, after=1791073800)},
                   events=[dict(status="deferred", commit=B, reason="bundle_network_unavailable")])
        engine = Engine(self.backend.state, backend=None)
        output = human_status(status_document(engine))
        self.assertIn("等待下次重试，旧版未切换", output)
        self.assertIn("最早再次尝试（UTC）", output)
        self.state(failed={B: dict(reason="bundle_retry_exhausted")},
                   events=[dict(status="rejected", commit=B)])
        self.assertIn("该版本被阻止", human_status(status_document(engine)))
        self.assertIn(B, engine.load_state()["failed"])

    def test_status_invalid_or_symlink_progress_is_rejected_without_mutation(self):
        self.state(events=[dict(status="deferred", commit=B)])
        cache = self.backend.state / "bundle-cache"
        cache.mkdir()
        path = cache / (B + ".download.json")
        atomic_json(path, dict(version=1, commit=B, stage="downloading", route="untrusted",
                              received_bytes=0, expected_bytes=10, at="2026-10-04T00:00:00Z"))
        before = path.read_bytes()
        with patch("deploy.linux_backend.trusted"):
            with self.assertRaisesRegex(Rejected, "download_progress_invalid"):
                status_document(Engine(self.backend.state, backend=None))
        self.assertEqual(path.read_bytes(), before)
        with patch("deploy.linux_backend.trusted", side_effect=Rejected("controller_path_not_trusted")):
            with self.assertRaisesRegex(Rejected, "controller_path_not_trusted"):
                status_document(Engine(self.backend.state, backend=None))

    def test_human_option_cannot_trigger_update_and_uninitialized_is_clear(self):
        self.assertIn("尚未初始化", human_status({"status": "uninitialized"}))
        with patch("deploy.linux_backend.LinuxBackend") as backend, patch("builtins.print") as printer:
            self.assertEqual(main(["check", "--human"]), 1)
        backend.assert_not_called()
        self.assertIn("human_status_only", printer.call_args.args[0])

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

    def test_ci_read_interruption_never_permanently_blocks_target(self):
        class Broken(io.BytesIO):
            def read(self, *args):
                raise http.client.IncompleteRead(b"", 30)
        self.state()
        engine = Engine(self.backend.state, self.backend)
        with patch.object(self.backend, "current_release", return_value=self.baseline), \
             patch.object(self.backend, "resolve_main", return_value=B), \
             patch.object(self.backend, "prepare", side_effect=Deferred("bundle_not_published")) as prepare, \
             patch("deploy.linux_backend.urllib.request.urlopen", side_effect=lambda *a, **k: Broken()) as fetch:
            for _ in range(2):
                result = engine.run()
                self.assertEqual((result.status, result.reason), ("deferred", "github_unavailable"))
                self.assertNotIn(B, engine.load_state()["failed"])
            self.assertEqual(fetch.call_count, 2)
            prepare.assert_not_called()
            runs, jobs = self.ci_rows()
            fetch.side_effect = [io.BytesIO(json.dumps(runs).encode()), io.BytesIO(json.dumps(jobs).encode())]
            result = engine.run()
            self.assertEqual((result.status, result.reason), ("deferred", "bundle_not_published"))
            prepare.assert_called_once_with(B)
            self.assertNotIn(B, engine.load_state()["failed"])

    def test_actual_failed_ci_stays_blocked_after_transport_fix(self):
        runs, _ = self.ci_rows()
        runs["workflow_runs"][0]["conclusion"] = "failure"
        self.state()
        engine = Engine(self.backend.state, self.backend)
        with patch.object(self.backend, "current_release", return_value=self.baseline), \
             patch.object(self.backend, "resolve_main", return_value=B), \
             patch.object(self.backend, "prepare") as prepare, \
             patch("deploy.linux_backend.urllib.request.urlopen", return_value=io.BytesIO(json.dumps(runs).encode())) as fetch:
            self.assertEqual(engine.run().reason, "ci_failed")
            self.assertEqual(engine.run().status, "blocked")
            fetch.assert_called_once()
            prepare.assert_not_called()

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

    def test_note_preparation_exposes_only_persistent_artwork_not_other_shared_data(self):
        release = self.candidate()
        self.config['credential_paths'] = [str(self.base / 'config'), str(self.base / 'data'), str(self.base / 'logs')]
        self.backend._prepare_note_assets(release)
        command = next(argv for argv, _ in self.calls if argv[0] == '/usr/bin/systemd-run')
        mounted = next(v for v in command if v.startswith('BindPaths=')).split('=',1)[1].split()
        writable = next(v for v in command if v.startswith('ReadWritePaths=')).split('=',1)[1].split()
        self.assertEqual(mounted, [str(Path(release.code_dir).parent), str(self.base / 'data/game-assets')])
        self.assertEqual(writable, mounted)
        self.assertNotIn('BindPaths=' + str(self.base / 'data'), command)
        hidden = next(v for v in command if v.startswith('InaccessiblePaths='))
        self.assertIn(str(self.base / 'config'), hidden)
        self.assertIn(str(self.base / 'logs'), hidden)
        self.assertNotIn('-' + str(self.base / 'data'), hidden.split())
        self.assertIn('ProtectHome=tmpfs', command)
        self.assertIn('User=ubuntu', command)
        for option in ('QQ_APP_ID=', 'QQ_APP_SECRET=', 'AI_API_KEY='):
            self.assertIn(option, command)
        self.assertNotIn('PrivateNetwork=yes', command)
        self.assertFalse(any(argv[:2] == ['/usr/bin/systemctl', 'stop'] for argv, _ in self.calls))

    def test_different_release_preparations_keep_the_same_persistent_note_cache(self):
        cache=self.base/'data/game-assets'
        cache.mkdir(parents=True)
        existing=cache/'old-version-hash.png'
        existing.write_bytes(b'previous verified version')
        for sha in (B,'c'*40):
            self.backend._prepare_note_assets(self.candidate(sha))
        commands=[argv for argv,_ in self.calls if argv[0]=='/usr/bin/systemd-run']
        self.assertEqual(len(commands),2)
        self.assertTrue(all(argv[-2:]==['--cache-dir',str(cache)] for argv in commands))
        self.assertEqual(existing.read_bytes(),b'previous verified version')

    def test_failed_note_preparation_aborts_update_before_service_stop(self):
        self.state()
        release = self.candidate()
        with patch.object(self.backend, 'current_release', return_value=self.baseline), \
             patch.object(self.backend, 'resolve_main', return_value=B), \
             patch.object(self.backend, 'check_ci'), \
             patch.object(self.backend, 'prepare', side_effect=Rejected('note_assets_unavailable')), \
             patch.object(self.backend, 'stop') as stop, \
             patch.object(self.backend, 'activate') as activate:
            result = Engine(self.backend.state, self.backend).run()
        self.assertEqual(result.status, 'rejected')
        self.assertEqual(result.reason, 'note_assets_unavailable')
        stop.assert_not_called()
        activate.assert_not_called()

    def test_query_asset_sandbox_exposes_only_candidate_and_two_cache_directories(self):
        release = self.candidate()
        self.config['credential_paths'] = [str(self.base / 'config'), str(self.base / 'data')]
        self.backend._sandbox(release, ['python', 'prepare_query_assets.py'], 'query-assets', offline=False, query_cache=True)
        argv = self.calls[-1][0]
        allowed = str(Path(release.code_dir).parent) + ' ' + str(self.base / 'data/game-assets') + ' ' + str(self.base / 'data/haneoka-asset-cache')
        self.assertIn('BindPaths=' + allowed, argv)
        self.assertIn('ReadWritePaths=' + allowed, argv)
        self.assertIn('ProtectHome=tmpfs', argv)
        self.assertNotIn('PrivateNetwork=yes', argv)
        hidden = next(v for v in argv if v.startswith('InaccessiblePaths='))
        self.assertIn(str(self.base / 'config'), hidden)
        self.assertIn('QQ_APP_ID=', argv)
        self.assertIn('QQ_APP_SECRET=', argv)

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

    def test_koishi_stop_closes_gateway_before_backend(self):
        order=[]
        self.backend.koishi=Mock()
        self.backend.koishi.stop.side_effect=lambda:order.append('gateway')
        self.backend.run=lambda *args,**kw:order.append('backend')
        self.backend._service=lambda:{'MainPID':'0','ActiveState':'inactive'}
        self.backend._snapshot_stopped_data=Mock()
        self.backend.stop()
        self.assertEqual(order,['gateway','backend'])

    def test_koishi_health_uses_both_pids_and_exact_combination(self):
        from deploy.koishi_control import health_matches
        row=dict(ready=True,release_sha=B,pid=12,backend_pid=11,backend_code_digest='digest',
                 qq_online=True,handshake=True,backend_ready=True)
        self.assertTrue(health_matches(row,B,'11','12','digest'))
        for field,value in [('pid',13),('backend_pid',10),('release_sha',A),('handshake',False),('qq_online',False)]:
            self.assertFalse(health_matches({**row,field:value},B,'11','12','digest'))
        self.backend.koishi=Mock();self.backend.koishi.healthy.return_value=False
        self.assertFalse(self.backend.healthy(self.baseline))
        self.assertEqual(self.calls,[])

    def test_koishi_start_handles_unloaded_stopped_and_failed_units(self):
        from deploy.koishi_control import KoishiControl
        for initial in ('inactive', 'failed', 'active', 'activating'):
            with self.subTest(state=initial):
                control = KoishiControl(self.backend)
                control.combination = Mock()
                self.backend.current_release = lambda: self.baseline
                state = {'ActiveState': initial}
                commands = []
                def runner(argv, **kwargs):
                    action = argv[1]
                    if action == 'show':
                        return 'ActiveState=' + state['ActiveState'] + '\nMainPID=0\n'
                    commands.append(action)
                    if action == 'reset-failed':
                        if state['ActiveState'] != 'failed':
                            raise Rejected('unit_not_loaded')
                        state['ActiveState'] = 'inactive'
                    elif action == 'start':
                        state['ActiveState'] = 'active'
                    return ''
                self.backend.run = runner
                control.start()
                self.assertEqual(state['ActiveState'], 'active')
                self.assertEqual(commands, ['reset-failed', 'start'] if initial == 'failed' else ['start'])
                control.combination.assert_called_once_with(self.baseline)

    def test_koishi_start_preserves_validation_reset_and_start_failures(self):
        from deploy.koishi_control import KoishiControl
        for failure in ('combination', 'reset-failed', 'start'):
            with self.subTest(failure=failure):
                control = KoishiControl(self.backend)
                control.combination = Mock(side_effect=ValueError('invalid_combination') if failure == 'combination' else None)
                control.state = Mock(return_value={'ActiveState': 'failed' if failure == 'reset-failed' else 'inactive'})
                self.backend.current_release = lambda: self.baseline
                def runner(argv, **kwargs):
                    if argv[1] == failure: raise Rejected('command_failed')
                    return ''
                self.backend.run = Mock(side_effect=runner)
                with self.assertRaises((ValueError, Rejected)):
                    control.start()
                if failure == 'combination': self.backend.run.assert_not_called()
                if failure == 'reset-failed':
                    self.assertFalse(any(c.args[0][1] == 'start' for c in self.backend.run.call_args_list))

    def test_backend_start_resets_only_failed_and_keeps_permit_before_processes(self):
        original_path = Path
        boot = self.root / 'boot-id'
        boot.write_text('test-boot')
        for initial in ('inactive', 'failed', 'active'):
            with self.subTest(state=initial):
                runtime = self.root / ('run-' + initial)
                def selected_path(value):
                    if value == '/run/taki-updater': return runtime
                    if value == '/proc/sys/kernel/random/boot_id': return boot
                    return original_path(value)
                self.backend.current_release = lambda: self.baseline
                self.backend._service = Mock(return_value={'ActiveState': initial})
                commands = []
                def runner(argv, **kwargs):
                    if argv[1] == 'reset-failed' and initial != 'failed': raise Rejected('unit_not_loaded')
                    permit = json.loads((runtime / 'allowed.json').read_text())
                    self.assertEqual(permit, {'release': asdict(self.baseline), 'boot_id': 'test-boot'})
                    self.assertEqual((runtime / 'allowed.json').stat().st_mode & 0o777, 0o644 if os.name != 'nt' else 0o666)
                    commands.append(argv[1])
                    return ''
                self.backend.run = runner
                self.backend.koishi = Mock()
                self.backend.koishi.start.side_effect = lambda: commands.append('gateway')
                with patch('deploy.linux_backend.Path', side_effect=selected_path), patch('deploy.linux_backend.trusted'):
                    self.backend.start()
                expected = ['reset-failed'] if initial == 'failed' else []
                self.assertEqual(commands, expected + ['start', 'gateway'])

    @unittest.skipUnless(os.name == 'posix', 'root CLI and POSIX directory permissions')
    def test_recover_cli_initializes_missing_runtime_before_notice_and_start(self):
        from deploy.koishi_control import KoishiControl
        self.state()
        runtime=self.root/'run'
        boot=self.root/'boot-id';boot.write_text('fixture-boot')
        config=self.root/'config.json';config.write_text(json.dumps(self.config))
        self.backend.current_release=lambda:self.baseline
        self.backend._service=Mock(return_value={'MainPID':'0','ActiveState':'inactive'})
        self.backend.healthy=Mock(return_value=True)
        self.backend.koishi=KoishiControl(self.backend)
        self.backend.koishi.start=Mock()
        def selected_path(value):
            return runtime if value=='/run/taki-updater' else boot if value=='/proc/sys/kernel/random/boot_id' else Path(value)
        def runner(argv,**kwargs):
            self.assertEqual(json.loads((runtime/'notice.json').read_text()),{'rejected':True})
            self.assertEqual(json.loads((runtime/'allowed.json').read_text()),
                             {'release':asdict(self.baseline),'boot_id':'fixture-boot'})
            self.assertEqual(runtime.stat().st_mode&0o777,0o755)
            self.assertEqual((runtime/'notice.json').stat().st_mode&0o777,0o644)
            return ''
        self.backend.run=runner
        with patch('deploy.linux_backend.os.geteuid',return_value=0), \
             patch('deploy.linux_backend.trusted'),patch('deploy.linux_backend.validate_config'), \
             patch('deploy.linux_backend.LinuxBackend',return_value=self.backend), \
             patch('deploy.linux_backend.Path',side_effect=selected_path),patch('builtins.print') as printer:
            previous=os.umask(0o077)
            try:code=main(['--config',str(config),'recover'])
            finally:os.umask(previous)
        self.assertEqual(code,0)
        self.assertEqual(json.loads(printer.call_args.args[0])['reason'],'known_good_restarted')
        self.backend.koishi.start.assert_called_once()

    def test_runtime_directory_rejects_untrusted_existing_path_before_notice(self):
        from deploy.koishi_control import KoishiControl
        runtime=self.root/'run';runtime.mkdir()
        marker=runtime/'notice.json';marker.write_text('unchanged')
        with patch('deploy.linux_backend.Path',return_value=runtime), \
             patch('deploy.linux_backend.trusted',side_effect=Rejected('controller_path_not_trusted')):
            with self.assertRaisesRegex(Rejected,'controller_path_not_trusted'):
                KoishiControl(self.backend).publish_notice(None)
        self.assertEqual(marker.read_text(),'unchanged')
        self.assertFalse((runtime/'allowed.json').exists())

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

    def state(self, *, previous=None, transaction=None, **fields):
        state = dict(version=1, current=asdict(self.baseline),
                     previous=asdict(previous) if previous else None, transaction=transaction,
                     paused=None, failed={}, events=[])
        state.update(fields)
        atomic_json(self.backend.state / "state.json", state)

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

    def test_code_backup_scopes_budget_to_its_own_archive(self):
        release = self.candidate()
        def runner(argv, **kwargs):
            self.calls.append((argv, kwargs))
            Path(next(v.removeprefix('--file=') for v in argv if v.startswith('--file='))).write_bytes(b'code archive')
        self.backend.run = runner
        with patch('deploy.linux_backend.shutil.disk_usage', return_value=Mock(free=8 * GIB)):
            archive = Path(self.backend.backup(self.baseline, release))
        self.assertEqual(archive.read_bytes(), b'code archive')
        self.assertEqual(self.calls[0][1]['budget_root'], self.backend.backups)
        self.assertEqual(self.calls[0][1]['budget_file'], archive)

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
        self.assertEqual(row['excluded_rebuildable_paths'], ['data/haneoka-asset-cache'])
        self.assertEqual((self.base / 'data/cache.json').read_text(), 'live data')
        self.assertNotIn('--extract', self.calls[0][0])
        self.assertEqual(self.calls[0][1]['budget_file'], self.backend.backups / 'owned-data.tar')
        self.state(transaction=dict(phase="rollback_stopping", backup=str(archive)))
        self.backend._snapshot_stopped_data()
        self.assertEqual(len(self.calls), 1)

    @unittest.skipUnless(os.name == 'posix', 'requires native GNU tar')
    def test_data_snapshot_excludes_only_root_query_art_and_preserves_business_data(self):
        archive = self.backend.backups / 'owned.tar'
        archive.write_bytes(b'code')
        atomic_json(archive.with_suffix('.json'), dict(owner='taki-linux-updater-v1', archive=archive.name))
        retained = ('cache.json', 'ai-quota.json', 'ai-metrics.json', 'ai-metrics.v2.json',
                    'history.sqlite3', 'update-notices.sqlite3',
                    'nested/haneoka-asset-cache/keep.png', 'haneoka-asset-cache-other/keep.png',
                    'game-assets/notes.png')
        original = {}
        for name in (*retained, 'haneoka-asset-cache/art.png', '.env'):
            path = self.base / 'data' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            original[name] = name.encode()
            path.write_bytes(original[name])
        self.state(transaction=dict(phase='stopping', backup=str(archive)))
        self.backend.run = run_bounded
        self.backend._snapshot_stopped_data()
        snapshot = archive.with_name('owned-data.tar')
        with tarfile.open(snapshot) as saved:
            names = saved.getnames()
            self.assertFalse(any(n == 'data/haneoka-asset-cache' or n.startswith('data/haneoka-asset-cache/') for n in names))
            self.assertNotIn('data/.env', names)
            for name in retained:
                self.assertEqual(saved.extractfile('data/' + name).read(), original[name])
        for name, content in original.items():
            self.assertEqual((self.base / 'data' / name).read_bytes(), content)

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

    def test_candidate_preparation_keeps_install_offline_and_prepares_note_cache_before_freeze(self):
        self.state(transaction=dict(phase="preparing", commit=B, previous=asdict(self.baseline), candidate=None))
        self.backend.current_release = lambda: self.baseline
        def unpack(archive, payload, commit, expected):
            self.assertEqual(json.loads((self.backend.state / "state.json").read_text())["transaction"]["preparation_step"],
                             "offline_preparation")
            payload.mkdir()
        def extract(archive, code, commit):
            code.mkdir()
            (code / 'critical.py').write_text('original')
        def bundle(*args, **kwargs):
            self.assertEqual(json.loads((self.backend.state / "state.json").read_text())["transaction"]["preparation_step"],
                             "downloading")
            return self.root / 'bundle.zip', 'f' * 64
        with patch('deploy.linux_backend.require_target'), \
                patch('deploy.linux_backend.get_bundle', side_effect=bundle), \
                patch('deploy.linux_backend.unpack_bundle', side_effect=unpack), \
                patch('deploy.linux_backend.extract_archive', side_effect=extract), \
                patch('deploy.linux_backend.freeze_release'), \
                patch.object(self.backend, '_check_reviewed_fingerprints'), \
                patch.object(self.backend, '_sandbox') as sandbox:
            release = self.backend.prepare(B)
        note_step = next(call for call in sandbox.call_args_list if call.args[2] == 'note-assets')
        self.assertEqual(note_step.kwargs, {'offline': False, 'note_cache': True})
        self.assertEqual(note_step.args[1][-2:], ['--cache-dir', str(self.base / 'data/game-assets')])
        self.assertTrue(all(call.kwargs.get('offline') is True for call in sandbox.call_args_list
                            if call.args[2] not in ('note-assets','query-assets')))
        query_step = next(call for call in sandbox.call_args_list if call.args[2] == 'query-assets')
        self.assertEqual(query_step.kwargs, {'offline': False, 'query_cache': True})
        self.assertIn(str(self.base / 'data'), query_step.args[1])
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
    def test_archive_budget_excludes_retained_backups_and_preserves_them(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            retained, current = root / 'retained.tar', root / 'current.tar'
            retained.write_bytes(b'old' * 1024)
            original = retained.read_bytes()
            with patch('deploy.linux_backend.GIB', 1024):
                run_bounded([sys.executable, '-c',
                    "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'new' * 128)", str(current)],
                    timeout=5, budget_root=root, budget_file=current)
            self.assertEqual(retained.read_bytes(), original)
            self.assertEqual(current.read_bytes(), b'new' * 128)

    def test_archive_size_and_free_space_limits_remain_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            current = root / 'current.tar'
            command = [sys.executable, '-c',
                       "import pathlib,sys,time; print('started',flush=True); time.sleep(.1); pathlib.Path(sys.argv[1]).write_bytes(b'x' * 4096)", str(current)]
            with patch('deploy.linux_backend.GIB', 1024):
                with self.assertRaisesRegex(Rejected, 'candidate_disk_budget'):
                    run_bounded(command, timeout=5, budget_root=root, budget_file=current)
            with patch('deploy.linux_backend.shutil.disk_usage', return_value=Mock(free=0)):
                with self.assertRaisesRegex(Rejected, 'candidate_disk_budget'):
                    run_bounded([sys.executable, '-c', 'pass'], timeout=5,
                                budget_root=root, budget_file=root / 'not-created.tar')

    def test_candidate_directory_still_has_a_total_size_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'large').write_bytes(b'x' * 4096)
            with patch('deploy.linux_backend.GIB', 1024):
                with self.assertRaisesRegex(Rejected, 'candidate_disk_budget'):
                    run_bounded([sys.executable, '-c', 'pass'], timeout=5, budget_root=root)

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

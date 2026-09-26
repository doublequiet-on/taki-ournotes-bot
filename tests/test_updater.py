"""Updater tests never connect to QQ, install tasks, or stop actual processes."""
import importlib.util
import base64
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import subprocess
import unittest
from unittest.mock import patch, Mock

SPEC = importlib.util.spec_from_file_location("taki_updater", Path(__file__).resolve().parents[1] / "scripts/update_bot.py")
u = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(u)
A, B = "a" * 40, "b" * 40


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.up = u.Updater(self.root)
        self.up.event = Mock()

    def test_corrupt_state_fails_closed(self):
        self.up.state_file.write_text("{broken", encoding="utf-8")
        with self.assertRaises(u.Paused):
            u.read_json(self.up.state_file)

    def test_atomic_state_and_exclusive_lock(self):
        u.atomic_json(self.up.state_file, {"active": A})
        self.assertEqual(u.read_json(self.up.state_file), {"active": A})
        with u.exclusive(self.up.folder / "lock"):
            with self.assertRaises(u.Paused):
                with u.exclusive(self.up.folder / "lock"):
                    self.fail("must not acquire twice")
        with u.exclusive(self.up.folder / "lock"):
            pass

    def test_validation_environment_drops_credentials(self):
        with patch.dict(os.environ, {"QQ_APP_SECRET": "SECRET", "AI_API_KEY": "SECRET",
                                     "X_BEARER_TOKEN": "SECRET", "OURNOTES_CACHE_FILE": "PRIVATE",
                                     "PYTHONPATH": "PRIVATE"}):
            env = u.validation_env()
        self.assertNotIn("SECRET", env.values())
        self.assertNotIn("PRIVATE", env.values())
        self.assertEqual(env["QQ_APP_SECRET"], "")

    def test_ci_requires_exact_sha_and_both_jobs(self):
        run = {"id": 7, "head_sha": B, "event": "push", "head_branch": "main",
               "status": "completed", "conclusion": "success", "run_attempt": 2}
        jobs = [{"name": f"test-and-package ({v})", "conclusion": "success"} for v in ("3.10", "3.12")]
        get = Mock(side_effect=[{"workflow_runs": [run]}, {"jobs": jobs}])
        self.assertTrue(u.ci_passed(B, get))
        self.assertIn("/attempts/2/jobs", get.call_args.args[0])
        self.assertFalse(u.ci_passed(A, Mock(return_value={"workflow_runs": [run]})))
        self.assertFalse(u.ci_passed(B, Mock(side_effect=[{"workflow_runs": [run]}, {"jobs": jobs[:1]}])))

    def test_ci_latest_failed_run_blocks_older_success(self):
        common = {"head_sha": B, "event": "push", "head_branch": "main", "status": "completed"}
        get = Mock(return_value={"workflow_runs": [dict(common, id=1, conclusion="success"),
                                                   dict(common, id=2, conclusion="failure")]})
        self.assertFalse(u.ci_passed(B, get))
        self.assertEqual(get.call_count, 1)

    def test_archive_rejects_escape_links_and_runtime_data(self):
        for name, kind in [("../outside", tarfile.REGTYPE), ("/outside", tarfile.REGTYPE),
                           ("src/link", tarfile.SYMTYPE), (".env", tarfile.REGTYPE),
                           ("data/cache.json", tarfile.REGTYPE), ("a\\b", tarfile.REGTYPE)]:
            with self.subTest(name=name):
                blob = io.BytesIO()
                with tarfile.open(fileobj=blob, mode="w") as archive:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    archive.addfile(entry)
                with self.assertRaises(u.Paused):
                    u.extract_snapshot(blob.getvalue(), self.root / "extract")

    def test_archive_accepts_example_and_aliases(self):
        blob = io.BytesIO()
        with tarfile.open(fileobj=blob, mode="w") as archive:
            for name in (".env.example", "data/.gitkeep", "query_aliases.json"):
                entry = tarfile.TarInfo(name)
                entry.size = 2
                archive.addfile(entry, io.BytesIO(b"{}"))
        u.extract_snapshot(blob.getvalue(), self.root / "extract")
        self.assertEqual((self.root / "extract/query_aliases.json").read_bytes(), b"{}")

    def test_dirty_worktree_never_fetches_or_stops(self):
        self.up.git = Mock(side_effect=["main", " M src/file.py"])
        self.up.prepare = Mock()
        with patch.object(u, "stop") as stop:
            with self.assertRaises(u.Paused):
                self.up.once()
            stop.assert_not_called()
        self.up.prepare.assert_not_called()
        self.assertEqual(self.up.git.call_count, 2)

    def configured(self):
        state = {"active": {"sha": A}, "rejected": None}
        u.atomic_json(self.up.state_file, state)
        self.up.clean = Mock(return_value=A)
        self.up.validate_release = Mock()
        self.up.git = Mock(side_effect=["", B, ""])
        self.up.start = Mock()
        self.up.prepare = Mock(return_value={"sha": B})
        return state

    def test_rejected_commit_is_not_retried(self):
        state = self.configured()
        state["rejected"] = B
        u.atomic_json(self.up.state_file, state)
        with patch.object(u, "processes", return_value=[{}]), patch.object(u, "ci_passed") as ci:
            with self.assertRaises(u.Paused):
                self.up.once()
        ci.assert_not_called()
        self.up.prepare.assert_not_called()

    def test_pending_ci_and_check_only_do_not_deploy(self):
        self.configured()
        self.up.deploy = Mock()
        with patch.object(u, "processes", return_value=[{}]), patch.object(u, "ci_passed", return_value=False):
            with self.assertRaises(u.Paused):
                self.up.once()
        self.up.prepare.assert_not_called()
        self.up.git = Mock(side_effect=["", B, ""])
        with patch.object(u, "ci_passed", return_value=True):
            self.up.once(check=True)
        self.up.start.assert_not_called()
        self.up.deploy.assert_not_called()

    def test_build_failure_does_not_stop_live_bot(self):
        self.configured()
        self.up.prepare.side_effect = TimeoutError("SECRET")
        with patch.object(u, "processes", return_value=[{}]), patch.object(u, "ci_passed", return_value=True), patch.object(u, "stop") as stop:
            with self.assertRaises(u.Paused) as error:
                self.up.once()
        stop.assert_not_called()
        self.assertNotIn("SECRET", str(error.exception))
        self.assertEqual(u.read_json(self.up.state_file)["rejected"], B)

    def test_start_failure_rolls_back_without_rewinding_git_or_data(self):
        state = self.configured()
        quota = self.root / "data/ai-quota.json"
        quota.write_text('{"used":42}', encoding="utf-8")
        self.up.start.side_effect = [u.Paused("failed"), None]
        with patch.object(u, "stop") as stop:
            with self.assertRaises(u.Paused):
                self.up.deploy({"sha": B}, state["active"], state, A)
        self.assertEqual(stop.call_count, 2)
        self.assertEqual(self.up.start.call_args.args[0]["sha"], A)
        self.up.git.assert_not_called()
        self.assertEqual(quota.read_text(), '{"used":42}')
        self.assertFalse(self.up.journal.exists())
        self.assertEqual(u.read_json(self.up.state_file)["active"]["sha"], A)

    def test_success_only_fast_forwards_after_ready(self):
        state = self.configured()
        self.up.git = Mock()
        with patch.object(u, "stop"):
            self.up.deploy({"sha": B}, state["active"], state, A)
        self.up.git.assert_called_once_with("merge", "--ff-only", "--no-overwrite-ignore", B)
        self.assertEqual(u.read_json(self.up.state_file)["active"]["sha"], B)

    def test_interrupted_transaction_restores_before_fetch(self):
        state = self.configured()
        u.atomic_json(self.up.journal, {"previous": state["active"], "candidate": {"sha": B}, "old_state": state})
        with patch.object(u, "stop"):
            self.up.once()
        self.up.git.assert_not_called()
        self.up.start.assert_called_once_with(state["active"])

    def test_start_only_reuses_existing_instance(self):
        self.configured()
        with patch.object(u, "processes", return_value=[{}]):
            self.up.once(start_only=True)
        self.up.start.assert_not_called()
        self.up.git.assert_not_called()

    def test_changed_head_cancels_switch(self):
        state = self.configured()
        self.up.clean.return_value = B
        with patch.object(u, "stop") as stop:
            with self.assertRaises(u.Paused):
                self.up.deploy({"sha": B}, state["active"], state, A)
        stop.assert_not_called()

    def test_process_selection_excludes_feed_and_other_checkout(self):
        rows = [dict(ProcessId=1, ParentProcessId=0, ExecutablePath=str(self.root / ".venv/Scripts/python.exe"),
                     CommandLine="legacy"),
                dict(ProcessId=2, ParentProcessId=1, ExecutablePath="base-python", CommandLine="legacy"),
                dict(ProcessId=3, ParentProcessId=0, ExecutablePath="pythonw", CommandLine="feed")]
        decode = lambda cmd: ["python", "-m", "ournotes_bot.main", "bot"] if cmd == "legacy" else ["python", "feed.py"]
        with patch.object(u, "windows_processes", return_value=rows), patch.object(u, "argv_windows", side_effect=decode):
            self.assertEqual([p["ProcessId"] for p in u.processes(self.root)], [1, 2])
            rows.append(dict(rows[0], ProcessId=4))
            with self.assertRaises(u.Paused):
                u.processes(self.root)

    def test_unknown_query_process_blocks_start(self):
        row = dict(ProcessId=6, ParentProcessId=0, ExecutablePath="unrelated-python", CommandLine="query")
        with patch.object(u, "windows_processes", return_value=[row]), patch.object(u, "argv_windows", return_value=["python", "-m", "ournotes_bot.main", "bot"]):
            with self.assertRaises(u.Paused):
                u.processes(self.root)

    def test_runtime_uses_separate_unlimited_task(self):
        python = self.root / "python.exe"
        python.with_name("pythonw.exe").touch()
        with patch.object(u, "run") as command:
            u.launch_runtime(self.root, {"python": str(python), "source": str(self.root / "source")}, "c" * 32)
        args = command.call_args.args[0]
        script = base64.b64decode(args[-1]).decode("utf-16-le")
        self.assertIn("Taki-OurNotes-Runtime", script)
        self.assertIn("([TimeSpan]::Zero)", script)
        self.assertIn("-LogonType Interactive -RunLevel Limited", script)
        self.assertNotIn("QQ_APP_SECRET", script)

    def test_git_snapshot_and_fast_forward_protect_ignored_file(self):
        def git(*args):
            return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args], cwd=self.root, stderr=subprocess.DEVNULL).decode().strip()
        git("init", "-b", "main")
        (self.root / ".gitignore").write_text("data/\nlocal.txt\n", encoding="utf-8")
        git("add", ".gitignore")
        git("commit", "-m", "base")
        base = git("rev-parse", "HEAD")
        git("switch", "-c", "candidate")
        (self.root / "local.txt").write_text("incoming", encoding="utf-8")
        git("add", "-f", "local.txt")
        git("commit", "-m", "candidate")
        target = git("rev-parse", "HEAD")
        git("switch", "main")
        (self.root / "local.txt").write_text("private", encoding="utf-8")
        release = self.up.snapshot(target)
        self.assertEqual((Path(release["source"]) / "local.txt").read_text(), "incoming")
        with self.assertRaises(u.Paused):
            self.up.git("merge", "--ff-only", "--no-overwrite-ignore", target)
        self.assertEqual(git("rev-parse", "HEAD"), base)
        self.assertEqual((self.root / "local.txt").read_text(), "private")


if __name__ == "__main__":
    unittest.main()

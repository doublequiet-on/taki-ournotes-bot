"""Linux effects for the durable updater; run the installed controller as root.

L3 Input: root-owned JSON configuration, public GitHub, systemd and reviewed policy.
Output: isolated releases, bounded logs, recovery snapshots, current symlink and
read-only status including download progress; status --human uses Chinese text.
Pos: deployment backend for linux_updater; see deploy/L2.md.
Effects: HTTPS, disk writes and service control; candidate code only runs as ubuntu.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

try:
    from .offline_bundle import get_bundle, require_target, unpack_bundle
    from .linux_updater import Deferred, Engine, LockBusy, Rejected, Release, RunResult, SHA_RE, atomic_json
except ImportError:  # Direct execution from the installed, root-owned directory.
    from offline_bundle import get_bundle, require_target, unpack_bundle
    from linux_updater import Deferred, Engine, LockBusy, Rejected, Release, RunResult, SHA_RE, atomic_json


REPO = "doublequiet-on/taki-ournotes-bot"
API = "https://api.github.com/repos/" + REPO
GIB = 1024 ** 3
MAX_DOWNLOAD = 32 * 1024 ** 2
MAX_EXTRACTED = 256 * 1024 ** 2
MAX_OUTPUT = 2 * 1024 ** 2
JOBS = {"test-and-package (3.10)", "test-and-package (3.12)", "offline-linux-bundle"}
READY = re.compile(r"ournotes_bot\.qq\b.*机器人 .+ 已上线")
ERROR = re.compile(r"\b(?:ERROR|CRITICAL|Traceback|ModuleNotFoundError|ImportError)\b")


def fingerprints(root: Path, paths: list[str]) -> dict[str, str]:
    result = {}
    for value in paths:
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts or "\\" in value:
            raise Rejected("invalid_compatibility_path")
        path = root.joinpath(*relative.parts)
        if path.is_symlink() or (path.exists() and path.resolve() != path):
            raise Rejected("compatibility_path_symlink")
        raw = path.read_bytes().replace(b"\r\n", b"\n") if path.is_file() else None
        result[value] = hashlib.sha256(raw).hexdigest() if raw is not None else "absent"
    return result


def directory_bytes(root: Path) -> int:
    total = entries = 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        entries += len(dirs) + len(files)
        if entries > 50000:
            raise Rejected("candidate_file_limit")
        for name in files:
            try:
                info = (Path(parent) / name).lstat()
            except FileNotFoundError:  # pip atomically moves temporary files.
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
            if total > 2 * GIB:
                return total
    return total


def trusted(path: Path, *, directory: bool = False) -> None:
    """Reject symlinked or application-writable controller configuration/state."""
    for item in (path, *path.parents):
        mode = item.lstat()
        if stat.S_ISLNK(mode.st_mode) or mode.st_uid != 0 or mode.st_mode & 0o022:
            raise Rejected("controller_path_not_trusted")
    if directory and not path.is_dir():
        raise Rejected("controller_directory_missing")


def tree_digest(root: Path) -> str:
    """Digest shipped code, excluding runtime/build products; never follow links."""
    excluded = {"data", ".git", ".venv", "venv", "build", "dist"}
    digest = hashlib.sha256()
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.endswith(".egg-info")
                         and (Path(parent) != root or d not in excluded))
        for name in dirs + sorted(files):
            path = Path(parent) / name
            if path.is_symlink():
                raise Rejected("code_contains_symlink")
            if path.is_file() and name != ".env" and not name.endswith(".pyc"):
                digest.update(path.relative_to(root).as_posix().encode() + b"\0")
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                digest.update(b"\0")
    return digest.hexdigest()


def environment_digest(root: Path) -> str:
    """Pin installed files and symlink targets without following external links."""
    digest = hashlib.sha256()
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(dirs + files):
            path = Path(parent) / name
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            if path.is_symlink():
                digest.update(b"link:" + os.readlink(path).encode())
            elif path.is_file() and not name.endswith(".pyc"):
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
            digest.update(b"\0")
    return digest.hexdigest()


def freeze_release(root: Path) -> None:
    """After testing, give ubuntu read/execute access while root owns code/venv."""
    os.chown(root, 0, 0)
    root.chmod(0o755)
    for folder in (root / "code", root / "venv"):
        for parent, dirs, files in os.walk(folder, followlinks=False):
            for path in (Path(parent), *(Path(parent) / n for n in dirs + files)):
                os.chown(path, 0, 0, follow_symlinks=False)
                if not path.is_symlink():
                    executable = path.is_dir() or bool(path.stat().st_mode & 0o111)
                    path.chmod(0o755 if executable else 0o644)


def extract_archive(archive: Path, target: Path, expected_commit: str | None = None) -> None:
    """Extract only regular files/directories below a single GitHub archive root."""
    with tarfile.open(archive, "r:gz") as stream:
        members = []
        roots, names, total = set(), set(), 0
        for item in stream:
            members.append(item)
            if len(members) > 20000:
                raise Rejected("archive_entry_limit")
            path = PurePosixPath(item.name)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.name
                    or not path.parts or not (item.isfile() or item.isdir())):
                raise Rejected("unsafe_archive_member")
            roots.add(path.parts[0])
            relative = PurePosixPath(*path.parts[1:])
            if relative.as_posix() == ".":
                continue
            if relative in names or any(p in {".git", ".env"} for p in relative.parts):
                raise Rejected("unsafe_archive_member")
            names.add(relative)
            total += item.size
            if total > MAX_EXTRACTED or item.size < 0:
                raise Rejected("archive_size_limit")
        if len(roots) != 1:
            raise Rejected("archive_multiple_roots")
        if expected_commit is not None and roots != {"taki-ournotes-bot-" + expected_commit}:
            raise Rejected("archive_commit_mismatch")
        target.mkdir(mode=0o755)
        for item in members:
            relative = PurePosixPath(*PurePosixPath(item.name).parts[1:])
            if relative.as_posix() == ".":
                continue
            destination = target.joinpath(*relative.parts)
            if item.isdir():
                destination.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with stream.extractfile(item) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                destination.chmod(0o755 if item.mode & 0o111 else 0o644)


def run_bounded(argv: list[str], *, timeout: int = 120, log: Path | None = None,
                budget_root: Path | None = None, success_codes: tuple[int, ...] = (0,)) -> str:
    """Drain output with a fixed memory/log cap and kill timed-out process groups."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True,
                               env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
    result = bytearray()
    selector = selectors.DefaultSelector()
    os.set_blocking(process.stdout.fileno(), False)
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    next_budget_check = 0
    failure = None
    try:
        while selector.get_map() or process.poll() is None:
            if time.monotonic() >= deadline:
                failure = "command_timeout"
                break
            for key, _ in selector.select(timeout=0.2):
                block = os.read(key.fd, 65536)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                room = MAX_OUTPUT - len(result)
                result.extend(block[:max(0, room)])
                if len(block) > room:
                    failure = "command_output_limit"
                    break
            if failure:
                break
            if budget_root is not None and time.monotonic() >= next_budget_check:
                next_budget_check = time.monotonic() + 1
                used = directory_bytes(budget_root)
                if used > 2 * GIB or shutil.disk_usage(budget_root).free < 2 * GIB:
                    failure = "candidate_disk_budget"
                    break
    finally:
        # Kill leaked children even after their direct parent has exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)
        selector.close()
        process.stdout.close()
        if log is not None:
            log.write_bytes(result)
    if failure or process.returncode not in success_codes:
        raise Rejected(failure or "command_failed")
    return result.decode("utf-8", "replace")


class LinuxBackend:
    def __init__(self, config: dict):
        self.config = config
        self.base = Path(config["base"])
        self.state = Path(config["state_dir"])
        self.baseline = Release(**config["baseline"])
        self.releases = self.base / "releases"
        self.manifests = self.state / "releases"
        self.logs = self.state / "logs"
        self.backups = self.state / "backups"
        self.run = run_bounded
        self.clock, self.sleep = time.monotonic, time.sleep
        self.health_seconds = int(config.get("health_seconds", 120))
        self.stable_seconds = int(config.get("stable_seconds", 20))
        self.started_at = None
        for folder in (self.manifests, self.logs, self.backups):
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _json(self, url: str) -> dict:
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                "User-Agent": "Taki-public-main-updater", "X-GitHub-Api-Version": "2022-11-28"})
            deadline, raw = time.monotonic() + 60, bytearray()
            with urllib.request.urlopen(request, timeout=30) as response:
                while block := response.read(65536):
                    raw.extend(block)
                    if len(raw) > MAX_OUTPUT or time.monotonic() > deadline:
                        raise ValueError("response limit")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("object expected")
            return result
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise Deferred("github_unavailable") from exc

    def resolve_main(self) -> str:
        sha = self._json(API + "/git/ref/heads/main").get("object", {}).get("sha", "")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise Deferred("invalid_main_sha")
        return sha

    def check_ci(self, commit: str) -> None:
        if not SHA_RE.fullmatch(commit):
            raise Rejected("invalid_commit")
        result = self._json(API + "/actions/workflows/checks.yml/runs?branch=main&event=push&head_sha=" + commit + "&per_page=100")
        runs = [run for run in result.get("workflow_runs", []) if
                run.get("head_sha") == commit and run.get("head_branch") == "main"
                and run.get("event") == "push" and run.get("name") == "Checks"
                and run.get("path") == ".github/workflows/checks.yml"]
        if not runs:
            raise Deferred("ci_missing")
        run = max(runs, key=lambda value: (value.get("run_number", 0), value.get("run_attempt", 0)))
        if run.get("status") != "completed":
            raise Deferred("ci_pending")
        if run.get("conclusion") != "success":
            raise Rejected("ci_failed")
        run_id, attempt = run.get("id"), run.get("run_attempt")
        if type(run_id) is not int or type(attempt) is not int:
            raise Deferred("ci_invalid_run")
        jobs = self._json(API + f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
        rows = jobs.get("jobs", [])
        if jobs.get("total_count", 101) > 100:
            raise Deferred("ci_job_limit")
        found = {job.get("name"): job for job in rows}
        if not JOBS.issubset(found):
            raise Rejected("ci_required_jobs_missing")
        for name in JOBS:
            job = found[name]
            if (job.get("head_sha") != commit or job.get("run_id") != run_id
                    or job.get("status") != "completed" or job.get("conclusion") != "success"):
                raise Rejected("ci_required_job_failed")

    def _manifest(self, release: Release) -> dict:
        if release == self.baseline:
            for path in (Path(release.code_dir), Path(release.venv_dir)):
                if path.resolve() != path:
                    raise Rejected("baseline_path_not_canonical")
            if tree_digest(Path(release.code_dir)) != self.config["baseline_digest"]:
                raise Rejected("baseline_code_drift")
            return {"release": asdict(release), "digest": self.config["baseline_digest"]}
        expected = Release(release.commit, str(self.releases / release.commit / "code"),
                           str(self.releases / release.commit / "venv"))
        if release != expected:
            raise Rejected("release_path_not_allowed")
        for path in (Path(release.code_dir), Path(release.venv_dir)):
            if path.resolve() != path or path.is_symlink():
                raise Rejected("release_path_not_canonical")
        try:
            record = json.loads((self.manifests / (release.commit + ".json")).read_text())
        except (OSError, ValueError) as exc:
            raise Rejected("release_manifest_missing") from exc
        if (record.get("release") != asdict(release) or record.get("owner") != "taki-linux-updater-v1"
                or record.get("prepared") is not True):
            raise Rejected("release_manifest_mismatch")
        if record.get("digest") != tree_digest(Path(release.code_dir)):
            raise Rejected("release_code_drift")
        if record.get("environment_digest") != environment_digest(Path(release.venv_dir)):
            raise Rejected("release_environment_drift")
        return record

    def current_release(self) -> Release:
        pointer = self.base / "current"
        if not pointer.is_symlink():
            raise Rejected("current_not_symlink")
        code = str(pointer.resolve(strict=True))
        release = self.baseline if code == self.baseline.code_dir else Release(
            Path(code).parent.name, code, str(Path(code).parent / "venv"))
        self._manifest(release)
        return release

    def _sandbox(self, release: Release, argv: list[str], step: str, *, offline: bool = True):
        root = Path(release.code_dir).parent
        runtime_seconds = 1800 if step == "install" else 900
        unit = "taki-prepare-" + release.commit + "-" + step
        options = ["/usr/bin/systemd-run", "--quiet", "--wait", "--pipe", "--collect", "--unit=" + unit,
            "-p", "User=ubuntu", "-p", "Group=ubuntu", "-p", "NoNewPrivileges=yes",
            "-p", "ProtectSystem=strict", "-p", "ProtectHome=tmpfs",
            "-p", "BindPaths=" + str(root), "-p", "ReadWritePaths=" + str(root),
            "-p", "TemporaryFileSystem=/tmp:rw,size=256M /var/tmp:rw,size=64M",
            "-p", "PrivateDevices=yes", "-p", "UMask=0077",
            "-p", "SystemCallFilter=~ptrace process_vm_readv process_vm_writev",
            "-p", "MemoryMax=768M", "-p", "CPUQuota=50%", "-p", "Nice=10",
            "-p", "TasksMax=64", "-p", "RuntimeMaxSec=" + str(runtime_seconds), "-p", "KillMode=control-group",
            "-p", "WorkingDirectory=" + release.code_dir]
        if offline:
            options += ["-p", "PrivateNetwork=yes"]
        # One list assignment avoids resetting an earlier InaccessiblePaths list.
        hidden = ["/proc", "-/run/user", "-/run/dbus"] + ["-" + p for p in self.config.get("credential_paths", [])]
        options += ["-p", "InaccessiblePaths=" + " ".join(hidden)]
        env = ["/usr/bin/env", "-i", "PATH=" + release.venv_dir + "/bin:/usr/bin:/bin",
            "HOME=" + str(root / "home"), "TMPDIR=" + str(root / "tmp"), "LANG=C.UTF-8",
            "PYTHONPATH=" + release.code_dir + "/src", "PYTHONDONTWRITEBYTECODE=1",
            "QQ_APP_ID=", "QQ_APP_SECRET=", "AI_API_KEY=", "OURNOTES_UPDATE_NOTICES=0",
            "XDG_DATA_HOME=" + str(root / "test-data"), "PIP_NO_INDEX=1", "PIP_NO_INPUT=1", "PIP_DISABLE_PIP_VERSION_CHECK=1",
            "PIP_NO_CACHE_DIR=1", "OURNOTES_CACHE_FILE=" + str(root / "test-data/cache.json"),
            "OURNOTES_AI_QUOTA_FILE=" + str(root / "test-data/quota.json"),
            "OURNOTES_AI_METRICS_FILE=" + str(root / "test-data/metrics.json")]
        try:
            self.run(options + env + argv, timeout=runtime_seconds + 50, log=self.logs / (release.commit + "-" + step + ".log"), budget_root=root)
        except Exception:
            # Killing systemd-run alone does not terminate its transient service.
            try:
                self.run(["/usr/bin/systemctl", "stop", unit + ".service"], timeout=30)
            except Rejected:
                pass  # Already collected units also return nonzero.
            raise

    def _preparation_step(self, commit: str, step: str) -> None:
        """Persist the download/execution boundary under the engine's lock."""
        path = self.state / "state.json"
        state = json.loads(path.read_text())
        transaction = state.get("transaction")
        if not transaction or transaction.get("commit") != commit or transaction.get("phase") != "preparing":
            raise Rejected("preparation_transaction_changed")
        transaction["preparation_step"] = step
        state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        atomic_json(path, state, mode=0o644)

    def prepare(self, commit: str) -> Release:
        if not SHA_RE.fullmatch(commit):
            raise Rejected("invalid_commit")
        self._quiesce_preparations()
        if shutil.disk_usage(self.releases).free < 4 * GIB:
            raise Rejected("insufficient_free_disk")
        root = self.releases / commit
        if root.exists() or root.is_symlink():
            self._discard_candidate(commit)
        root.mkdir(mode=0o755)
        release = Release(commit, str(root / "code"), str(root / "venv"))
        atomic_json(self.manifests / (commit + ".json"), {"owner": "taki-linux-updater-v1", "release": asdict(release), "prepared": False})
        require_target()
        self._preparation_step(commit, "downloading")
        bundle, bundle_hash = get_bundle(self.state / "bundle-cache", commit,
                                        lambda suffix: self._json(API + suffix))
        self._preparation_step(commit, "offline_preparation")
        payload = root / "payload"
        unpack_bundle(bundle, payload, commit, bundle_hash)
        extract_archive(payload / "source.tar.gz", Path(release.code_dir), commit)
        digest = tree_digest(Path(release.code_dir))
        # Reject known-incompatible source before downloading dependencies or
        # running candidate build code. Verify again after complete preparation.
        self._check_reviewed_fingerprints(self.current_release(), release)
        for directory in ("venv", "home", "tmp", "test-data"):
            (root / directory).mkdir(mode=0o700)
        self.run(["/usr/bin/chown", "-R", "ubuntu:ubuntu", str(root)], timeout=60)
        python = str(Path(release.venv_dir) / "bin/python")
        self._sandbox(release, ["/usr/bin/python3.12", "-m", "venv", release.venv_dir], "venv", offline=True)
        self._sandbox(release, [python, "-m", "pip", "install", "--no-index",
            "--find-links", str(payload / "wheelhouse"), "--require-hashes", "--no-deps",
            "-r", str(payload / "requirements.lock")], "install", offline=True)
        self._sandbox(release, [python, "-I", "scripts/check_installed_query_upgrade.py"], "installed", offline=True)
        self._sandbox(release, [python, "-m", "pip", "check"], "dependencies", offline=True)
        self._sandbox(release, [python, "-B", "-m", "unittest", "discover", "-s", "tests", "-q"], "tests", offline=True)
        self._sandbox(release, [python, "-B", "-m", "ournotes_bot.main", "--help"], "help", offline=True)
        self._sandbox(release, [python, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation", "-w", "dist"], "wheel", offline=True)
        self._sandbox(release, [python, "-c", "from setuptools.build_meta import build_sdist; build_sdist('dist')"], "sdist", offline=True)
        self._sandbox(release, [python, "scripts/check_release_artifact.py", "dist"], "artifacts", offline=True)
        if tree_digest(Path(release.code_dir)) != digest:
            raise Rejected("candidate_source_changed")
        freeze_release(root)
        atomic_json(self.manifests / (commit + ".json"), {"owner": "taki-linux-updater-v1", "release": asdict(release),
                    "digest": digest, "environment_digest": environment_digest(Path(release.venv_dir)),
                    "bundle_sha256": bundle_hash, "prepared": True})
        return release

    def _quiesce_preparations(self) -> None:
        """An interrupted controller may leave a separately managed test unit."""
        for manifest in self.manifests.glob('*.json'):
            row = json.loads(manifest.read_text())
            if row.get('owner') != 'taki-linux-updater-v1':
                raise Rejected('preparation_manifest_not_owned')
            release = Release(**row['release'])
            root = self.releases / release.commit
            if release.code_dir != str(root / 'code') or release.venv_dir != str(root / 'venv'):
                raise Rejected('preparation_path_not_owned')
            prefix = 'taki-prepare-' + release.commit + '-'
            output = self.run(['/usr/bin/systemctl', 'list-units', '--all', '--type=service',
                               '--plain', '--no-legend', '--full', '--no-pager', prefix + '*.service'], timeout=30)
            names = [line.split()[0] for line in output.splitlines() if line.strip()]
            if any(not re.fullmatch(re.escape(prefix) + r'[a-z0-9-]+\.service', name) for name in names):
                raise Rejected('preparation_unit_not_owned')
            if names:
                self.run(['/usr/bin/systemctl', 'stop', *names], timeout=60)

    def _discard_candidate(self, commit: str) -> None:
        """Explicit retry can rebuild only our unprotected, canonical directory."""
        root = self.releases / commit
        expected = Release(commit, str(root / "code"), str(root / "venv"))
        try:
            row = json.loads((self.manifests / (commit + ".json")).read_text())
            state = json.loads((self.state / "state.json").read_text())
        except (OSError, ValueError) as exc:
            raise Rejected("candidate_already_exists") from exc
        protected = [self.baseline, self.current_release(), Release(**state["current"])]
        if state.get("previous"):
            protected.append(Release(**state["previous"]))
        if expected in protected:
            raise Rejected("candidate_is_protected")
        if (root.is_symlink() or root.resolve() != root or row.get("owner") != "taki-linux-updater-v1"
                or row.get("release") != asdict(expected)):
            raise Rejected("candidate_path_not_owned")
        shutil.rmtree(root)
        (self.manifests / (commit + ".json")).unlink()

    def verify_compatibility(self, previous: Release, candidate: Release) -> None:
        """A successful CI run cannot waive an absent hotfix/data compatibility review."""
        self._manifest(previous)
        self._manifest(candidate)
        self._check_reviewed_fingerprints(previous, candidate)
        # Baseline bytes are pinned in configuration and separately reviewed at
        # installation; never rerun its real environment during a rollback gate.
        if candidate != self.baseline:
            self._sandbox(candidate, ["/usr/bin/python3.12", self.config["compatibility_gate"],
                "--repo", candidate.code_dir, "--fixture", self.config["compatibility_fixture"]],
                "compatibility", offline=True)

    def _check_reviewed_fingerprints(self, previous: Release, candidate: Release) -> None:
        try:
            path = Path(self.config["compatibility_policy"])
            trusted(path)
            policy = json.loads(path.read_text())
        except (KeyError, OSError, ValueError) as exc:
            raise Rejected("compatibility_review_required") from exc
        paths = self.config.get("compatibility_paths", [])
        if (policy.get("version") != 2 or not paths or policy.get("critical_paths") != paths
                or len(paths) != len(set(paths))):
            raise Rejected("compatibility_review_required")
        contracts = []
        for release in (previous, candidate):
            observed = fingerprints(Path(release.code_dir), paths)
            matches = [review for review in policy.get("reviews", []) if
                       review.get("fingerprints") == observed
                       and review.get("forward_and_rollback_data_compatible") is True
                       and isinstance(review.get("evidence"), str) and review["evidence"].strip()
                       and review.get("schema_contract")]
            if len(matches) != 1:
                raise Rejected("compatibility_review_required")
            contracts.append(matches[0]["schema_contract"])
        if contracts[0] != contracts[1]:
            raise Rejected("persistence_contract_changed")
        for field in ("compatibility_gate", "compatibility_fixture"):
            artifact = Path(self.config.get(field, "/missing"))
            trusted(artifact)
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != self.config.get(field + "_sha256"):
                raise Rejected("compatibility_gate_untrusted")

    def backup(self, previous: Release, candidate: Release) -> str:
        old, new = self._manifest(previous), self._manifest(candidate)
        if shutil.disk_usage(self.backups).free < 4 * GIB:
            raise Rejected("insufficient_backup_disk")
        name = str(time.time_ns()) + "-" + previous.commit
        path = self.backups / (name + ".tar")
        # Shared data and credentials stay outside snapshots and are never restored.
        try:
            self.run(["/usr/bin/tar", "--create", "--file=" + str(path), "--exclude=.env",
                      "--exclude=" + previous.code_dir.lstrip("/") + "/data", "--exclude=__pycache__", "--directory=/",
                      previous.code_dir.lstrip("/"), previous.venv_dir.lstrip("/")], timeout=120, budget_root=self.backups)
            if path.stat().st_size > 2 * GIB:
                raise Rejected("backup_disk_budget")
            atomic_json(self.backups / (name + ".json"), {"owner": "taki-linux-updater-v1", "archive": path.name,
                         "previous": old, "candidate": new, "shared_data_restore": False})
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return str(path)

    def _service(self) -> dict:
        output = self.run(["/usr/bin/systemctl", "show", "taki.service", "--property=ActiveState,SubState,MainPID,InvocationID,NRestarts"], timeout=15)
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    def stop(self) -> None:
        self.run(["/usr/bin/systemctl", "stop", "taki.service"], timeout=90)
        state = self._service()
        if state.get("MainPID") != "0" or state.get("ActiveState") not in {"inactive", "failed"}:
            raise Rejected("service_not_stopped")
        self._snapshot_stopped_data()

    def _snapshot_stopped_data(self) -> None:
        """Capture shared data consistently after stop; rollback never restores it."""
        state_file = self.state / "state.json"
        if not state_file.exists():
            return
        transaction = json.loads(state_file.read_text()).get("transaction")
        if not transaction or transaction.get("phase") != "stopping":
            return  # Installer/recovery rollback must not depend on data backup.
        archive = Path(transaction["backup"])
        if archive.parent != self.backups or archive.suffix != ".tar" or archive.is_symlink():
            raise Rejected("backup_path_not_owned")
        manifest = archive.with_suffix(".json")
        row = json.loads(manifest.read_text())
        if row.get("owner") != "taki-linux-updater-v1" or row.get("archive") != archive.name:
            raise Rejected("backup_path_not_owned")
        if row.get("data_archive"):
            return
        data = self.base / "data"
        if data.is_symlink() or data.resolve() != data or not data.is_dir():
            raise Rejected("shared_data_path_changed")
        snapshot = archive.with_name(archive.stem + "-data.tar")
        try:
            self.run(["/usr/bin/tar", "--create", "--file=" + str(snapshot), "--directory=" + str(self.base),
                      "--exclude=.env", "data"], timeout=120, budget_root=self.backups)
            row["data_archive"] = snapshot.name
            row["shared_data_restore"] = False
            atomic_json(manifest, row)
        except Exception:
            snapshot.unlink(missing_ok=True)
            raise

    def activate(self, release: Release) -> None:
        self._manifest(release)
        if self._service().get("MainPID") != "0":
            raise Rejected("activation_requires_stopped_service")
        pointer = self.base / ".current-updater"
        if pointer.exists() or pointer.is_symlink():
            pointer.unlink()
        pointer.symlink_to(release.code_dir, target_is_directory=True)
        os.replace(pointer, self.base / "current")
        descriptor = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def start(self) -> None:
        runtime = Path("/run/taki-updater")
        runtime.mkdir(mode=0o755, exist_ok=True)
        trusted(runtime, directory=True)
        runtime.chmod(0o755)  # UMask=0077 must not hide the non-secret start permit.
        atomic_json(runtime / "allowed.json", {"release": asdict(self.current_release()),
                    "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()})
        (runtime / "allowed.json").chmod(0o644)
        self.started_at = time.time()
        self.run(["/usr/bin/systemctl", "reset-failed", "taki.service"], timeout=30)
        self.run(["/usr/bin/systemctl", "start", "taki.service"], timeout=90)

    def healthy(self, release: Release) -> bool:
        self._manifest(release)
        if self.current_release() != release:
            return False
        first = self._service()
        pid, invocation = first.get("MainPID", "0"), first.get("InvocationID", "")
        if not pid.isdecimal() or pid == "0" or not re.fullmatch(r"[0-9a-f]{32}", invocation):
            return False
        since = self.started_at if self.started_at is not None else time.time()
        deadline, ready_at = self.clock() + self.health_seconds, None
        journal = ["/usr/bin/journalctl", "--namespace=taki", "--no-pager", "--output=json",
                   "_SYSTEMD_UNIT=taki.service", "_SYSTEMD_INVOCATION_ID=" + invocation, "_PID=" + pid]
        while self.clock() < deadline:
            current = self._service()
            if (current != first or current.get("ActiveState") != "active"
                    or current.get("SubState") != "running"):
                return False
            output = self.run(journal + ["--lines=2000", "--since=@" + str(since)], timeout=15)
            ready = False
            for line in output.splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    return False
                if row.get("_SYSTEMD_INVOCATION_ID") != invocation or row.get("_PID") != pid:
                    return False
                message = row.get("MESSAGE", "")
                if not isinstance(message, str) or ERROR.search(message) or str(row.get("PRIORITY")) in {"0", "1", "2", "3"}:
                    return False
            # Ready may predate controller initialization; only errors during the
            # observed window (or the complete new start) should fail stability.
            readiness = self.run(journal + ["--lines=1", "--grep=机器人 .* 已上线"], timeout=15, success_codes=(0, 1))
            for line in readiness.splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    return False
                ready |= (row.get("_SYSTEMD_INVOCATION_ID") == invocation and row.get("_PID") == pid
                          and isinstance(row.get("MESSAGE"), str) and bool(READY.search(row["MESSAGE"])))
            if ready and ready_at is None:
                ready_at = self.clock()
            if ready_at is not None and self.clock() - ready_at >= self.stable_seconds:
                return self._service() == first
            self.sleep(2)
        return False

    def cleanup(self, protected: tuple[Release, ...]) -> None:
        self._quiesce_preparations()
        protected_paths = {release.code_dir for release in protected} | {self.baseline.code_dir}
        owned = sorted(self.manifests.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for index, manifest in enumerate(owned):
            record = json.loads(manifest.read_text())
            release = Release(**record["release"])
            root = self.releases / release.commit
            if (record.get("owner") != "taki-linux-updater-v1" or release.code_dir != str(root / "code")
                    or release.venv_dir != str(root / "venv") or root.is_symlink() or root.resolve() != root):
                raise Rejected("cleanup_path_not_owned")
            if index >= 3 and release.code_dir not in protected_paths:
                shutil.rmtree(root)
                manifest.unlink()
                for log in self.logs.glob(release.commit + "-*.log"):
                    log.unlink()
        snapshots = sorted(self.backups.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for manifest in snapshots[2:]:
            row = json.loads(manifest.read_text())
            archive = self.backups / row.get("archive", "")
            if row.get("owner") != "taki-linux-updater-v1" or archive.parent != self.backups or archive.suffix != ".tar" or archive.is_symlink():
                raise Rejected("backup_path_not_owned")
            archive.unlink(missing_ok=True)
            if row.get("data_archive"):
                data = self.backups / row["data_archive"]
                if data.parent != self.backups or data.suffix != ".tar" or data.is_symlink():
                    raise Rejected("backup_path_not_owned")
                data.unlink(missing_ok=True)
            manifest.unlink()


def recover_engine(engine: Engine) -> RunResult:
    """After abandoning preparation, restart a boot-gated known-good unit."""
    result = engine.run(recover_only=True)
    if result.status not in {"recovered", "no_transaction"}:
        return result
    try:
        with engine.lock_factory(engine.state_dir / "update.lock"):
            state = engine.load_state()
            if not state or state.get("paused") or state.get("transaction"):
                return RunResult("paused", reason="recovery_state_changed")
            release = Release(**state["current"])
            backend = engine.backend
            if backend.current_release() != release:
                return engine._pause(state, "release_drift_during_recovery")
            service = backend._service()
            if service.get("MainPID") == "0" and service.get("ActiveState") in {"inactive", "failed"}:
                try:
                    backend.start()
                    if not backend.healthy(release):
                        raise Rejected("recovered_release_unhealthy")
                except Exception as exc:
                    reason = exc.code if isinstance(exc, Rejected) else "recovery_start_failed"
                    return engine._pause(state, reason)
                return RunResult("recovered", release.commit, "known_good_restarted")
            return result
    except LockBusy:
        return RunResult("busy", reason="another_controller_holds_lock")


def validate_config(config: dict) -> None:
    """The installed controller is scoped to the already-reviewed Taki instance."""
    base, control = "/home/ubuntu/taki", "/usr/local/lib/taki-updater"
    baseline = {"commit": "5ea0f085c9863b98c0384e086460cedd98a5f662",
                "code_dir": base + "/releases/5ea0f085c9863b98c0384e086460cedd98a5f662-snapshot",
                "venv_dir": base + "/venv"}
    required = {"base": base, "state_dir": "/var/lib/taki-updater", "baseline": baseline,
                "compatibility_policy": control + "/compatibility-policy.json",
                "compatibility_gate": control + "/validate_no_fever.py",
                "compatibility_fixture": control + "/song_meta_no_fever.json",
                "credential_paths": [base + "/config", base + "/data", base + "/logs"]}
    if any(config.get(k) != v for k, v in required.items()):
        raise Rejected("controller_configuration_out_of_scope")
    for key in ("baseline_digest", "launcher_sha256", "compatibility_gate_sha256", "compatibility_fixture_sha256"):
        if not isinstance(config.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", config[key]):
            raise Rejected("controller_configuration_invalid")
    if not isinstance(config.get("compatibility_paths"), list) or not config["compatibility_paths"]:
        raise Rejected("controller_configuration_invalid")
    if (config.get("health_seconds") != 120 or config.get("stable_seconds") != 20):
        raise Rejected("controller_configuration_invalid")


def status_document(engine: Engine) -> dict:
    """Read recorded state/progress only; never resolve main or run the updater."""
    state = engine.load_state()
    if state is None:
        return {"status": "uninitialized"}
    transaction = state.get("transaction")
    target = transaction["commit"] if transaction else next(
        (event["commit"] for event in reversed(state["events"])
         if event.get("status") in {"deferred", "rejected", "updated"}
         and isinstance(event.get("commit"), str) and SHA_RE.fullmatch(event["commit"])),
        state["current"]["commit"])
    result = dict(state, observed_target=target)
    path = engine.state_dir / "bundle-cache" / (target + ".download.json")
    if path.exists() or path.is_symlink():
        trusted(path)
        if path.stat().st_size > 4096:
            raise Rejected("download_progress_invalid")
        row = json.loads(path.read_text())
        if (not isinstance(row, dict) or row.get("version") != 1 or row.get("commit") != target
                or row.get("stage") not in {"downloading", "verifying_digest", "digest_verified",
                                             "verified", "waiting_for_retry", "rejected"}
                or row.get("route") not in {"release", "asset_api", "cache"}
                or type(row.get("received_bytes")) is not int or not 0 <= row["received_bytes"] <= 128 * 1024 * 1024
                or (row.get("expected_bytes") is not None and
                    (type(row["expected_bytes"]) is not int or not 0 < row["expected_bytes"] <= 128 * 1024 * 1024))
                or not isinstance(row.get("at"), str) or len(row["at"]) > 40):
            raise Rejected("download_progress_invalid")
        result["download"] = {key: row[key] for key in
                              ("stage", "route", "received_bytes", "expected_bytes", "at")}
    return result


def human_status(state: dict) -> str:
    """Explain recorded updater state without claiming live process/QQ health."""
    if state.get("status") == "uninitialized":
        return "更新控制器尚未初始化。"
    target = state["observed_target"]
    lines = ["控制器记录的运行版本：" + state["current"]["commit"], "观察目标版本：" + target]
    transaction = state.get("transaction")
    retry = state.get("download_retries", {}).get(target)
    if state.get("paused"):
        lines.append("更新状态：已暂停（" + state["paused"] + "）")
    elif target in state["failed"]:
        lines.append("更新状态：该版本被阻止（" + state["failed"][target]["reason"] + "）")
    elif transaction:
        phases = {"preparing": "准备候选版本", "verifying": "检查兼容性", "backing_up": "备份",
                  "stopping": "停止旧进程", "activating": "切换版本", "starting": "启动新版",
                  "checking_health": "验收新版"}
        phase = "下载候选安装包" if (transaction["phase"] == "preparing" and
                                      transaction.get("preparation_step") == "downloading") else phases.get(transaction["phase"], "正在回退")
        lines.append("更新状态：" + phase)
    elif retry and target != state["current"]["commit"]:
        lines.append("更新状态：等待下次重试，旧版未切换")
    else:
        lines.append("更新状态：无活动事务，等待定时检查")
    download = state.get("download")
    if download:
        total = download["expected_bytes"]
        amount = f'{download["received_bytes"] / 1048576:.2f} MB'
        if total is not None:
            amount += f" / {total / 1048576:.2f} MB"
        stages = {"downloading": "下载中", "verifying_digest": "校验摘要中", "digest_verified": "摘要通过",
                  "verified": "安装包验证通过", "waiting_for_retry": "下载等待重试", "rejected": "下载被拒绝"}
        routes = {"release": "Release 主线路", "asset_api": "官方资产 API", "cache": "已验证缓存"}
        lines.append("目标包：" + stages[download["stage"]] + "；" + amount + "；" + routes[download["route"]])
        lines.append("下载记录时间（UTC）：" + download["at"])
    if retry and target != state["current"]["commit"]:
        next_at = datetime.fromtimestamp(retry["after"], timezone.utc).isoformat(timespec="seconds")
        lines.append("最早再次尝试（UTC）：" + next_at + "；实际由定时器触发")
    lines.append("此处为控制器记录；实际进程版本和 QQ 功能另行核验。")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Taki reviewed public-main Linux updater")
    parser.add_argument("--config", default="/etc/taki-updater.json")
    parser.add_argument("command", choices=("check", "recover", "status", "resume", "rollback"))
    parser.add_argument("--retry-commit")
    parser.add_argument("--human", action="store_true", help="Chinese read-only status (status only)")
    args = parser.parse_args(argv)
    try:
        if args.human and args.command != "status":
            raise Rejected("human_status_only")
        if os.name != "posix" or os.geteuid() != 0:
            raise Rejected("controller_requires_root")
        path = Path(args.config)
        trusted(path)
        config = json.loads(path.read_text())
        validate_config(config)
        trusted(Path(config["state_dir"]), directory=True)
        if args.command == "check":
            marker = Path("/usr/local/lib/taki-updater/installation.json")
            trusted(marker)
            if json.loads(marker.read_text()).get("phase") != "installed":
                print(json.dumps({"status": "deferred", "reason": "installation_incomplete"}))
                return 0
        if args.command == "status":
            # Loading state does not need a backend; its constructor creates
            # preparation folders, which a read-only status must not do.
            result = status_document(Engine(config["state_dir"], backend=None))
            if args.human:
                print(human_status(result))
                return 0
        else:
            engine = Engine(config["state_dir"], LinuxBackend(config))
            if args.command == "resume":
                result = asdict(engine.resume(retry_commit=args.retry_commit))
            elif args.command == "rollback":
                result = asdict(engine.rollback())
            elif args.command == "recover":
                result = asdict(recover_engine(engine))
            else:
                result = asdict(engine.run())
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        if args.command == "rollback":
            return 0 if result.get("status") == "rolled_back" and result.get("reason") == "operator_requested" else 1
        return 1 if result.get("status") in {"error", "state_error", "paused"} else 0
    except Exception as exc:
        print(json.dumps({"status": "error", "reason": exc.code if isinstance(exc, (Deferred, Rejected)) else type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""L3: Build a Linux CPython 3.12 release from committed source and hashed wheels.

Input: clean Git checkout, reviewed deploy/linux-cp312.lock; output: commit-bound ZIP.
CI proves installation with the network namespace disabled. See deploy/LINUX_OFFLINE.md.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.offline_bundle import ASSET, TARGET, digest, inspect_bundle, require_target
from deploy.linux_backend import extract_archive


def run(*args, cwd=ROOT, env=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)


def build(output: Path):
    require_target()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT):
        raise SystemExit("Build requires unchanged tracked source")
    tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", commit], cwd=ROOT, text=True).splitlines()
    if any("local-ops" in Path(name).parts or Path(name).name == ".env" for name in tracked):
        raise SystemExit("Private operational files cannot be published")
    epoch = subprocess.check_output(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=ROOT, text=True).strip()
    env = dict(os.environ, SOURCE_DATE_EPOCH=epoch, PIP_DISABLE_PIP_VERSION_CHECK="1")
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary)
        source_archive = staging / "source.tar.gz"
        run("git", "archive", "--format=tar.gz", "--prefix=taki-ournotes-bot-" + commit + "/",
            "--output=" + str(source_archive), commit)
        source = staging / "source"
        extract_archive(source_archive, source, commit)
        wheels = staging / "wheelhouse"
        wheels.mkdir()
        lock = source / "deploy/linux-cp312.lock"
        run(sys.executable, "-m", "pip", "download", "--require-hashes", "--no-deps", "--only-binary=:all:",
            "--platform", "manylinux2014_x86_64", "--platform", "manylinux_2_28_x86_64",
            "--implementation", "cp", "--python-version", "3.12", "--abi", "cp312",
            "-r", lock, "-d", wheels, env=env)
        build_env = staging / "build-env"
        run(sys.executable, "-m", "venv", build_env)
        python = build_env / "bin/python"
        run(python, "-m", "pip", "install", "--no-index", "--find-links", wheels,
            "--require-hashes", "--no-deps", "-r", lock, env=env)
        run(python, "-m", "pip", "check", env=env)
        run(python, "-m", "pip", "wheel", source, "--no-index", "--no-deps", "--no-build-isolation", "-w", wheels, env=env)
        application = list(wheels.glob("taki_ournotes_bot-*.whl"))
        if len(application) != 1:
            raise SystemExit("Expected one application wheel")
        from scripts.check_release_artifact import check
        check(application[0])
        version = application[0].name.split("-")[1]
        requirements = lock.read_text(encoding="utf-8") + "\ntaki-ournotes-bot==" + version + " --hash=sha256:" + digest(application[0]) + "\n"
        (staging / "requirements.lock").write_text(requirements, encoding="utf-8")
        files = {"source.tar.gz": source_archive, "requirements.lock": staging / "requirements.lock"}
        files.update({"wheelhouse/" + path.name: path for path in wheels.glob("*.whl")})
        manifest = {"version": 1, "commit": commit, "target": TARGET,
                    "files": {name: digest(path) for name, path in files.items()}}
        output.mkdir(parents=True, exist_ok=True)
        bundle = output / ASSET
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            contents = {name: path.read_bytes() for name, path in files.items()}
            contents["manifest.json"] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
            for name, raw in sorted(contents.items()):
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.external_attr = 0o100644 << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, raw)
        sha = digest(bundle)
        inspect_bundle(bundle, commit, sha)
        bundle.with_suffix(".sha256").write_text(sha + "\n", encoding="ascii")
        print(json.dumps({"commit": commit, "bundle": str(bundle), "sha256": sha}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output.resolve())

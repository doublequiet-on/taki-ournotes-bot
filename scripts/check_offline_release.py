"""L3: Exercise the delivered archive, installed wheel and source tests with no network.

CI invokes under unshare --net. No service, credentials or live data are needed.
"""
import argparse
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.offline_bundle import digest, require_target, unpack_bundle
from deploy.linux_backend import extract_archive


def verify(bundle: Path, commit: str):
    require_target()
    # Require an actual isolated namespace, not just pip's --no-index option.
    if {name for _, name in socket.if_nameindex()} - {"lo"}:
        raise SystemExit("Run in an isolated network namespace (unshare --net)")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        payload = root / "payload"
        unpack_bundle(bundle, payload, commit, digest(bundle))
        code = root / "code"
        extract_archive(payload / "source.tar.gz", code, commit)
        env = dict(os.environ, QQ_APP_ID="", QQ_APP_SECRET="", AI_API_KEY="",
                   PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONDONTWRITEBYTECODE="1",
                   XDG_DATA_HOME=str(root / "data"), OURNOTES_UPDATE_NOTICES="0")
        env.pop("PYTHONPATH", None)
        def run(*args, cwd=root):
            subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=True)
        venv = root / "venv"
        run(sys.executable, "-m", "venv", venv)
        python = venv / "bin/python"
        run(python, "-m", "pip", "install", "--no-index", "--find-links", payload / "wheelhouse",
            "--require-hashes", "--no-deps", "-r", payload / "requirements.lock")
        run(python, "-m", "pip", "check")
        run(python, "-I", code / "scripts/check_installed_query_upgrade.py")
        run(python, "-I", "-m", "ournotes_bot.main", "--help")
        env["PYTHONPATH"] = str(code / "src")
        run(python, "-B", "-m", "unittest", "discover", "-s", "tests", "-q", cwd=code)
        print("Offline installation, installed resources and source regression passed:", commit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("commit")
    args = parser.parse_args()
    verify(args.bundle.resolve(), args.commit)

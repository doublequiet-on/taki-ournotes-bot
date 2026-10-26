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
from deploy.offline_bundle import digest, require_target, unpack_bundle, unpack_dependency_bundle, dependency_lock
from deploy.linux_backend import extract_archive


def verify(bundle: Path, commit: str, knowledge: Path | None = None):
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
        if knowledge is not None:
            extra = root/'knowledge'
            manifest = unpack_dependency_bundle(knowledge, extra, commit, digest(knowledge))
            import tomllib
            declared = tomllib.loads((code/'pyproject.toml').read_text(encoding='utf-8'))
            assert set(declared['project']['optional-dependencies']['knowledge']) == {k+'=='+v for k,v in manifest['roots'].items()}
            base_lock = dependency_lock((payload/'requirements.lock').read_text(), required=())
            extra_lock = dependency_lock((extra/'requirements.lock').read_text())
            assert all(base_lock[k] == extra_lock[k] for k in base_lock.keys() & extra_lock.keys())
            run(python, '-m', 'pip', 'install', '--no-index', '--find-links', extra/'wheelhouse',
                '--require-hashes', '--no-deps', '-r', extra/'requirements.lock')
            run(python, '-I', '-c', 'import onnxruntime,tokenizers,numpy')
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
    parser.add_argument('--knowledge', type=Path)
    args = parser.parse_args()
    verify(args.bundle.resolve(), args.commit, args.knowledge)

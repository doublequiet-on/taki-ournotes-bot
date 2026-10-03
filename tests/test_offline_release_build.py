"""Keep installed application bytes bound to the archived Git commit."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_offline_release as builder
from scripts import check_release_artifact

COMMIT = "a" * 40


class OfflineBuildTests(unittest.TestCase):
    def test_working_tree_and_stale_build_files_cannot_enter_commit_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "checkout"
            package = checkout / "src/ournotes_bot"
            package.mkdir(parents=True)
            (package / "commands.py").write_bytes(b"committed commands")
            (package / "untracked.py").write_bytes(b"uncommitted extra module")
            stale = checkout / "build/lib/ournotes_bot"
            stale.mkdir(parents=True)
            (stale / "commands.py").write_bytes(b"stale build output")
            (checkout / "deploy").mkdir()
            (checkout / "deploy/linux-cp312.lock").write_text("dependency==1\n")
            (checkout / "query_aliases.json").write_text("{}")
            committed = {"src/ournotes_bot/commands.py": b"committed commands",
                         "deploy/linux-cp312.lock": b"dependency==1\n",
                         "query_aliases.json": b"{}"}

            def git_output(args, **kwargs):
                if args[1] == "rev-parse":
                    return COMMIT + "\n"
                if args[1] == "status":
                    return b""
                if args[1] == "ls-tree":
                    return "\n".join(committed)
                if args[1] == "show":
                    return "1700000000\n"
                self.fail("unexpected Git read")

            def run(*args, **kwargs):
                if args[:2] == ("git", "archive"):
                    archive = Path(next(a.removeprefix("--output=") for a in args if isinstance(a, str) and a.startswith("--output=")))
                    with tarfile.open(archive, "w:gz") as stream:
                        for name, raw in committed.items():
                            info = tarfile.TarInfo("taki-ournotes-bot-" + COMMIT + "/" + name)
                            info.size = len(raw)
                            stream.addfile(info, io.BytesIO(raw))
                elif args[1:4] == ("-m", "pip", "wheel"):
                    source, wheels = Path(args[4]), Path(args[-1])
                    with zipfile.ZipFile(wheels / "taki_ournotes_bot-0.1.0-py3-none-any.whl", "w") as wheel:
                        for module in (source / "src/ournotes_bot").glob("*.py"):
                            # Emulate setuptools retaining pre-existing build files.
                            old = source / "build/lib/ournotes_bot" / module.name
                            wheel.write(old if old.exists() else module, "ournotes_bot/" + module.name)
                        wheel.write(source / "query_aliases.json", "share/query_aliases.json")
                elif args[1:4] == ("-m", "pip", "download"):
                    Path(args[-1], "dependency-1-py3-none-any.whl").write_bytes(b"dependency")

            # The legacy script imported this helper without its package prefix.
            with patch.object(builder, "ROOT", checkout), patch.object(builder, "require_target"), \
                    patch.object(builder.subprocess, "check_output", side_effect=git_output), \
                    patch.object(builder, "run", side_effect=run), \
                    patch.dict("sys.modules", {"check_release_artifact": check_release_artifact}), \
                    patch.object(check_release_artifact, "check"), \
                    patch("builtins.print"):
                builder.build(root / "output")
            with zipfile.ZipFile(root / "output" / builder.ASSET) as bundle:
                manifest = json.loads(bundle.read("manifest.json"))
                self.assertEqual(manifest["commit"], COMMIT)
                with zipfile.ZipFile(io.BytesIO(bundle.read("wheelhouse/taki_ournotes_bot-0.1.0-py3-none-any.whl"))) as wheel:
                    self.assertEqual(wheel.read("ournotes_bot/commands.py"), committed["src/ournotes_bot/commands.py"])
                    self.assertNotIn("ournotes_bot/untracked.py", wheel.namelist())
                with tarfile.open(fileobj=io.BytesIO(bundle.read("source.tar.gz")), mode="r:gz") as source:
                    self.assertEqual(source.extractfile("taki-ournotes-bot-" + COMMIT + "/src/ournotes_bot/commands.py").read(),
                                     committed["src/ournotes_bot/commands.py"])

    def test_changed_tracked_source_is_rejected_before_build_or_download(self):
        with patch.object(builder, "require_target"), \
                patch.object(builder.subprocess, "check_output", side_effect=[COMMIT + "\n", b" M pyproject.toml\n"]), \
                patch.object(builder, "run") as run:
            with self.assertRaisesRegex(SystemExit, "unchanged tracked source"):
                builder.build(Path("unused"))
            run.assert_not_called()

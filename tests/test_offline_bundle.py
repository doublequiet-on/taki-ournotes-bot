"""Release integrity, download fault injection and offline preparation boundaries."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

from deploy import offline_bundle as b

COMMIT = "a" * 40


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def bundle(self, *, commit=COMMIT, target=b.TARGET, extra=None, bad_hash=False):
        files = {"source.tar.gz": b"source", "requirements.lock": b"lock",
                 "wheelhouse/taki_ournotes_bot-0.1.0-py3-none-any.whl": b"wheel"}
        files.update(extra or {})
        manifest = {"version": 1, "commit": commit, "target": target,
                    "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
        if bad_hash:
            manifest["files"]["source.tar.gz"] = "f" * 64
        path = self.root / "bundle.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
            archive.writestr("manifest.json", json.dumps(manifest))
        return path, b.digest(path)

    def test_verified_bundle_extracts_only_declared_members(self):
        path, sha = self.bundle()
        output = self.root / "out"
        b.unpack_bundle(path, output, COMMIT, sha)
        self.assertEqual((output / "source.tar.gz").read_bytes(), b"source")
        with self.assertRaises(FileExistsError):
            b.unpack_bundle(path, output, COMMIT, sha)

    def test_wrong_commit_platform_or_member_hash_is_rejected(self):
        for options in ({"commit": "b" * 40}, {"target": "windows-x64"}, {"bad_hash": True}):
            with self.subTest(options=options):
                path, sha = self.bundle(**options)
                with self.assertRaises(b.Rejected):
                    b.inspect_bundle(path, COMMIT, sha)

    def test_path_traversal_and_unexpected_members_are_rejected_before_writes(self):
        for name in ("../outside", "/tmp/outside", "wheelhouse/../../outside", ".env", "local-ops/x"):
            path, sha = self.bundle(extra={name: b"bad"})
            with self.assertRaises(b.Rejected):
                b.unpack_bundle(path, self.root / "out", COMMIT, sha)
            self.assertFalse((self.root / "out").exists())

    def test_duplicate_member_is_rejected(self):
        path, sha = self.bundle()
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("source.tar.gz", b"duplicate")
        with self.assertRaises(b.Rejected):
            b.inspect_bundle(path, COMMIT, b.digest(path))

    def test_tampering_rejected_before_unpack(self):
        path, sha = self.bundle()
        path.write_bytes(path.read_bytes() + b"tampered")
        with self.assertRaisesRegex(b.Rejected, "bundle_digest_mismatch"):
            b.unpack_bundle(path, self.root / "out", COMMIT, sha)

    def test_timeout_retries_then_atomically_caches_complete_download(self):
        path, sha = self.bundle()
        contents = path.read_bytes()
        calls, delays = [], []
        def opener(*args, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError()
            return io.BytesIO(contents)
        target = self.root / "cached.zip"
        b.download("https://example.invalid", target, sha, opener=opener, sleep=delays.append)
        self.assertEqual(b.digest(target), sha)
        self.assertEqual(delays, [2, 4])
        self.assertFalse(target.with_suffix(".part").exists())

    def test_exhausted_timeout_is_deferred_without_partial_archive(self):
        def opener(*args, **kwargs):
            raise TimeoutError()
        target = self.root / "cache.zip"
        with self.assertRaisesRegex(b.Deferred, "bundle_network_unavailable"):
            b.download("https://example.invalid", target, "f" * 64, opener=opener, sleep=lambda _: None)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix(".part").exists())

    def test_integrity_failure_and_http_forbidden_do_not_retry(self):
        for response in (io.BytesIO(b"bad"), urllib.error.HTTPError("url", 403, "denied", {}, None)):
            with patch.object(b.time, "sleep") as sleeper:
                def opener(*args, **kwargs):
                    if isinstance(response, Exception):
                        raise response
                    return response
                with self.assertRaises(b.Rejected):
                    b.download("https://example.invalid", self.root / "cache.zip", "f" * 64, opener=opener, sleep=sleeper)
                sleeper.assert_not_called()

    def test_verified_cache_works_without_network_and_rejects_later_corruption(self):
        path, sha = self.bundle()
        cache = self.root / "cache"
        cache.mkdir()
        target = cache / (COMMIT + ".zip")
        target.write_bytes(path.read_bytes())
        (cache / (COMMIT + ".sha256")).write_text(sha)
        def no_network(*args):
            self.fail("validated cache must not redownload")
        with patch.object(b.os, "name", "nt"):
            self.assertEqual(b.get_bundle(cache, COMMIT, no_network), (target, sha))
            target.write_bytes(b"corrupt")
            with self.assertRaises(b.Rejected):
                b.get_bundle(cache, COMMIT, no_network)

    def test_release_tag_and_api_digest_bind_download(self):
        path, sha = self.bundle()
        tag = "linux-" + COMMIT
        release = {"tag_name": tag, "draft": False, "assets": [{"name": b.ASSET,
            "state": "uploaded", "size": path.stat().st_size, "digest": "sha256:" + sha,
            "browser_download_url": "https://github.com/doublequiet-on/taki-ournotes-bot/releases/download/" + tag + "/" + b.ASSET}]}
        def api(suffix):
            return release if suffix.startswith("/releases/") else {"object": {"type": "commit", "sha": COMMIT}}
        def downloader(url, target, expected):
            self.assertEqual(expected, sha)
            target.write_bytes(path.read_bytes())
        with patch.object(b.os, "name", "nt"):
            target, expected = b.get_bundle(self.root / "cache", COMMIT, api, downloader=downloader)
        self.assertEqual(b.digest(target), expected)


if __name__ == "__main__":
    unittest.main()

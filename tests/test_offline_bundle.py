"""Release integrity, download fault injection and offline preparation boundaries."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import re
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

from deploy import offline_bundle as b

COMMIT = "a" * 40


class Response(io.BytesIO):
    def __init__(self, data, *, status=200, headers=None, fail_after=None, fault=TimeoutError):
        super().__init__(data)
        self.status, self.headers = status, headers or {}
        self.fail_after, self.fault = fail_after, fault

    def read1(self, count):
        if self.fail_after is not None:
            if self.tell() >= self.fail_after:
                raise self.fault()
            count = min(count, self.fail_after - self.tell())
        return self.read(count)


def range_response(contents, start, last=None, **kwargs):
    last = len(contents) - 1 if last is None else last
    return Response(contents[start:last + 1], status=206, headers={
        "Content-Range": f"bytes {start}-{last}/{len(contents)}",
        "Content-Length": str(last - start + 1)}, **kwargs)


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
            "state": "uploaded", "id": 123, "size": path.stat().st_size, "digest": "sha256:" + sha,
            "browser_download_url": "https://github.com/doublequiet-on/taki-ournotes-bot/releases/download/" + tag + "/" + b.ASSET}]}
        def api(suffix):
            return release if suffix.startswith("/releases/") else {"object": {"type": "commit", "sha": COMMIT}}
        def downloader(url, target, expected, **kwargs):
            self.assertEqual(expected, sha)
            self.assertEqual(kwargs["fallback_url"], "https://api.github.com/repos/doublequiet-on/taki-ournotes-bot/releases/assets/123?download=1")
            self.assertEqual(kwargs["size"], path.stat().st_size)
            target.write_bytes(path.read_bytes())
        with patch.object(b.os, "name", "nt"):
            target, expected = b.get_bundle(self.root / "cache", COMMIT, api, downloader=downloader)
        self.assertEqual(b.digest(target), expected)
        progress = json.loads(target.with_suffix(".download.json").read_text())
        self.assertEqual(progress["stage"], "verified")
        self.assertEqual(progress["received_bytes"], path.stat().st_size)

    def test_primary_interruption_resumes_same_bytes_through_official_fallback(self):
        contents = b"abcdefghijklmno"
        target, calls, progress = self.root / "cache.zip", [], []
        def opener(request, **kwargs):
            calls.append((request.full_url, request.get_header("Range")))
            self.assertNotIn("Authorization", dict(request.header_items()))
            if len(calls) == 1:
                return range_response(contents, 0, fail_after=4)
            return range_response(contents, 4)
        b.download("https://primary.invalid", target, hashlib.sha256(contents).hexdigest(),
                   fallback_url="https://api.invalid", size=len(contents), opener=opener,
                   sleep=lambda _: None, progress=progress.append)
        self.assertEqual(calls, [("https://primary.invalid", "bytes=0-14"), ("https://api.invalid", "bytes=4-14")])
        self.assertEqual(target.read_bytes(), contents)
        self.assertTrue(any(row["route"] == "asset_api" and row["received_bytes"] == 4 for row in progress))

    def test_partial_bytes_survive_all_attempts_and_resume_in_next_invocation(self):
        contents = b"abcdefghijklmno"
        target = self.root / "cache.zip"
        calls = []
        def broken(request, **kwargs):
            calls.append(request.get_header("Range"))
            if len(calls) == 1:
                return range_response(contents, 0, fail_after=4)
            raise TimeoutError()
        sha = hashlib.sha256(contents).hexdigest()
        with self.assertRaises(b.Deferred):
            b.download("https://example.invalid", target, sha, size=len(contents),
                       opener=broken, sleep=lambda _: None)
        self.assertEqual(target.with_suffix(".part").read_bytes(), contents[:4])
        self.assertFalse(target.exists())
        def recovered(request, **kwargs):
            self.assertEqual(request.get_header("Range"), "bytes=4-14")
            return range_response(contents, 4)
        b.download("https://example.invalid", target, sha, size=len(contents), opener=recovered)
        self.assertEqual(target.read_bytes(), contents)

    def test_killed_download_resumes_without_losing_prior_data(self):
        contents = b"abcdefghijklmno"
        target = self.root / "cache.zip"
        sha = hashlib.sha256(contents).hexdigest()
        class Killed(BaseException):
            pass
        with self.assertRaises(Killed):
            b.download("https://example.invalid", target, sha, size=len(contents),
                       opener=lambda *a, **k: range_response(contents, 0, fail_after=4, fault=Killed))
        self.assertEqual(target.with_suffix(".part").read_bytes(), contents[:4])
        b.download("https://example.invalid", target, sha, size=len(contents),
                   opener=lambda *a, **k: range_response(contents, 4))
        self.assertEqual(target.read_bytes(), contents)

    def test_server_ignoring_range_replaces_prefix_instead_of_appending(self):
        contents = b"abcdefghijklmno"
        target = self.root / "cache.zip"
        target.with_suffix(".part").write_bytes(contents[:4])
        b.download("https://example.invalid", target, hashlib.sha256(contents).hexdigest(), size=len(contents),
                   opener=lambda *a, **k: Response(contents, headers={"Content-Length": str(len(contents))}))
        self.assertEqual(target.read_bytes(), contents)

    def test_successful_ranges_do_not_use_failure_budget(self):
        contents = b"abcdefghijklmnopqrstuvwxyz"
        target, ranges = self.root / "cache.zip", []
        def opener(request, **kwargs):
            start, last = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", request.get_header("Range")).groups())
            ranges.append((start, last))
            return range_response(contents, start, last)
        with patch.object(b, "CHUNK", 4):
            b.download("https://example.invalid", target, hashlib.sha256(contents).hexdigest(),
                       size=len(contents), opener=opener)
        self.assertEqual(len(ranges), 7)
        self.assertEqual(target.read_bytes(), contents)

    def test_official_redirect_is_reused_and_expired_signature_is_renewed(self):
        contents = b"abcdefghijklmnopqrstuvwxyz"
        target, calls, progress = self.root / "cache.zip", [], []
        first = "https://release-assets.githubusercontent.com/file?signature=first"
        renewed = "https://release-assets.githubusercontent.com/file?signature=renewed"
        def opener(request, **kwargs):
            calls.append(request.full_url)
            if request.full_url == "https://primary.invalid":
                raise TimeoutError()
            if len(calls) == 4:
                raise urllib.error.HTTPError(request.full_url, 403, "expired", {}, None)
            start, last = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", request.get_header("Range")).groups())
            response = range_response(contents, start, last)
            response.geturl = lambda: first if len(calls) < 4 else renewed
            return response
        with patch.object(b, "CHUNK", 4):
            b.download("https://primary.invalid", target, hashlib.sha256(contents).hexdigest(),
                       fallback_url="https://api.invalid", size=len(contents), opener=opener,
                       sleep=lambda _: None, progress=progress.append)
        self.assertEqual(calls[:5], ["https://primary.invalid", "https://api.invalid", first, first, "https://api.invalid"])
        self.assertTrue(all(url == renewed for url in calls[5:]))
        self.assertNotIn("signature", json.dumps(progress))
        self.assertEqual(target.read_bytes(), contents)

    def test_real_permission_denial_is_not_bypassed_through_other_route(self):
        calls = []
        def opener(request, **kwargs):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 403, "denied", {}, None)
        with self.assertRaisesRegex(b.Rejected, "bundle_http_rejected"):
            b.download("https://primary.invalid", self.root / "cache.zip", "f" * 64,
                       fallback_url="https://api.invalid", opener=opener)
        self.assertEqual(calls, ["https://primary.invalid"])

    def test_invalid_range_size_and_corrupt_content_do_not_fallback(self):
        contents = b"abcdefghijklmno"
        for response in (Response(contents[4:], status=206, headers={"Content-Range": "bytes 5-15/16"}),
                         Response(contents, headers={"Content-Length": "16"}),
                         Response(b"X" * len(contents))):
            target, calls = self.root / "cache.zip", []
            target.with_suffix(".part").write_bytes(contents[:4])
            def opener(*args, **kwargs):
                calls.append(1)
                return response
            with self.assertRaises(b.Rejected):
                b.download("https://primary.invalid", target, hashlib.sha256(contents).hexdigest(),
                           size=len(contents), fallback_url="https://api.invalid", opener=opener)
            self.assertEqual(len(calls), 1)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".part").exists())

    def test_json_api_metadata_is_not_cached_as_archive(self):
        contents = b"abcdefghijklmno"
        target, calls = self.root / "cache.zip", []
        def opener(request, **kwargs):
            calls.append(request.full_url)
            if len(calls) == 1:
                return Response(b'{"url":"metadata"}', headers={"Content-Type": "application/json"})
            return range_response(contents, 0)
        b.download("https://primary.invalid", target, hashlib.sha256(contents).hexdigest(),
                   size=len(contents), fallback_url="https://api.invalid", opener=opener, sleep=lambda _: None)
        self.assertEqual(calls, ["https://primary.invalid", "https://api.invalid"])
        self.assertEqual(target.read_bytes(), contents)

    def test_rate_limit_defers_without_treating_permission_denial_as_transient(self):
        for code, headers in ((429, {}), (403, {"X-RateLimit-Remaining": "0"})):
            with self.subTest(code=code):
                def opener(*args, **kwargs):
                    raise urllib.error.HTTPError("url", code, "limited", headers, None)
                with self.assertRaises(b.Deferred), patch.object(b.time, "sleep") as sleeper:
                    b.download("https://example.invalid", self.root / "cache.zip", "f" * 64,
                               opener=opener, sleep=sleeper)
                sleeper.assert_not_called()

    def test_total_time_budget_preserves_prefix_and_exposes_waiting_state(self):
        target, progress = self.root / "cache.zip", []
        target.with_suffix(".part").write_bytes(b"abcd")
        ticks = iter((0, b.DOWNLOAD_SECONDS + 1))
        with self.assertRaises(b.Deferred), patch.object(b.urllib.request, "urlopen") as opener:
            b.download("https://example.invalid", target, "f" * 64, size=10,
                       clock=lambda: next(ticks), progress=progress.append, opener=opener)
        opener.assert_not_called()
        self.assertEqual(target.with_suffix(".part").read_bytes(), b"abcd")
        self.assertEqual(progress[-1]["stage"], "waiting_for_retry")

    def test_complete_checkpoint_is_verified_without_another_request(self):
        contents, target = b"abcdef", self.root / "cache.zip"
        target.with_suffix(".part").write_bytes(contents)
        with patch.object(b.urllib.request, "urlopen") as opener:
            b.download("https://example.invalid", target, hashlib.sha256(contents).hexdigest(),
                       size=len(contents), opener=opener)
        opener.assert_not_called()
        self.assertEqual(target.read_bytes(), contents)

    def test_symlink_checkpoint_cannot_overwrite_another_file(self):
        target = self.root / "cache.zip"
        with patch.object(Path, "is_symlink", return_value=True), patch.object(b.urllib.request, "urlopen") as opener:
            with self.assertRaisesRegex(b.Rejected, "bundle_cache_path_invalid"):
                b.download("https://example.invalid", target, "f" * 64, opener=opener)
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()

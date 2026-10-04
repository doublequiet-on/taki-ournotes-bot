"""L3: Verify commit-bound Linux bundles and download into a controller-owned cache.

Input: public GitHub release metadata, full commit, archive SHA256.
Output: verified files, resumable partial downloads and non-secret progress.
Effects: bounded public HTTPS requests and controller-owned cache writes only;
never executes candidate code. See LINUX_OFFLINE.md.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import stat
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import zipfile
from datetime import datetime, timezone

try:
    from .linux_updater import Deferred, Rejected, atomic_json
except ImportError:
    from linux_updater import Deferred, Rejected, atomic_json

TARGET = "linux-x86_64-cp312"
ASSET = "taki-" + TARGET + ".zip"
LIMIT = 128 * 1024 * 1024
EXPANDED_LIMIT = 256 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
FILE = re.compile(r"(?:source\.tar\.gz|requirements\.lock|wheelhouse/[A-Za-z0-9_.+-]+\.whl)\Z")
CHUNK = 1024 * 1024
DOWNLOAD_SECONDS = 180


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()


def require_target():
    if (sys.platform != "linux" or platform.machine() != "x86_64"
            or sys.version_info[:2] != (3, 12)):
        raise Rejected("bundle_platform_mismatch")


def inspect_bundle(path: Path, commit: str, expected: str) -> dict:
    if (not re.fullmatch(r"[0-9a-f]{40}", commit) or not SHA256.fullmatch(expected)
            or path.is_symlink() or path.stat().st_size > LIMIT or digest(path) != expected):
        raise Rejected("bundle_digest_mismatch")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if (len(entries) > 128 or len(set(names)) != len(names)
                    or sum(item.file_size for item in entries) > EXPANDED_LIMIT
                    or "manifest.json" not in names):
                raise Rejected("bundle_structure_invalid")
            for item in entries:
                mode = item.external_attr >> 16
                if (item.filename != "manifest.json" and not FILE.fullmatch(item.filename)
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                        or item.flag_bits & 1):
                    raise Rejected("bundle_structure_invalid")
            if archive.getinfo("manifest.json").file_size > 65536:
                raise Rejected("bundle_structure_invalid")
            manifest = json.loads(archive.read("manifest.json"))
            files = manifest["files"]
            if (manifest.get("version") != 1 or manifest.get("commit") != commit
                    or manifest.get("target") != TARGET or not isinstance(files, dict)
                    or set(files) != set(names) - {"manifest.json"}
                    or not {"source.tar.gz", "requirements.lock"} <= set(files)
                    or not any(name.startswith("wheelhouse/taki_ournotes_bot-") for name in files)):
                raise Rejected("bundle_manifest_mismatch")
            for name, value in files.items():
                if not isinstance(value, str) or not SHA256.fullmatch(value) or hashlib.sha256(archive.read(name)).hexdigest() != value:
                    raise Rejected("bundle_member_digest_mismatch")
            return manifest
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise Rejected("bundle_structure_invalid") from exc


def unpack_bundle(path: Path, destination: Path, commit: str, expected: str):
    manifest = inspect_bundle(path, commit, expected)
    destination.mkdir(mode=0o700)  # must be a fresh candidate directory
    with zipfile.ZipFile(path) as archive:
        for name in manifest["files"]:
            target = destination / name
            target.parent.mkdir(exist_ok=True)
            with target.open("xb") as stream:
                stream.write(archive.read(name))
    return manifest


def download(url: str, target: Path, expected: str, *, fallback_url: str | None = None,
             size: int | None = None, progress=None, sleep=time.sleep,
             opener=urllib.request.urlopen, clock=time.monotonic):
    """Resume verified-identity bytes; switch official routes on transient faults.

    A round has a fixed time/size budget and at most three transient failures.
    Only the final SHA256 makes a partial archive usable. Permission, integrity
    and invalid range responses are rejected rather than bypassed via fallback.
    """
    partial = target.with_suffix(".part")
    if (partial.is_symlink() or target.is_symlink()
            or (partial.exists() and not partial.is_file())):
        raise Rejected("bundle_cache_path_invalid")
    if (not isinstance(expected, str) or not SHA256.fullmatch(expected)
            or (size is not None and (type(size) is not int or not 0 < size <= LIMIT))):
        raise Rejected("bundle_asset_invalid")
    routes = [("release", url)] + ([("asset_api", fallback_url)] if fallback_url else [])
    redirects = {}  # Signed CDN URLs live only in memory, never in progress/logs.
    route = failures = 0
    deadline = clock() + DOWNLOAD_SECONDS

    def report(stage):
        if progress is not None:
            progress(dict(stage=stage, route=routes[route][0],
                          received_bytes=partial.stat().st_size if partial.exists() else 0,
                          expected_bytes=size))

    def finish():
        report("verifying_digest")
        if digest(partial) != expected:
            raise Rejected("bundle_digest_mismatch")
        os.replace(partial, target)
        if progress is not None:
            progress(dict(stage="digest_verified", route=routes[route][0],
                          received_bytes=target.stat().st_size, expected_bytes=size))

    try:
        while failures < 3 and clock() < deadline:
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > LIMIT or (size is not None and offset > size):
                raise Rejected("bundle_download_size_limit")
            if size is not None and offset == size:
                finish()
                return
            headers = {"User-Agent": "Taki-offline-updater", "Accept": "application/octet-stream",
                       "Accept-Encoding": "identity", "Cache-Control": "no-cache",
                       "X-GitHub-Api-Version": "2022-11-28"}
            end = min(offset + CHUNK - 1, size - 1) if size is not None else None
            if end is not None or offset:
                headers["Range"] = f"bytes={offset}-{end if end is not None else ''}"
            report("downloading")
            try:
                request = urllib.request.Request(redirects.get(route, routes[route][1]), headers=headers)
                with opener(request, timeout=min(15, max(0.1, deadline - clock()))) as source:
                    status = getattr(source, "status", 200)
                    response_headers = getattr(source, "headers", {})
                    content_type = response_headers.get("Content-Type", "").split(";", 1)[0].lower()
                    if content_type.startswith("text/") or content_type == "application/json":
                        raise urllib.error.URLError("binary response unavailable")
                    if response_headers.get("Content-Encoding", "identity") != "identity":
                        raise Rejected("bundle_response_invalid")
                    if status == 206:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response_headers.get("Content-Range", ""))
                        if not match:
                            raise Rejected("bundle_range_invalid")
                        start, last, total_size = map(int, match.groups())
                        if (start != offset or not start <= last < total_size <= LIMIT
                                or (size is not None and total_size != size)
                                or (end is not None and last > end)):
                            raise Rejected("bundle_range_invalid")
                        size, response_size, mode = total_size, last - start + 1, "ab"
                    elif status == 200:
                        # A server may ignore Range. Replace, never append, its full body.
                        offset, response_size, mode = 0, size, "wb"
                    else:
                        raise Rejected("bundle_response_invalid")
                    final_url = source.geturl() if hasattr(source, "geturl") else ""
                    final = urlsplit(final_url)
                    if (final.scheme == "https" and final.hostname in
                            {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
                            and final.username is None and final.password is None):
                        # Reuse the official redirect for later ranges, avoiding
                        # one GitHub API quota charge per megabyte.
                        redirects[route] = final_url
                    length = response_headers.get("Content-Length")
                    if length is not None:
                        if not re.fullmatch(r"[0-9]+", length):
                            raise Rejected("bundle_response_invalid")
                        length = int(length)
                        if length > LIMIT or (response_size is not None and length != response_size):
                            raise Rejected("bundle_size_mismatch")
                        response_size = length
                        if size is None:
                            size = length
                    received = 0
                    with partial.open(mode) as stream:
                        try:
                            reader = getattr(source, "read1", source.read)
                            next_report = clock()
                            while True:
                                if clock() >= deadline:
                                    raise TimeoutError()
                                chunk = reader(65536)
                                if not chunk:
                                    break
                                received += len(chunk)
                                if (offset + received > LIMIT or (size is not None and offset + received > size)
                                        or (response_size is not None and received > response_size)):
                                    raise Rejected("bundle_download_size_limit")
                                stream.write(chunk)
                                if clock() >= next_report:
                                    stream.flush()
                                    report("downloading")
                                    next_report = clock() + 5
                        finally:
                            stream.flush()
                            os.fsync(stream.fileno())
                    if response_size is not None and received != response_size:
                        raise http.client.IncompleteRead(b"")
                    if size is None or partial.stat().st_size == size:
                        finish()
                        return
                    report("downloading")
                    continue  # A complete range is progress, not a failed attempt.
            except urllib.error.HTTPError as exc:
                if exc.code == 403 and request.full_url == redirects.get(route):
                    # A short-lived CDN signature may expire. Renew through the
                    # original official route once per bounded failure budget.
                    redirects.pop(route)
                    failures += 1
                    continue
                rate_limited = exc.code == 403 and (exc.headers or {}).get("X-RateLimit-Remaining") == "0"
                if rate_limited or exc.code == 429:
                    break  # Let the timer back off, rather than hammer the public API.
                if exc.code not in (408, 500, 502, 503, 504):
                    raise Rejected("bundle_http_rejected") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError, http.client.IncompleteRead):
                pass
            failures += 1
            route = (route + 1) % len(routes)
            if failures < 3 and clock() + 2 ** failures < deadline:
                sleep(2 ** failures)
        report("waiting_for_retry")
        raise Deferred("bundle_network_unavailable")
    except Rejected:
        report("rejected")
        # No corrupt bytes are reused; the immutable digest pin stays in place.
        partial.unlink(missing_ok=True)
        raise


def get_bundle(cache: Path, commit: str, api, *, downloader=download) -> tuple[Path, str]:
    """Pin exact asset digest locally; validated cached data needs no download."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Rejected("invalid_commit")
    cache.mkdir(mode=0o700, exist_ok=True)
    if cache.is_symlink() or (os.name == "posix" and (cache.stat().st_uid != 0 or cache.stat().st_mode & 0o022)):
        raise Rejected("bundle_cache_path_invalid")
    target, pin = cache / (commit + ".zip"), cache / (commit + ".sha256")
    progress_path = cache / (commit + ".download.json")

    def report(row):
        atomic_json(progress_path, dict(version=1, commit=commit,
                    at=datetime.now(timezone.utc).isoformat(timespec="seconds"), **row), mode=0o644)
    if pin.is_symlink() or target.is_symlink():
        raise Rejected("bundle_cache_path_invalid")
    if pin.exists():
        expected = pin.read_text(encoding="ascii").strip()
        if target.exists():
            inspect_bundle(target, commit, expected)
            report(dict(stage="verified", route="cache", received_bytes=target.stat().st_size,
                        expected_bytes=target.stat().st_size))
            return target, expected
    else:
        expected = None
    tag = "linux-" + commit
    release = api("/releases/tags/" + tag)
    ref = api("/git/ref/tags/" + tag)
    if (release.get("tag_name") != tag or release.get("draft") is not False
            or ref.get("object", {}).get("type") != "commit" or ref["object"].get("sha") != commit):
        raise Rejected("bundle_release_mismatch")
    assets = [row for row in release.get("assets", []) if row.get("name") == ASSET and row.get("state") == "uploaded"]
    if len(assets) != 1:
        raise Deferred("bundle_not_published")
    asset = assets[0]
    asset_id = asset.get("id")
    value = asset.get("digest", "")
    url = "https://github.com/doublequiet-on/taki-ournotes-bot/releases/download/" + tag + "/" + ASSET
    if (not isinstance(value, str) or not value.startswith("sha256:") or not SHA256.fullmatch(value[7:])
            or type(asset_id) is not int or asset_id <= 0
            or asset.get("browser_download_url") != url or type(asset.get("size")) is not int or not 0 < asset["size"] <= LIMIT):
        raise Rejected("bundle_asset_invalid")
    if expected is not None and expected != value[7:]:
        raise Rejected("bundle_asset_changed")
    expected = value[7:]
    # A durable first-use pin also prevents replacement after interrupted downloads.
    if not pin.exists():
        with pin.open("x", encoding="ascii") as stream:
            stream.write(expected + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    fallback = "https://api.github.com/repos/doublequiet-on/taki-ournotes-bot/releases/assets/" + str(asset_id) + "?download=1"
    downloader(url, target, expected, fallback_url=fallback, size=asset["size"], progress=report)
    inspect_bundle(target, commit, expected)
    report(dict(stage="verified", route="cache", received_bytes=target.stat().st_size,
                expected_bytes=asset["size"]))
    # Retain the newest three verified downloads. Application rollback environments
    # have their own retention policy and are never touched here.
    for old in sorted(cache.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[3:]:
        if re.fullmatch(r"[0-9a-f]{40}\.zip", old.name) and old != target and not old.is_symlink():
            old.unlink()
            old.with_suffix(".sha256").unlink(missing_ok=True)
            old.with_suffix(".download.json").unlink(missing_ok=True)
            old.with_suffix(".part").unlink(missing_ok=True)
    return target, expected

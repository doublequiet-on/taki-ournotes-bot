"""L3: Verify commit-bound Linux bundles and download into a controller-owned cache.

Input: public GitHub release metadata, full commit, archive SHA256.
Output: verified files only; never executes candidate code. See LINUX_OFFLINE.md.
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
import sys
import time
import urllib.error
import urllib.request
import zipfile

try:
    from .linux_updater import Deferred, Rejected
except ImportError:
    from linux_updater import Deferred, Rejected

TARGET = "linux-x86_64-cp312"
ASSET = "taki-" + TARGET + ".zip"
LIMIT = 128 * 1024 * 1024
EXPANDED_LIMIT = 256 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
FILE = re.compile(r"(?:source\.tar\.gz|requirements\.lock|wheelhouse/[A-Za-z0-9_.+-]+\.whl)\Z")


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


def download(url: str, target: Path, expected: str, *, sleep=time.sleep, opener=urllib.request.urlopen):
    """Three bounded attempts; integrity/HTTP permission errors never retry."""
    partial = target.with_suffix(".part")
    if partial.is_symlink() or target.is_symlink():
        raise Rejected("bundle_cache_path_invalid")
    for attempt in range(3):
        partial.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Taki-offline-updater"})
            deadline = time.monotonic() + 180
            with opener(request, timeout=30) as source, partial.open("xb") as stream:
                total = 0
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > LIMIT:
                        raise Rejected("bundle_download_size_limit")
                    if time.monotonic() > deadline:
                        raise TimeoutError()
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if digest(partial) != expected:
                raise Rejected("bundle_digest_mismatch")
            os.replace(partial, target)
            return
        except urllib.error.HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise Rejected("bundle_http_rejected") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead):
            pass
        finally:
            partial.unlink(missing_ok=True)
        if attempt < 2:
            sleep(2 ** (attempt + 1))
    raise Deferred("bundle_network_unavailable")


def get_bundle(cache: Path, commit: str, api, *, downloader=download) -> tuple[Path, str]:
    """Pin exact asset digest locally; validated cached data needs no download."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Rejected("invalid_commit")
    cache.mkdir(mode=0o700, exist_ok=True)
    if cache.is_symlink() or (os.name == "posix" and (cache.stat().st_uid != 0 or cache.stat().st_mode & 0o022)):
        raise Rejected("bundle_cache_path_invalid")
    target, pin = cache / (commit + ".zip"), cache / (commit + ".sha256")
    if pin.is_symlink() or target.is_symlink():
        raise Rejected("bundle_cache_path_invalid")
    if pin.exists():
        expected = pin.read_text(encoding="ascii").strip()
        if target.exists():
            inspect_bundle(target, commit, expected)
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
    value = asset.get("digest", "")
    url = "https://github.com/doublequiet-on/taki-ournotes-bot/releases/download/" + tag + "/" + ASSET
    if (not isinstance(value, str) or not value.startswith("sha256:") or not SHA256.fullmatch(value[7:])
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
    downloader(url, target, expected)
    inspect_bundle(target, commit, expected)
    # Retain the newest three verified downloads. Application rollback environments
    # have their own retention policy and are never touched here.
    for old in sorted(cache.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[3:]:
        if re.fullmatch(r"[0-9a-f]{40}\.zip", old.name) and old != target and not old.is_symlink():
            old.unlink()
            old.with_suffix(".sha256").unlink(missing_ok=True)
    return target, expected

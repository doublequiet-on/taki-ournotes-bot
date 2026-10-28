"""Verify the R7-R9 evidence archive without executing archived scripts.

Input: published ZIP and the SHA-256 copied from the reviewed evidence index.
Output: verified member count and bytes; optional extraction into a new directory.
Effects: local reads, optional new output directory; no network or application state.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def verify(archive, expected_sha256, extract=None):
    archive = Path(archive)
    hasher = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if digest != expected_sha256:
        raise ValueError("archive SHA-256 mismatch")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive member")
        for name in names:
            parts = PurePosixPath(name).parts
            if not parts or name.startswith("/") or ".." in parts or "\\" in name or ":" in name:
                raise ValueError("unsafe archive member")
            mode = bundle.getinfo(name).external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ValueError("archive symlink")
        manifest = json.loads(bundle.read("manifest.json"))
        if manifest["schema"] != "taki-performance-evidence/1":
            raise ValueError("unknown evidence manifest")
        files = manifest["files"]
        if set(names) != {"manifest.json", *files}:
            raise ValueError("evidence member set mismatch")
        total = 0
        for name, record in files.items():
            data = bundle.read(name)
            if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("evidence content mismatch: " + name)
            total += len(data)
        if extract is not None:
            destination = Path(extract)
            destination.mkdir(parents=True, exist_ok=False)
            bundle.extractall(destination)
    return {"sha256": digest, "files": len(files), "bytes": total}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("sha256")
    parser.add_argument("--extract", type=Path, help="new, previously nonexistent directory")
    args = parser.parse_args()
    print(json.dumps(verify(args.archive, args.sha256, args.extract), indent=2))

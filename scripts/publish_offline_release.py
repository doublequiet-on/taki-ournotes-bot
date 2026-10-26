"""L3: CI-only main release publisher; never replaces an existing tag/asset.

Input: verified bundle, push SHA and workflow token. Output: public commit release.
Only called after both Python suites and the isolated Linux installation pass.
"""
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.offline_bundle import ASSET, KNOWLEDGE_ASSET, digest, inspect_bundle, inspect_dependency_bundle
from deploy.koishi_control import COMBINATION_ASSET, inspect_combination_archive

REPO = "doublequiet-on/taki-ournotes-bot"
API = "https://api.github.com/repos/" + REPO


def publish(bundle, commit, *, combination=None, knowledge=None):
    if (os.environ.get("GITHUB_EVENT_NAME") != "push" or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_REPOSITORY") != REPO or os.environ.get("GITHUB_SHA") != commit):
        raise SystemExit("Publisher requires this repository's exact main push")
    expected = digest(bundle)
    inspect_bundle(bundle, commit, expected)
    payloads = [(ASSET, bundle, expected)]
    if combination is not None:
        sha = digest(combination)
        row = inspect_combination_archive(combination, commit, sha)
        if row['python_sha256'] != expected: raise SystemExit('Combination Python bundle mismatch')
        payloads.append((COMBINATION_ASSET, combination, sha))
    if knowledge is not None:
        sha = digest(knowledge)
        inspect_dependency_bundle(knowledge, commit, sha)
        payloads.append((KNOWLEDGE_ASSET, knowledge, sha))
    token = os.environ["GH_TOKEN"]
    def api(path, body=None, *, binary=False):
        url = path if path.startswith("https://uploads.github.com/repos/" + REPO + "/") else API + path
        raw = body if binary else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(url, data=raw, headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "Content-Type": "application/zip" if binary else "application/json",
            "User-Agent": "Taki-offline-release"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and body is None:
                return None
            raise SystemExit("GitHub publication failed: HTTP " + str(exc.code)) from None
    tag = "linux-" + commit
    ref = api("/git/ref/tags/" + tag)
    if ref is None:
        ref = api("/git/refs", {"ref": "refs/tags/" + tag, "sha": commit})
    if ref.get("object") != {"type": "commit", "sha": commit, "url": API + "/git/commits/" + commit}:
        raise SystemExit("Existing tag does not point directly at the expected commit")
    release = api("/releases/tags/" + tag)
    if release is None:
        release = api("/releases", {"tag_name": tag, "target_commitish": commit,
            "name": "Linux offline " + commit[:12], "prerelease": True, "make_latest": "false",
            "body": "Linux x86_64 / CPython 3.12. Exact commit: `" + commit + "`.\n"
                    "Locked dependencies, hashes and offline installation were verified by Checks. "
                    "This asset does not install or replace the server controller."})
    if release.get("draft") or release.get("tag_name") != tag:
        raise SystemExit("Unexpected release state")
    for name, path, sha in payloads:
        assets = [row for row in release.get("assets", []) if row["name"] == name]
        if assets:
            if len(assets) != 1 or assets[0].get("digest") != "sha256:" + sha or assets[0].get("state") != "uploaded":
                raise SystemExit("Refusing to overwrite an existing asset")
            print("Identical published bundle already exists:", tag, name)
            continue
        url = "https://uploads.github.com/repos/" + REPO + "/releases/" + str(release["id"]) + "/assets?name=" + name
        asset = api(url, path.read_bytes(), binary=True)
        if asset.get("digest") != "sha256:" + sha:
            raise SystemExit("Uploaded asset digest was not confirmed")
        print("Published verified bundle:", tag, name, sha)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('commit')
    parser.add_argument('--combination', type=Path)
    parser.add_argument('--knowledge', type=Path)
    args = parser.parse_args()
    publish(args.bundle, args.commit, combination=args.combination, knowledge=args.knowledge)

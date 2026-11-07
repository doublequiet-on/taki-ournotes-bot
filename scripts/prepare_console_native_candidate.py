"""Derive candidate.4 inputs from the frozen candidate.3 source input and rebuilt native tool."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

RECIPES = Path(__file__).resolve().parents[1] / "deploy/licenses/koishi-console"
NATIVE = "node_modules/@rollup/rollup-linux-x64-gnu/rollup.linux-x64-gnu.node"
CACHE = "source/.yarn/cache/@rollup-rollup-linux-x64-gnu-npm-4.67.4-685e5ff8c1-10c0.zip"
PROVENANCE = "materials/rollup-native-rebuild.json"
VERSION = "0.1.0-candidate.4"
NPM_VERSION = "5.30.10-taki.4"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def bind(path, data):
    return {"path": path, "size": len(data), "sha256": sha(data)}


def assemble(base, native, output):
    spec = json.loads((RECIPES / "downstream-inputs-public-v2.json").read_bytes())
    base_digest = sha(base.read_bytes())
    if base.stat().st_size != spec["inputs_size"] or base_digest != spec["inputs_sha256"]:
        raise ValueError("candidate.3 source inputs differ from the fixed specification")
    tool = native.read_bytes()
    if not tool.startswith(b"\x7fELF\x02\x01"):
        raise ValueError("expected a Linux 64-bit little-endian ELF native tool")
    with zipfile.ZipFile(base) as original:
        manifest = json.loads(original.read("input-manifest.json"))
        with zipfile.ZipFile(io.BytesIO(original.read(CACHE))) as cache:
            old_tool = cache.read(NATIVE)
            if sha(old_tool) == sha(tool):
                raise ValueError("the native tool still matches the frozen upstream binary")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as patched:
                for member in cache.infolist():
                    data = tool if member.filename == NATIVE else cache.read(member)
                    patched.writestr(member, data)
        patch = (RECIPES / "rollup-xxhash-base.patch").read_bytes()
        native_recipe = (RECIPES / "rebuild-rollup-native.sh").read_bytes()
        provenance = {
            "component": "@rollup/rollup-linux-x64-gnu",
            "upstream_version": "4.67.4",
            "upstream_commit": "18b5cb60e55926f6d917cc1aeb17fd81d0da5cc6",
            "target": "x86_64-unknown-linux-gnu",
            "source_archive_sha256": "951175a04825fabbf078497a7de4ff0d318cc9727b449614a704ee1fe4556f2a",
            "cargo_lock_sha256": "4419e784a43fe1546066b7c350e3fc52c55776992558243a369a1a2fc2f3fab6",
            "patch_sha256": sha(patch),
            "native_recipe_sha256": sha(native_recipe),
            "upstream_binary_sha256": sha(old_tool),
            "installed_binary": bind(NATIVE, tool),
        }
        updated = {
            CACHE: buffer.getvalue(),
            PROVENANCE: encoded(provenance),
            "recipe/rollup-xxhash-base.patch": patch,
            "recipe/rebuild-rollup-native.sh": native_recipe,
        }
        expected = {row["path"]: row for row in manifest["files"]}
        if len(original.namelist()) != len(expected) + 1 or set(original.namelist()) != set(expected) | {"input-manifest.json"}:
            raise ValueError("base input manifest differs from archive")
        for name, data in updated.items():
            expected[name] = bind(name, data)
        manifest.update(component_version=VERSION, base_inputs_sha256=base_digest,
                        purpose="candidate.4 inputs with a downstream-built Rollup native tool",
                        files=[expected[name] for name in sorted(expected)])
        output.mkdir()
        bundle = output / "console-candidate4-build-inputs.zip"
        with zipfile.ZipFile(bundle, "x", compression=zipfile.ZIP_DEFLATED) as result:
            for name in sorted(expected):
                data = updated[name] if name in updated else original.read(name)
                if bind(name, data) != expected[name]:
                    raise ValueError(f"input member differs: {name}")
                result.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), data,
                                compress_type=zipfile.ZIP_DEFLATED)
            result.writestr("input-manifest.json", encoded(manifest))
    spec.update(version=VERSION, package_version=NPM_VERSION, inputs_file=bundle.name,
                inputs_size=bundle.stat().st_size, inputs_sha256=sha(bundle.read_bytes()),
                rollup_native=provenance)
    (output / "downstream-inputs-candidate4.json").write_bytes(encoded(spec))
    print(encoded(spec).decode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--native", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    assemble(args.base, args.native, args.output)

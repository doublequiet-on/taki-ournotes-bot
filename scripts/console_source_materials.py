"""Index a task09 Console reconstruction; never certify npm provenance or activate it.

Input: isolated upstream build, pinned npm tarball, new evidence directory.
Output: per-file comparison, module/dependency/license evidence. See RELEASE_MATERIALS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def collect(root: Path, npm: Path, output: Path):
    """Read only explicitly supplied build inputs; write to a new directory."""
    output.mkdir(parents=True, exist_ok=False)
    source = root / 'source'
    evidence = root / 'evidence'
    dependencies = []
    for cache in sorted((source / '.yarn/cache').glob('*.zip')):
        with zipfile.ZipFile(cache) as archive:
            names = archive.namelist()
            packages = [n for n in names if n.startswith('node_modules/')
                         and n.endswith('/package.json')
                         and len(n.split('/')) == (4 if n.split('/')[1].startswith('@') else 3)]
            for name in sorted(set(packages)):
                meta = json.loads(archive.read(name))
                prefix = name[:-len('package.json')]
                licenses = [n for n in names if n.startswith(prefix) and
                            any(word in Path(n).name.lower() for word in ('license', 'licence', 'notice', 'copying'))
                            and not n.endswith('/')]
                dependencies.append(dict(name=meta.get('name'), version=meta.get('version'),
                    license_declaration=meta.get('license'), cache=cache.name,
                    cache_sha256=sha(cache.read_bytes()), package_json_sha256=sha(archive.read(name)),
                    materials=[dict(path=n, sha256=sha(archive.read(n))) for n in licenses],
                    status='materials_in_cache' if licenses else 'no_license_file_found_review_required'))
    dump(output / 'dependencies.json', dependencies)

    graphs = []
    modules = {}
    for graph in sorted(evidence.glob('graph-*.json')):
        value = json.loads(graph.read_text())
        for module in value['modules']:
            raw = module.split('?', 1)[0].lstrip('\0')
            path = Path(raw)
            key = module.replace(str(source) + '/', '')
            local_file = path.resolve().is_relative_to(source.resolve()) and path.is_file()
            owner = None
            if local_file and path.is_relative_to(source):
                for parent in path.parents:
                    meta_path = parent / 'package.json'
                    if meta_path.is_file():
                        meta = json.loads(meta_path.read_text(encoding='utf-8'))
                        owner = dict(name=meta.get('name'), version=meta.get('version'),
                                     package_json=str(meta_path.relative_to(source)),
                                     license_declaration=meta.get('license'))
                        break
                    if parent == source:
                        break
            modules[key] = dict(sha256=sha(path.read_bytes()) if local_file else None,
                                kind='file' if local_file else 'virtual_or_external', owner=owner)
        # Keep generated query suffixes and virtual modules, normalize only build root.
        graphs.append(json.loads(json.dumps(value).replace(str(source) + '/', '')))
    dump(output / 'module-graphs.json', graphs)
    dump(output / 'module-hashes.json', modules)

    entries = []
    dist_inputs = {
        'client.js': ['packages/client/client/index.ts', 'packages/client/client/**', 'packages/components/client/**'],
        'element.js': ['element-plus (manual chunk)', 'transitive modules in module-graphs.json'],
        'index.js': ['packages/client/app/index.html', 'packages/client/app/**'],
        'index.html': ['packages/client/app/index.html'],
        'style.css': ['packages/client/app/**', 'packages/client/client/**', 'element-plus/theme-chalk/**', 'Sass/UnoCSS'],
        'logo.png': ['packages/client/app/assets/logo.png'],
        'vue.js': ['node_modules/vue/dist/vue.runtime.esm-browser.prod.js (copy)'],
        'vue-router.js': ['node_modules/vue-router/dist/vue-router.esm-browser.js'],
        'vueuse.js': ['node_modules/@vueuse/core/index.mjs'],
    }
    with tarfile.open(npm) as archive:
        for member in archive:
            if not member.isfile() or not member.name.startswith(('package/lib/', 'package/dist/')):
                continue
            rel = member.name[len('package/'):]
            published = archive.extractfile(member).read()
            rebuilt = evidence / 'rebuilt' / rel
            generated = rebuilt.read_bytes() if rebuilt.is_file() else None
            if rel.startswith('dist/'):
                inputs = dist_inputs[Path(rel).name]
                command = 'node --require tsx/cjs -e require(client-script).default(); see REPRODUCE.md'
            else:
                inputs = ['plugins/console/src/' + ('browser/index.ts' if '/browser/' in rel
                          else 'node/index.ts' if '/node/' in rel else 'index.ts')]
                if '/node/' in rel:
                    inputs += ['plugins/console/src/node/locales/zh-CN.yml', 'packages/console/src/** (workspace API)']
                command = 'yarn build: yakumo tsc / esbuild; see build logs'
            entries.append(dict(path=rel, npm_size=len(published), npm_sha256=sha(published),
                rebuilt_size=len(generated) if generated is not None else None,
                rebuilt_sha256=sha(generated) if generated is not None else None,
                comparison='identical' if generated == published else 'different' if generated is not None else 'not_generated',
                source_inputs=inputs, build_command=command,
                workspace_license='plugins/console, packages/client/components: AGPL-3.0; packages/console: MIT',
                dependency_evidence='module-graphs.json + module-hashes.json + dependencies.json + yarn.lock',
                historical_dependency_resolution='unknown',
                provenance_status='reconstruction_evidence_not_historical_release_attestation'))
    dump(output / 'provenance.json', entries)
    dump(output / 'summary.json', dict(npm_sha256=sha(npm.read_bytes()),
        outputs=len(entries), identical=sum(e['comparison'] == 'identical' for e in entries),
        different=sum(e['comparison'] == 'different' for e in entries),
        not_generated=sum(e['comparison'] == 'not_generated' for e in entries),
        dependency_cache_records=len(dependencies), modules=len(modules),
        source_materials_complete=False, original_npm_provenance_proven=False,
        final_product=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--npm', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    collect(args.build_root, args.npm, args.output)

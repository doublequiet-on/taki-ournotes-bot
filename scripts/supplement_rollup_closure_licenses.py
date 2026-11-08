"""Fetch bounded original texts at the exact .cargo_vcs_info commit.

Retains HTTP failures and manifest evidence. Presence of texts is not a legal
approval or a claim that generated third-party sources have been reproduced.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.error
import urllib.request
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read()
            return {'url': url, 'status': response.status, 'sha256': sha(data)}, data
    except urllib.error.HTTPError as exc:
        return {'url': url, 'status': exc.code}, None
    except (OSError, TimeoutError) as exc:
        return {'url': url, 'error': str(exc)}, None


def main(source, output, evidence_cache):
    if output.exists():
        raise ValueError('Refusing to overwrite material')
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    index = json.loads(members['index.json'])
    requests, subjects = set(), []
    for row in index['selected']:
        if row['status'] != 'missing_original_license_text':
            continue
        with tarfile.open(fileobj=io.BytesIO(members[row['archive_path']]), mode='r:gz') as crate:
            vcs = next((json.load(crate.extractfile(entry)) for entry in crate
                        if entry.name.endswith('/.cargo_vcs_info.json')), None)
        if not vcs or not row['repository'].startswith('https://github.com/'):
            row['remaining_gap'] = 'No fixed GitHub VCS binding in exact crate'
            continue
        repo = row['repository'].removesuffix('.git').removeprefix('https://github.com/')
        commit = vcs['git']['sha1']
        prefix = vcs.get('path_in_vcs', '').strip('/')
        paths = {'LICENSE', 'LICENSE-MIT', 'LICENSE-APACHE', 'Cargo.toml'}
        if prefix:
            paths |= {prefix + '/' + leaf for leaf in ('Cargo.toml', 'LICENSE', 'LICENSE-MIT', 'LICENSE-APACHE')}
        urls = {path: f'https://raw.githubusercontent.com/{repo}/{commit}/{path}' for path in paths}
        requests.update(urls.values())
        subjects.append((row, repo, commit, prefix, urls))
    results = {}
    if evidence_cache:
        with zipfile.ZipFile(evidence_cache) as previous:
            for subject in json.loads(previous.read('evidence/upstream-license-fetches.json')):
                for item in subject['fixed_upstream']['evidence']:
                    results[item['url']] = (item, previous.read(item['path']) if 'path' in item else None)
    pending = sorted(requests - set(results))
    with ThreadPoolExecutor(max_workers=8) as executor:
        results.update(zip(pending, executor.map(fetch, pending)))
    report = []
    for row, repo, commit, prefix, urls in subjects:
        evidence, texts = [], []
        for path, url in sorted(urls.items()):
            result, raw = results[url]
            result = dict(result)
            if raw is not None:
                dest = f'upstream/{repo}/{commit}/{path}'
                members[dest] = raw
                result['path'] = dest
                if not path.endswith('Cargo.toml'):
                    texts.append({'path': dest, 'sha256': sha(raw), 'source_url': url})
            evidence.append(result)
        row['fixed_upstream'] = {'repository': repo, 'commit': commit,
                                 'package_directory': prefix, 'evidence': evidence}
        row['license_materials'].extend(texts)
        if texts:
            row['status'] = 'collected_with_fixed_upstream_texts_pending_scope_review'
        report.append({'name': row['name'], 'version': row['version'],
                       'status': row['status'], 'fixed_upstream': row['fixed_upstream']})
    index['identity'] = 'rollup-4.67.4-linux-x64-glibc-remaining-closure.2'
    index['license_scope_review_required'] = True
    members['index.json'] = (json.dumps(index, indent=2) + '\n').encode()
    members['evidence/upstream-license-fetches.json'] = (json.dumps(report, indent=2) + '\n').encode()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, data in sorted(members.items()):
            archive.writestr(zipfile.ZipInfo(name, (2026, 11, 4, 0, 0, 0)), data,
                             compress_type=zipfile.ZIP_DEFLATED)
    print(json.dumps({'path': str(output), 'size': output.stat().st_size,
                      'sha256': sha(output.read_bytes()), 'subjects': len(subjects),
                      'fixed_requests': len(requests),
                      'still_missing_texts': [row['id'] for row in index['selected']
                                              if row['status'] == 'missing_original_license_text']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence-cache', type=Path)
    args = parser.parse_args()
    main(args.source, args.output, args.evidence_cache)

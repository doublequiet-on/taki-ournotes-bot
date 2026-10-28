"""Release integrity, download fault injection and offline preparation boundaries."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch
import urllib.error
import zipfile
import os
from types import SimpleNamespace
from deploy.koishi_control import inspect_combination_archive, stage_combination, COMBINATION_ASSET

from deploy import offline_bundle as b
from scripts import build_koishi_release as koishi_release

COMMIT = "a" * 40


class KoishiClientPatchTests(unittest.TestCase):
    def test_pinned_reload_fix_and_unexpected_asset_rejection(self):
        original = b'fixture:' + b'|'.join(old for old, _ in koishi_release.KOISHI_RECONNECT_PATCHES) + b'end'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'client.js'
            path.write_bytes(original)
            with patch.object(koishi_release, 'KOISHI_CLIENT_SHA256', hashlib.sha256(original).hexdigest()):
                koishi_release.patch_koishi_client(path)
                expected = original
                for old, new in koishi_release.KOISHI_RECONNECT_PATCHES:
                    expected = expected.replace(old, new, 1)
                self.assertEqual(path.read_bytes(), expected)
                with self.assertRaisesRegex(ValueError, 'differs from reviewed'):
                    koishi_release.patch_koishi_client(path)
            changed = b'unexpected asset'
            path.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, 'differs from reviewed'):
                koishi_release.patch_koishi_client(path)
            self.assertEqual(path.read_bytes(), changed)
            for old, _ in koishi_release.KOISHI_RECONNECT_PATCHES:
                for malformed in (original + old, original.replace(old, b'')):
                    path.write_bytes(malformed)
                    with patch.object(koishi_release, 'KOISHI_CLIENT_SHA256', hashlib.sha256(malformed).hexdigest()):
                        with self.assertRaisesRegex(ValueError, 'differs from reviewed'):
                            koishi_release.patch_koishi_client(path)
                    self.assertEqual(path.read_bytes(), malformed)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for reconnect behavior')
    def test_disconnect_captures_current_url_and_keeps_it_across_failed_retries(self):
        original = b'''function p2(e,t){const n=t(),o=()=>{cr.value=null;for(const a in ot)ot[a]=void 0;
setTimeout(()=>{p2(e,t).then(location.reload,()=>{})},1e3)};n.close=o;return n.promise}'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'client.js'; path.write_bytes(original)
            with patch.object(koishi_release, 'KOISHI_CLIENT_SHA256', hashlib.sha256(original).hexdigest()):
                koishi_release.patch_koishi_client(path)
            script = '''const assert=require('node:assert/strict');
(async()=>{for(const failures of [0,2]){
let location={href:'http://localhost/initial',replace(url){this.href=url;this.restored=url},reload(){throw Error('unbound reload')}};
const cr={value:1},ot={set entry(value){location.href='http://localhost/'}},queue=[],connections=[];
const setTimeout=fn=>queue.push(fn),t=()=>{const n={promise:connections.length>0&&connections.length<=failures?Promise.reject(Error('offline')):Promise.resolve()};connections.push(n);return n};
''' + path.read_text() + '''
await p2({},t);const expected='http://localhost/taki?request_id=abc#detail';location.href=expected;
for(let index=0;index<=failures;index++){connections[index].close();queue.shift()();await new Promise(setImmediate)}
assert.equal(location.restored,expected);assert.equal(connections.length,failures+2);
}})().catch(error=>{console.error(error);process.exitCode=1});'''
            subprocess.run([shutil.which('node'), '-e', script], check=True, capture_output=True, text=True)


class CombinationArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.addCleanup(self.clean)

    def clean(self):
        for p in self.root.rglob('*'): p.chmod(0o700 if p.is_dir() else 0o600)
        self.temp.cleanup()

    def bundle(self, change=None):
        files = {'bin/node':b'fixture-node', 'gateway/scripts/probe.cjs':b'fixture-probe',
                 'gateway/package-lock.json':b'fixture-lock', 'python/taki-linux-x86_64-cp312.zip':b'fixture-python'}
        row = {'schema':1, 'commit':COMMIT, 'node':{'version':'v24.19.0','platform':'linux','arch':'x64'},
               'protocol':{'major':2,'minor':0}, 'capabilities':['group_policies_v1','query_chat_contract_v1'],
               'python_artifact':'python/taki-linux-x86_64-cp312.zip',
               'files':{n:hashlib.sha256(v).hexdigest() for n,v in files.items()}}
        row['python_sha256']=row['files'][row['python_artifact']]
        row['lock_sha256']=row['files']['gateway/package-lock.json']
        if change: change(row,files)
        path = self.root/'combination.zip'
        with zipfile.ZipFile(path,'w') as z:
            for n,v in files.items(): z.writestr(n,v)
            z.writestr('combination.json',json.dumps(row))
        return path,b.digest(path),row

    def test_stage_pins_python_and_publishes_complete_directory(self):
        path,sha,row=self.bundle(); out=self.root/COMMIT
        self.assertEqual(stage_combination(path,out,COMMIT,sha,row['python_sha256']),row)
        self.assertEqual((out/'bin/node').read_bytes(),b'fixture-node')
        if os.name=='posix':
            self.assertEqual((out/'bin/node').stat().st_mode & 0o777,0o555)
            self.assertFalse((out/'combination.json').stat().st_mode & 0o222)
        with self.assertRaises(FileExistsError): stage_combination(path,out,COMMIT,sha,row['python_sha256'])
        with self.assertRaisesRegex(ValueError,'python_mismatch'):
            stage_combination(path,self.root/'wrong',COMMIT,sha,'f'*64)
        self.assertFalse((self.root/'wrong').exists())

    def test_archive_wrong_commit_protocol_inventory_hash_and_paths_rejected(self):
        cases = [lambda m,f:m.update(commit='b'*40),lambda m,f:m.update(protocol={'major':99,'minor':0}),
                 lambda m,f:f.update({'extra':b'bad'}),lambda m,f:f.update({'bin/node':b'changed'})]
        for name in ('../outside','/absolute','gateway/../outside','x\\outside','C:outside'):
            cases.append(lambda m,f,n=name:(f.update({n:b'bad'}),m['files'].update({n:hashlib.sha256(b'bad').hexdigest()})))
        for change in cases:
            path,sha,_=self.bundle(change)
            with self.assertRaises(b.Rejected): inspect_combination_archive(path,COMMIT,sha)

    def test_failed_extraction_never_publishes_partial_directory(self):
        path,sha,row=self.bundle(); out=self.root/COMMIT
        with patch('deploy.koishi_control.shutil.copyfileobj',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): stage_combination(path,out,COMMIT,sha,row['python_sha256'])
        self.assertFalse(out.exists())
        self.assertEqual(list(self.root.glob('.'+COMMIT+'-*')),[])

    def test_stage_records_ownership_before_extract_and_preserves_it_after_rename_failure(self):
        from deploy.release_retention import read_record, manifest_digest
        from deploy.linux_updater import atomic_json
        path,sha,row=self.bundle(); out=self.root/COMMIT; owners=self.root/'owners'
        calls=[]
        def save(destination, value):
            calls.append(value['phase'])
            if value['phase']=='published': raise OSError('fixture stopped after rename')
            atomic_json(destination,value)
        with patch('deploy.linux_updater.atomic_json',side_effect=save):
            with self.assertRaises(OSError):
                stage_combination(path,out,COMMIT,sha,row['python_sha256'],ownership_dir=owners)
        saved=read_record(owners/(COMMIT+'.json'))
        self.assertEqual(calls,['staging','published'])
        self.assertEqual(saved['manifest_sha256'],manifest_digest(row))
        self.assertEqual(saved['destination'],str(out))
        self.assertTrue(out.exists()); self.assertFalse(Path(saved['stage']).exists())

    def test_failed_stage_removes_only_its_own_record(self):
        path,sha,row=self.bundle(); out=self.root/COMMIT; owners=self.root/'owners'
        with patch('deploy.koishi_control.shutil.copyfileobj',side_effect=OSError('fixture full disk')):
            with self.assertRaises(OSError):
                stage_combination(path,out,COMMIT,sha,row['python_sha256'],ownership_dir=owners)
        self.assertFalse(out.exists());self.assertEqual(list(owners.iterdir()),[])

    def test_release_asset_uses_own_inspector_cache_and_first_use_pin(self):
        path,sha,row=self.bundle(); tag='linux-'+COMMIT
        release={'tag_name':tag,'draft':False,'assets':[{'name':COMBINATION_ASSET,'state':'uploaded','id':12,
            'size':path.stat().st_size,'digest':'sha256:'+sha,
            'browser_download_url':'https://github.com/doublequiet-on/taki-ournotes-bot/releases/download/'+tag+'/'+COMBINATION_ASSET}]}
        def api(suffix): return release if suffix.startswith('/releases/') else {'object':{'type':'commit','sha':COMMIT}}
        def download(url,target,expected,**kwargs): target.write_bytes(path.read_bytes())
        # Only bypass the root-owner requirement of this download fixture. Changing
        # global os.name would make pathlib construct Windows paths on Linux.
        with patch.object(b,'os',SimpleNamespace(name='nt',fsync=os.fsync)):
            cache=self.root/'cache'
            result=b.get_bundle(cache,COMMIT,api,downloader=download,asset_name=COMBINATION_ASSET,inspector=inspect_combination_archive)
            self.assertEqual(result[1],sha)
            self.assertEqual(b.get_bundle(cache,COMMIT,lambda _:self.fail('network on cache hit'),asset_name=COMBINATION_ASSET,inspector=inspect_combination_archive),result)
            result[0].unlink()
            release['assets'][0]['digest']='sha256:'+'f'*64
            with self.assertRaisesRegex(b.Rejected,'asset_changed'):
                b.get_bundle(cache,COMMIT,api,downloader=download,asset_name=COMBINATION_ASSET,inspector=inspect_combination_archive)


class PublicationTests(unittest.TestCase):
    def test_retry_preserves_existing_python_and_uploads_missing_sidecars(self):
        from scripts import publish_offline_release as p
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); bundle=root/'python.zip'; combo=root/'combo.zip'; knowledge=root/'knowledge.zip'
            for f in (bundle,combo,knowledge): f.write_bytes(f.name.encode())
            digest=b.digest(bundle); uploaded=[]
            assets=[{'name':b.ASSET,'state':'uploaded','digest':'sha256:'+digest}]
            def api(request,**kwargs):
                if '/git/ref/' in request.full_url:
                    row={'object':{'type':'commit','sha':COMMIT,'url':p.API+'/git/commits/'+COMMIT}}
                elif '/releases/tags/' in request.full_url:
                    row={'id':123,'draft':False,'tag_name':'linux-'+COMMIT,'assets':assets}
                else:
                    name=request.full_url.split('?name=')[1];uploaded.append(name)
                    row={'digest':'sha256:'+hashlib.sha256(request.data).hexdigest()}
                return io.BytesIO(json.dumps(row).encode())
            env={'GITHUB_EVENT_NAME':'push','GITHUB_REF':'refs/heads/main','GITHUB_REPOSITORY':p.REPO,'GITHUB_SHA':COMMIT,'GH_TOKEN':'fixture'}
            with patch.dict(os.environ,env),patch.object(p,'inspect_bundle'), \
                 patch.object(p,'inspect_combination_archive',return_value={'python_sha256':digest}), \
                 patch.object(p,'inspect_dependency_bundle'),patch.object(p.urllib.request,'urlopen',side_effect=api):
                p.publish(bundle,COMMIT,combination=combo,knowledge=knowledge)
                self.assertEqual(uploaded,[COMBINATION_ASSET,b.KNOWLEDGE_ASSET])
                assets[0]['digest']='sha256:'+'f'*64
                with self.assertRaisesRegex(SystemExit,'overwrite'):
                    p.publish(bundle,COMMIT,combination=combo,knowledge=knowledge)


class KnowledgeBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def bundle(self, change=None):
        roots={'onnxruntime':'1.23.2','tokenizers':'0.22.2','numpy':'2.2.6'}
        files={f'wheelhouse/{name}-{version}-py3-none-any.whl':name.encode() for name,version in roots.items()}
        lines=[name+'=='+version+' --hash=sha256:'+hashlib.sha256(name.encode()).hexdigest() for name,version in roots.items()]
        files['requirements.lock']=('\n'.join(lines)+'\n').encode()
        m={'version':1,'profile':'knowledge','commit':COMMIT,'target':b.TARGET,'roots':roots,
           'files':{n:hashlib.sha256(v).hexdigest() for n,v in files.items()}}
        if change:change(m,files)
        p=self.root/'knowledge.zip'
        with zipfile.ZipFile(p,'w') as z:
            for n,v in files.items():z.writestr(n,v)
            z.writestr('manifest.json',json.dumps(m))
        return p,b.digest(p)

    def test_commit_bound_payload_is_complete_and_extracts_without_overwrite(self):
        p,h=self.bundle();out=self.root/'payload'
        m=b.unpack_dependency_bundle(p,out,COMMIT,h)
        self.assertEqual(set(m['roots']),{'onnxruntime','tokenizers','numpy'})
        self.assertEqual(len(list((out/'wheelhouse').glob('*.whl'))),3)
        with self.assertRaises(FileExistsError):b.unpack_dependency_bundle(p,out,COMMIT,h)

    def test_wrong_commit_digest_and_missing_wheel_reject_before_extract(self):
        p,h=self.bundle()
        for commit,digest in [('b'*40,h),(COMMIT,'f'*64)]:
            with self.assertRaises(b.Rejected):b.unpack_dependency_bundle(p,self.root/'out',commit,digest)
        self.assertFalse((self.root/'out').exists())
        def missing(m,files):
            key=next(n for n in files if n.startswith('wheelhouse/'));files.pop(key);m['files'].pop(key)
        p,h=self.bundle(missing)
        with self.assertRaisesRegex(b.Rejected,'knowledge_wheel_missing'):b.inspect_dependency_bundle(p,COMMIT,h)

    def test_extra_file_traversal_or_unlocked_wheel_rejected(self):
        for name in ('../outside','.env','wheelhouse/extra-1-py3-none-any.whl'):
            def extra(m,files):files[name]=b'bad';m['files'][name]=hashlib.sha256(b'bad').hexdigest()
            p,h=self.bundle(extra)
            with self.assertRaises(b.Rejected):b.inspect_dependency_bundle(p,COMMIT,h)

    def test_member_hash_and_root_version_must_match_lock(self):
        def corrupt(m,files):files['requirements.lock']+=b'# changed\n'
        p,h=self.bundle(corrupt)
        with self.assertRaisesRegex(b.Rejected,'member_digest'):b.inspect_dependency_bundle(p,COMMIT,h)
        p,h=self.bundle(lambda m,f:m['roots'].update(numpy='99'))
        with self.assertRaisesRegex(b.Rejected,'contract_mismatch'):b.inspect_dependency_bundle(p,COMMIT,h)

    def test_lock_cannot_download_unpinned_or_alternate_sources(self):
        for text in ('numpy>=2','--index-url https://example.invalid',
                     'numpy==2 --hash=sha256:'+'f'*64+'\nnumpy==2 --hash=sha256:'+'f'*64):
            with self.assertRaises(b.Rejected):b.dependency_lock(text)


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

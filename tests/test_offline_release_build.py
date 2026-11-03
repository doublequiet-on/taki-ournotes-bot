"""Keep installed application bytes bound to the archived Git commit."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_offline_release as builder
from scripts import check_release_artifact
from scripts import product_release
from deploy.product_identity import check_identity, check_scope_binding, read_json
import copy
import hashlib

COMMIT = "a" * 40


class ProductIdentityTests(unittest.TestCase):
    def setUp(self):
        self.c = dict(contract_version=1, product='haneoka-custom', instance='synthetic-09',
                      app_id='0000', channel='custom', release_seq=2, source_commit=COMMIT,
                      capabilities=['query.rules'], components={'backend': 'b' * 64},
                      protocol={'major': 2, 'minor': 2}, schemas={'ledger': {'read': [1, 2], 'write': [2, 2]}},
                      min_controller=2, inventory_sha256='c' * 64)
        self.e = dict(contract_version=1, product='haneoka-custom', instance='synthetic-09',
                      app_id='0000', channel='custom', capability_ceiling=['query.rules'],
                      manifest_sha256='d' * 64, minimum_release_seq=2,
                      protocol={'major': 2, 'min_minor': 2, 'max_minor': 3}, schemas={'ledger': 2})
        self.o = dict(manifest_sha256='d' * 64, components={'backend': 'b' * 64}, controller=2)

    def test_identity_and_independent_authorities(self):
        self.assertEqual(check_identity(self.c, self.e, self.o)['status'], 'allow')
        cases = [
            ('c', 'product', 'taki-personal', 'identity_mismatch'),
            ('c', 'instance', 'other', 'identity_mismatch'),
            ('c', 'app_id', '0001', 'identity_mismatch'),
            ('c', 'channel', 'personal', 'identity_mismatch'),
            ('o', 'manifest_sha256', 'e' * 64, 'unapproved_manifest'),
            ('c', 'capabilities', ['chat.free'], 'capability_exceeded'),
            ('o', 'components', {'gateway': 'b' * 64}, 'component_mismatch'),
            ('o', 'components', {'backend': 'e' * 64}, 'component_mismatch'),
            ('c', 'protocol', {'major': 3, 'minor': 2}, 'protocol_incompatible'),
            ('c', 'protocol', {'major': 2, 'minor': 1}, 'protocol_incompatible'),
            ('o', 'controller', 1, 'controller_too_old'),
            ('c', 'release_seq', 1, 'rollback_denied'),
            ('e', 'schemas', {'ledger': 1}, 'schema_incompatible'),
            ('e', 'schemas', {'unknown': 2}, 'schema_incompatible'),
        ]
        for target, key, value, reason in cases:
            with self.subTest(reason=reason, key=key):
                values = copy.deepcopy({'c': self.c, 'e': self.e, 'o': self.o})
                values[target][key] = value
                self.assertEqual(check_identity(values['c'], values['e'], values['o'])['reason'], reason)
        self.e['capability_ceiling'].append('chat.proactive')
        self.c['capabilities'].append('chat.proactive')
        self.assertEqual(check_identity(self.c, self.e, self.o)['reason'], 'capability_exceeded')

    def test_missing_unknown_and_invalid_types_fail_closed(self):
        for key in self.c:
            candidate = dict(self.c)
            del candidate[key]
            self.assertEqual(check_identity(candidate, self.e, self.o)['status'], 'reject')
        for key, value in [('contract_version', True), ('contract_version', 2), ('app_id', 0),
                           ('release_seq', True), ('capabilities', [{}]), ('components', {}),
                           ('schemas', {}), ('min_controller', 0), ('extra', 1)]:
            candidate = dict(self.c, **{key: value})
            self.assertEqual(check_identity(candidate, self.e, self.o)['reason'], 'malformed_contract')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.json'
            for raw in ('{"x":1,"x":2}', '{"x":NaN}'):
                path.write_text(raw, encoding='utf-8')
                with self.assertRaises(ValueError):
                    read_json(path)

    def test_scope_binding_uses_current_external_preflight_not_saved_verdict(self):
        binding = {'contract_version': 1, 'manifest_sha256': self.e['manifest_sha256']}
        result = check_scope_binding(binding, self.c, self.e, self.o)
        self.assertEqual(result['outcome'], 'allow')
        self.assertEqual(len(result['evidence']), 64)
        self.assertEqual(check_scope_binding({**binding, 'status': 'allow'}, self.c, self.e, self.o)['outcome'], 'deny')
        self.assertEqual(check_scope_binding({**binding, 'manifest_sha256': 'e'*64}, self.c, self.e, self.o)['reason'], 'scope_binding_mismatch')
        # A legitimate old binding and its previous success cannot approve a new instance.
        self.e['instance'] = 'new-external-instance'
        rejected = check_scope_binding(binding, self.c, self.e, self.o)
        self.assertEqual(rejected['outcome'], 'deny')
        self.assertNotEqual(rejected['evidence'], result['evidence'])
        self.e['instance'] = self.c['instance']
        self.e['schemas']['ledger'] = 1
        self.assertEqual(check_scope_binding(binding, self.c, self.e, self.o)['outcome'], 'manual')


class ProductInventoryTests(unittest.TestCase):
    def test_recursive_archive_and_install_tree_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wheel = io.BytesIO()
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('package/query.py', b'query')
                archive.writestr('package/native/NOTICE', b'native license')
            tar = io.BytesIO()
            with tarfile.open(fileobj=tar, mode='w:gz') as archive:
                member = tarfile.TarInfo('wheel.whl'); member.size = len(wheel.getvalue())
                archive.addfile(member, io.BytesIO(wheel.getvalue()))
            (root / 'source.tar.gz').write_bytes(tar.getvalue())
            result = product_release.inventory(root)
            self.assertTrue(any(name.endswith('wheel.whl!package/native/NOTICE') for name in result['files']))
            self.assertTrue(any(row['parent_sha256'] for row in result['files'].values()))
            (root / 'unexpected-chat.js.map').write_text('{"sourcesContent":["chat"]}')
            self.assertNotEqual(product_release.inventory(root), result)

    def test_unsafe_members_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'payload.zip'
            for names in (['../escape'], ['C:/escape'], ['x\\escape'], ['A.py', 'a.py'], ['a', 'a/b']):
                with zipfile.ZipFile(path, 'w') as archive:
                    for name in names:
                        archive.writestr(name, b'data')
                # Windows ZipInfo normalizes backslashes; restore hostile bytes.
                if names == ['x\\escape']:
                    path.write_bytes(path.read_bytes().replace(b'x/escape', b'x\\escape'))
                with self.assertRaises(ValueError):
                    product_release.inventory(path)
            with zipfile.ZipFile(path, 'w') as archive:
                link = zipfile.ZipInfo('link'); link.create_system = 3; link.external_attr = 0o120777 << 16
                archive.writestr(link, b'elsewhere')
            with self.assertRaises(ValueError):
                product_release.inventory(path)
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('large', b'0' * 1024)
            with patch.object(product_release, 'MAX_FILE', 512):
                with self.assertRaises(ValueError):
                    product_release.inventory(path)

    def test_staging_uses_committed_blob_and_leaves_failed_output_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'selected'
            raw = b'committed query source'
            selection = {'source_commit': COMMIT, 'files': {'src/query.py': hashlib.sha256(raw).hexdigest()}}
            def git(args, **kwargs):
                return b'100644 blob ' + b'b' * 40 + b'\tsrc/query.py\0' if args[1] == 'ls-tree' else raw
            with patch.object(product_release.subprocess, 'check_output', side_effect=git):
                product_release.stage(Path(tmp), selection, output)
                self.assertEqual((output / 'src/query.py').read_bytes(), raw)
                selection['files']['src/query.py'] = 'e' * 64
                failed = Path(tmp) / 'failed'
                with self.assertRaises(ValueError):
                    product_release.stage(Path(tmp), selection, failed)
                self.assertFalse(failed.exists())


class OfflineBuildTests(unittest.TestCase):
    def test_working_tree_and_stale_build_files_cannot_enter_commit_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / "checkout"
            package = checkout / "src/ournotes_bot"
            package.mkdir(parents=True)
            (package / "commands.py").write_bytes(b"committed commands")
            (package / "untracked.py").write_bytes(b"uncommitted extra module")
            stale = checkout / "build/lib/ournotes_bot"
            stale.mkdir(parents=True)
            (stale / "commands.py").write_bytes(b"stale build output")
            (checkout / "deploy").mkdir()
            (checkout / "deploy/linux-cp312.lock").write_text("dependency==1\n")
            (checkout / "query_aliases.json").write_text("{}")
            committed = {"src/ournotes_bot/commands.py": b"committed commands",
                         "deploy/linux-cp312.lock": b"dependency==1\n",
                         "query_aliases.json": b"{}"}

            def git_output(args, **kwargs):
                if args[1] == "rev-parse":
                    return COMMIT + "\n"
                if args[1] == "status":
                    return b""
                if args[1] == "ls-tree":
                    return "\n".join(committed)
                if args[1] == "show":
                    return "1700000000\n"
                self.fail("unexpected Git read")

            def run(*args, **kwargs):
                if args[:2] == ("git", "archive"):
                    archive = Path(next(a.removeprefix("--output=") for a in args if isinstance(a, str) and a.startswith("--output=")))
                    with tarfile.open(archive, "w:gz") as stream:
                        for name, raw in committed.items():
                            info = tarfile.TarInfo("taki-ournotes-bot-" + COMMIT + "/" + name)
                            info.size = len(raw)
                            stream.addfile(info, io.BytesIO(raw))
                elif args[1:4] == ("-m", "pip", "wheel"):
                    source, wheels = Path(args[4]), Path(args[-1])
                    with zipfile.ZipFile(wheels / "taki_ournotes_bot-0.1.0-py3-none-any.whl", "w") as wheel:
                        for module in (source / "src/ournotes_bot").glob("*.py"):
                            # Emulate setuptools retaining pre-existing build files.
                            old = source / "build/lib/ournotes_bot" / module.name
                            wheel.write(old if old.exists() else module, "ournotes_bot/" + module.name)
                        wheel.write(source / "query_aliases.json", "share/query_aliases.json")
                elif args[1:4] == ("-m", "pip", "download"):
                    Path(args[-1], "dependency-1-py3-none-any.whl").write_bytes(b"dependency")

            # The legacy script imported this helper without its package prefix.
            with patch.object(builder, "ROOT", checkout), patch.object(builder, "require_target"), \
                    patch.object(builder.subprocess, "check_output", side_effect=git_output), \
                    patch.object(builder, "run", side_effect=run), \
                    patch.dict("sys.modules", {"check_release_artifact": check_release_artifact}), \
                    patch.object(check_release_artifact, "check"), \
                    patch("builtins.print"):
                builder.build(root / "output")
            with zipfile.ZipFile(root / "output" / builder.ASSET) as bundle:
                manifest = json.loads(bundle.read("manifest.json"))
                self.assertEqual(manifest["commit"], COMMIT)
                with zipfile.ZipFile(io.BytesIO(bundle.read("wheelhouse/taki_ournotes_bot-0.1.0-py3-none-any.whl"))) as wheel:
                    self.assertEqual(wheel.read("ournotes_bot/commands.py"), committed["src/ournotes_bot/commands.py"])
                    self.assertNotIn("ournotes_bot/untracked.py", wheel.namelist())
                with tarfile.open(fileobj=io.BytesIO(bundle.read("source.tar.gz")), mode="r:gz") as source:
                    self.assertEqual(source.extractfile("taki-ournotes-bot-" + COMMIT + "/src/ournotes_bot/commands.py").read(),
                                     committed["src/ournotes_bot/commands.py"])

    def test_changed_tracked_source_is_rejected_before_build_or_download(self):
        with patch.object(builder, "require_target"), \
                patch.object(builder.subprocess, "check_output", side_effect=[COMMIT + "\n", b" M pyproject.toml\n"]), \
                patch.object(builder, "run") as run:
            with self.assertRaisesRegex(SystemExit, "unchanged tracked source"):
                builder.build(Path("unused"))
            run.assert_not_called()

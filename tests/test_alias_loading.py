"""Effective alias snapshots survive invalid edits and invalidate by content."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.query import entity_lexicon as lexicon


class AliasLoadingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "aliases.json"
        self.env = patch.dict(os.environ, {"OURNOTES_ALIAS_FILE": str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, value):
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_bad_edits_keep_last_good_and_recovery_changes_version(self):
        good = {"support_card": {"照片": "7"}}
        self.write(good)
        version = lexicon.alias_version()
        for bad in ('{', '[]', '{"unknown": {}}', '{"card": []}',
                    '{"card":{"a":"1","a":"2"}}',
                    '{"card":{"A":"1","a":"2"}}',
                    '{"card":{"x":"-1"}}', '{"card":{"x":null}}'):
            with self.subTest(bad=bad):
                self.path.write_text(bad, encoding="utf-8")
                self.assertEqual(lexicon._aliases(), good)
                self.assertEqual(lexicon.alias_version(), version)
                self.assertEqual(lexicon.alias_status(), "stale")
        self.write({"support_card": {"照片": "8"}})
        self.assertNotEqual(lexicon.alias_version(), version)
        self.assertEqual(lexicon.alias_status(), "current")

    def test_same_mtime_content_and_path_switch(self):
        self.write({"card": {"a": "1"}})
        before = lexicon.alias_version()
        stat = self.path.stat()
        self.write({"card": {"a": "2"}})
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertNotEqual(lexicon.alias_version(), before)
        other = self.path.with_name("other.json")
        with patch.dict(os.environ, {"OURNOTES_ALIAS_FILE": str(other)}):
            self.assertEqual(lexicon._aliases(), {})
            self.assertEqual(lexicon.alias_status(), "unavailable")
            self.assertNotEqual(lexicon.alias_version(), before)
        self.assertEqual(lexicon._aliases(), {"card": {"a": "2"}})

    def test_deleted_unreadable_and_same_content_recovery(self):
        good = {"song": {"a": ["100001", 100002]}}
        self.write(good)
        version = lexicon.alias_version()
        self.path.unlink()
        self.assertEqual(lexicon._aliases(), good)
        with patch.object(Path, "read_bytes", side_effect=PermissionError):
            self.assertEqual(lexicon.alias_status(), "stale")
        self.write(good)
        self.assertEqual(lexicon.alias_version(), version)
        self.assertEqual(lexicon.alias_status(), "current")

    def test_cold_invalid_and_concurrent_reads(self):
        self.path.write_bytes(b"\xff")
        self.assertEqual(lexicon.alias_status(), "unavailable")
        self.write({"song": {"a": "100001"}, "card": {"a": "1"}})
        with ThreadPoolExecutor(max_workers=8) as pool:
            versions = list(pool.map(lambda _: lexicon.alias_version(), range(40)))
        self.assertEqual(len(set(versions)), 1)
        self.assertEqual(lexicon.alias_status(), "current")

    def test_query_scope_retains_content_version_and_status_then_reads_new_file(self):
        old = {'card': {'needle': '1'}}
        new = {'card': {'needle': '26'}}
        self.write(old)
        version = lexicon.alias_version()
        with self.assertRaisesRegex(RuntimeError, 'leave scope'):
            with lexicon.alias_scope():
                self.write(new)
                with lexicon.alias_scope():
                    self.assertEqual(lexicon._aliases(), old)
                    self.assertEqual(lexicon.alias_version(), version)
                # An independent worker captures the latest content.
                with ThreadPoolExecutor(max_workers=1) as pool:
                    current = pool.submit(lexicon.alias_version).result()
                self.assertNotEqual(current, version)
                self.path.unlink()
                self.assertEqual(lexicon.alias_status(), 'current')
                raise RuntimeError('leave scope')
        self.assertEqual(lexicon._aliases(), new)
        self.assertEqual(lexicon.alias_version(), current)
        self.assertEqual(lexicon.alias_status(), 'stale')

    def test_disabled_query_scope_preserves_legacy_file_observation(self):
        self.write({'card': {'needle': '1'}})
        with lexicon.alias_scope(enabled=False):
            before = lexicon.alias_version()
            self.write({'card': {'needle': '26'}})
            self.assertNotEqual(lexicon.alias_version(), before)

"""Preparation verifies persistent assets without starting QQ."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ournotes_bot.sources.errors import ChartDataError
from ournotes_bot.sources.haneoka import game_assets


class NoteAssetPreparationTests(unittest.TestCase):
    def setUp(self):
        self.payloads = {'skin001.json': b'{"notes":{}}', 'skin001.png': b'fixed-test-atlas'}
        self.manifest = {name: {'url': 'https://example.invalid/' + name,
                               'sha256': hashlib.sha256(raw).hexdigest()}
                         for name, raw in self.payloads.items()}
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        manifest_patch = patch.dict(game_assets.MANIFEST, self.manifest, clear=True)
        manifest_patch.start()
        self.addCleanup(manifest_patch.stop)

    def filename(self, name):
        return self.manifest[name]['sha256'] + Path(name).suffix

    def test_offline_import_and_repeated_preparation_use_valid_local_bytes(self):
        source, cache = self.root / 'source', self.root / 'cache'
        source.mkdir()
        for name, raw in self.payloads.items():
            (source / self.filename(name)).write_bytes(raw)
        with patch.object(game_assets, '_fetch', side_effect=AssertionError('network attempted')):
            paths = game_assets.prepare_note_assets(cache, source)
            self.assertEqual([p.read_bytes() for p in paths], list(self.payloads.values()))
            for path in source.iterdir():
                path.unlink()
            self.assertEqual(game_assets.prepare_note_assets(cache), paths)
        self.assertEqual(sorted(p.name for p in cache.iterdir()), sorted(self.filename(n) for n in self.payloads))

    def test_wrong_or_missing_import_fails_without_network_or_installing_wrong_bytes(self):
        source, cache = self.root / 'source', self.root / 'cache'
        source.mkdir()
        for name in self.payloads:
            (source / self.filename(name)).write_bytes(b'wrong')
        with patch.object(game_assets, '_fetch', side_effect=AssertionError('network attempted')):
            with self.assertRaises(ChartDataError):
                game_assets.prepare_note_assets(cache, source)
            self.assertFalse(cache.exists())
            (source / self.filename('skin001.json')).unlink()
            with self.assertRaises(OSError):
                game_assets.prepare_note_assets(cache, source)

    def test_preparation_budget_is_separate_and_wrong_hash_cannot_replace_cache(self):
        cache = self.root / 'cache'
        with patch.object(game_assets, '_fetch', side_effect=self.payloads.values()) as fetch:
            paths = game_assets.prepare_note_assets(cache)
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(call.kwargs == {'timeout': 180} for call in fetch.call_args_list))
        paths[0].write_bytes(b'corrupt previous cache')
        with patch.object(game_assets, '_fetch', return_value=b'wrong'):
            with self.assertRaises(ChartDataError):
                game_assets.prepare_note_assets(cache)
        self.assertEqual(paths[0].read_bytes(), b'corrupt previous cache')

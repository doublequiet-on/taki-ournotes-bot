"""Preparation verifies persistent assets without starting QQ."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ournotes_bot.sources.errors import ChartDataError
from ournotes_bot.sources.haneoka import game_assets


class NoteAssetPreparationTests(unittest.TestCase):
    def test_partial_download_retains_failure_and_retry_only_repairs_that_snapshot(self):
        import io,json
        from types import SimpleNamespace
        from PIL import Image
        from ournotes_bot.application.operations_sources import SourceOperations
        from ournotes_bot.sources.haneoka import prepare_catalog_assets as artwork
        saved=json.loads((Path(__file__).parent/'fixtures/haneoka_catalog.json').read_text('utf-8'))['snapshot']
        versions={k:saved['identities'][s]['releaseId'] for k,s in (('data_version','jp'),('text_version','intl'))}
        repository=SimpleNamespace(capture_catalog=lambda:SimpleNamespace(metadata=versions),capture_source_snapshot=lambda:saved)
        ops=SourceOperations(SimpleNamespace(repository=repository))
        url='https://haneoka.org/assets/jp/Assets/AddressableResources/Image/Jacket/retry.png?release='+versions['data_version']
        stream=io.BytesIO();Image.new('RGB',(8,8),'blue').save(stream,format='PNG')
        with patch('ournotes_bot.application.operations_sources.runtime_data_dir',return_value=self.root),patch.object(artwork,'catalog_urls',return_value=(url,)),patch.dict(game_assets.MANIFEST,{},clear=True):
            with patch.object(artwork,'download_asset',side_effect=ChartDataError('offline')):
                report=ops.assets({'snapshot':versions},repair=True)
            self.assertEqual(report['_state'],'partial');self.assertEqual(report['failed'],1)
            with patch.object(artwork,'download_asset',return_value=stream.getvalue()) as download:
                report=ops.assets({'snapshot':versions},repair=True,retry=True)
            self.assertEqual(report['repaired'],1);self.assertEqual(download.call_count,1)

    def test_verified_local_assets_clear_negative_cache_without_network_or_warm_eviction(self):
        import time
        from types import SimpleNamespace
        from ournotes_bot.application.operations_sources import SourceOperations
        from ournotes_bot.sources.haneoka import prepare_catalog_assets as artwork
        cache = self.root/'game-assets'
        cache.mkdir()
        for name, raw in self.payloads.items():
            (cache/self.filename(name)).write_bytes(raw)
        ops = SourceOperations(SimpleNamespace(repository=None))
        warm = self.payloads['skin001.json']
        with patch.object(ops, 'snapshot', return_value={'identities':{}}), \
             patch('ournotes_bot.application.operations_sources.runtime_data_dir', return_value=self.root), \
             patch.object(artwork, 'catalog_urls', return_value=()), \
             patch.object(game_assets, '_fetch', side_effect=AssertionError('network')), \
             patch.dict(game_assets._MEMORY, {'skin001.json':warm}, clear=True), \
             patch.dict(game_assets._FAILED, {'skin001.png':time.monotonic()+100}, clear=True):
            report = ops.assets({'snapshot':{}}, repair=True)
            self.assertEqual(report['failed'], 0)
            self.assertEqual(report['repaired'], 0)
            self.assertNotIn('skin001.png', game_assets._FAILED)
            self.assertIs(game_assets._MEMORY['skin001.json'], warm)
            self.assertEqual(game_assets.asset_bytes('skin001.png', cache), self.payloads['skin001.png'])

    def test_runtime_artwork_repair_clears_negative_cache_and_renders(self):
        self.check_runtime_artwork_repair(reuse=False)

    def test_runtime_artwork_reuse_clears_negative_cache_and_renders(self):
        self.check_runtime_artwork_repair(reuse=True)

    def check_runtime_artwork_repair(self, *, reuse):
        import io,json,time
        from types import SimpleNamespace
        from PIL import Image
        from ournotes_bot.application.operations_sources import SourceOperations
        from ournotes_bot.sources.haneoka import prepare_catalog_assets as artwork
        from ournotes_bot import visuals
        saved=json.loads((Path(__file__).parent/'fixtures/haneoka_catalog.json').read_text('utf-8'))['snapshot']
        versions={'data_version':saved['identities']['jp']['releaseId'],'text_version':saved['identities']['intl']['releaseId']}
        repository=SimpleNamespace(capture_catalog=lambda:SimpleNamespace(metadata=versions),capture_source_snapshot=lambda:saved)
        ops=SourceOperations(SimpleNamespace(repository=repository))
        url='https://haneoka.org/assets/jp/Assets/AddressableResources/Image/Jacket/example.png?release='+versions['data_version']
        image=Image.new('RGB',(16,16),'purple');stream=io.BytesIO();image.save(stream,format='PNG');raw=stream.getvalue()
        cache=self.root/'haneoka-asset-cache';cache.mkdir()
        (cache/(hashlib.sha256(url.encode()).hexdigest()+'.png')).write_bytes(raw if reuse else b'broken')
        visuals._NATIVE_ASSET_FAILED[url]=time.monotonic()+100
        with patch('ournotes_bot.application.operations_sources.runtime_data_dir',return_value=self.root),patch.object(artwork,'catalog_urls',return_value=(url,)),patch.object(artwork,'download_asset',side_effect=AssertionError('network') if reuse else None,return_value=raw),patch.dict(game_assets.MANIFEST,{},clear=True):
            report=ops.assets({'snapshot':versions},repair=True)
        self.assertEqual(report['repaired'],0 if reuse else 1);self.assertEqual(report['failed'],0)
        self.assertEqual(report['reused'],1 if reuse else 0)
        self.assertNotIn(url,visuals._NATIVE_ASSET_FAILED)
        with patch.object(visuals,'runtime_data_dir',return_value=self.root),patch.object(artwork,'download_asset',side_effect=AssertionError('network')):
            rendered=visuals.load_visual_asset(url,(8,8))
        self.assertEqual(rendered.size,(8,8));rendered.close();visuals.invalidate_visual_assets((url,))

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
        game_assets.MANIFEST['skin001.png']['prepare_url']='https://api.github.com/fixed-blob'
        with patch.object(game_assets, '_fetch', side_effect=self.payloads.values()) as fetch:
            paths = game_assets.prepare_note_assets(cache)
        self.assertEqual(len(paths), 2)
        self.assertEqual(fetch.call_args_list[1].args[0], 'https://api.github.com/fixed-blob')
        self.assertTrue(all(call.kwargs == {'timeout': 180} for call in fetch.call_args_list))
        paths[0].write_bytes(b'corrupt previous cache')
        with patch.object(game_assets, '_fetch', return_value=b'wrong'):
            with self.assertRaises(ChartDataError):
                game_assets.prepare_note_assets(cache)
        self.assertEqual(paths[0].read_bytes(), b'corrupt previous cache')

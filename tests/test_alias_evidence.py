"""Audit reviewed aliases against a versioned public identity snapshot, offline."""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from ournotes_bot.data import Song, SongRepository, normalize
from ournotes_bot.query.entity_lexicon import resolve_entity, known_alias_names, _validate_aliases, _resolve_alias_target
from ournotes_bot.commands import handle_command, resolve_command
from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.config import Settings
from ournotes_bot.sources.haneoka.catalog import validate

ROOT = Path(__file__).resolve().parents[1]

class AliasEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.env = patch.dict(os.environ, {'OURNOTES_ALIAS_FILE': str(ROOT / 'query_aliases.json')})
        self.env.start(); self.addCleanup(self.env.stop)
        self.repo = SongRepository('', Path(temp.name) / 'cache.json')
        saved = json.loads((ROOT / 'tests/fixtures/haneoka_catalog.json').read_bytes())['snapshot']
        self.repo.songs, self.repo.cards, self.repo.support_cards = validate(saved, 1100)[0]
        # Synthetic cards bind all 25 real roster identities; no claims about owned/live cards.
        template = self.repo.cards[0]
        self.repo.cards = [replace(template, id=10000+int(k), title='身份夹具'+k,
            character=r['characterName'][0].replace(' ', ''),
            localized={'character': {'ja':r['characterName'][0], 'en':r['englishName'][0]}})
            for k,r in saved['documents']['jp']['characters'].items()]
        songs = json.loads((ROOT / 'tests/fixtures/alias_song_catalog.json').read_bytes())['songs']
        self.repo.songs = [Song(r['id'],r['title'],(r['title'],),r['band'],'','','','','',()) for r in songs]
        self.entries = json.loads((ROOT / 'docs/research/alias-evidence.json').read_bytes())['entries']
        self.words = json.loads((ROOT / 'query_aliases.json').read_bytes())
        self.parser = AIQueryParser(Settings('', '', '', self.repo.cache_file, 6, 'fixture',
            ai_quota_file=Path(temp.name)/'quota.json', ai_metrics_file=Path(temp.name)/'metrics.json'))

    def test_evidence_runtime_bijection_and_grounding(self):
        _validate_aliases(self.words)
        tracked = {(e['kind'],e['alias']):e['target'] for e in self.entries if e['status'] in ('accepted','legacy_pending')}
        runtime = {(k,a):t for k,v in self.words.items() if isinstance(v,dict) for a,t in v.items()}
        self.assertEqual(tracked,runtime)
        for e in self.entries:
            with self.subTest(alias=e['alias']):
                if e['status']=='accepted':
                    self.assertGreaterEqual(len(set(e['sources'])), 2 if e['evidence_kind']=='community' else 1)
                    self.assertTrue(all(u.startswith('https://') for u in e['sources']))
                    self.assertTrue(e['accessed'] and e['reason'])
                    self.assertIsNotNone(_resolve_alias_target(e['kind'],e['target'],self.repo))
                if e['status']=='automatic':
                    self.assertIsNotNone(resolve_entity('card',e['alias'],self.repo))

    def test_each_added_alias_direct_local_and_discovery(self):
        with patch.object(self.parser,'_request',side_effect=AssertionError('model called')), patch.object(self.parser._quota,'reserve',side_effect=AssertionError('quota used')):
            for e in self.entries:
                if e['status']!='accepted': continue
                a=e['alias']; song=e['kind'] in ('song','band')
                with self.subTest(alias=a):
                    self.assertIn(a,known_alias_names(self.repo))
                    self.assertIn(a,handle_command('/查缩写 '+a,self.repo))
                    direct=resolve_command(('/查曲 ' if song else '/查卡 ')+a,self.repo)
                    answer=self.parser.answer_with_outcome('/问 '+a+('的歌曲有哪些' if song else '的成员卡有哪些'),self.repo)
                    self.assertEqual(answer.code.value,'success',answer.text)
                    self.assertEqual({x.id for x in (direct.songs if song else direct.cards)}, {x.id for x in (answer.result.songs if song else answer.result.cards)})

    def test_song_versions_and_missing_targets(self):
        self.assertNotIn('春日影',self.words['song'])
        self.assertNotEqual(resolve_entity('song','100007',self.repo),resolve_entity('song','100091',self.repo))
        self.repo.songs=[]
        self.assertIsNone(resolve_entity('song','まよいうた',self.repo))

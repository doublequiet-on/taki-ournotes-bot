"""Short band names and standalone Han character selectors."""
import unittest
from unittest.mock import patch
from dataclasses import replace
import test_alias_evidence as fixture
from ournotes_bot.commands import resolve_command
from ournotes_bot.query.entity_lexicon import find_anchor, resolve_entity, resolve_character_fragment, CharacterAmbiguity
from ournotes_bot.query.character_query import execute_character
from test_info_queries import person, catalog, Repository


class ShortNameTests(unittest.TestCase):
    setUp = fixture.AliasEvidenceTests.setUp

    def test_user_band_aliases_complete_song_sets(self):
        expected = {'梦':'夢限大みゅーたいぷ', '孟宪达':'夢限大みゅーたいぷ', '狗':'MyGO!!!!!',
                    '鸡':'Ave Mujica', 'm':'millsage', 'm团':'millsage', '家':'一家Dumb Rock!', '家石笨':'一家Dumb Rock!'}
        with patch.object(self.parser, '_request', side_effect=AssertionError('model called')), patch.object(self.parser._quota, 'reserve', side_effect=AssertionError('quota used')):
            for term, band in expected.items():
                with self.subTest(term=term):
                    ids = {s.id for s in self.repo.songs if s.band == band}
                    self.assertTrue(ids)
                    self.assertEqual({s.id for s in resolve_command('/查曲 '+term,self.repo).songs},ids)
                    result = self.parser.answer_with_outcome('/问 '+term+'的歌曲有哪些',self.repo)
                    self.assertEqual(result.code.value,'success',result.text)
                    self.assertEqual({s.id for s in result.result.songs},ids)

    def test_character_fragment_unique_and_ambiguous_no_model(self):
        with patch.object(self.parser, '_request', side_effect=AssertionError('model called')), patch.object(self.parser._quota, 'reserve', side_effect=AssertionError('quota used')):
            for term, target in [('枣','伊沢なつめ'),('凪','琴平凪'),('萤','汐見蛍'),('铃','ティモリス/八幡海鈴'),('贺','須賀蕾叶'),('麦','アモーリス/祐天寺にゃむ')]:
                with self.subTest(term=term):
                    ids = {c.id for c in self.repo.cards if c.character == target}
                    self.assertTrue(ids)
                    direct = resolve_command('/查卡 '+term,self.repo)
                    self.assertEqual({c.id for c in direct.cards},ids,direct.catalog.error)
                    result = self.parser.answer_with_outcome('/问 '+term+'的成员卡有哪些',self.repo)
                    self.assertEqual(result.code.value,'success',result.text)
                    self.assertEqual({c.id for c in result.result.cards},ids)
            for command in ('/查卡 千','/问 千的成员卡有哪些','/问 千的SSR成员卡有哪些'):
                direct = resolve_command(command,self.repo) if command.startswith('/查') else None
                result = self.parser.answer_with_outcome(command,self.repo) if direct is None else None
                text = direct.catalog.error if direct else result.text
                self.assertEqual(direct.status if direct else result.code.value,'ambiguous')
                self.assertIn('千早',text);self.assertIn('千石',text)

    def test_short_names_do_not_match_inside_other_names(self):
        for name in ('COMBO','MyGO!!!!!','Tomori','Nyamu'):
            self.assertNotEqual(resolve_entity('song',name,self.repo),'millsage')
            anchor=find_anchor('song',name,self.repo)
            self.assertFalse(anchor.entity and anchor.entity.value=='millsage',name)
        for name in ('喵梦','夢限大みゅーたいぷ','家石笨'):
            self.assertFalse(find_anchor('card',name,self.repo).ambiguous,name)
        self.assertIsNone(resolve_character_fragment('想看千的卡',self.repo))
        with self.assertRaises(CharacterAmbiguity):resolve_character_fragment('千',self.repo)
        with self.assertRaises(CharacterAmbiguity):resolve_character_fragment('叶',self.repo)

    def test_legacy_character_aliases_resolve_to_current_catalog(self):
        for entry in self.entries:
            if entry['kind']=='character' and entry['status']=='legacy_pending':
                with self.subTest(alias=entry['alias']):
                    self.assertIsNotNone(resolve_entity('card',entry['alias'],self.repo))

    def test_character_profile_fragments_and_band_aliases(self):
        people=(person(1,'千早爱音'),person(2,'千石由乃',band='夢限大みゅーたいぷ'),person(3,'伊沢なつめ',band='millsage'))
        repo=Repository(catalog(characters=people))
        self.assertIn('伊沢なつめ',execute_character('枣',repo).text)
        result=execute_character('千',repo)
        self.assertIn('千早爱音',result.text);self.assertIn('千石由乃',result.text)
        self.assertIn('角色候选',result.text)
        self.assertIn('伊沢なつめ',execute_character('m',repo).text)

    def test_support_card_fragments(self):
        import json
        from ournotes_bot.sources.haneoka.catalog import validate
        saved=json.loads((fixture.ROOT/'tests/fixtures/haneoka_catalog.json').read_bytes())['snapshot']
        template=validate(saved,1100)[0][2][0]
        self.repo.support_cards=[replace(template,id=700,character='伊泽枣',characters=('伊泽枣',),localized={})]
        self.assertEqual({c.id for c in resolve_command('/查留影卡 枣',self.repo).support_cards},{700})
        with patch.object(self.parser,'_request',side_effect=AssertionError('model called')), patch.object(self.parser._quota,'reserve',side_effect=AssertionError('quota used')):
            result=self.parser.answer_with_outcome('/问 枣的留影卡有哪些',self.repo)
            self.assertEqual(result.code.value,'success',result.text)
            self.assertEqual({c.id for c in result.result.support_cards},{700})

    def test_all_25_chinese_names_offer_result_or_candidates(self):
        import json
        rows=json.loads((fixture.ROOT/'docs/research/feed-glossary/import-audit.json').read_bytes())['entries']
        seen=set()
        for row in rows:
            if row['category']!='character' or row['identity'] in seen:continue
            seen.add(row['identity'])
            for term in set(row['translation']):
                if not '\u3400' <= term <= '\u9fff':continue
                with self.subTest(name=row['translation'],term=term):
                    result=resolve_command('/查卡 '+term,self.repo)
                    self.assertIn(result.status,('success','ambiguous'),result.catalog.error)
                    if result.status=='success':
                        self.assertIn(row['target'],{c.character for c in result.cards})
        self.assertEqual(len(seen),25)

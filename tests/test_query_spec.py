"""Query-spec-v1 synthetic snapshot: semantic parity, never live accuracy claims."""
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.commands import resolve_command
from ournotes_bot.config import Settings
from ournotes_bot.data import Chart, SongRepository
from ournotes_bot.natural_query.query_capabilities import CAPABILITIES
from ournotes_bot.natural_query.query_validation import validate_capability_action
from ournotes_bot.query.event_cutoff_query import parse_cutoff, parse_natural_cutoff
from ournotes_bot.sources.haneoka.song_traits import SongTraits
from ournotes_bot.sources.yatta import BASE, build_data, detail_catalog
from ournotes_bot.structured_query import resolve_query
from test_query import CHARACTERS, CARDS, SONGS, META
from test_card_catalog import raw_detail


class QuerySpecificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        aliases = root / 'aliases.json'
        aliases.write_text(json.dumps({'song': {}, 'card': {}}), encoding='utf-8')
        env = patch.dict('os.environ', {'OURNOTES_ALIAS_FILE': str(aliases)})
        env.start()
        self.addCleanup(env.stop)
        self.repo = SongRepository(BASE, root / 'cache.json')
        self.repo.songs, self.repo.cards = build_data(CHARACTERS, CARDS, SONGS, META)
        song = replace(self.repo.songs[0], traits=SongTraits(2, ('JUST', 'JUST', 'COMBO')),
                       charts=(Chart('HARD', 20, 20.0, 0, ''), Chart('EXPERT', 25, 25.5, None, '')))
        self.repo.songs = [song, replace(song, id=100002, title='合成乙', titles=('合成乙',), localized={},
            traits=SongTraits(2, ('JUST',)), charts=(Chart('EXPERT', 25, 25.0, 123, ''),))]
        card = replace(self.repo.cards[0], id=1, card_type=2, rarity=4,
                       catalog=detail_catalog(raw_detail()))
        self.repo.cards = [card, replace(card, id=2, rarity=3), replace(card, id=3, rarity=20)]
        from ournotes_bot.data import SupportCard
        self.repo.support_cards = [SupportCard(7, '合成SNAP', card.character, (card.character,),
            10, 2, 0, 0, 0, '', '', '', catalog=detail_catalog(raw_detail(True)))]
        self.repo.metadata = {'cached_at': 'query-spec-v1', 'schema': self.repo.CACHE_SCHEMA,
                              'member_skill_index_complete': True, 'card_catalog_version': 1}
        self.parser = AIQueryParser(Settings('', '', BASE, self.repo.cache_file, 6, 'fixture-key',
            ai_quota_file=root / 'quota.json', ai_metrics_file=root / 'metrics.json'))

    def test_spoken_rarity_multiselect_preserves_color_and_ids(self):
        with patch.object(self.parser, '_request', side_effect=AssertionError('model called')) as model, \
             patch.object(self.parser._quota, 'reserve', side_effect=AssertionError('quota used')) as quota:
            for separator in ('或', ' 或 ', '/', '、', '|', ','):
                answer = self.parser.answer_with_outcome('/问 蓝色 SSR' + separator + 'SR 成员卡', self.repo)
                self.assertEqual(answer.code.value, 'success', answer.text)
                self.assertEqual(tuple(c.id for c in answer.result.catalog.cards), (1, 2))
                direct = resolve_command('/查卡 颜色=蓝 稀有度=SSR/SR', self.repo)
                self.assertEqual(answer.result.catalog.request.filters, direct.catalog.request.filters)
            invalid = self.parser.answer_with_outcome('/问 蓝色 SSR或未知 成员卡', self.repo)
            self.assertNotIn(invalid.code.value, ('success', 'empty'))
            model.assert_not_called()
            quota.assert_not_called()

    def test_snap_alias_discovery_and_collision(self):
        from ournotes_bot.commands import handle_command
        from ournotes_bot.query.entity_lexicon import known_alias_names, resolve_entity, unverified_alias_count
        import os
        path = Path(os.environ['OURNOTES_ALIAS_FILE'])
        path.write_text(json.dumps({'support_card': {'照片': '7', '不存在照片': '999'}}), encoding='utf-8')
        self.assertIn('照片', known_alias_names(self.repo))
        self.assertNotIn('不存在照片', known_alias_names(self.repo))
        self.assertEqual(unverified_alias_count(self.repo), 1)
        self.assertIn('词库：有效', handle_command('/数据状态', self.repo))
        self.assertEqual(resolve_entity('support_card', '照片', self.repo), '7')
        self.assertIn('/查留影卡 照片', handle_command('/查缩写 照片', self.repo))
        with patch.object(self.parser, '_request', side_effect=AssertionError('model called')), \
             patch.object(self.parser._quota, 'reserve', side_effect=AssertionError('quota used')):
            answer = self.parser.answer_with_outcome('/问 照片的留影卡', self.repo)
            self.assertEqual(answer.code.value, 'success', answer.text)
        path.write_text(json.dumps({'support_card': {'照片': '7'}, 'card': {'照片': '1'}}), encoding='utf-8')
        self.assertIn('多个对象', handle_command('/查缩写 照片', self.repo))

    def test_snap_only_character_alias_and_failed_file_status(self):
        from ournotes_bot.commands import handle_command
        from ournotes_bot.query.entity_lexicon import known_alias_names
        import os
        self.repo.cards = []
        path = Path(os.environ['OURNOTES_ALIAS_FILE'])
        path.write_text(json.dumps({'character': {'灯照片': '高松灯'}}), encoding='utf-8')
        for nickname in ('tmr', '灯照片'):
            self.assertIn(nickname, known_alias_names(self.repo))
            self.assertIn('/查留影卡', handle_command('/查缩写 ' + nickname, self.repo))
        path.write_text('{', encoding='utf-8')
        self.assertIn('沿用本进程最后有效版本', handle_command('/数据状态', self.repo))
        self.assertIn('灯照片', known_alias_names(self.repo))

    def test_alias_change_invalidates_query_and_continuation_versions(self):
        from ournotes_bot.query.continuation import catalog_version
        from ournotes_bot.natural_query.query_agent import QueryAgent
        import os
        path = Path(os.environ['OURNOTES_ALIAS_FILE'])
        self.parser._agent._sync_cache(self.repo)
        self.parser._agent._cache["sentinel"] = object()
        before = (QueryAgent._source(self.repo), catalog_version(self.repo))
        stat = path.stat()
        path.write_text(json.dumps({'support_card': {'照片': '7'}}), encoding='utf-8')
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        after = (QueryAgent._source(self.repo), catalog_version(self.repo))
        self.parser._agent._sync_cache(self.repo)
        self.assertEqual(len(self.parser._agent._cache), 0)
        self.assertNotEqual(before[0], after[0])
        self.assertNotEqual(before[1], after[1])
        path.write_text('{', encoding='utf-8')
        self.assertEqual(after, (QueryAgent._source(self.repo), catalog_version(self.repo)))

    def test_song_normalization_and_complete_sets(self):
        cases = (
            ('蓝色 lv25', '蓝色 lv25的歌曲', True, '=', 25.0, (100001, 100002)),
            ('蓝色 lv25.0', '蓝色 lv25.0的歌曲', False, '=', 25.0, (100002,)),
            ('蓝色 lv<=20', '蓝色 20级以下的歌曲', False, '<=', 20.0, ()),
            ('蓝色 HD lv<=20', '蓝色 HD 20级以下的歌曲', False, '<=', 20.0, (100001,)),
        )
        with patch.object(self.parser, '_request', side_effect=AssertionError('model called')) as model, \
             patch.object(self.parser._quota, 'reserve', side_effect=AssertionError('quota used')) as quota:
            for direct, question, integer, operator, level, ids in cases:
                with self.subTest(direct=direct):
                    a = resolve_command('/查曲 ' + direct, self.repo)
                    b = self.parser.answer_with_outcome('/问 ' + question, self.repo)
                    self.assertIsNotNone(b.result, b.text)
                    for selection in (a.song_selection, b.result.song_selection):
                        self.assertEqual(selection.request.colors, (2,))
                        self.assertEqual(selection.request.integer_level, integer)
                        self.assertEqual(selection.request.comparison, operator)
                        self.assertEqual(selection.request.level, level)
                        self.assertEqual(tuple(s.id for s in selection.songs), ids)
                    self.assertEqual(b.code.value, 'success' if ids else 'empty')
            model.assert_not_called()
            quota.assert_not_called()

    def test_mission_modes_and_order_are_not_interchangeable(self):
        for value, mode, ids in (
            ('JUST', 'contains', (100001, 100002)), ('纯JUST', 'pure', (100002,)),
            ('混合', 'mixed', (100001,)), ('包含全部JUST/COMBO', 'all', (100001,)),
            ('JUST/JUST/COMBO', 'exact', (100001,)), ('JUST/COMBO/JUST', 'exact', ()),
        ):
            with self.subTest(value=value):
                answer = resolve_command('/查曲 激奏=' + value, self.repo)
                self.assertEqual(answer.song_selection.request.mode, mode)
                self.assertEqual(tuple(s.id for s in answer.songs), ids)

    def test_card_filters_bd_ex_and_list_detail_parity(self):
        cases = (
            ('/查卡 颜色=蓝 稀有度=SSR/SR', '成员卡 颜色=蓝 稀有度=SSR/SR', False, (1, 2)),
            ('/查卡 BD', 'BD成员卡有哪些', False, (3,)),
            ('/查留影卡 EX', 'EX留影卡有哪些', True, (7,)),
        )
        with patch.object(self.parser, '_request', side_effect=AssertionError('model called')), \
             patch.object(self.parser._quota, 'reserve', side_effect=AssertionError('quota used')):
            for command, question, support, ids in cases:
                with self.subTest(command=command):
                    direct = resolve_command(command, self.repo)
                    local = self.parser.answer_with_outcome('/问 ' + question, self.repo)
                    self.assertIsNotNone(local.result, local.text)
                    self.assertEqual(local.code.value, 'success')
                    self.assertEqual(local.result.catalog.request.filters, direct.catalog.request.filters)
                    self.assertEqual(tuple(c.id for c in local.result.catalog.cards), ids)
                    self.assertEqual(direct.catalog.request.mode, 'list')
        with patch.object(self.repo, 'card_with_detail', side_effect=lambda card: card):
            self.assertEqual(resolve_command('/查卡 3', self.repo).catalog.request.mode, 'detail')
        self.assertEqual(resolve_command('/查卡 EX', self.repo).status, 'invalid_arguments')
        self.assertEqual(resolve_command('/查留影卡 BD', self.repo).status, 'invalid_arguments')

    def test_model_contract_parity_only_for_exposed_fields(self):
        question = '高松灯的SSR成员卡有哪些'
        validated = validate_capability_action({
            'action': 'call_tool', 'capability': 'member_card.search',
            'arguments': {'query': '高松灯', 'rarity': 4, 'page': 1},
        }, CAPABILITIES['member_card.search'], question, self.repo)
        self.assertIsNotNone(validated.spec)
        self.assertEqual(validated.spec.rarity, 4)
        actual = resolve_query(validated.spec, self.repo)
        direct = resolve_command('/查卡 角色=高松灯 稀有度=SSR', self.repo)
        self.assertEqual(tuple(c.id for c in actual.cards), (1,))
        self.assertEqual(actual.cards, direct.cards)
        for capability, arguments, field in (
            ('member_card.search', {'query': '', 'rarity': 20}, 'rarity'),
            ('support_card.search', {'query': '', 'rarity': 10}, 'rarity'),
            ('member_card.search', {'query': '', 'colors': ['blue']}, 'colors'),
        ):
            with self.subTest(capability=capability, field=field):
                self.assertIn(field, CAPABILITIES[capability].invalid_fields(arguments))

    def test_missing_zero_unknown_and_ambiguity_are_distinct(self):
        with patch.object(self.parser, '_request', side_effect=AssertionError('model called')), \
             patch.object(self.parser._quota, 'reserve', side_effect=AssertionError('quota used')):
            missing = self.parser.answer_with_outcome('/问 100001 EX Note', self.repo)
            zero = self.parser.answer_with_outcome('/问 100001 HD Note', self.repo)
            unknown = self.parser.answer_with_outcome('/问 不存在歌曲 EX Note', self.repo)
            self.assertIn('暂无资料', missing.text)
            self.assertIn('0 Notes', zero.text)
            self.assertEqual(zero.code.value, 'success')
            self.assertEqual(unknown.code.value, 'unknown_entity')
            self.repo.songs.append(replace(self.repo.songs[0], id=100003))
            ambiguous = self.parser.answer_with_outcome('/问 迷星叫 EX Note', self.repo)
            self.assertEqual(ambiguous.code.value, 'ambiguous')

    def test_cutoff_numbers_keep_their_namespaces(self):
        direct = parse_cutoff('/查榜线 hk id1 歌曲ID=100001 20-40')
        local = parse_natural_cutoff('查榜线 hk id1 歌曲ID=100001 20-40')
        for request in (direct, local):
            self.assertIsNotNone(request)
            self.assertFalse(request.error)
            self.assertEqual(request.server, 'tw')
            self.assertEqual(request.event_id, '1')
            self.assertEqual(request.song_id, '100001')
            self.assertEqual(request.mode, 'range')
            self.assertEqual(request.ranks, tuple(range(20, 41)))
        near = parse_cutoff('/查榜线 1')
        self.assertEqual(near.event_id, '')
        self.assertEqual(near.ranks, tuple(range(1, 12)))

    def test_card_contract_is_immutable_and_keeps_import_identity(self):
        from dataclasses import FrozenInstanceError
        from ournotes_bot.domain.card_query import CardFilters, CardRequest
        from ournotes_bot.query.card_catalog import CardRequest as CompatibleRequest
        from ournotes_bot.structured_query import QuerySpec
        from ournotes_bot.query.entity_lexicon import EntityRef
        self.assertIs(CardRequest, CompatibleRequest)
        filters=CardFilters(rarity=(4,),character=('高松灯',))
        with self.assertRaises(FrozenInstanceError):filters.rarity=(3,)
        with self.assertRaises(TypeError):CardFilters(rarity=[4])
        request=CardRequest(page=7,filters=filters)
        spec=QuerySpec('card',card_request=request,page=2)
        self.assertEqual((request.page,spec.card_request.page,replace(spec,page=3).card_request.page),(7,2,3))
        for fields in ({'card_query':''},{'subject':EntityRef('card',1)},{'rarity':4},
                       {'skill_query':'得分'},{'skill_kind':'live'}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                QuerySpec('card',card_request=request,**fields)
        with self.assertRaises(ValueError):QuerySpec('support_card',card_request=request)
        with self.assertRaises(ValueError):CardRequest(card_id=1,filters=filters)

    def test_canonical_local_model_direct_parity_and_no_execution_reparse(self):
        from ournotes_bot.natural_query import on_query
        from ournotes_bot.query.card_catalog import parse_card_request
        question='成员卡 颜色=蓝 稀有度=SSR/SR 角色=高松灯'
        command='/查卡 颜色=蓝 稀有度=SSR/SR 角色=高松灯'
        direct=resolve_command(command,self.repo)
        local=self.parser.answer_with_outcome('/问 '+question,self.repo)
        with patch.object(on_query,'parse_card_request',wraps=parse_card_request) as parsed:
            grounded=validate_capability_action({'action':'call_tool','capability':'member_card.search',
                'arguments':{'query':'','colors':[2],'rarities':[3,4],'characters':['tmr'],'page':1}},
                CAPABILITIES['member_card.search'],question,self.repo)
            self.assertIsNotNone(grounded.spec,grounded)
            self.assertEqual(parsed.call_count,1)
        with patch('ournotes_bot.structured_query.parse_card_request',side_effect=AssertionError('display reparsed')):
            model=resolve_query(grounded.spec,self.repo)
            label_only=resolve_query(replace(grounded.spec,card_request=replace(
                grounded.spec.card_request,query='展示标签不能作为过滤条件')),self.repo)
        self.assertEqual(local.result.cards,direct.cards)
        self.assertEqual(model.cards,direct.cards)
        self.assertEqual(label_only.cards,direct.cards)
        for changes in ({'colors':[]},{'rarities':[4]},{'characters':['skk']},{'bands':['MyGO']},
                        {'page':2},{'query':'角色=高松灯'},{'tags':['invented']},{'live_categories':['life']}):
            arguments={'query':'','colors':[2],'rarities':[3,4],'characters':['tmr'],'page':1,**changes}
            rejected=validate_capability_action({'action':'call_tool','capability':'member_card.search',
                'arguments':arguments},CAPABILITIES['member_card.search'],question,self.repo)
            self.assertIsNone(rejected.spec,changes)

    def test_typed_count_and_followup_never_parse_display_text(self):
        from ournotes_bot.query.card_catalog import parse_card_request
        from ournotes_bot.query.continuation import capture_context,execute_followup,Operation
        from ournotes_bot.structured_query import QuerySpec
        self.repo.cards=[replace(self.repo.cards[0],id=i) for i in range(1,36)]
        request=parse_card_request('SSR 页2',self.repo)
        result=resolve_query(QuerySpec('card',card_request=request,page=2),self.repo)
        context=capture_context(result,self.repo)
        self.assertEqual(context.query.card_request,request)
        with patch('ournotes_bot.structured_query.parse_card_request',side_effect=AssertionError('reparsed')):
            count=resolve_query(QuerySpec('card',card_request=request,page=2,field='count'),self.repo)
            next_result,_=execute_followup(context,Operation('page',1),self.repo)
            with patch.object(self.repo,'card_with_detail',side_effect=lambda card:card):
                selected,_=execute_followup(context,Operation('select',2),self.repo)
        self.assertIn('35 张',count.short_text)
        self.assertEqual(next_result.catalog.visible[0].id,33)
        self.assertEqual(selected.catalog.cards[0].id,18)

    def test_filtered_id_is_not_the_unfiltered_shortcut_and_legacy_adapts_once(self):
        from ournotes_bot.query.card_catalog import parse_card_request,execute_card_request
        from ournotes_bot.structured_query import QuerySpec
        from ournotes_bot.query.entity_lexicon import EntityRef
        with patch.object(self.repo,'card_with_detail',side_effect=lambda card:card) as detail:
            exact=parse_card_request('1 颜色=红色',self.repo)
            self.assertEqual((exact.mode,exact.card_id,exact.filters.title_ids),('detail',None,(1,)))
            self.assertEqual(execute_card_request(exact,self.repo).status,'empty')
            detail.assert_not_called()
            with patch('ournotes_bot.structured_query.parse_card_request',side_effect=AssertionError('generated command parsed')):
                legacy=resolve_query(QuerySpec('card',EntityRef('character','高松灯'),rarity=4),self.repo)
            self.assertEqual(tuple(c.id for c in legacy.cards),(1,))
            with patch('ournotes_bot.structured_query.parse_card_request',wraps=parse_card_request) as parse:
                resolve_query(QuerySpec('card',card_query='SSR',page=2),self.repo)
                self.assertEqual(parse.call_count,1)

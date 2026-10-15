"""Information queries select one captured catalog and never request player data."""
import unittest
from contextlib import nullcontext
from types import SimpleNamespace

from ournotes_bot.domain.info_catalog import ActivityInfo, CardRef, CharacterInfo, GachaInfo
from ournotes_bot.query.activity_query import execute_activity
from ournotes_bot.query.character_query import execute_character
from ournotes_bot.query.contracts.info import info_text
from ournotes_bot.query.gacha_query import execute_gacha
from ournotes_bot.query.info_query import named_matches
from ournotes_bot.sources.moenotes_events import EventSnapshot, EventSong

NOW = 1_790_000_000


def card(identity, name='高松灯', *, support=False, people=(1,), complete=True, title=None):
    return SimpleNamespace(id=identity, title=title or f'卡牌{identity}', character=name,
        characters=(name,), band='MyGO!!!!!', localized={'character': {'zh': name}, 'band': {'zh': 'MyGO!!!!!'}},
        thumbnail_url=f'https://example.invalid/{"support" if support else "member"}/{identity}.png', rarity=3,
        catalog={'character_ids': list(people), 'character_links_complete': complete})


def person(identity, name, band='MyGO!!!!!', **kwargs):
    return CharacterInfo(identity, name, {'zh': name}, band_id=1 if band == 'MyGO!!!!!' else 2,
                         band=band, band_names={'zh': band}, **kwargs)


def gacha(identity, title, start=NOW - 100, end=NOW + 100, **kwargs):
    return GachaInfo(identity, title, {'zh': title}, start_at=start, end_at=end, **kwargs)


def activity(identity, title, start=NOW - 100, end=NOW + 100, **kwargs):
    return ActivityInfo(identity, title, {'zh': title}, start_at=start, end_at=end, **kwargs)


def catalog(**kwargs):
    values = dict(characters=(), gachas=(), activities=(), songs=(), cards=(), support_cards=(),
        metadata={'info_catalog_available': dict(characters=True, gachas=True, activities=True),
                  'data_version': 'r-test-release', 'cached_at': '2026-10-15T00:00:00+00:00'},
        cache_state='fresh', last_successful_sync_at='2026-10-15T00:00:00+00:00')
    values.update(kwargs)
    return SimpleNamespace(**values)


class Repository:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.captures = 0

    def capture_catalog(self):
        self.captures += 1
        return self.snapshot

    @property
    def event_cutoffs(self):
        raise AssertionError('Static information must not request current events or rankings')


class CurrentSource:
    def __init__(self, event):
        self.value = event
        self.calls = []

    def foreground(self):
        return nullcontext()

    def deadline(self):
        return 0

    def event(self, server, deadline):
        self.calls.append(server)
        return self.value

    def board(self, *args, **kwargs):
        raise AssertionError('Activity information must never read player rankings')


class InformationQueryTests(unittest.TestCase):
    def assert_same_text(self, result):
        self.assertEqual(result.text, info_text(result.views))

    def test_symbol_only_queries_are_invalid_before_catalog_reads(self):
        for execute, command in ((execute_character, '/查角色'), (execute_gacha, '/查卡池'), (execute_activity, '/查活动')):
            for term in ('!!!', '--', '??', '！ ？ -'):
                with self.subTest(command=command, term=term):
                    repo = Repository(catalog())
                    result = execute(term, repo)
                    self.assertEqual(result.status, 'invalid_arguments')
                    self.assertIn(command, result.text)
                    self.assertEqual(result.views, ())
                    self.assertEqual(repo.captures, 0)

    def test_history_symbol_only_terms_are_invalid_not_all_history(self):
        for execute in (execute_gacha, execute_activity):
            for term in ('历史 !!!', '历史 --', '历史 ??'):
                with self.subTest(execute=execute.__name__, term=term):
                    repo = Repository(catalog(gachas=(gacha(1, '旧池', end=NOW),),
                                              activities=(activity(1, '旧活动', end=NOW),)))
                    result = execute(term, repo, now=NOW)
                    self.assertEqual(result.status, 'invalid_arguments')
                    self.assertEqual(result.views, ())
                    self.assertEqual(repo.captures, 0)

    def test_jp_activity_symbol_term_does_not_request_current_event(self):
        result = execute_activity('jp !!!', Repository(catalog()))
        self.assertEqual(result.status, 'invalid_arguments')
        self.assertIn('/查活动', result.text)

    def test_name_matcher_rejects_empty_normalized_terms(self):
        records = (gacha(1, '当前池'), gacha(2, '未来池', start=NOW + 1))
        for term in ('', '  ', '!!!', '--', '??'):
            with self.subTest(term=term), self.assertRaises(ValueError):
                named_matches(records, term)

    def test_real_empty_arguments_keep_directory_and_current_modes(self):
        repo = Repository(catalog(characters=(person(1, '高松灯'),),
                                  gachas=(gacha(1, '当前池'), gacha(2, '未来池', start=NOW + 1))))
        self.assertIn('/查角色 1', execute_character('  ', repo).text)
        result = execute_gacha('  ', repo, now=NOW)
        self.assertIn('/查卡池 1', result.text)
        self.assertNotIn('/查卡池 2', result.text)
        live, source = self._current_repository(activity(1, '当前活动'))
        self.assertEqual(execute_activity('  ', live).status, 'success')
        self.assertEqual(source.calls, ['jp'])

    def test_character_directory_groups_all_roster_even_without_cards(self):
        repo = Repository(catalog(characters=(person(1, '高松灯'), person(2, '千早爱音'), person(3, '三角初华', 'Ave Mujica'))))
        result = execute_character('', repo)
        self.assertEqual(repo.captures, 1)
        self.assertEqual(len(result.views), 1)
        self.assertIn('/查角色 3', result.text)
        self.assert_same_text(result)

    def test_character_id_and_builtin_alias_use_same_profile(self):
        repo = Repository(catalog(characters=(person(1, '高松灯'),), cards=(card(1),)))
        self.assertEqual(execute_character('1', repo).text, execute_character('tmr', repo).text)
        self.assertEqual(execute_character('灯', repo).views[0].layout, 'character')

    def test_character_custom_alias_and_band_alias_follow_existing_registry(self):
        repo = Repository(catalog(characters=(person(1, '高松灯'), person(2, '千早爱音'))))
        self.assertEqual(execute_character('灯皇', repo).views[0].title, '高松灯')
        self.assertEqual(len(execute_character('卖狗', repo).views[0].rows), 2)

    def test_character_explicit_band_filter_and_alias(self):
        repo = Repository(catalog(characters=(person(1, '高松灯'), person(2, '千早爱音'), person(3, '三角初华', 'Ave Mujica'))))
        for term in ('乐队=MyGO', '乐队=卖狗', '乐队＝MyGO!!!!!'):
            result = execute_character(term, repo)
            self.assertEqual([r.command for r in result.views[0].rows], ['/查角色 1', '/查角色 2'])
        self.assertEqual(execute_character('乐队=', repo).status, 'invalid_arguments')
        self.assertEqual(execute_character('乐队=不存在', repo).status, 'no_results')

    def test_mujica_uses_civilian_title_and_preserves_stage_name_search(self):
        repo = Repository(catalog(characters=(person(7, 'Mortis / 若叶睦', 'Ave Mujica'),)))
        for term in ('7', 'Mortis', '若叶睦', '睦头'):
            result = execute_character(term, repo)
            self.assertEqual(result.views[0].title, '若叶睦')
            self.assertIn('艺名\nMortis', result.text)
            self.assertIn('/查卡 若叶睦', result.text)

    def test_character_ambiguous_partial_keeps_candidates(self):
        repo = Repository(catalog(characters=(person(1, '高松灯'), person(2, '高桥测试'))))
        result = execute_character('高', repo)
        self.assertIn('/查角色 1', result.text)
        self.assertIn('/查角色 2', result.text)
        self.assertEqual(result.views[0].layout, 'list')

    def test_character_numeric_lookup_is_not_prefix_or_name(self):
        repo = Repository(catalog(characters=(person(11, '1'),)))
        self.assertEqual(execute_character('1', repo).status, 'no_results')

    def test_character_card_counts_deduplicate_multicharacter_support(self):
        member = card(1)
        support = card(1, support=True, people=(1, 1, 2))
        repo = Repository(catalog(characters=(person(1, '高松灯'),), cards=(member, member), support_cards=(support, support)))
        result = execute_character('1', repo)
        counts = [row.body[0] for row in result.views[0].rows if row.title in {'成员卡', '留影卡'}]
        self.assertEqual(counts, ['1 张', '1 张'])
        self.assertIn('/查留影卡 高松灯', result.text)
        self.assert_same_text(result)

    def test_character_incomplete_links_do_not_claim_exact_count(self):
        repo = Repository(catalog(characters=(person(1, '高松灯'),), cards=(card(1, people=(), complete=False),)))
        self.assertIn('至少 0 张', execute_character('1', repo).text)

    def test_character_missing_profile_fields_do_not_invent_facts(self):
        repo = Repository(catalog(characters=(person(1, '高松灯', fields={'height': {'ja': '155'}}),)))
        result = execute_character('1', repo)
        self.assertNotIn('生日', result.text)
        self.assertNotIn('155cm', result.text)
        self.assertIn('155', result.text)

    def test_missing_source_differs_from_confirmed_empty(self):
        missing = catalog(metadata={'info_catalog_available': {}})
        for execute in (execute_character, execute_gacha, execute_activity):
            term = '列表' if execute is execute_activity else ''
            self.assertEqual(execute(term, Repository(missing)).status, 'data_unavailable')
            self.assertEqual(execute(term, Repository(catalog())).status, 'no_results')

    def test_stale_cache_warning_present_for_results_and_empty_search(self):
        repo = Repository(catalog(characters=(person(1, '高松灯'),), cache_state='stale'))
        for term in ('1', '999'):
            self.assertIn('旧缓存', execute_character(term, repo).text)
        self.assertIn('旧缓存', execute_gacha('', repo, now=NOW).text)

    def test_gacha_current_uses_closed_open_interval_and_excludes_unknown_dates(self):
        records = (gacha(1, '刚开始', start=NOW), gacha(2, '刚结束', end=NOW),
                   gacha(3, '未来', start=NOW + 1), gacha(4, '未知', start=None, end=None))
        repo = Repository(catalog(gachas=records))
        result = execute_gacha('', repo, now=NOW)
        self.assertEqual(repo.captures, 1)
        self.assertIn('/查卡池 1', result.text)
        for identity in (2, 3, 4):
            self.assertNotIn(f'/查卡池 {identity}', result.text)
        self.assertIn('不代表所有玩家当前可抽', result.text)
        self.assert_same_text(result)

    def test_gacha_unknown_dates_are_not_1970_or_permanent(self):
        repo = Repository(catalog(gachas=(gacha(1, '未知', start=None, end=None),)))
        self.assertEqual(execute_gacha('', repo, now=NOW).status, 'no_results')
        detail = execute_gacha('1', repo, now=NOW)
        self.assertIn('开始：未确认', detail.text)
        self.assertIn('开放时间未确认', detail.text)
        self.assertNotIn('1970', detail.text)
        self.assertNotIn('永久', detail.text)

    def test_gacha_history_filters_and_numeric_id_wins(self):
        repo = Repository(catalog(gachas=(gacha(1, '旧池', end=NOW), gacha(2, '1'), gacha(3, '新池'))))
        history = execute_gacha('历史', repo, now=NOW)
        self.assertIn('旧池', history.text)
        self.assertNotIn('新池', history.text)
        self.assertEqual(execute_gacha('1', repo, now=NOW).views[0].title, '旧池')

    def test_gacha_up_card_types_same_id_and_duplicates_remain_correct(self):
        refs = (CardRef(3, 7), CardRef(2, 7), CardRef(2, 7))
        repo = Repository(catalog(gachas=(gacha(1, '混合池', pickup_cards=refs),),
            cards=(card(7, title='成员标题'),), support_cards=(card(7, support=True, title='留影标题'),)))
        result = execute_gacha('1', repo, now=NOW)
        self.assertEqual(result.text.count('/查卡 7'), 1)
        self.assertEqual(result.text.count('/查留影卡 7'), 1)
        self.assertIn('成员标题', result.text)
        self.assertIn('留影标题', result.text)
        self.assertLess(result.text.index('/查卡 7'), result.text.index('/查留影卡 7'))

    def test_gacha_missing_up_mapping_differs_from_known_empty(self):
        missing = gacha(1, '缺字段')
        empty = gacha(2, '确认为空', available_relations=frozenset({'pickup_cards'}))
        repo = Repository(catalog(gachas=(missing, empty)))
        self.assertIn('未提供 UP 卡关联', execute_gacha('1', repo, now=NOW).text)
        self.assertIn('没有标记 UP 卡', execute_gacha('2', repo, now=NOW).text)

    def test_gacha_condition_does_not_decode_account_eligibility_or_pity(self):
        repo = Repository(catalog(gachas=(gacha(1, '条件池', display_condition=2, limited=True),)))
        result = execute_gacha('1', repo, now=NOW)
        self.assertIn('有展示条件', result.text)
        self.assertNotIn('必出', result.text)
        self.assertNotIn('概率', result.text)

    def test_long_gacha_lists_leave_actual_pagination_to_renderer(self):
        records = tuple(gacha(i, f'卡池{i}') for i in range(1, 15))
        result = execute_gacha('', Repository(catalog(gachas=records)), now=NOW)
        self.assertEqual(len(result.views), 1)
        self.assertEqual(sum(len(v.rows) for v in result.views), 14)
        self.assert_same_text(result)

    def test_activity_history_never_reads_current_source_or_gives_ranking_command(self):
        record = activity(1, '过去活动', end=NOW - 1, reward_cards=(CardRef(2, 7),),
                          available_relations=frozenset({'reward_cards'}), song_ids=(101,))
        repo = Repository(catalog(activities=(record,), cards=(card(7),)))
        result = execute_activity('1', repo, now=NOW)
        self.assertEqual(repo.captures, 1)
        self.assertIn('/查卡 7', result.text)
        self.assertIn('/查曲 101', result.text)
        self.assertNotIn('/查榜线', result.text)
        self.assertIn('不据此判断是否为挑战歌曲', result.text)
        self.assert_same_text(result)

    def test_activity_history_uses_end_not_display_end(self):
        record = activity(1, '结算期', end=NOW - 1, display_end_at=NOW + 100)
        repo = Repository(catalog(activities=(record, activity(2, '正在进行'))))
        result = execute_activity('历史', repo, now=NOW)
        self.assertIn('/查活动 1', result.text)
        self.assertNotIn('/查活动 2', result.text)

    def test_activity_foreign_history_is_invalid_without_network(self):
        self.assertEqual(execute_activity('hk 历史', Repository(catalog())).status, 'invalid_arguments')

    def _current_repository(self, static):
        event = EventSnapshot('jp', '1', '当前活动', 'nowOn', (NOW - 100) * 1000, (NOW + 100) * 1000,
                              (EventSong('5', '101', '挑战曲'),), source='haneoka')
        source = CurrentSource(event)
        repo = SimpleNamespace(event_cutoffs=source, capture_catalog=lambda: catalog(activities=(static,), cards=(card(7),)))
        return repo, source

    def test_current_activity_same_id_and_dates_can_attach_static_cards(self):
        record = activity(1, '静态标题', bonus_cards=(CardRef(2, 7),), available_relations=frozenset({'bonus_cards'}))
        repo, source = self._current_repository(record)
        result = execute_activity('', repo)
        self.assertEqual(source.calls, ['jp'])
        self.assertIn('/查榜线 jp 歌曲ID=101', result.text)
        self.assertIn('/查卡 7', result.text)
        self.assert_same_text(result)

    def test_current_activity_mismatched_window_does_not_attach_wrong_cards(self):
        record = activity(1, '旧复刻', start=NOW - 1000, bonus_cards=(CardRef(2, 7),),
                          available_relations=frozenset({'bonus_cards'}))
        repo, _ = self._current_repository(record)
        self.assertNotIn('/查卡 7', execute_activity('', repo).text)

    def test_current_activity_matching_window_wrong_id_does_not_attach_cards(self):
        record = activity(2, '另一活动', bonus_cards=(CardRef(2, 7),), available_relations=frozenset({'bonus_cards'}))
        repo, _ = self._current_repository(record)
        self.assertNotIn('/查卡 7', execute_activity('', repo).text)


if __name__ == '__main__':
    unittest.main()

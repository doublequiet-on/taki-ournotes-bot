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
    def test_calendar_gap_boundary_next_and_missing_times(self):
        ended = activity(1, '上一期', NOW-100, NOW, display_end_at=NOW+500)
        repo = Repository(catalog(activities=(ended,)))
        result = execute_activity('日程', repo, now=NOW)
        self.assertIn('最近一期《上一期》已于', result.text)
        for metadata in ('资料来源', '资料版本', '本机获取', 'r-test-release'):
            self.assertNotIn(metadata, result.text)
        self.assertIn('还不能确定下一期', result.text)
        self.assertNotIn('清 CP', result.text)
        future = activity(2, '下一期', NOW+50, NOW+100)
        repo.snapshot = catalog(activities=(ended, future))
        self.assertIn('下一期《下一期》将于', execute_activity('日程', repo, now=NOW).text)
        self.assertIn('目前还没开始', execute_activity('日程', repo, now=NOW).text)
        self.assertIn('清 CP', execute_activity('日程', repo, now=NOW+50).text)
        repo.snapshot = catalog(activities=(ended, future), cache_state='stale')
        self.assertNotIn('清 CP', execute_activity('日程', repo, now=NOW+50).text)
        self.assertIn('旧缓存', execute_activity('日程', repo, now=NOW+50).text)
        repo.snapshot = catalog(activities=(ended, future, activity(3, '时间未知', None, None)))
        self.assertIn('部分活动时间缺失', execute_activity('日程', repo, now=NOW).text)
        self.assertNotIn('下一期《', execute_activity('下期', repo, now=NOW).text)

    def test_calendar_keeps_intl_identity_and_rejects_ambiguous_schedules(self):
        from ournotes_bot.domain.info_catalog import ActivityCalendar
        jp = activity(1, '日服一期')
        intl = activity(2, '国际一期', NOW-200, NOW-100)
        repo = Repository(catalog(activities=(jp,), activity_calendars={
            'intl': ActivityCalendar((intl,), 'r-intl', 'intl-source', 'fixture')}))
        for alias in ('台服', '国际服', 'intl', 'tw', 'en'):
            result = execute_activity(alias+' 日程', repo, now=NOW)
            self.assertIn('国际一期', result.text); self.assertNotIn('日服一期', result.text)
            self.assertIn('国际服（台服）', result.text)
            self.assertNotIn('r-intl', result.text)
        repo.snapshot = catalog(activities=(jp,))
        self.assertEqual(execute_activity('国际服 日程', repo, now=NOW).status, 'data_unavailable')
        repo.snapshot = catalog(activities=(jp, activity(3, '重叠')))
        self.assertIn('暂不能唯一确定', execute_activity('日程', repo, now=NOW).text)

    def test_activity_merges_roles_by_card_type_and_id_and_keeps_known_partial_rewards(self):
        ref=CardRef(2,7)
        record=activity(1,'活动',bonus_cards=(ref,),reward_cards=(ref,CardRef(3,7)),pickup_cards=(ref,),
                        available_relations=frozenset({'bonus_cards','pickup_cards'}))
        repo=Repository(catalog(activities=(record,),cards=(card(7),),support_cards=(card(7,support=True),)))
        result=execute_activity('1',repo,now=NOW)
        self.assertEqual(result.text.count('/查卡 7'),1)
        self.assertEqual(result.text.count('/查留影卡 7'),1)
        member=next(r for r in result.views[0].rows if r.command=='/查卡 7')
        self.assertEqual(member.image_kind,'portrait')
        self.assertEqual(member.tags,('加成','奖励','展示'))
        self.assertEqual(member.label,'指定加成卡 · 卡牌奖励 · 活动展示卡')
        support=next(r for r in result.views[0].rows if r.command=='/查留影卡 7')
        self.assertEqual(support.image_kind,'wide')
        self.assertIn('卡牌奖励关联不完整',result.text)
        self.assert_same_text(result)

    def test_partly_known_up_cards_remain_visible_with_incomplete_notice(self):
        record=gacha(1,'卡池',pickup_cards=(CardRef(2,7),),available_relations=frozenset({'featured_cards'}))
        result=execute_gacha('1',Repository(catalog(gachas=(record,),cards=(card(7),))),now=NOW)
        self.assertIn('/查卡 7',result.text)
        self.assertIn('部分 UP 标记未确认',result.text)
        self.assertNotIn('没有标记 UP 卡',result.text)
        self.assert_same_text(result)

    def test_directory_captures_band_groups_and_profile_separates_prose(self):
        entry=person(1,'高松灯',fields={'height':{'zh':'155cm'},'description':{'zh':'完整简介'}})
        repo=Repository(catalog(characters=(entry,person(2,'三角初华','Ave Mujica'))))
        directory=execute_character('',repo).views[0]
        self.assertEqual(directory.layout,'directory')
        self.assertEqual([r.group for r in directory.rows],['MyGO!!!!!','Ave Mujica'])
        profile=execute_character('1',repo).views[0]
        self.assertEqual(next(r for r in profile.rows if r.title=='身高').kind,'fact')
        self.assertEqual(next(r for r in profile.rows if r.title=='简介').kind,'prose')
        self.assertEqual(next(r for r in profile.rows if r.title=='成员卡').kind,'links')

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
        self.assertTrue(all('MyGO!!!!!' in row.label for row in result.views[0].rows))

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
        self.assertIn('关联歌曲', result.text)
        self.assertNotIn('不据此判断是否为挑战歌曲', result.text)
        self.assert_same_text(result)

    def test_activity_list_embeds_typed_card_strips_and_partial_relation_notice(self):
        record=activity(1,'活动',bonus_cards=(CardRef(2,7),),reward_cards=(CardRef(3,7),),
                        available_relations=frozenset({'bonus_cards'}))
        result=execute_activity('列表',Repository(catalog(activities=(record,),cards=(card(7),),
                                support_cards=(card(7,support=True),))),now=NOW)
        entry=result.views[0].rows[0]
        self.assertEqual(entry.kind,'banner')
        cards=[r for r in entry.children if r.kind=='card']
        self.assertEqual([r.image_kind for r in cards],['portrait','wide'])
        self.assertIn('/查卡 7',result.text)
        self.assertIn('/查留影卡 7',result.text)
        self.assertIn('卡牌奖励关联不完整',result.text)
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

    def test_current_activity_does_not_repeat_live_challenge_as_static_related_song(self):
        record = activity(1, '活动', song_ids=(101, 102))
        repo, _ = self._current_repository(record)
        result = execute_activity('', repo)
        self.assertEqual(result.text.count('歌曲 ID 101'), 1)
        self.assertIn('/查榜线 jp 歌曲ID=101', result.text)
        self.assertNotIn('/查曲 101', result.text)
        self.assertIn('/查曲 102', result.text)
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


class ActivityAnnouncementTests(unittest.TestCase):
    def test_only_explicit_body_event_period_is_used(self):
        from ournotes_bot.sources.haneoka.activity_announcements import announced_time
        row = dict(title='イベント「次のステージ」開催予告！',
                   text='【イベント開催期間】\n2026/10/30(金) 15:00 ～ 2026/11/07(土) 14:59', text_truncated=False)
        name, stamp = announced_time(row, 'jp')
        from datetime import datetime, timezone
        self.assertEqual(name, '次のステージ')
        self.assertEqual(stamp, datetime(2026,10,30,6,tzinfo=timezone.utc).timestamp())
        for change in ({'text_truncated':True}, {'text':'【ランキング結果集計期間】\n2026/10/30(金) 15:00'},
                       {'text':'2026/10/30(金) 15:00'}, {'title':'イベント「次のステージ」延期のお知らせ'},
                       {'text':row['text']+'\n延期となります'}, {'text':'【イベント開催期間】\n2026/02/30 15:00'}):
            self.assertIsNone(announced_time({**row,**change},'jp'))
        self.assertIsNotNone(announced_time({**row,'text':row['text']+'\n内容は予告なく変更する場合がございます'},'jp'))
        en=dict(title='Challenge Live Event "Next" Coming Soon!',text='Event Period: 2026/10/30 06:00 - 2026/11/07 05:59 (UTC)',text_truncated=False)
        self.assertEqual(announced_time(en,'intl')[1],stamp)
        self.assertIsNone(announced_time({**en,'text':en['text'].replace(' (UTC)','')},'intl'))

    def test_notice_identity_change_missing_body_and_explicit_future(self):
        from unittest.mock import Mock
        from ournotes_bot.sources.haneoka.activity_announcements import ActivityAnnouncements
        from ournotes_bot.sources.errors import ChartDataError
        from datetime import datetime, timezone
        now = datetime(2026,10,26,tzinfo=timezone.utc).timestamp()
        row=dict(id=10,title='イベント「次のステージ」開催予告！',startAt=int(now-100),endAt=None,updatedAt=int(now-10))
        index=dict(observed_at='2026-10-26T00:00:00Z',rows=[row])
        full={**row,'text':'【イベント開催期間】\n2026/10/30(金) 15:00 ～ 2026/11/07(土) 14:59','text_truncated':False}
        notices=Mock();notices.read.side_effect=[index,{**index,'rows':[full]}]
        result,note=ActivityAnnouncements(notices).next('jp',now)
        self.assertEqual(result.title,'次のステージ');self.assertTrue(result.url.endswith('/jp/ja/announcements/10/'))
        self.assertEqual(notices.read.call_count,2)
        revision={**row,'id':11,'updatedAt':int(now),'title':'イベント「次のステージ」について'}
        revised={**revision,'text':'開催を中止します','text_truncated':False}
        revised_index={**index,'rows':[revision,row]}
        notices.read.side_effect=[revised_index,{**index,'rows':[revised]},{**index,'rows':[full]}]
        result,note=ActivityAnnouncements(notices).next('jp',now)
        self.assertIsNone(result);self.assertIn('延期或调整',note)
        notices.read.side_effect=[index,{**index,'observed_at':'different','rows':[full]}]
        self.assertIsNone(ActivityAnnouncements(notices).next('jp',now)[0])
        notices.read.side_effect=[index,ChartDataError('missing body')]
        result,note=ActivityAnnouncements(notices).next('jp',now)
        self.assertIsNone(result);self.assertIn('暂不能确认',note)
        notices.read.side_effect=ChartDataError('unavailable')
        self.assertIn('读取失败',ActivityAnnouncements(notices).next('jp',now)[1])

    def test_gap_uses_verified_notice_when_next_calendar_entry_is_absent(self):
        from unittest.mock import Mock
        from ournotes_bot.domain.info_catalog import AnnouncedActivity
        repo=Repository(catalog(activities=(activity(1,'最近一期',NOW-100,NOW),)))
        repo.activity_announcements=Mock()
        repo.activity_announcements.next.return_value=(AnnouncedActivity('下一期公告',NOW+100,
             'https://haneoka.org/jp/ja/announcements/10/','fixture'),'')
        result=execute_activity('日程',repo,now=NOW)
        self.assertIn('最近一期',result.text);self.assertIn('公告预告《下一期公告》将于',result.text)
        self.assertIn('/announcements/10/',result.text);self.assertNotIn('清 CP',result.text)
        self.assertNotIn('还不能确定',result.text)
        repo.activity_announcements.next.assert_called_once_with('jp',NOW)


class PublicQueryContractTests(unittest.TestCase):
    def test_game_art_is_pinned_allowlisted_and_bonus_art_keeps_native_role(self):
        from ournotes_bot.sources.haneoka.catalog_assets import asset_url
        from ournotes_bot.sources.haneoka.public_queries import PublicQueries
        from ournotes_bot.sources.haneoka.activity_details import project_details
        release='r-'+'a'*20
        rank='/runtime/jp/unity/Assets/AddressableResources/UI/Atlas/FixUiSpriteAtlas.spriteatlasv2/rank_gold--Sprite-123.png'
        mission='/runtime/intl/unity/Assets/AddressableResources/Live/Images/Atlas/LiveAtlas.spriteatlasv2/Icon_gekisou_just--Sprite--123.png'
        self.assertTrue(asset_url(rank,release).endswith('?release='+release))
        self.assertIn('/intl/',asset_url(mission,release))
        for path in (rank.replace('rank_gold','unrelated'),mission.replace('just','arbitrary')):
            with self.assertRaises(ValueError):asset_url(path,release)
        mark='runtime/unity/Assets/AddressableResources/UI/Atlas/FixUiSpriteAtlas.spriteatlasv2/sp_icon_live_music_type_99--Sprite-1.png'
        snapshot={'identities':{'jp':{'releaseId':release}},'documents':{'jp':{'ui-marks':{'sp_icon_live_music_type_99.png':mark}}}}
        source=PublicQueries(fetch=lambda *args:self.fail('unexpected network'))
        self.assertTrue(source.arena_attribute('jp',99,snapshot,release))
        self.assertEqual(source.arena_attribute('en',99,snapshot,release),'')
        self.assertEqual(source.arena_attribute('jp',99,snapshot,'r-'+'b'*20),'')
        event={'effects':[{'targets':{'memberCard':{'name':'测试卡','image':'card'}},'resourceTypeConstraint':2}]}
        fact=project_details(event,lambda path:'verified:'+path if path else '')[0].facts[0]
        self.assertEqual((fact.image_url,fact.image_kind),('verified:card','portrait'))

    def test_public_id_precision_regions_and_invalid_arguments(self):
        from ournotes_bot.query.public_queries import parse_player, parse_arena
        from ournotes_bot.domain.public_queries import PublicQueryError
        self.assertEqual(parse_player('9223372036854775807').profile_id, '9223372036854775807')
        for region, ident in [('hk', '21234567890'), ('en', '31234567890'), ('kr', '41234567890')]:
            self.assertEqual(parse_player(ident + ' ' + region + ' 名片 页3').page, 3)
        for query in ('0', '9223372036854775808', '31234567890 hk', '1 jp hk', '1 页3', '1 名片 页0'):
            with self.subTest(query=query), self.assertRaises(PublicQueryError):
                parse_player(query)
        self.assertEqual(parse_arena('MyGO!!!!! hk 榜单 排名=90-100').last, 100)
        for query in ('MyGO!!!!! 榜单 排名=2-1', 'MyGO!!!!! 榜单 排名=1-101', 'MyGO!!!!! jp hk', '不存在的乐队'):
            with self.subTest(query=query), self.assertRaises(PublicQueryError):
                parse_arena(query)
        from ournotes_bot.query.public_queries import inside, rule_text
        self.assertTrue(inside({'startAt': 1000, 'endAt': 1999}, 1999))
        self.assertFalse(inside({'startAt': 1000, 'endAt': 1999}, 2999))
        self.assertIn('COMBO/JUST/LUCK', rule_text({'musicType': 0, 'missions': [0, 3, 0]},
                                                {'musicType': 1, 'arenaMissions': [1, 1, 2]}))

    def test_pending_and_limits_do_not_cache_or_retry(self):
        from ournotes_bot.sources.haneoka.public_queries import PublicQueries
        from ournotes_bot.domain.public_queries import PublicQueryError
        for status, expected in ((202, 'pending'), (404, 'not_found'), (429, 'rate_limited'), (503, 'data_unavailable')):
            calls = []
            def fetch(*args):
                calls.append(args)
                return status, b'{}', {'retry-after': '10'}
            source = PublicQueries(fetch=fetch)
            with self.assertRaises(PublicQueryError) as result:
                source.read('/fixture')
            self.assertEqual(result.exception.status, expected)
            self.assertEqual(result.exception.retry_after, 10)
            self.assertEqual(len(calls), 1)
            self.assertFalse(source.cache)
            if status in (202, 429):
                with self.assertRaises(PublicQueryError):
                    source.read('/fixture')
                self.assertEqual(len(calls), 1)

    def test_player_identity_and_cache_expiration(self):
        import json
        from ournotes_bot.sources.haneoka.public_queries import PublicQueries
        from ournotes_bot.domain.public_queries import PlayerRequest, PublicQueryError
        calls, now = [], [100]
        value = {'region': 'jp', 'profileId': '99', 'profile': {'name': 'fixture', 'rankExp': 0, 'totalFavorite': 0}}
        def fetch(*args):
            calls.append(args)
            return 200, json.dumps(value).encode(), {}
        source = PublicQueries(fetch=fetch, clock=lambda: now[0])
        first = source.player(PlayerRequest('99'))
        self.assertIs(first, source.player(PlayerRequest('99')))
        self.assertEqual(len(calls), 1)
        now[0] += 60
        source.player(PlayerRequest('99'))
        self.assertEqual(len(calls), 2)
        value['region'] = 'kr'
        with self.assertRaises(PublicQueryError):
            source.player(PlayerRequest('100'))

    def test_source_bound_namecard_does_not_fetch_foreign_or_other_player(self):
        from ournotes_bot.sources.haneoka.public_queries import PublicQueries
        from ournotes_bot.domain.public_queries import PlayerRequest, PublicQueryError
        calls = []
        source = PublicQueries(fetch=lambda *args: calls.append(args))
        for path in ('https://evil.invalid/image.png', '/api/v1/game/records/jp/players/100/cards/1/download?file=99_a',
                     '/api/v1/game/records/jp/players/99/cards/1/download?file=100_a'):
            with self.assertRaises(PublicQueryError):
                source.namecard(PlayerRequest('99'), {'page': 1, 'downloadUrl': path})
        self.assertFalse(calls)
        source = PublicQueries(fetch=lambda *args: (200, b'{}', {}))
        with self.assertRaises(PublicQueryError) as failure:
            source.arena('jp')
        self.assertEqual(failure.exception.status, 'invalid_source')

    def test_arena_null_song_rules_and_malformed_namecard_reject_at_source(self):
        import json
        from ournotes_bot.sources.haneoka.public_queries import arena_document, PublicQueries
        from ournotes_bot.domain.public_queries import PublicQueryError, PlayerRequest
        song = {'musicId': 1, 'musicTitle': 'fixture', 'musicType': 1, 'arenaMissions': [1, 2, 3], 'arenaPublishedAt': 0}
        league = {'id': 1, 'bandId': 1, 'name': 'fixture', 'rules': [], 'ranks': [], 'songs': [song]}
        document = {'entries': {'1': {'seasonId': 1, 'title': 'fixture', 'startAt': 1, 'endAt': 2, 'leagues': [league]}}}
        arena_document(document)
        for field in ('musicType', 'arenaMissions', 'arenaPublishedAt'):
            original = song[field]
            song[field] = None
            with self.subTest(field=field), self.assertRaises(PublicQueryError):
                arena_document(document)
            song[field] = original
        for card in ({'name': 1}, {'pages': [{'page': 1, 'imageUrl': 1}]}):
            data = {'region': 'jp', 'profileId': '99', 'profile': {'profileCard': card}}
            source = PublicQueries(fetch=lambda *args: (200, json.dumps(data).encode(), {}))
            with self.assertRaises(PublicQueryError):
                source.player(PlayerRequest('99'))

    def test_local_grammar_requires_whole_question(self):
        from ournotes_bot.natural_query.on_query import public_information_command
        self.assertEqual(public_information_command('玩家 9223372036854775807 jp'), '/查玩家 9223372036854775807 jp')
        self.assertEqual(public_information_command('MyGO竞技场人数'), '/查竞技场 MyGO 人数')
        self.assertEqual(public_information_command('本期活动奖励'), '/查活动 奖励')
        self.assertIsNone(public_information_command('玩家 99 jp 顺便猜一下密码'))

    def test_activity_sections_keep_conditions_unknown_and_explicit_links(self):
        from ournotes_bot.sources.haneoka.activity_details import project_details
        from ournotes_bot.query.activity_query import detail_rows
        from types import SimpleNamespace
        value = {'effects': [{'targets': {'band': {'name': ['MyGO!!!!!']}}, 'bonusType': 2,
                             'perRank': [{'rank': 1, 'percent': 15}, {'rank': 2, 'percent': 20}]}],
                 'rewards': [{'unresolvedRewardId': 71}], 'missions': [], 'recruitments': []}
        sections = project_details(value)
        self.assertEqual(sections[0].facts[0].table, (('活动道具加成','档位1：+15%','档位2：+20%'),))
        self.assertFalse(next(s for s in sections if s.name == '奖励').complete)
        result = detail_rows(SimpleNamespace(details=sections), '加成')
        text = '\n'.join('\n'.join(r.body) for r in result)
        self.assertIn('档位1：+15%', text)
        self.assertIn('档位2：+20%', text)
        self.assertFalse(next(s for s in sections if s.name == '卡池').facts)
        self.assertFalse(any(s.complete for s in project_details({'effects': None})))
        from ournotes_bot.query.activity_query import overview_rows
        from ournotes_bot.query.contracts.info import InfoRow
        record = SimpleNamespace(details=sections, event_type_id=1, ranking_modes=('歌曲榜',))
        ordered = overview_rows(record, (InfoRow('挑战曲', label='挑战歌曲', kind='song'),), '/查活动 1')
        self.assertEqual(ordered[0].title, '活动类型与榜制')
        self.assertTrue(ordered[1].title.startswith('加成 · '))
        self.assertEqual(ordered[2].title, '挑战曲')


if __name__ == '__main__':
    unittest.main()

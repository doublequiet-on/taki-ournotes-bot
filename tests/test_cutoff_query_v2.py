# L3
# Input: fixed ordered server events, synthetic boards and isolated history/network/QQ stubs.
# Output: A01-A28 cutoff view and flexible-parameter regressions, independent of current events.
# Pos: Tests / cutoff-query adjustment; see L2.md and docs/EVENT_CUTOFFS.md.
# Effects: temporary caches and offline image rendering; no real QQ, model or external network.
import io
import itertools
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from PIL import Image

from ournotes_bot.query.event_cutoff_query import execute_cutoff, parse_cutoff
from ournotes_bot.sources.moenotes_events import EventCutoffRepository
from ournotes_bot.sources.cutoff_history import CutoffHistory
from ournotes_bot.structured_query import QuerySpec
from ournotes_bot.rendering.event_cutoff_visuals import render_cutoff
from ournotes_bot.platforms.qq.qq import PreparedReply, _expand_replies
from test_event_cutoffs import PublicFixture, encoded


class CutoffQueryV2Tests(unittest.TestCase):
    def halloween_event(self):
        from ournotes_bot.sources.moenotes_events import EventSnapshot, EventSong
        songs = tuple(EventSong(str(10+i), mid, '合成课题曲'+str(i+1), collect_status='collecting')
                      for i, mid in enumerate(('100118','100115','100005','100029')))
        return EventSnapshot('jp','4','万圣节 · 合成验收','nowOn',1793156400000,1794031199000,songs)

    def test_ranking_scope_roundtrip_and_conflicts(self):
        for tokens in itertools.permutations(('总榜','100','jp','仅数值')):
            request = parse_cutoff(' '.join(tokens))
            self.assertFalse(request.error)
            self.assertEqual(request.board_scope, 'total')
            self.assertEqual(parse_cutoff(request.command_label()), request)
        for query in ('总榜 单曲', '总榜 歌曲1', '总榜 歌曲ID=100118', '总榜 歌名="总榜"', '总榜 abc'):
            self.assertTrue(parse_cutoff(query).error, query)
        self.assertEqual(parse_cutoff('歌名="总榜"').query, '总榜')
        self.assertEqual(parse_cutoff('单曲 20-40').board_scope, 'song')

    def test_verified_format_is_scoped_to_complete_event_identity(self):
        from ournotes_bot.sources.moenotes_events import event_ranking_format
        event = self.halloween_event()
        self.assertTrue(event_ranking_format(event).total_enabled)
        self.assertTrue(event_ranking_format(replace(event, songs=event.songs[::-1])).total_enabled)
        for changed in (replace(event, server='tw'), replace(event, event_id='5'),
                        replace(event, start_ms=event.start_ms+1), replace(event, songs=event.songs[:3])):
            self.assertIsNone(event_ranking_format(changed).total_enabled)
        old = replace(event, event_id='1', start_ms=1790758800000, end_ms=1791460799000,
                      songs=tuple(replace(s, challenge_id=str(i+1),music_id=mid)
                                  for i,(s,mid) in enumerate(zip(event.songs,('100109','100056','100063')))))
        self.assertFalse(event_ranking_format(old).total_enabled)

    def test_total_query_never_reads_song_boards_or_writes_history(self):
        event = self.halloween_event()
        with patch.object(self.source, 'event', return_value=event), \
                patch.object(self.source, 'boards', side_effect=AssertionError('song read')), \
                patch.object(self.source.history, 'read', side_effect=AssertionError('history read')):
            answer = self.ask('总榜 100')
            self.assertIn('综合榜数据暂未接入', answer.text)
            self.assertEqual(answer.status, 'data_unavailable')
            self.assertFalse(answer.boards)
            self.assertFalse(render_cutoff(answer))

    def test_four_song_overview_and_outage_keep_total_capability(self):
        from ournotes_bot.sources.moenotes_events import BoardSnapshot
        event = self.halloween_event()
        boards = tuple(BoardSnapshot(song, tuple(20_000_000-r for r in range(100))) for song in event.songs)
        with patch.object(self.source, 'event', return_value=event), patch.object(self.source, 'boards', return_value=boards):
            answer = self.ask('100')
            self.assertEqual(len(answer.boards), 4)
            self.assertIn('综合榜数据暂未接入', answer.text)
            self.assertFalse(self.ask('单曲 100').ranking_notes)
            self.assertEqual(len(render_cutoff(answer)), 1)
            self.assertEqual(len(render_cutoff(self.ask())), 2)
        missing = tuple(BoardSnapshot(song, status='来源暂不可用') for song in event.songs)
        with patch.object(self.source, 'event', return_value=event), patch.object(self.source, 'boards', return_value=missing):
            answer = self.ask()
            self.assertIn('综合榜数据暂未接入', answer.text)
            self.assertFalse(render_cutoff(answer))
            self.assertIn('所选单曲榜暂无可用分数', answer.text)
        partial = (boards[0], *missing[1:])
        with patch.object(self.source, 'event', return_value=event), patch.object(self.source, 'boards', return_value=partial):
            self.assertEqual(self.ask().status, 'success')
            self.assertFalse(self.ask().message)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = PublicFixture()
        def transport(url, timeout):
            raw, headers = self.fx(url, timeout)
            if '/tw/events/current' in url:
                import json
                payload = json.loads(raw)
                payload['challengeRankings'].reverse()
                raw = encoded(payload)
            return raw, headers
        self.source = EventCutoffRepository(Path(self.tmp.name)/'current', transport=transport,
                                           clock=lambda: self.fx.now, monotonic=lambda: self.fx.now)
        self.source.history = CutoffHistory(Path(self.tmp.name)/'history.sqlite3', min_free_mb=0)
        self.repo = SimpleNamespace(event_cutoffs=self.source)

    def ask(self, text=''):
        return execute_cutoff(parse_cutoff('/查榜线 ' + text), self.repo)

    def test_server_song_rank_all_six_orders_and_dedup(self):  # A11/A14/A17
        requests = []
        for groups in itertools.permutations(('hk', '歌曲一', '100')):
            answer = self.ask(' '.join(groups))
            self.assertEqual(answer.status, 'success', answer.text)
            self.assertEqual(answer.request.server, 'tw')
            self.assertEqual(answer.ranks, tuple(range(90, 101)))
            self.assertEqual(answer.request.target_rank, 100)
            self.assertEqual(answer.boards[0].song.music_id, '103')
            requests.append(answer.request)
        self.assertTrue(all(r == requests[0] for r in requests))
        self.assertEqual(self.ask('100 歌曲1 hk 国服 歌曲一 100').request, requests[0])
        self.assertEqual(sum(u.endswith('/ranking') for u in self.fx.calls), 1)

    def test_range_and_modifier_all_orders(self):  # A07/A12/A20
        canonical = self.ask('hk 歌曲二 20-40 仅数值')
        for groups in itertools.permutations(('hk', '歌曲二', '20—40', '仅数值')):
            with patch.object(self.source.history, 'read', side_effect=AssertionError('history read')):
                answer = self.ask(' '.join(groups))
            self.assertEqual(answer.request, canonical.request)
            self.assertEqual(answer.ranks, tuple(range(20, 41)))
            self.assertEqual(answer.request.mode, 'range')

    def test_near_boundaries_and_t_compatibility(self):  # A04/A06/A08/A10
        for n, first, last in ((1,1,11),(5,1,15),(50,40,60),(95,85,100),(100,90,100)):
            answers = [self.ask(token) for token in (str(n),f'T{n}',f't{n}')]
            self.assertTrue(all(a.ranks == tuple(range(first,last+1)) for a in answers))
            self.assertTrue(all(a.request.mode == 'near' and a.request.target_rank == n for a in answers))
        exact = self.ask('50-50')
        self.assertEqual(exact.ranks, (50,))
        self.assertEqual(exact.request.mode, 'range')
        self.assertIsNone(exact.request.target_rank)

    def test_song_number_aliases_and_original_position(self):  # A03/A05/A13/A16
        for text in ('歌曲2','歌曲二','歌2','第2首','第二首'):
            answer = self.ask(text)
            self.assertEqual(answer.request.mode,'trend')
            self.assertEqual(answer.boards[0].song.music_id,'102')
            self.assertIn('歌曲2',answer.text)
        for text in ('歌曲0','歌曲4','歌曲-1','歌曲1.5'):
            self.assertEqual(self.ask(text).status,'invalid_arguments',text)
        self.assertEqual(len(self.ask('1').boards),3)

    def test_conflicting_and_invalid_slots_never_guess(self):  # A17/A18
        for text in ('jp hk 歌曲1','歌曲1 歌曲2','50 60','2 50','50 40-60',
                     '40-20','0-110','-1','1.5','101','T1 T2 T3 T4 T5 T6','T1 2',
                     '歌曲1 50 server=us','歌曲1 50 美服'):
            self.assertEqual(self.ask(text).status,'invalid_arguments',text)

    def test_explicit_name_keeps_numbers_spaces_dashes_and_region_words(self):  # A19
        self.fx.table_rows['jp']['MasterText'][1].update(japanese='GO en 50-50 37', simplifiedChinese='GO en 50-50 37')
        answer = self.ask('50 歌名="GO en 50-50 37" jp')
        self.assertEqual(answer.status,'success',answer.text)
        self.assertFalse(answer.request.extra_queries)
        self.assertEqual(answer.boards[0].song.music_id,'101')
        self.assertEqual(answer.ranks,tuple(range(40,61)))
        numeric_name = self.ask('歌名="123"')
        self.assertEqual(numeric_name.boards[0].song.music_id,'103')
        self.assertEqual(numeric_name.request.mode,'trend')
        self.assertEqual(self.ask('123').status,'invalid_arguments')
        self.assertEqual(self.ask('歌曲ID=103 50-50').boards[0].song.music_id,'103')

    def test_ranked_queries_read_five_representative_ranks_and_record_boards(self):  # A20/A21
        with patch.object(self.source.history,'read',wraps=self.source.history.read) as read:
            answer = self.ask('1-100')
        self.assertEqual(read.call_count,3)
        self.assertEqual(answer.history_ranks,(1,26,51,75,100))
        self.assertEqual(len(answer.ranks),100)
        self.assertEqual(self.source.history.read(answer.event,answer.boards[0].song,(50,)).total,1)
        self.assertEqual(sum(u.endswith('/ranking') for u in self.fx.calls),3)

    def test_command_labels_preserve_modes_and_target(self):  # A27
        for query in ('hk 歌曲二 100','歌曲2 50-50','歌曲1 T1,T10,T37','歌名="长歌名 37" 仅数值'):
            answer = self.ask(query)
            label = QuerySpec('event_cutoff',cutoff_request=answer.request).command_label()
            again = self.ask(label.removeprefix('查榜线 '))
            self.assertEqual(answer.request,again.request,label)

    def test_default_three_songs_are_one_image_even_without_history(self):  # A01/A22
        for answer in (self.ask(), replace(self.ask(), histories=())):
            pages = render_cutoff(answer, preview_label='合成离线测试')
            self.assertEqual(len(pages),1)
            for number in range(1,4):
                self.assertIn(f'歌曲{number}',pages[0].text)
            with Image.open(io.BytesIO(pages[0].image)) as image:
                self.assertLessEqual(image.width*image.height,12_000_000)
            self.assertLessEqual(len(pages[0].image),3_000_000)

    def test_rank_table_does_not_enter_trend_renderer(self):  # A04/A05/A20
        with patch('ournotes_bot.rendering.cutoff_trends.render_trends',side_effect=AssertionError('trend drawing')):
            pages = render_cutoff(self.ask('歌曲2 50'))
        self.assertEqual(len(pages),1)
        self.assertIn('歌曲2',pages[0].text)
        self.assertNotIn('歌曲1',pages[0].text)
        for rank in range(40,61):
            self.assertIn(f'T{rank}：',pages[0].text)

    def test_full_100_rows_rank_pages_complete_before_budget(self):  # A09/A24
        import re
        answer = self.ask('1-100')
        pages = render_cutoff(answer)
        self.assertLessEqual(len(pages),5)
        rows = []
        for page in pages:
            per_rank = re.findall(r'^T(\d+)：',page.text,re.M)
            rows += list(map(int,per_rank))
            for line in page.text.splitlines():
                if re.match(r'^T\d+：',line):
                    self.assertEqual(len(line.split('：',1)[1].split('｜')),3)
            self.assertLessEqual(len(page.text),1800)
            for number in range(1,4):
                self.assertIn(f'歌曲{number}',page.text)
        self.assertEqual(rows,list(range(1,101)))
        expanded = _expand_replies([PreparedReply(answer.text,pages=tuple(PreparedReply(p.text,p.image) for p in pages))])
        self.assertEqual(len(expanded),len(pages))
        self.assertTrue(all(p.image for p in expanded))

    def test_render_failure_or_capacity_never_sends_a_truncated_table(self):  # A24
        from ournotes_bot.platforms.qq.qq import _render_prepared
        answer = self.ask('1-100')
        result = SimpleNamespace(cutoff=answer,catalog=None)
        with patch('ournotes_bot.rendering.ranking_layout.render',side_effect=ValueError('render')):
            prepared = _render_prepared(answer.text,result,self.repo,'zh')
        expanded = _expand_replies([prepared])
        self.assertLessEqual(len(expanded),5)
        self.assertIn('T100：','\n'.join(p.text for p in expanded + list(getattr(expanded[-1].context,"remaining",()))))
        self.assertTrue(all(len(p.text)<=1800 for p in expanded))
        pages = render_cutoff(answer)
        batch = [PreparedReply(answer.text,pages=tuple(PreparedReply(p.text,p.image) for p in pages))] + [PreparedReply('other')]*4
        constrained = _expand_replies(batch)
        self.assertEqual(len(constrained),5)
        self.assertTrue(constrained[0].image)
        self.assertTrue(constrained[-1].context.remaining)
        self.assertEqual(sum(p.text=='other' for p in constrained[-1].context.remaining),4)

    def test_names_and_equivalent_repeated_selectors_match_actual_identity(self):  # A17/A19
        with patch('ournotes_bot.query.entity_lexicon._aliases',return_value={'song':{'同曲别名':'夢我夢中'}}):
            answer = self.ask('歌曲1 歌名="夢我夢中" 歌名="同曲别名" 歌曲ID=101 50')
        self.assertEqual(answer.status,'success',answer.text)
        self.assertEqual(self.ask('歌曲1 歌名="长歌名 37"').status,'invalid_arguments')
        self.assertEqual(self.ask('非本期歌曲').status,'empty')

    def test_song_number_selects_challenge_identity_when_music_id_is_repeated(self):  # A15/A17
        import json
        original = self.source.transport
        def repeated(url, timeout):
            raw, headers = original(url, timeout)
            if url.endswith('/jp/events/current'):
                payload = json.loads(raw)
                payload['challengeRankings'][1]['musicId'] = '101'
                raw = encoded(payload)
            return raw, headers
        self.source.transport = repeated
        self.fx.table_rows['jp']['MasterChallengeMusic'][1]['liveMusicId'] = 101
        answer = self.ask('歌曲2 50-50')
        self.assertEqual(answer.status, 'success', answer.text)
        self.assertEqual([(b.song.challenge_id, b.song.music_id) for b in answer.boards], [('2', '101')])
        self.assertIn('歌曲2 · 夢我夢中', answer.text)
        self.assertEqual(sum(u.endswith('/ranking') for u in self.fx.calls), 1)
        first = self.ask('歌曲1 歌曲ID=101 歌名="夢我夢中" 50-50')
        self.assertEqual([b.song.challenge_id for b in first.boards], ['1'])
        for query in ('歌曲ID=101', '夢我夢中'):
            ambiguous = self.ask(query)
            self.assertEqual(ambiguous.status, 'ambiguous', query)
            self.assertIn('歌曲编号', ambiguous.text)
        overview = self.ask()
        self.assertEqual([b.song.challenge_id for b in overview.boards], ['1', '2', '3'])
        self.assertEqual([overview.song_label(b).split(' · ')[0] for b in overview.boards], ['歌曲1', '歌曲2', '歌曲3'])

    def test_supported_range_separators_and_invalid_chinese_numbers(self):  # A07/A18
        expected=self.ask('歌曲2 20-40').request
        for text in ('20—40','20 - 40','20～40','20到40','Ｔ２０－Ｔ４０','２０　～　４０'):
            self.assertEqual(self.ask(text+' 歌曲二').request,expected,text)
        for text in ('歌曲一二','歌曲十十','歌名="未闭合'):
            self.assertEqual(self.ask(text).status,'invalid_arguments',text)

    def test_local_natural_entry_shares_new_semantics_without_model(self):  # A26/A28
        from ournotes_bot.ai_query import AIQueryParser
        from ournotes_bot.config import Settings
        from ournotes_bot.data import SongRepository
        repo = SongRepository('https://offline.invalid',Path(self.tmp.name)/'main.json')
        repo.event_cutoffs = self.source
        parser = AIQueryParser(Settings('', '', 'https://offline.invalid',repo.cache_file,6,'','','https://offline.invalid',0))
        expected = self.ask('歌曲一 hk 100')
        with patch.object(parser,'_request',side_effect=AssertionError('model called')) as model:
            text,result = parser.answer_with_plan('/问 查榜线 100 hk 歌曲一',repo)
            self.assertEqual(result.cutoff.request,expected.request)
            self.assertEqual(text,expected.text)
            self.assertIn('不同服务器',parser.answer('/问 查榜线 hk jp 100',repo))
            model.assert_not_called()

    def test_closed_song_and_holes_do_not_move_ordinal_or_rank(self):  # A15/A23
        import json
        original = self.source.transport
        def closed(url, timeout):
            raw,headers = original(url,timeout)
            if url.endswith('/events/current'):
                payload = json.loads(raw)
                payload['challengeRankings'][1]['rankingEnabled']=False
                raw=encoded(payload)
            return raw,headers
        self.source.transport=closed
        self.fx.players=[{'score':5},None,{'score':0}]+self.fx.players[3:]
        answer=self.ask('1-3')
        self.assertEqual([b.song.music_id for b in answer.boards],['101','102','103'])
        self.assertEqual(answer.boards[0].score(2),None)
        self.assertEqual(answer.boards[0].score(3),0)
        self.assertFalse(answer.boards[1].scores)
        self.assertIn('歌曲3',self.ask('歌曲3 3-3').text)
        pages=render_cutoff(answer)
        self.assertEqual(len(pages),1)
        self.assertIn('T2：暂无数据',pages[0].text)
        self.assertIn('T3：0',pages[0].text)

    def test_non_three_song_activity_is_complete_and_not_reindexed(self):  # A15
        answer=self.ask()
        songs=tuple(replace(answer.event.songs[i%3],challenge_id=str(i+1),music_id=str(201+i),title=f'合成歌曲 {i+1}') for i in range(5))
        answer=replace(answer,event=replace(answer.event,songs=songs),boards=tuple(replace(answer.boards[i%3],song=s) for i,s in enumerate(songs)),histories=())
        pages=render_cutoff(answer)
        text='\n'.join(p.text for p in pages)
        for n in range(1,6):
            self.assertIn(f'歌曲{n} · 合成歌曲 {n}',text)

    def test_flat_large_integer_overview_ticks_have_readable_signs(self):  # A22
        from ournotes_bot.rendering import cutoff_trends
        from ournotes_bot.sources.cutoff_history import HistoryPoint, HistoryView
        answer = self.ask()
        giant = 10**15
        points = tuple(HistoryPoint(answer.boards[0].fetched_ms + i * 300000, (giant,) * 5,
                                    answer.boards[0].received_ms + i * 300000) for i in range(2))
        # Explicitly include the synthetic large values to keep testing axis geometry.
        answer = replace(answer, score_display_limit=giant,
                         histories=tuple((b.song.challenge_id, HistoryView(points)) for b in answer.boards))
        with patch.object(cutoff_trends, '_text', wraps=cutoff_trends._text) as draw:
            pages = render_cutoff(answer)
        rendered = '\n'.join(line for call in draw.call_args_list for line in call.args[1])
        self.assertEqual(len(pages), 1)
        self.assertIn('-1', rendered)
        self.assertIn('+0', rendered)
        self.assertNotIn('+-', rendered)
        self.assertIn('T1 / T2 / T3 / T10 / T100', rendered)


class TotalRankingOfflineTests(unittest.TestCase):
    """Normalized adapter fixtures only; no claim about an upstream HTTP schema."""
    def setUp(self):
        from ournotes_bot.sources.total_rankings import TotalRankingHistory, TotalRankingRepository
        self.case=CutoffQueryV2Tests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.event=self.case.halloween_event()
        self.now=self.event.start_ms/1000+300
        self.history=TotalRankingHistory(Path(self.case.tmp.name)/'totals.sqlite3','tracker',
                                        min_free_mb=0,clock=lambda:self.now)
        self.calls=0
        def fetch(event,deadline):
            self.calls+=1
            return self.snapshot()
        self.totals=TotalRankingRepository(self.history,fetch=fetch,clock=lambda:self.now)
        self.case.source.total_rankings=self.totals

    def snapshot(self):
        from ournotes_bot.sources.total_rankings import TotalRankingSnapshot, event_key
        return TotalRankingSnapshot(event_key(self.event),'tracker',tuple(60_000_000-i*12345 for i in range(100)),
                                    int(self.now*1000)-1000,int(self.now*1000),
                                    player_ids=tuple(str(11000000001+i) for i in range(100)),
                                    player_names=tuple('合成玩家'+str(i+1) for i in range(100)))

    def ask(self,query='总榜'):
        with patch.object(self.case.source,'event',return_value=self.event):
            return self.case.ask(query)

    def test_disconnected_source_never_creates_files_or_calls_adapter(self):
        self.totals.fetch=None
        self.assertIn('暂未接入',self.ask().text)
        self.assertEqual(self.totals.sample(self.event,0),'not_connected')
        self.assertFalse(self.history.path.exists())
        self.assertEqual(self.calls,0)

    def test_current_queries_do_not_write_history_and_accept_large_totals(self):
        import sqlite3
        answer=self.ask()
        self.assertIn('60000000',answer.text)
        self.assertNotIn('异常分数',answer.text)
        self.assertFalse(answer.boards)
        self.assertEqual(answer.total.player_id(1),'11000000001')
        with sqlite3.connect(self.history.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM samples').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM current_totals').fetchone()[0],1)
        with patch.object(self.history,'read',side_effect=AssertionError('numeric history read')):
            self.assertFalse(render_cutoff(self.ask('总榜 100 仅数值')))
        self.assertEqual(self.calls,1)

    def test_full_overview_splits_total_and_four_songs_without_duplicate_text(self):
        from ournotes_bot.sources.moenotes_events import BoardSnapshot
        boards=tuple(BoardSnapshot(s,(10_000_000,)*100) for s in self.event.songs)
        with patch.object(self.case.source,'boards',return_value=boards):
            answer=self.ask('')
            pages=render_cutoff(answer,preview_label='合成离线验收')
            self.assertEqual(len(pages),3)
            self.assertIn('60000000',pages[0].text)
            self.assertNotIn('60000000',pages[1].text)
            self.assertTrue(all(len(p.text)<1800 for p in pages))
            self.assertFalse(self.ask('单曲').total)

    def test_total_only_event_can_sample_without_song_history(self):
        from ournotes_bot.sources.cutoff_sampler import HistorySampler
        self.event=replace(self.event,event_id='99',songs=(),total_ranking_enabled=True)
        self.case.source.history=None
        with patch.object(self.case.source,'event',return_value=self.event):
            sampler=HistorySampler(self.case.source,servers=('jp',),clock=lambda:self.now)
            self.assertEqual(sampler.sample_once()['new_valid_observations'],1)
            self.assertEqual(sampler.sample_once()['new_valid_observations'],0)
            self.now+=300
            self.assertEqual(sampler.sample_once()['new_valid_observations'],1)
        self.assertEqual(len(self.history.read(self.event,(1,)).points),2)
        self.assertEqual(self.history.events('jp')[0].event_id,'99')
        self.assertTrue(self.ask('').total)

    def test_validation_rejects_wrong_identity_source_precision_and_position(self):
        from ournotes_bot.sources.total_rankings import validate
        from ournotes_bot.sources.moenotes_events import SourceError
        base=self.snapshot()
        for changed in (replace(base,event_key='wrong'),replace(base,source='haneoka'),
                        replace(base,scores=(True,)),replace(base,scores=(2**53,)),
                        replace(base,scores=(0,),player_ids=base.player_ids),
                        replace(base,scores=(None,),player_ids=('11000000001',),player_names=('name',))):
            with self.assertRaises(SourceError):validate(self.event,changed,'tracker')
        self.assertNotEqual(base.event_key,__import__('ournotes_bot.sources.total_rankings',fromlist=['event_key']).event_key(
            replace(self.event,songs=self.event.songs[:3])))

    def test_restart_cache_fallback_expiry_conflict_and_recovery(self):
        from ournotes_bot.sources.total_rankings import TotalRankingRepository
        from ournotes_bot.sources.moenotes_events import SourceError
        board=self.totals.capture(self.event,0)
        self.now+=61
        restored=TotalRankingRepository(self.history,fetch=Mock(side_effect=SourceError('upstream')),clock=lambda:self.now)
        self.assertEqual(restored.capture(self.event,0).score(1),60_000_000)
        self.now+=600
        self.assertIsNone(restored.capture(self.event,0).score(1))
        self.now+=61
        self.totals.capture(self.event,0)
        cached=self.totals.current[board.event_key]
        self.now+=61
        self.totals.fetch=lambda e,d:replace(cached,scores=(99,)*100)
        self.assertEqual(self.totals.capture(self.event,0).quality,'conflict')
        self.assertEqual(self.totals.sample(self.event,0),'conflict')
        self.assertFalse(self.history.read(self.event,(1,)).points)
        self.now+=61
        self.totals.fetch=lambda e,d:self.snapshot()
        self.assertEqual(self.totals.sample(self.event,0),'valid')
        self.assertTrue(self.history.read(self.event,(1,)).points)

    def test_sampling_gaps_old_history_and_independent_database(self):
        import sqlite3
        self.assertEqual(self.totals.sample(self.event,0),'valid')
        self.now+=300
        self.history.failure(self.event,'offline')
        self.now+=300
        self.assertEqual(self.totals.sample(self.event,0),'valid')
        view=self.history.read(self.event,(1,100))
        self.assertTrue(view.points[1].break_before)
        answer=self.ask('id4 总榜')
        self.assertEqual(answer.total.score(1),60_000_000)
        self.assertIn('最后有效观测',answer.text)
        with patch.object(self.case.source,'event',side_effect=AssertionError('historical network')):
            self.assertTrue(self.case.ask('id4 总榜').total)
        with sqlite3.connect(self.history.path) as db:
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name='observations'").fetchone())

    def test_same_time_player_change_is_a_conflict_in_cache_and_history(self):
        from ournotes_bot.sources.total_rankings import TotalRankingRepository
        self.assertEqual(self.totals.sample(self.event,0),'valid')
        original=self.history.cached(self.event)
        changed=replace(original,player_ids=('11999999999',*original.player_ids[1:]))
        self.now+=61
        restarted=TotalRankingRepository(self.history,fetch=lambda e,d:changed,clock=lambda:self.now)
        self.assertEqual(restarted.capture(self.event,0).quality,'conflict')
        self.assertEqual(restarted.sample(self.event,0),'conflict')
        self.assertFalse(self.history.read(self.event,(1,)).points)
        self.assertIsNone(self.history.latest(self.event))

    def test_history_keeps_same_score_player_conflict_as_distinct_raw_rows(self):
        import sqlite3
        original=replace(self.snapshot(),received_ms=int(self.now*1000))
        self.assertEqual(self.history.record(self.event,original),'valid')
        changed=replace(original,player_names=('另一位合成玩家',*original.player_names[1:]))
        self.assertEqual(self.history.record(self.event,changed),'conflict')
        self.assertFalse(self.history.read(self.event,(1,)).points)
        with sqlite3.connect(self.history.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM samples').fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM samples WHERE quality='conflict'").fetchone()[0],2)

    def test_sampler_foreground_yield_marks_total_gap_without_fetching(self):
        from ournotes_bot.sources.cutoff_sampler import HistorySampler
        self.case.source.history=None
        with patch.object(self.case.source,'event',return_value=self.event):
            sampler=HistorySampler(self.case.source,servers=('jp',),clock=lambda:self.now)
            sampler.sample_once()
            self.now+=300
            with self.case.source.foreground():sampler.sample_once()
            self.assertEqual(self.calls,1)
            self.now+=300
            sampler.sample_once()
        self.assertTrue(self.history.read(self.event,(1,)).points[1].break_before)

    def test_full_range_pagination_keeps_all_ranks(self):
        pages=render_cutoff(self.ask('总榜 1-100'))
        text='\n'.join(p.text for p in pages)
        for rank in range(1,101):self.assertIn(f'T{rank}：',text)
        self.assertTrue(all(len(p.text)<1800 for p in pages))

    @patch('ournotes_bot.sources.haneoka.game_assets.asset_bytes',return_value=None)
    def test_total_uses_real_command_and_reply_renderer_with_captured_fallback(self, _game_art):
        from ournotes_bot.commands import resolve_command
        from ournotes_bot.application.replies import render_prepared
        from ournotes_bot.natural_query.query_agent import QueryAgent
        from ournotes_bot.structured_query import QueryResult
        with patch.object(self.case.source,'event',return_value=self.event):
            result=resolve_command('/查榜线 总榜 100',self.case.repo)
        with patch('ournotes_bot.rendering.event_cutoff_visuals.load_artwork',return_value={}):
            prepared=render_prepared(result.cutoff.text,result,self.case.repo,'zh')
        self.assertTrue(prepared.image)
        self.assertIn('58777845',prepared.text)
        self.assertIn('综合分数榜',prepared.text)
        self.assertEqual(self.calls,1)
        query_result=QueryResult(QuerySpec(intent='event_cutoff'),cutoff=result.cutoff)
        self.assertTrue(QueryAgent._has_useful_result(query_result))
        with patch('ournotes_bot.rendering.ranking_layout.render',side_effect=ValueError('synthetic render failure')):
            failed=render_prepared(result.cutoff.text,result,self.case.repo,'zh')
        self.assertFalse(failed.image)
        self.assertEqual(failed.text,result.cutoff.text)
        self.assertEqual(self.calls,1)
        from ournotes_bot.sources.moenotes_events import BoardSnapshot
        boards=tuple(BoardSnapshot(s,(10_000_000,)*100) for s in self.event.songs)
        with patch.object(self.case.source,'event',return_value=self.event),patch.object(self.case.source,'boards',return_value=boards):
            overview=resolve_command('/查榜线',self.case.repo)
        with patch('ournotes_bot.rendering.event_cutoff_visuals.load_artwork',return_value={}):
            prepared=render_prepared(overview.cutoff.text,overview,self.case.repo,'zh')
        self.assertTrue(prepared.pages)
        self.assertIn('60000000',prepared.pages[0].text)
        self.assertNotIn('60000000',prepared.pages[1].text)

    def test_concurrent_capture_coalesces_and_corrupt_cache_is_replaced(self):
        import sqlite3
        from concurrent.futures import ThreadPoolExecutor
        from ournotes_bot.sources.total_rankings import TotalRankingRepository
        with ThreadPoolExecutor(max_workers=4) as pool:
            boards=list(pool.map(lambda _:self.totals.capture(self.event,0),range(4)))
        self.assertEqual(self.calls,1)
        self.assertTrue(all(b.scores==boards[0].scores for b in boards))
        with sqlite3.connect(self.history.path) as db:
            db.execute("UPDATE current_totals SET payload='{}'")
        restored=TotalRankingRepository(self.history,fetch=self.totals.fetch,clock=lambda:self.now)
        self.assertEqual(restored.capture(self.event,0).score(1),60_000_000)
        self.assertEqual(self.calls,2)

    def test_foreign_legacy_database_and_low_disk_are_not_modified(self):
        import sqlite3
        from ournotes_bot.sources.total_rankings import TotalRankingHistory
        other=Path(self.case.tmp.name)/'legacy.sqlite3'
        with sqlite3.connect(other) as db:
            db.execute('CREATE TABLE observations (score INTEGER)')
            db.execute('INSERT INTO observations VALUES (123)')
            db.execute('PRAGMA user_version=7')
        before=other.read_bytes()
        foreign=TotalRankingHistory(other,'tracker',min_free_mb=0,clock=lambda:self.now)
        raw=replace(self.snapshot(),received_ms=int(self.now*1000))
        self.assertEqual(foreign.record(self.event,raw),'storage_error')
        self.assertEqual(other.read_bytes(),before)
        self.history.min_free_bytes=1
        self.history.disk_usage=lambda _:SimpleNamespace(free=0)
        self.assertEqual(self.history.record(self.event,raw),'low_disk')
        self.assertFalse(self.history.path.exists())

    def test_other_source_cannot_write_existing_total_database(self):
        from ournotes_bot.sources.total_rankings import TotalRankingHistory
        self.totals.sample(self.event,0)
        before=self.history.path.read_bytes()
        foreign=TotalRankingHistory(self.history.path,'haneoka',min_free_mb=0,clock=lambda:self.now)
        raw=replace(self.snapshot(),source='haneoka',received_ms=int(self.now*1000))
        self.assertEqual(foreign.record(replace(self.event,source='haneoka'),raw),'storage_error')
        self.assertEqual(self.history.path.read_bytes(),before)

    def test_clock_quarantine_and_recovery_preserve_raw_without_plotting(self):
        import sqlite3,json
        base=self.snapshot()
        self.totals.fetch=lambda e,d:replace(base,fetched_ms=base.fetched_ms+120000,server_ms=base.server_ms+120000)
        self.assertEqual(self.totals.sample(self.event,0),'clock_unknown')
        self.assertFalse(self.history.read(self.event,(1,)).points)
        with sqlite3.connect(self.history.path) as db:
            payload,state=db.execute('SELECT payload,quality FROM samples').fetchone()
            self.assertEqual(json.loads(payload)['scores'][0],60_000_000)
            self.assertEqual(state,'clock_unknown')
        self.now+=300
        self.totals.fetch=lambda e,d:self.snapshot()
        self.assertEqual(self.totals.sample(self.event,0),'valid')
        view=self.history.read(self.event,(1,))
        self.assertEqual(len(view.points),1)
        self.assertTrue(view.points[0].break_before)


if __name__ == '__main__':
    unittest.main()

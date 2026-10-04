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
from unittest.mock import patch

from PIL import Image

from ournotes_bot.query.event_cutoff_query import execute_cutoff, parse_cutoff
from ournotes_bot.sources.moenotes_events import EventCutoffRepository
from ournotes_bot.sources.cutoff_history import CutoffHistory
from ournotes_bot.structured_query import QuerySpec
from ournotes_bot.rendering.event_cutoff_visuals import render_cutoff
from ournotes_bot.platforms.qq.qq import PreparedReply, _expand_replies
from test_event_cutoffs import PublicFixture, encoded


class CutoffQueryV2Tests(unittest.TestCase):
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

    def test_ranked_queries_skip_history_reads_but_still_record_boards(self):  # A20/A21
        with patch.object(self.source.history,'read',side_effect=AssertionError('history read')):
            answer = self.ask('1-100')
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
            self.assertLessEqual(len(pages[0].image),1_500_000)

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
        with patch('ournotes_bot.rendering.event_cutoff_visuals.render_cutoff',side_effect=ValueError('render')):
            prepared = _render_prepared(answer.text,result,self.repo,'zh')
        expanded = _expand_replies([prepared])
        self.assertLessEqual(len(expanded),5)
        self.assertIn('T100：','\n'.join(p.text for p in expanded))
        self.assertTrue(all(len(p.text)<=1800 for p in expanded))
        pages = render_cutoff(answer)
        batch = [PreparedReply(answer.text,pages=tuple(PreparedReply(p.text,p.image) for p in pages))] + [PreparedReply('other')]*4
        constrained = _expand_replies(batch)
        self.assertEqual(len(constrained),5)
        self.assertIn('无法完整发送',constrained[0].text)
        self.assertFalse(constrained[0].image)

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
        answer = replace(answer, histories=tuple((b.song.challenge_id, HistoryView(points)) for b in answer.boards))
        with patch.object(cutoff_trends, '_text', wraps=cutoff_trends._text) as draw:
            pages = render_cutoff(answer)
        rendered = '\n'.join(line for call in draw.call_args_list for line in call.args[1])
        self.assertEqual(len(pages), 1)
        self.assertIn('-1', rendered)
        self.assertIn('+0', rendered)
        self.assertNotIn('+-', rendered)
        self.assertIn('T1 / T2 / T3 / T10 / T100', rendered)


if __name__ == '__main__':
    unittest.main()

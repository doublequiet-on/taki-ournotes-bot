# L3
# Input: synthetic public ranking rows, temporary current/history caches and captured answers.
# Output: player ID alignment, cache compatibility, privacy and image/text delivery regressions.
# Pos: Tests / event cutoff player IDs; see L2.md.
# Effects: offline temporary files/Pillow only; no production network, identities or QQ sends.
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from test_event_cutoffs import PublicFixture
from ournotes_bot.sources.moenotes_events import EventCutoffRepository
from ournotes_bot.sources.cutoff_history import CutoffHistory
from ournotes_bot.query.event_cutoff_query import execute_cutoff, parse_cutoff
from ournotes_bot.rendering.event_cutoff_visuals import render_cutoff
from ournotes_bot.platforms.qq.qq import PreparedReply, _expand_replies


class PlayerIdTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.fx = PublicFixture()
        self.fx.players = [dict(p, playerData={'id':str(11000000001+i), 'profileId':'99999999999',
                                             'name':f'玩家{i+1}'}, highScoreDeck={'private':'deck'})
                           for i,p in enumerate(self.fx.players)]
        self.source = EventCutoffRepository(self.root/'current',transport=self.fx,
                                           clock=lambda:self.fx.now,monotonic=lambda:self.fx.now)
        self.source.history = CutoffHistory(self.root/'history.sqlite3',min_free_mb=0)
        self.repo = SimpleNamespace(event_cutoffs=self.source)

    def ask(self, query=''):
        return execute_cutoff(parse_cutoff(query),self.repo)

    def test_id_is_same_response_position_not_sorted_or_shifted(self):
        self.fx.players = [{'score':123,'playerData':{'id':'000123'}}, None,
                           {'score':0,'playerData':{'id':9007199254740993}},
                           {'score':123,'playerData':{'id':'12345678901234567890'}},
                           {'score':True,'playerData':{'id':'456'}}]
        answer = self.ask('歌曲1 1-5')
        board = answer.boards[0]
        self.assertEqual(board.player_ids,('000123',None,'9007199254740993','12345678901234567890','456'))
        self.assertEqual([board.player_id(i) for i in range(1,7)],['000123',None,'9007199254740993','12345678901234567890',None,None])
        self.assertIn('T1：123/000123',answer.text)
        self.assertIn('T2：暂无数据/未获取',answer.text)
        self.assertIn('T3：0/9007199254740993',answer.text)

    def test_invalid_ids_do_not_invalidate_scores_or_fall_back_to_other_fields(self):
        bad = (True, False, 0, -1, 1.5, '0', '-1', '１２３', '1e3', ' 123', '9'*21, None, {}, [])
        self.fx.players = [{'score':i,'playerData':{'id':value,'profileId':'123'}} for i,value in enumerate(bad)]
        board = self.ask('歌曲1').boards[0]
        self.assertEqual(board.scores,tuple(range(len(bad))))
        self.assertEqual(board.player_ids,(None,)*len(bad))

    def test_current_cache_reload_keeps_ids_but_history_has_only_scores(self):
        answer = self.ask('歌曲1 50-50')
        body = json.loads(self.source._path('board:jp:1:1').read_text(encoding='utf-8'))['payload']
        self.assertEqual(set(body),{'scores','player_ids','player_names'})
        self.assertEqual(body['player_ids'][49],'11000000050')
        serialized = json.dumps(body)
        self.assertEqual(body['player_names'][49],'玩家50')
        for unwanted in ('playerData','profileId','highScoreDeck','private'):
            self.assertNotIn(unwanted,serialized)
        reloaded = EventCutoffRepository(self.source.cache_dir,transport=Mock(side_effect=AssertionError('network')),
                                        clock=lambda:self.fx.now,monotonic=lambda:self.fx.now)
        self.repo.event_cutoffs = reloaded
        self.assertEqual(self.ask('歌曲1 50-50').text,answer.text)
        self.assertNotIn(b'11000000050',(self.root/'history.sqlite3').read_bytes())
        self.assertNotIn('玩家50'.encode(),(self.root/'history.sqlite3').read_bytes())

    def test_old_score_only_cache_remains_usable_with_unknown_id(self):
        self.ask('歌曲1')
        path = self.source._path('board:jp:1:1')
        body = json.loads(path.read_text(encoding='utf-8')); del body['payload']['player_ids']; del body['payload']['player_names']
        path.write_text(json.dumps(body))
        self.repo.event_cutoffs = EventCutoffRepository(self.source.cache_dir,transport=Mock(side_effect=AssertionError('network')),
                                                      clock=lambda:self.fx.now,monotonic=lambda:self.fx.now)
        answer = self.ask('歌曲1 50-50')
        self.assertEqual(answer.boards[0].score(50),self.fx.players[49]['score'])
        self.assertIsNone(answer.boards[0].player_id(50))
        self.assertIsNone(answer.boards[0].player_name(50))
        self.assertIn('/未获取',answer.text)

    def test_expired_unknown_position_and_history_conflict_hide_ids(self):
        answer = self.ask('歌曲1 50-50'); board = answer.boards[0]
        entry = self.source._entries['board:jp:1:1']
        for headers in (dict(entry.headers, **{'x-position-source':'unknown'}),
                        dict(entry.headers, **{'x-fetched-at':str(int(self.fx.now*1000)-700000)})):
            snapshot = self.source._snapshot(answer.event,board.song,replace(entry,headers=headers))
            self.assertIsNone(snapshot.score(50))
            self.assertIsNone(snapshot.player_id(50))
        with patch.object(self.source.history,'record',return_value='conflict'):
            conflict = self.ask('歌曲1 50-50').boards[0]
        self.assertIsNone(conflict.player_id(50))

    def test_misaligned_or_invalid_id_cache_is_refetched(self):
        self.ask('歌曲1')
        path = self.source._path('board:jp:1:1')
        for ids in (['123'], ['bad']*100):
            body=json.loads(path.read_text(encoding='utf-8')); body['payload']['player_ids']=ids
            path.write_text(json.dumps(body))
            self.repo.event_cutoffs=EventCutoffRepository(self.source.cache_dir,transport=self.fx,
                                                        clock=lambda:self.fx.now,monotonic=lambda:self.fx.now)
            before=sum(u.endswith('/ranking') for u in self.fx.calls)
            answer=self.ask('歌曲1 50-50')
            self.assertEqual(answer.boards[0].player_id(50),'11000000050')
            self.assertEqual(sum(u.endswith('/ranking') for u in self.fx.calls),before+1)

    def test_new_snapshot_and_failure_never_mix_captured_owner_or_other_server(self):
        from ournotes_bot.sources.moenotes_events import SourceError
        original=self.ask('歌曲1 50-50')
        self.fx.now+=61
        self.fx.players[49]['playerData']['id']='22000000050'
        self.fx.players[49]['playerData']['name']='新用户名'
        current=self.ask('歌曲1 50-50')
        self.assertEqual(original.boards[0].player_id(50),'11000000050')
        self.assertEqual(current.boards[0].player_id(50),'22000000050')
        self.assertEqual(original.boards[0].player_name(50),'玩家50')
        self.assertEqual(current.boards[0].player_name(50),'新用户名')
        self.fx.now+=61
        self.fx.errors['/jp/events/1/challenges/1/ranking']=SourceError('network')
        fallback=self.ask('歌曲1 50-50')
        self.assertEqual(fallback.boards[0].player_id(50),'22000000050')
        self.assertIn('旧快照',fallback.text)
        self.assertEqual(fallback.boards[0].player_name(50),'新用户名')
        for server in ('hk','kr','en'):
            self.assertEqual(self.ask(server+' 歌曲1 50-50').boards[0].player_id(50),'22000000050')

    def test_all_current_views_render_complete_ids_from_captured_rows(self):
        for query in ('','歌曲1','歌曲2 50-50','50','1-100'):
            answer = self.ask(query)
            with patch('ournotes_bot.rendering.event_cutoff_visuals._text', wraps=__import__(
                    'ournotes_bot.rendering.event_cutoff_visuals',fromlist=['_text'])._text) as draw:
                pages = render_cutoff(answer)
            self.assertTrue(pages,query)
            self.assertTrue(all(len(p.text)<=1800 for p in pages),query)
            expanded = _expand_replies([PreparedReply(answer.text,pages=tuple(PreparedReply(p.text,p.image) for p in pages))])
            self.assertLessEqual(len(expanded),5,query)
            self.assertIn('T100：' if query=='1-100' else f'T{answer.ranks[0]}：','\n'.join(p.text for p in expanded))
            self.assertIn(answer.boards[0].player_id(answer.ranks[0]),'\n'.join(p.text for p in pages))
            for page in pages:
                with Image.open(io.BytesIO(page.image)) as img:
                    self.assertLessEqual(img.width*img.height,12_000_000)
            if answer.request.mode!='trend':
                rendered = '\n'.join(line for call in draw.call_args_list for line in call.args[1])
                self.assertIn('ID：'+answer.boards[0].player_id(answer.ranks[0]),rendered)

    def test_full_table_render_failure_keeps_all_ids_within_reply_budget(self):
        answer = self.ask('1-100')
        replies = _expand_replies([PreparedReply(answer.text,complete_text=True)])
        joined = '\n'.join(p.text for p in replies)
        self.assertLessEqual(len(replies),5)
        self.assertIn('T100：',joined)
        for row in self.fx.players:
            self.assertIn(row['playerData']['id'],joined)
            self.assertIn(row['playerData']['name'],joined)

    def test_names_are_independent_bounded_unicode_and_safe_for_table_text(self):
        self.fx.players=[{'score':0,'playerData':{'id':'123','name':'夏天 / "A｜B"'}},
                         {'score':1,'playerData':{'name':'  姓名\n第二行\u202e  '}},
                         {'score':2,'playerData':{'id':'456','name':False}},
                         {'score':3,'playerData':{'id':'789','name':'很'*81}},None]
        answer=self.ask('歌曲1 1-5'); b=answer.boards[0]
        self.assertEqual(b.player_name(1),'夏天 / "A｜B"')
        self.assertEqual(b.player_name(2),'姓名 第二行')
        self.assertIsNone(b.player_id(2))
        self.assertIsNone(b.player_name(3)); self.assertIsNone(b.player_name(4)); self.assertIsNone(b.player_name(5))
        self.assertIn('T1：0/123/'+json.dumps(b.player_name(1),ensure_ascii=False),answer.text)
        self.assertNotIn('\u202e',answer.text)
        for query in ('歌曲1','歌曲1 1-5'):
            result=self.ask(query)
            self.assertIn(b.player_name(1),result.text if result.request.mode=='trend' else json.loads(
                result.text.split('T1：0/123/',1)[1].splitlines()[0]))
        self.assertTrue(render_cutoff(answer))

    def test_old_numeric_id_cache_has_missing_name_and_current_images_draw_both(self):
        answer=self.ask('歌曲1 50-50')
        from ournotes_bot.rendering import event_cutoff_visuals,cutoff_trends
        for query,module in (('歌曲1 50-50',event_cutoff_visuals),('歌曲1',cutoff_trends),('',cutoff_trends)):
            with patch.object(module,'_text',wraps=module._text) as draw:
                render_cutoff(self.ask(query))
            text='\n'.join(line for call in draw.call_args_list for line in call.args[1])
            self.assertIn('ID：11000000050' if query=='歌曲1 50-50' else 'ID：11000000001',text)
            self.assertIn('玩家50' if query=='歌曲1 50-50' else '玩家1',text)
        path=self.source._path('board:jp:1:1'); body=json.loads(path.read_text(encoding='utf-8')); del body['payload']['player_names']
        path.write_text(json.dumps(body))
        self.repo.event_cutoffs=EventCutoffRepository(self.source.cache_dir,transport=Mock(side_effect=AssertionError('network')),
                                                    clock=lambda:self.fx.now,monotonic=lambda:self.fx.now)
        b=self.ask('歌曲1 50-50').boards[0]
        self.assertEqual(b.player_id(50),'11000000050'); self.assertIsNone(b.player_name(50))


if __name__=='__main__':
    unittest.main()

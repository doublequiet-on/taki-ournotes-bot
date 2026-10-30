"""Information queries share captured facts through rendering and continuation."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ournotes_bot.commands import handle_command, resolve_command
from ournotes_bot.data import SongRepository
from ournotes_bot.platforms.qq.qq import (
    DeliveryCursor, _expand_replies, _prepare_followup, _prepare_reply,
)
from ournotes_bot.query.contracts.info import InfoAnswer, InfoRow, InfoSchedule, InfoView, info_text
from ournotes_bot.query.continuation import parse_operation


class InfoCommandPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = SongRepository('', Path(self.tmp.name) / 'cache.json', data_source='haneoka')
        self.parser = Mock()
        self.views = (InfoView('资料详情', rows=(InfoRow('同一份已捕获资料', ('正文',)),)),)
        self.answer = InfoAnswer(info_text(self.views), views=self.views)
        assets = patch('ournotes_bot.sources.haneoka.game_assets.asset_bytes', return_value=None)
        assets.start()
        self.addCleanup(assets.stop)

    def test_three_commands_select_once_and_render_the_identical_result_without_ai(self):
        for command, module, function, field in (
            ('/查角色 tmr', 'character_query', 'execute_character', 'info'),
            ('/查卡池 12', 'gacha_query', 'execute_gacha', 'info'),
            ('/查活动 hk', 'activity_query', 'execute_activity', 'activity'),
            ('/查竞技场 hk MyGO!!!!! 榜单', 'public_queries', 'execute_arena', 'info'),
            ('/查玩家 9223372036854775807 jp', 'public_queries', 'execute_player', 'info'),
        ):
            with self.subTest(command=command), \
                 patch(f'ournotes_bot.query.{module}.{function}', return_value=self.answer) as execute, \
                 patch('ournotes_bot.rendering.info_layout.render_info',
                       return_value=(SimpleNamespace(text=self.answer.text, image=b'image'),)) as render:
                reply = _prepare_reply(command, self.repo, self.parser)
                self.assertEqual(reply.text, self.answer.text)
                self.assertEqual(reply.image, b'image')
                execute.assert_called_once()
                self.assertIs(render.call_args.args[0], self.answer)
        self.assertFalse(self.parser.mock_calls)

    def test_local_questions_reach_real_reply_pipeline_without_model(self):
        from ournotes_bot.ai_query import AIQueryParser
        from ournotes_bot.config import Settings
        parser = AIQueryParser(Settings('', '', '', self.repo.cache_file, 6, 'fixture-key'))
        for question, module, function in (
            ('/问 高松灯的生日是什么', 'character_query', 'execute_character'),
            ('/问 当前的卡池有哪些', 'gacha_query', 'execute_gacha'),
            ('/问 历史活动有哪些', 'activity_query', 'execute_activity'),
            ('/问 本期卡池什么时候结束呢', 'gacha_query', 'execute_gacha'),
            ('/问 这期活动详情', 'activity_query', 'execute_activity'),
            ('/问 本期卡池啥时候结束', 'gacha_query', 'execute_gacha'),
            ('/查 MyGO竞技场人数', 'public_queries', 'execute_arena'),
            ('/查 玩家 9223372036854775807 jp', 'public_queries', 'execute_player'),
            ('/查 本期活动奖励', 'activity_query', 'execute_activity'),
        ):
            with self.subTest(question=question), \
                 patch(f'ournotes_bot.query.{module}.{function}', return_value=self.answer) as execute, \
                 patch('ournotes_bot.rendering.info_layout.render_info', return_value=(SimpleNamespace(text=self.answer.text, image=b'image'),)) as render, \
                 patch.object(parser, '_request', side_effect=AssertionError('model called')), \
                 patch.object(parser._quota, 'reserve', side_effect=AssertionError('quota used')):
                reply = _prepare_reply(question, self.repo, parser)
                self.assertEqual(reply.image, b'image')
                self.assertEqual(reply.text, self.answer.text)
                execute.assert_called_once()
                self.assertIs(render.call_args.args[0], self.answer)

    def test_current_activity_time_is_one_text_reply_on_local_and_cached_paths(self):
        from ournotes_bot.ai_query import AIQueryParser
        from ournotes_bot.config import Settings
        parser = AIQueryParser(Settings('', '', '', self.repo.cache_file, 6, 'fixture-key'))
        view = InfoView('夹具活动', '日服 · 当前／最近活动', rows=(InfoRow('时间', schedule=InfoSchedule(
            '活动 ID 1', '进行中', '2026-10-21 15:00:00', '2026-10-30 14:59:59', 'UTC+09:00')),),
            footer=('资料来源：夹具', '来源覆盖提示保留'))
        answer = InfoAnswer('2026-10-30 14:59:59（UTC+09:00）\n记得及时清 CP\n来源覆盖提示保留', views=(view,))
        for question in ('/问 这期活动啥时候结束啊', '/问 本期活动什么时候结束？',
                         '/问 国际服这次活动结束时间是什么', '/问 国际服这期活动什么时候结束呀？', '/问 这期活动结束了吗',
                         '/问 现在活动还在吗', '/问 下期活动什么时候开始', '/问 下一次活动什么时候开始'):
            with self.subTest(question=question), \
                 patch('ournotes_bot.query.activity_query.execute_activity', return_value=answer) as execute, \
                 patch('ournotes_bot.application.replies.render_prepared', side_effect=AssertionError('image rendered')), \
                 patch.object(parser, '_request', side_effect=AssertionError('model called')), \
                 patch.object(parser._quota, 'reserve', side_effect=AssertionError('quota used')):
                for _ in range(2):
                    reply = _prepare_reply(question, self.repo, parser)
                    self.assertIsNone(reply.image)
                    self.assertFalse(reply.pages)
                    self.assertIn('2026-10-30 14:59:59（UTC+09:00）', reply.text)
                    self.assertIn('记得及时清 CP', reply.text)
                    self.assertIn('来源覆盖提示保留', reply.text)
                self.assertEqual(execute.call_count, 2)  # Cached intent still refreshes facts.

    def test_activity_short_reply_preserves_errors_missing_time_and_inactive_states(self):
        from ournotes_bot.application.replies import activity_time_reply
        for status, end in (('已结束', '2026-10-20 20:00:00'), ('尚未开始', '2026-10-30 20:00:00'),
                            ('进行中', '未知')):
            view = InfoView('夹具', rows=(InfoRow('时间', schedule=InfoSchedule('ID 1', status, '开始时间', end)),))
            answer = InfoAnswer(info_text((view,)), views=(view,))
            self.assertNotIn('清 CP', activity_time_reply(answer, 'end'))
            self.assertNotIn('清 CP', activity_time_reply(answer, 'start'))
        self.assertIn('暂未提供', activity_time_reply(answer, 'end'))
        self.assertEqual(activity_time_reply(InfoAnswer('来源暂不可用', 'data_unavailable'), 'end'), '来源暂不可用')
        self.assertIn('不能确定', activity_time_reply(InfoAnswer('缺少结构化时间'), 'end'))

    def test_calendar_deixis_does_not_discard_history_or_other_conditions(self):
        from ournotes_bot.natural_query.on_query import information_command
        for question in ('上期活动什么时候结束','2020年这期活动什么时候结束',
                         '国际服本期卡池什么时候结束','日服和国际服这期活动什么时候结束',
                         '这期活动和下期活动有什么不同','这期活动什么时候结束并预测榜线',
                         '这期活动啥时候结束啊并预测榜线','2020年这期活动什么时候结束啊',
                         '日服和国际服这期活动什么时候结束呢'):
            with self.subTest(question=question):
                self.assertIsNone(information_command(question,self.repo))
        self.assertEqual(information_command('国际服这期活动啥时候结束',self.repo),'/查活动 国际服 日程')

    def test_announced_time_expiry_reaches_delivery_gate_without_extension(self):
        from ournotes_bot.application.replies import bind_info_expiry, PreparedReply
        from ournotes_bot.commands import CommandResult
        result=CommandResult(('activity','日程',None),activity=InfoAnswer('已确认预告',evidence_expires_at=1010))
        with patch('ournotes_bot.application.replies.time.time',return_value=1000), \
             patch('ournotes_bot.application.replies.time.monotonic',return_value=50):
            reply=bind_info_expiry(PreparedReply('预告'),result)
            self.assertEqual(reply.evidence_deadline,60)
            self.assertEqual(bind_info_expiry(PreparedReply('预告',evidence_deadline=55),result).evidence_deadline,55)
        with patch('ournotes_bot.application.replies.time.time',return_value=1020), \
             patch('ournotes_bot.application.replies.time.monotonic',return_value=70):
            self.assertEqual(bind_info_expiry(PreparedReply('预告'),result).evidence_deadline,70)

    def test_group_mention_reserve_preserves_complete_text_and_private_budget(self):
        from ournotes_bot.application.replies import expand_replies,fallback_cursor,reserve_progress_slot,PreparedReply
        text='首段正文。'*1700+'最终字符'
        reply=PreparedReply(text,parse_origin='direct')
        pages=expand_replies([reply],text_limit=1500)
        self.assertEqual(len(pages),5)
        cursor=pages[0].context
        first=pages[0].text.split('\n结果 ',1)[0]
        self.assertEqual(first+''.join(p.text for p in cursor.remaining),text)
        self.assertTrue(all(len(p.text)<=1500 for p in pages))
        with_hint=reserve_progress_slot(pages,text_limit=1500)
        self.assertEqual(len(with_hint),4)
        first=with_hint[0].text.split('\n结果 ',1)[0]
        self.assertEqual(first+''.join(p.text for p in with_hint[0].context.remaining),text)
        fallback=fallback_cursor(reply,text_limit=1500)
        self.assertEqual(''.join(p.text for p in fallback.remaining),text)
        continued=expand_replies([PreparedReply('',pages=fallback.remaining,context=fallback)],text_limit=1500)
        self.assertTrue(all(len(p.text)<=1500 for p in continued))
        private=expand_replies([PreparedReply('文'*1800)])
        self.assertEqual(len(private),1);self.assertEqual(private[0].text,'文'*1800)

    def test_render_failure_retains_all_text_and_can_continue_without_requery(self):
        complete = '人物简介和来源\n' * 2200 + '完整结果最后一行'
        answer = InfoAnswer(complete, views=self.views)
        with patch('ournotes_bot.query.character_query.execute_character', return_value=answer) as execute, \
             patch('ournotes_bot.rendering.info_layout.render_info', side_effect=ValueError('image budget')):
            reply = _prepare_reply('/查角色 tmr', self.repo, self.parser)
            self.assertEqual(reply.text, complete)
            self.assertIsNone(reply.image)
            first = _expand_replies([reply])
            self.assertIsInstance(first[-1].context, DeliveryCursor)
            cursor = first[-1].context
            self.assertTrue(cursor.remaining)
            captured_tail = ''.join(p.text for p in cursor.remaining)
            self.assertTrue(captured_tail.endswith('完整结果最后一行'))
            continued = _prepare_followup(cursor, parse_operation('/继续'), self.repo)
            self.assertEqual(continued.pages, cursor.remaining)
            execute.assert_called_once()

    def test_image_pages_keep_complete_per_page_fallback_and_continue_saved_bytes(self):
        pages = tuple(SimpleNamespace(text=f'完整第{i}页', image=f'image{i}'.encode()) for i in range(8))
        with patch('ournotes_bot.query.gacha_query.execute_gacha', return_value=self.answer) as execute, \
             patch('ournotes_bot.rendering.info_layout.render_info', return_value=pages):
            reply = _prepare_reply('/查卡池 历史', self.repo, self.parser)
            expanded = _expand_replies([reply])
            cursor = expanded[-1].context
            self.assertIsInstance(cursor, DeliveryCursor)
            self.assertEqual([p.image for p in expanded] + [p.image for p in cursor.remaining],
                             [p.image for p in pages])
            self.assertEqual(cursor.remaining[-1].text, '完整第7页')
            next_reply = _prepare_followup(cursor, parse_operation('/继续'), self.repo)
            self.assertEqual(next_reply.pages, cursor.remaining)
            execute.assert_called_once()

    def test_errors_propagate_status_and_remain_text(self):
        answer = InfoAnswer('资料暂不可用', 'data_unavailable')
        with patch('ournotes_bot.query.gacha_query.execute_gacha', return_value=answer):
            result = resolve_command('/查卡池', self.repo)
            self.assertEqual(result.status, 'data_unavailable')
            self.assertEqual(handle_command('/查卡池', self.repo, resolved=result), answer.text)

    def test_original_namecard_keeps_geometry_and_broken_image_falls_back_to_text(self):
        from io import BytesIO
        from dataclasses import replace
        from PIL import Image
        data = BytesIO()
        Image.new('RGB', (640, 960), 'white').save(data, format='PNG')
        for body in (data.getvalue(), b'broken-image'):
            answer = replace(self.answer, original_image=body)
            with self.subTest(valid=body == data.getvalue()), \
                 patch('ournotes_bot.query.public_queries.execute_player', return_value=answer), \
                 patch('ournotes_bot.rendering.info_layout.render_info', side_effect=AssertionError('re-rendered original')):
                reply = _prepare_reply('/查玩家 99 名片', self.repo, self.parser)
                self.assertEqual(reply.text, answer.text)
                if body == data.getvalue():
                    with Image.open(BytesIO(reply.image)) as image:
                        self.assertEqual(image.size, (640, 960))
                else:
                    self.assertIsNone(reply.image)

    def test_joined_arguments_have_correct_command_hint_and_character_card_is_preserved(self):
        for command in ('查活动', '查卡池', '查角色'):
            text = handle_command('/' + command + '12', self.repo)
            self.assertIn('/' + command + ' 12', text)
        self.assertEqual(resolve_command('/查角色卡', self.repo).parsed[0], 'cards')


if __name__ == '__main__':
    unittest.main()

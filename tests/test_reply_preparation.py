"""Captured replies and drawing source boundaries without QQ transport."""
import sys
import subprocess
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from PIL import Image
from ournotes_bot.data import Song, Chart
from ournotes_bot.application.reply_preparation import prepare_chart_reply, prepare_song_reply
from ournotes_bot.rendering import haneoka_theme as theme, chart_layout, song_detail_layout
from ournotes_bot.sources.errors import ChartDataError

class ReplyPreparationTests(unittest.TestCase):
    def song(self):
        return Song(1,'歌曲',('歌曲',),'乐队','','','','','jacket',(Chart('EXPERT',25,25,None,'chart'),))

    def test_capture_once_and_source_failure_remains_distinct(self):
        song=self.song(); score={'events':{'bpm':[{'bpm':120,'tick':0}], 'skill':[]}}
        load=Mock(return_value=score)
        chart=prepare_chart_reply(song,song.charts,'moenotes','zh',load_score=load)
        self.assertIs(chart.score,score); self.assertIn('Note：暂无资料',chart.text)
        load.assert_called_once_with(song,song.charts[0])
        load.reset_mock()
        detail=prepare_song_reply(song,'moenotes','zh',load_score=load)
        self.assertIs(detail.score,score); self.assertIn('? Note',detail.text)
        load.assert_called_once_with(song,song.charts[0])
        failed=prepare_chart_reply(song,song.charts,'moenotes','zh',load_score=Mock(side_effect=ChartDataError('offline')))
        self.assertIsNone(failed.score);self.assertEqual(failed.notice,'谱面暂不可用')

    def test_module_import_does_not_load_qq(self):
        run=subprocess.run([sys.executable,'-B','-c',
            'import sys; import ournotes_bot.application.reply_preparation; assert "botpy" not in sys.modules; assert "ournotes_bot.platforms.qq.qq" not in sys.modules'],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)

    def test_capture_game_bytes_once_and_close_owned_art_on_failure(self):
        art=Image.new('RGB',(10,10)); source=Mock(return_value=b'captured')
        with theme.game_assets(source):
            with self.assertRaisesRegex(RuntimeError,'paint'):
                with theme.prepared_render_assets([('x',(10,10))],lambda *a:art,['icon']) as assets:
                    source.side_effect=AssertionError('late source access')
                    self.assertEqual(theme.game_asset('icon'),b'captured')
                    self.assertEqual(theme.game_asset('icon'),b'captured')
                    with self.assertRaises(KeyError):theme.game_asset('unprepared')
                    raise RuntimeError('paint')
        source.assert_called_once_with('icon')
        with self.assertRaises(ValueError):art.getpixel((0,0))

    def test_renderers_cannot_load_after_canvas_allocation(self):
        for module in (chart_layout,song_detail_layout):
            with self.subTest(renderer=module.__name__):
                started=False
                def load(*args):
                    self.assertFalse(started,'drawing performed source access')
                    return None
                original=module.Canvas
                def canvas(*args,**kwargs):
                    nonlocal started
                    started=True
                    return original(*args,**kwargs)
                with theme.game_assets(load), patch.object(module,'Canvas',canvas), patch('socket.socket.connect',side_effect=AssertionError('network')):
                    song=self.song()
                    if module is chart_layout:result=module.render(song,song.charts,None,'EXPERT',load=load)
                    else:result=module.render(song,None,load=load)
                    self.assertTrue(result)

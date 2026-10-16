"""Readable chart annotations: source-clock positions and real interval edges."""
import io
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from ournotes_bot.data import Chart, Song
from ournotes_bot.rendering import chart_layout as layout
from ournotes_bot.rendering.haneoka_theme import Canvas, font
from ournotes_bot.rendering.song_detail_layout import seconds_at


class ChartClockTests(unittest.TestCase):
    def test_quarter_minute_positions_and_exact_final_node(self):
        bpms=[{'t':0,'bpm':120}]
        self.assertEqual(layout.time_markers(bpms,33600),
                         ((0,0,False),(14400,15,False),(28800,30,False),(33600,35,True)))

    def test_positions_cross_tempo_changes_using_the_source_clock(self):
        bpms=[{'t':0,'bpm':120},{'t':9600,'bpm':240}]
        markers=layout.time_markers(bpms,59520)
        self.assertEqual(markers,((0,0,False),(19200,15,False),(48000,30,False),(59520,36,True)))
        for tick,seconds,_ in markers:self.assertEqual(seconds_at(tick,bpms),seconds)
        # A noninteger source BPM exercises binary32 millisecond rounding.
        bpms=[{'t':0,'bpm':128.5399932861328},{'t':51840,'bpm':128.4499969482422}]
        for tick,seconds,is_end in layout.time_markers(bpms,100000):
            if not is_end:self.assertEqual(seconds_at(tick,bpms),seconds)

    def test_exact_final_quarter_minute_is_not_duplicated(self):
        self.assertEqual(layout.time_markers([{'t':0,'bpm':120}],28800),
                         ((0,0,False),(14400,15,False),(28800,30,True)))

    def test_missing_or_invalid_clock_never_invents_labels(self):
        for bpms in ([],[{'t':10,'bpm':120}],[{'t':0,'bpm':0}]):
            self.assertEqual(layout.time_markers(bpms,40000),())

    def test_boundary_time_belongs_to_next_column(self):
        c=Mock();markers=((15360,15,False),(16000,16,True))
        layout.draw_times(c,markers,180,312,1230,5070,0,15360,'zh')
        c.text.assert_not_called()
        layout.draw_times(c,markers,180,312,1230,5070,15360,30720,'zh')
        labels=[v.args[2] for v in c.text.call_args_list]
        self.assertEqual(labels.count('0:15'),1)
        self.assertEqual(labels.count('0:16'),1)
        c.box.assert_not_called()

    def test_close_final_label_moves_but_its_tick_line_stays_exact(self):
        c=Mock();markers=((14400,15,False),(14500,15,True))
        layout.draw_times(c,markers,180,312,1230,5070,0,15360,'en')
        centers=[call.args[1] for call in c.text.call_args_list]
        self.assertGreaterEqual(abs(centers[0]-centers[1]),38)
        c.box.assert_not_called()
        lines=[v.args[0] for v in c.draw.line.call_args_list if len(v.args[0])==4]
        self.assertIn((180,1470,492,1470),lines)
        self.assertIn((180,1445,492,1445),lines)
        self.assertEqual([v.args[2] for v in c.text.call_args_list],['0:15','0:15'])

    def test_time_font_has_tabular_digits(self):
        self.assertEqual(len({font(28,False,True).getlength(str(i)) for i in range(10)}),1)


class IntervalEdgeTests(unittest.TestCase):
    def test_rounded_icons_have_antialiased_edges_and_hollow_skill_end(self):
        for kind in ('skill','fever'):
            for is_end in (False,True):
                alpha=layout.endpoint_sprite(kind,is_end).getchannel('A')
                values=set(alpha.tobytes())
                self.assertTrue(any(0<v<255 for v in values))
                self.assertEqual(alpha.getpixel((0,0)),0)
                self.assertEqual(alpha.getpixel((32,32)),0 if kind=='skill' and is_end else 255)
                alpha.close()

    def test_real_endpoint_glyphs_do_not_overlap_their_vertical_rails(self):
        events={'bpm':[{'t':0,'bpm':120}],'skill':[1000],'fever':[[1000,2000]]}
        with patch.object(layout,'endpoint') as endpoints:
            layout.draw_markers(Mock(),events,100,312,1140,4980,0,15360)
        for call in endpoints.call_args_list:
            _,cx,_,_,kind,_=call.args
            if kind=='skill':self.assertLessEqual(cx+28,100-16-4-8)
            else:self.assertGreaterEqual(cx-14,100+312+18+4+8)

    def test_skill_and_fever_have_only_two_real_edges_across_columns(self):
        events={'bpm':[{'t':0,'bpm':120}],'skill':[15000],'fever':[[15000,19800]]}
        c=Mock()
        with patch.object(layout,'endpoint') as endpoints,patch.object(layout,'continuation') as cuts:
            layout.draw_markers(c,events,180,312,1230,5070,0,15360)
            layout.draw_markers(c,events,180,312,1230,5070,15360,30720)
        edges=[(v.args[3],v.args[4],v.args[5]) for v in endpoints.call_args_list]
        self.assertCountEqual(edges,[('S1','skill',False),('S1','skill',True),
                                     ('G1','fever',False),('G1','fever',True)])
        self.assertEqual(cuts.call_count,4)
        self.assertEqual(sum(v.kwargs.get('width')==8 for v in c.draw.line.call_args_list),4)

    def test_exact_column_boundary_edges_are_drawn_once(self):
        events={'bpm':[{'t':0,'bpm':120}],'skill':[10560,15360],'fever':[[0,15360],[15360,18000]]}
        with patch.object(layout,'endpoint') as endpoints:
            for lo in (0,15360):layout.draw_markers(Mock(),events,180,312,1230,5070,lo,lo+15360)
        edges=[(v.args[3],v.args[5]) for v in endpoints.call_args_list]
        self.assertEqual(len(edges),8)
        self.assertEqual(len(set(edges)),8)

    def test_song_end_cuts_intervals_without_fabricated_endpoints(self):
        events={'bpm':[{'t':0,'bpm':120}],'skill':[15000],'fever':[[15000,19800]]}
        with patch.object(layout,'endpoint') as endpoints,patch.object(layout,'continuation') as cuts:
            layout.draw_markers(Mock(),events,180,312,1230,5070,0,15360,end_tick=16000)
            layout.draw_markers(Mock(),events,180,312,1230,5070,15360,30720,end_tick=16000)
        self.assertEqual(endpoints.call_count,2)
        self.assertTrue(all(not v.args[-1] for v in endpoints.call_args_list))
        self.assertEqual(cuts.call_count,6)

    def test_continuation_id_does_not_collide_with_nearby_real_edge(self):
        events={'bpm':[{'t':0,'bpm':120}],'skill':[10561]}
        with patch.object(layout,'continuation') as cuts:
            layout.draw_markers(Mock(),events,180,312,1230,5070,15360,30720)
        self.assertEqual(cuts.call_args.args[3],'')


class ChartAnnotationRenderTests(unittest.TestCase):
    def test_final_time_avoids_the_label_of_a_clipped_skill_window(self):
        song=Song(1,'Test',('Test',),'Band','','','','','',(Chart('EXPERT',25,25,1,''),))
        score={'events':{'bpm':[{'t':0,'bpm':120}],'skill':[1000]},
               'notes':[{'type':'tap','t':3000,'pos':0,'size':6}]}
        with patch.object(layout,'draw_note'),patch.object(layout,'draw_times',wraps=layout.draw_times) as times:
            layout.render(song,song.charts,score,'EXPERT',load=lambda *a:None)
        self.assertIn((4260,44),times.call_args.kwargs['occupied'])

    def test_plain_time_label_avoids_skill_icon_without_a_background(self):
        c=Mock();y=4980-14400/4
        layout.draw_times(c,((14400,15,False),),100,312,1140,4980,0,15360,'zh',occupied=((y,44),))
        self.assertGreaterEqual(abs(c.text.call_args.args[1]-y),44)
        c.box.assert_not_called()

    def test_three_locales_keep_whole_song_and_one_compact_legend(self):
        song=Song(1,'Test',('Test',),'Band','','','','','',(Chart('EXPERT',25,25,2,''),))
        score={'events':{'bpm':[{'t':0,'bpm':120}]},
               'notes':[{'type':'tap','t':0,'pos':0,'size':6},
                        {'type':'tap','t':130000,'pos':0,'size':6}]}
        for locale,start,end in (('zh','开始','结束'),('en','Start','End'),('ja','開始','終了')):
            texts=[];original=Canvas.text
            def capture(c,x,y,value,*args,**kwargs):
                texts.append(str(value));return original(c,x,y,value,*args,**kwargs)
            with patch.object(Canvas,'text',capture),patch.object(layout,'draw_note'):
                pages=layout.render(song,song.charts,score,'EXPERT',locale,load=lambda *a:None)
            self.assertEqual(len(pages),1)
            self.assertFalse(any('拍' in t or 'beats' in t for t in texts))
            self.assertEqual(texts.count(start),2*len(pages))
            self.assertEqual(texts.count(end),2*len(pages))
            self.assertEqual(texts.count('0:00'),1)
            for page in pages:
                self.assertLessEqual(len(page),3_000_000)
                with Image.open(io.BytesIO(page)) as im:self.assertLessEqual(im.width*im.height,layout.CHART_MAX_PIXELS)

    def test_unavailable_chart_has_no_time_axis_or_legend(self):
        song=Song(1,'Test',('Test',),'Band','','','','','',())
        with patch.object(layout,'draw_times') as times,patch.object(layout,'draw_legend') as legend:
            self.assertTrue(layout.render(song,(),None,None,load=lambda *a:None))
        times.assert_not_called();legend.assert_not_called()

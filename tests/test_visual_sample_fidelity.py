"""Regressions caught by comparing the approved art with native output."""
import io
import unittest
from dataclasses import replace
from unittest.mock import patch
from PIL import Image
from ournotes_bot.data import SupportCard, Song, Chart
from ournotes_bot.rendering import card_detail_layout, song_detail_layout
from ournotes_bot.rendering.haneoka_theme import Canvas


class SampleFidelityTests(unittest.TestCase):
    def test_stat_bars_keep_original_fixed_scale_across_different_cards(self):
        for values in ((1400,1200,1300),(1400,4000,1300)):
            card=SupportCard(61,'测试','灯',('灯',),10,1,*values,'','','',catalog={'source':'haneoka'})
            boxes=[];original=Canvas.box
            def capture(c,box,fill='#f0edf1',*args,**kwargs):
                if fill=='#d2518b':boxes.append(box)
                return original(c,box,fill,*args,**kwargs)
            with patch.object(Canvas,'box',capture):card_detail_layout.render(card,load=lambda *a:None)
            self.assertEqual(boxes[0][2],194+round(706*14/45))

    def test_reference_skill_table_retains_single_effect_row_meaning(self):
        from ournotes_bot.data import Skill
        card=SupportCard(61,'EX','灯',('灯',),10,1,1400,1200,1300,'','','',
            skills=(Skill('supportSkill','延时','演出技能延长3秒'),),
            catalog={'skill_levels':[[{'zh':f'演出技能延长{n}秒'} for n in (0.5,1,1.5,2,3)]],
                     'compact_skills':[{'levels':{str(lv):{'family':'演出延时','short':f'延时{n}秒'} for lv,n in enumerate((0.5,1,1.5,2,3),1)}}]})
        drawn=[];original=Canvas.text
        def capture(c,x,y,value,*a,**kw):drawn.append(str(value));return original(c,x,y,value,*a,**kw)
        with patch.object(Canvas,'text',capture):card_detail_layout.render(card,load=lambda *a:None)
        self.assertIn('延长时间',drawn)

    def test_memory_uses_max_level_percent_and_continuous_white_surface(self):
        card=SupportCard(51,'多人卡','灯',('灯',),4,1,3100,4000,3100,'','','')
        text=[];original=Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            text.append(str(value));return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture):
            parts=card_detail_layout.render(card,load=lambda *args:None)
        self.assertIn('31%',text);self.assertIn('40%',text)
        self.assertNotIn('3100%',text)
        with Image.open(io.BytesIO(parts[0].image)) as im:
            # First stats block: same broad white information surface as the sample.
            self.assertTrue(all(v>245 for v in im.getpixel((40,980))))

    def test_long_note_ticks_count_and_five_second_window_share_clock(self):
        score={'events':{'bpm':[{'t':0,'bpm':120}], 'sig':[{'t':0,'sig':[4,4]}],
                         'skill':[0], 'fever':[[0,1920]]},
               'notes':[{'type':'long','node':[{'t':0,'pos':0,'size':6},{'t':1920,'pos':0,'size':6}]}]}
        times=song_detail_layout.judged_times(score)
        self.assertEqual(times,[0,.25,.5,.75,1,1.25,1.5,1.75,2])
        data=song_detail_layout.timeline(score)
        self.assertEqual(data['windows'][0]['count'],9)
        self.assertEqual(data['windows'][0]['percent'],100)

    def test_hidden_guides_do_not_disable_judgments_and_density(self):
        score={'events':{'bpm':[{'t':0,'bpm':120}],'skill':[0]},
               'notes':[{'t':0,'pos':0,'size':6},{'t':480,'pos':6,'size':6},
                        {'type':'guide','node':[{'t':0,'pos':0,'size':6,'visible':False},
                                                {'t':960,'pos':6,'size':6,'visible':False}]}]}
        self.assertEqual(song_detail_layout.judged_times(score),[0,0.5])
        data=song_detail_layout.timeline(score)
        self.assertEqual(data['windows'][0]['count'],2)
        self.assertEqual(sum(data['density']),2)

    def test_variable_meter_preserves_the_judgment_clock_and_eighths(self):
        score={'events':{'bpm':[{'t':0,'bpm':120}],
                         'sig':[{'t':0,'sig':[4,4]},{'t':1920,'sig':[5,4]}]},
               'notes':[{'type':'long','node':[{'t':0,'pos':0,'size':6},
                                               {'t':4320,'pos':0,'size':6}]}]}
        self.assertEqual(song_detail_layout.judged_times(score),[i/4 for i in range(19)])

    def test_short_measure_boundaries_keep_native_rounding_and_generated_judgments(self):
        score={'events':{'bpm':[{'t':0,'bpm':120}],
                         'sig':[{'t':0,'sig':[4,4]},{'t':1920,'sig':[1,8]},
                                {'t':2160,'sig':[1,16]},{'t':2280,'sig':[1,32]},
                                {'t':2340,'sig':[6,4]}]},
               'notes':[{'type':'long','node':[{'t':0,'pos':0,'size':6},
                                               {'t':3000,'pos':0,'size':6}]}]}
        # Captured reference conversion: fractional bars are not a fixed 240-tick grid.
        self.assertEqual(song_detail_layout.judged_times(score),
                         [0,.25,.5,.75,1,1.25,1.5,1.75,2,2.25,2.437,3.124])

    def test_structural_guide_without_trace_has_no_judgments(self):
        self.assertEqual(song_detail_layout.judged_times({'events':{'bpm':[{'t':0,'bpm':120}]},
            'notes':[{'type':'guide','node':[{'t':0},{'t':480}]}]}), [])

    def test_odd_meter_and_guide_keep_chronological_skill_times_without_counts(self):
        score={'events':{'bpm':[{'t':0,'bpm':165},{'t':48000,'bpm':230}],
                         'sig':[{'t':0,'sig':[5,4]}], 'skill':[83040,19680,39840]},
               'notes':[{'type':'guide','node':[{'t':0},{'t':96000}]}]}
        data=song_detail_layout.timeline(score)
        self.assertEqual([w['start'] for w in data['windows']],
                         [song_detail_layout.seconds_at(t,score['events']['bpm']) for t in sorted(score['events']['skill'])])
        self.assertEqual(len(data['windows']),3)
        self.assertTrue(all(w['count'] is None and w['percent'] is None and w['rank'] is None for w in data['windows']))
        self.assertEqual(data['density'],[])

    def test_song_retains_approved_sections_and_compact_difficulty_tiles(self):
        song=Song(1,'测试',('测试',),'乐队','','','','','',tuple(Chart(name,7,7,100,'') for name in ('EASY','NORMAL','HARD','EXPERT')))
        drawn=[];original=Canvas.box
        def capture(canvas,box,*args,**kwargs):
            drawn.append(tuple(box));return original(canvas,box,*args,**kwargs)
        with patch.object(Canvas,'box',capture):
            raw=song_detail_layout.render(song,None,load=lambda *args:None)
        self.assertIn((136,708,296,808),drawn)
        with Image.open(io.BytesIO(raw)) as im:self.assertEqual(im.size,(1400,2080))


class AlignmentTests(unittest.TestCase):
    def test_band_name_keeps_its_exclamation_marks_when_wrapping(self):
        from ournotes_bot.rendering.haneoka_theme import lines
        wrapped=lines('×4 · MyGO!!!!!',180,26)
        self.assertIn('MyGO!!!!!',wrapped)
        self.assertFalse(any(v and set(v)<=set('!！') for v in wrapped))

    def test_multi_memory_combines_related_people_in_one_group(self):
        from ournotes_bot.rendering import catalog_layout
        people=[dict(id=2,name={'zh':'第二人'},band=1,order=2),dict(id=1,name={'zh':'首人'},band=1,order=1)]
        card=SupportCard(51,'多人卡','第二人',('第二人',),4,1,3100,4000,3100,'','','',catalog={'people':people})
        drawn=[];original=Canvas.text
        def capture(canvas,x,y,value,size,*args,**kwargs):
            drawn.append((str(value),size));return original(canvas,x,y,value,size,*args,**kwargs)
        with patch.object(Canvas,'text',capture):parts,_,_=catalog_layout.render_list([card],'全部',load=lambda *a:None)
        self.assertIn(('首人',18),drawn)
        self.assertIn(('第二人',18),drawn)
        self.assertIn(('ID 51',24),drawn)
        self.assertIn(('未知',24),drawn)
        with Image.open(io.BytesIO(parts[0].image)) as im:self.assertEqual(im.width,608)

    def test_different_character_combinations_have_distinct_rows(self):
        from ournotes_bot.rendering import catalog_layout
        people=[dict(id=i,name={'zh':'灯' if i==1 else str(i)},band=1,order=i) for i in range(1,10)]
        card=SupportCard(51,'多人卡','灯',('灯',),4,1,3100,4000,3100,'','','')
        a=replace(card,catalog={'people':people[:1],'character_ids':[1]})
        b=replace(card,id=52,catalog={'people':people,'character_ids':list(range(1,10))})
        positions={};original=Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            if str(value) in ('ID 51','ID 52'):positions[str(value)]=y
            return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture):
            catalog_layout.render_list([a,b],'灯',load=lambda *args:None)
        self.assertEqual(set(positions),{'ID 51','ID 52'})
        self.assertLess(positions['ID 52'],positions['ID 51'])


class FullChartTests(unittest.TestCase):
    def test_trace_uses_original_trace_sprite_and_bpm_stays_in_metric_cell(self):
        from ournotes_bot.rendering import chart_layout
        from ournotes_bot.rendering.haneoka_theme import font
        song=Song(1,'Odd Dice',('Odd Dice',),'millsage','','','','','',(Chart('EXPERT',28,28,965,''),))
        score={'events':{'bpm':[{'t':0,'bpm':165},{'t':1920,'bpm':230}]},
               'notes':[{'type':'trace','t':960,'pos':0,'size':6}]}
        drawn=[];original=Canvas.text
        def capture(canvas,x,y,value,size,*args,**kwargs):
            drawn.append((x,y,str(value),size));return original(canvas,x,y,value,size,*args,**kwargs)
        with patch.object(chart_layout,'draw_note') as draw,patch.object(Canvas,'text',capture):
            chart_layout.render(song,song.charts,score,'EXPERT',load=lambda *a:None)
        self.assertEqual(draw.call_args.args[4],'trace')
        x,_,value,size=next(r for r in drawn if r[2]=='165.00–230.00')
        next_x=88+2*(1800-176)/4
        self.assertLess(x+font(size,True,True).getlength(value),next_x)

    def test_source_auto_control_points_do_not_break_chart_or_skill_times(self):
        from ournotes_bot.rendering import chart_layout
        from test_haneoka_charts import SCORE
        song=Song(1,'测试',('测试',),'乐队','','','','','',(Chart('EXPERT',24,24,62,''),))
        with patch.object(chart_layout,'draw_note'):
            self.assertTrue(chart_layout.render(song,song.charts,SCORE['score'],'EXPERT',load=lambda *a:None))
        self.assertEqual(len(song_detail_layout.timeline(SCORE['score'])['windows']),2)

    def test_eight_columns_keep_lane_width_in_one_horizontal_image(self):
        from ournotes_bot.rendering import chart_layout
        song=Song(1,'测试',('测试',),'乐队','','','','','',(Chart('EXPERT',24,24,62,''),))
        score={'events':{'bpm':[{'t':0,'bpm':120}]},
               'notes':[{'type':'tap','t':i*1920,'pos':0,'size':6} for i in range(62)]}
        with patch.object(chart_layout,'draw_note') as draw:
            parts=chart_layout.render(song,song.charts,score,'EXPERT',load=lambda *args:None)
        self.assertEqual(len(parts),1)
        self.assertEqual(draw.call_count,62)
        with Image.open(io.BytesIO(parts[0])) as im:
            self.assertEqual(im.size,(4272,4152))
        self.assertTrue(all(call.args[3]==78 for call in draw.call_args_list))
        # Original vertical time scale: each bar still occupies 480 pixels.
        ys=[call.args[2] for call in draw.call_args_list[:8]]
        self.assertEqual([ys[i]-ys[i+1] for i in range(7)],[480]*7)
        starts=draw.call_args_list[::8]
        self.assertEqual(len({call.args[2] for call in starts}),1)
        self.assertEqual([call.args[1] for call in starts],[699+i*460 for i in range(8)])


class RemainingFidelityTests(unittest.TestCase):
    def test_song_columns_keep_query_numbers_across_parts(self):
        from ournotes_bot.rendering import song_layout
        songs=[Song(i,str(i),(str(i),),'乐队','','','','','',()) for i in range(1,6)]
        labels={};original=Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            if ' · ' in str(value) and str(value).split(' · ')[0].isdigit():labels[str(value)]=(x,y)
            return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture),patch.object(song_layout,'MAX_IMAGE_PIXELS',1760*650):
            parts,ranges=song_layout.render_list(songs,'测试',load=lambda *args:None)
        self.assertEqual(ranges,((0,4),(4,5)))
        self.assertEqual(len(parts),2)
        self.assertEqual(set(labels),{f'{i} · {i}' for i in range(1,6)})
        self.assertEqual(labels['1 · 1'][0],labels['2 · 2'][0])
        self.assertLess(labels['2 · 2'][0],labels['3 · 3'][0])
        self.assertEqual(labels['1 · 1'][1],labels['3 · 3'][1])

    def test_reference_metrics_preserve_warning_and_reject_nonfinite(self):
        from ournotes_bot.sources.haneoka.catalog_records import song_reference_metrics
        metric={'referenceId':'legacy-normal-theory-relative-v1','reference':{'downtimeSeconds':30},
                'sr':.46534359,'score':6.72562192,'eff':2.81324379,'metaWarnings':['canonical-full-combo-mismatch']}
        docs={'song-meta':{'100104':{'3':{'chart':metric}}}}
        result=song_reference_metrics({'musicId':100104},docs)['EXPERT']
        self.assertEqual(result['warnings'],['canonical-full-combo-mismatch'])
        self.assertEqual(result['downtime_seconds'],30)
        self.assertEqual(song_reference_metrics({'musicId':100104},{}),{})
        metric['score']=float('nan')
        with self.assertRaises(ValueError):song_reference_metrics({'musicId':100104},docs)


class ChartMarkerTests(unittest.TestCase):
    def test_column_boundary_skill_once_and_fever_continues(self):
        from ournotes_bot.rendering.chart_layout import draw_markers
        from unittest.mock import Mock
        c=Mock();events={'skill':[0,15360], 'fever':[[15000,16000]]}
        draw_markers(c,events,100,200,100,3940,0,15360)
        labels=[call.args[2] for call in c.text.call_args_list]
        self.assertIn('S1',labels);self.assertNotIn('S2',labels);self.assertIn('G1',labels)
        c.reset_mock()
        draw_markers(c,events,100,200,100,3940,15360,30720)
        labels=[call.args[2] for call in c.text.call_args_list]
        self.assertIn('S2',labels);self.assertNotIn('S1',labels);self.assertIn('G1',labels)
        skill=next(call for call in c.text.call_args_list if call.args[2]=='S2')
        self.assertEqual(skill.args[1],3940)

    def test_all_song_images_use_game_integer_level(self):
        from ournotes_bot.rendering import song_layout,chart_layout
        song=Song(1,'测试',('测试',),'乐队','','','','','',(Chart('EXPERT',25,25.5,100,''),))
        labels=[];original=Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            labels.append(str(value));return original(canvas,x,y,value,*args,**kwargs)
        for render in [lambda:song_layout.render_list([song],'测试',load=lambda *a:None),
                       lambda:song_detail_layout.render(song,None,load=lambda *a:None),
                       lambda:chart_layout.render(song,song.charts,None,'EXPERT',load=lambda *a:None)]:
            labels.clear()
            with patch.object(Canvas,'text',capture):render()
            self.assertIn('25',labels);self.assertNotIn('25.5',labels)


class SkillWindowMarkerTests(unittest.TestCase):
    def test_end_time_accounts_for_tempo_change(self):
        from ournotes_bot.rendering.chart_layout import skill_end_tick
        self.assertEqual(skill_end_tick(0,[{'t':0,'bpm':120},{'t':1920,'bpm':240}]),7680)
        self.assertEqual(skill_end_tick(1920,[{'t':0,'bpm':120}]),6720)
        self.assertIsNone(skill_end_tick(0,[]))

    def test_window_end_in_next_column_is_drawn_at_correct_height(self):
        from ournotes_bot.rendering import chart_layout
        from unittest.mock import Mock
        c=Mock();events={'skill':[15000],'bpm':[{'t':0,'bpm':120}]}
        with patch.object(chart_layout,'endpoint',wraps=chart_layout.endpoint) as endpoints:
            chart_layout.draw_markers(c,events,100,200,100,3940,0,15360)
            self.assertEqual(endpoints.call_count,1)
            self.assertFalse(endpoints.call_args.args[-1])
            endpoints.reset_mock();c.reset_mock()
            chart_layout.draw_markers(c,events,100,200,100,3940,15360,30720)
            self.assertEqual(endpoints.call_count,1)
            self.assertTrue(endpoints.call_args.args[-1])
            self.assertEqual(endpoints.call_args.args[2],2830)
            self.assertEqual(endpoints.call_args.args[3],'S1')


class DetailTableLabelsTests(unittest.TestCase):
    def test_conditional_score_growth_labels_preserve_all_values(self):
        from ournotes_bot.rendering.card_detail_layout import table_rows
        descriptions=[f'在COMBO激奏中，每达成10次激奏COMBO 得分提升{v}%（最高{v*10}%）若为「Ave Mujica」成员 则得分提升{v*2}%' for v in (2,2.5,3,3.5,5)]
        rows=table_rows(descriptions)
        self.assertEqual([r[0] for r in rows],['每次增幅','累计上限','乐队增幅'])
        self.assertEqual([r[1][-1] for r in rows],['5%','50%','10%'])

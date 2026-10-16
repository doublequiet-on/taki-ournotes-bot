"""Full-result coverage, source-bound summaries and receipt-safe continuation."""
import io
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from ournotes_bot.data import Song,Chart,Card
from ournotes_bot.rendering.catalog_layout import ordered,render_list,summaries
from ournotes_bot.rendering.song_layout import render_list as songs_image
from ournotes_bot.sources.haneoka.catalog import validate
from ournotes_bot.sources.haneoka.visual_skill_contracts import summary
from ournotes_bot.sources.haneoka.catalog_text import references
from ournotes_bot.query.continuation import Operation,QueryContext,Choice
from ournotes_bot.platforms.qq.qq import PreparedReply,DeliveryCursor,_expand_replies,_prepare_followup


class FullResultTests(unittest.TestCase):
    def test_song_counts_never_drop_the_seventeenth_or_tail(self):
        for count in (1,16,17,31,35):
            songs=tuple(Song(i,'歌名','歌名','MyGO!!!!!','','','','','',()) for i in range(count))
            parts,ranges=songs_image(songs,'全部',load=lambda u,s:None)
            self.assertEqual([i for start,stop in ranges for i in range(start,stop)],list(range(count)))
            self.assertEqual(len(parts),1)
            with Image.open(io.BytesIO(parts[0].image)) as im:
                self.assertEqual(im.width,892 if count==1 else 1760)
                if count==35:self.assertGreater(im.height,3600)

    def test_sort_keeps_colours_vertical_and_skips_empty_rarity_columns(self):
        base=Card(1,1,'卡','角色','MyGO',4,1,1,1,1,'','','','',catalog={'people':[{'id':1,'band':1,'order':2}]})
        cards=[replace(base,id=9,rarity=2,card_type=1),replace(base,id=4,rarity=4,card_type=2),
               replace(base,id=6,rarity=20,card_type=1),base]
        self.assertEqual([c.id for c in ordered(cards)],[1,6,9,4])
        parts,records,ranges=render_list(cards,'测试',load=lambda u,s:None)
        self.assertEqual([c.id for c in records],[1,6,9,4])
        self.assertEqual(ranges,((0,4),))
        self.assertEqual({s[0] for s in summaries(base)},{'队','演','激'})

    def test_skill_summary_is_invalidated_when_mechanics_change(self):
        saved=json.loads((Path(__file__).parent/'fixtures/haneoka_catalog.json').read_text('utf-8'))['snapshot']
        refs=references(saved['documents']['jp']['skill-reference'])
        card=saved['documents']['jp']['cards']['51'];skill=card['resolvedSkills']['leader']
        self.assertIsNotNone(summary(skill,refs))
        skill=json.loads(json.dumps(skill));skill['effects'][0]['effectValue']+=1
        self.assertIsNone(summary(skill,refs))

    def test_continue_retains_exact_bytes_across_repository_refresh(self):
        pages=tuple(PreparedReply(str(i),bytes([i])) for i in range(8))
        with patch('ournotes_bot.platforms.qq.qq.QQ_PASSIVE_REPLY_LIMIT',3):
            batch=_expand_replies([PreparedReply('',pages=pages)])
            self.assertEqual([p.image for p in batch],[bytes([i]) for i in range(3)])
            context=batch[-1].context
            next_reply=_prepare_followup(context,Operation('continue'),object())
            second=_expand_replies([next_reply])
            self.assertEqual([p.image for p in second],[bytes([i]) for i in range(3,6)])
            final=_expand_replies([_prepare_followup(second[-1].context,Operation('continue'),object())])
            self.assertEqual([p.image for p in final],[b'\x06',b'\x07'])
            self.assertFalse(final[-1].context.remaining)
            self.assertEqual(final[-1].context.sent,8)

    def test_multi_command_overflow_preserves_all_commands_in_order(self):
        replies=[PreparedReply('',pages=tuple(PreparedReply(f'a{i}',bytes([i])) for i in range(4))),PreparedReply('second command')]
        with patch('ournotes_bot.platforms.qq.qq.QQ_PASSIVE_REPLY_LIMIT',3):
            first=_expand_replies(replies)
            remaining=first[-1].context.remaining
            self.assertEqual([p.text for p in remaining],['a3','second command'])
            self.assertIsNone(first[-1].context.query_context)

    def test_uncertain_send_never_retries_from_continue(self):
        context=DeliveryCursor(None,(PreparedReply('not resent',b'xx'),),1,2,True)
        reply=_prepare_followup(context,Operation('continue'),object())
        self.assertIsNone(reply.image)
        self.assertIn('不确定',reply.text)

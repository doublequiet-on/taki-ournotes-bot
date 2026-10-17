"""Activity-only complete-view experiment; existing blocks and default pages remain intact."""
import hashlib
import io
import unittest
from unittest.mock import patch
from PIL import Image
from ournotes_bot.query.contracts.info import InfoAnswer,InfoView,InfoRow,info_text
from ournotes_bot.rendering import info_layout as layout,readable_pages
from ournotes_bot.rendering.image_output import ImageBudgetError

class FullActivityTests(unittest.TestCase):
    def answer(self,count=32,presentation='activity',views=1):
        view=InfoView('固定活动',rows=tuple(InfoRow(f'条目 {i}',(f'完整资料 {i}',),command=f'/查曲 {i}') for i in range(count)),presentation=presentation)
        return InfoAnswer(info_text((view,)*views),views=(view,)*views)

    def captured(self,answer,enabled):
        blocks=[];draw=layout._draw_block
        def capture(c,block,y,art):
            draw(c,block,y,art)
            with c.image.crop((0,y,c.image.width,y+block.height)) as image:
                blocks.append((block.text,hashlib.sha256(image.tobytes()).hexdigest()))
        with patch.object(layout,'_draw_block',capture):
            pages=layout.render_info(answer,try_full_activity=enabled)
        return pages,blocks

    def test_complete_activity_reduces_pages_preserving_every_block_pixel(self):
        answer=self.answer();old,old_blocks=self.captured(answer,False);new,new_blocks=self.captured(answer,True)
        self.assertGreater(len(old),1);self.assertEqual(len(new),1)
        self.assertEqual(old_blocks,new_blocks)
        for row in answer.views[0].rows:
            self.assertEqual(new[0].text.splitlines().count(row.title),1)
            self.assertEqual(new[0].text.splitlines().count(row.command),1)

    def test_original_single_page_and_other_templates_do_not_change(self):
        for count,presentation in ((1,'activity'),(32,'gacha'),(32,'character')):
            with self.subTest(count=count,presentation=presentation):
                answer=self.answer(count,presentation)
                self.assertEqual(layout.render_info(answer),layout.render_info(answer,try_full_activity=True))

    def test_multiple_views_stay_separate(self):
        pages=layout.render_info(self.answer(32,views=2),try_full_activity=True)
        self.assertEqual(len(pages),2);self.assertEqual(pages[0],pages[1])

    def test_geometry_rejection_does_not_allocate_full_canvas(self):
        answer=self.answer();heights=[];canvas=layout.Canvas
        def allocate(width,height):heights.append(height);return canvas(width,height)
        with patch.object(readable_pages,'MAX_IMAGE_EDGE',3000),patch.object(layout,'Canvas',allocate):
            old=layout.render_info(answer);new=layout.render_info(answer,try_full_activity=True)
        self.assertEqual(old,new);self.assertLessEqual(max(heights),3000)

    def test_byte_rejection_returns_identical_old_pagination(self):
        answer=self.answer();old=layout.render_info(answer);encode=readable_pages.encode_image;rejected=[]
        def limited(image,**kwargs):
            if image.height>2800:
                rejected.append(image.height);image.close();raise ImageBudgetError('fixture byte limit')
            return encode(image,**kwargs)
        with patch.object(readable_pages,'encode_image',limited):new=layout.render_info(answer,try_full_activity=True)
        self.assertEqual(new,old);self.assertEqual(len(rejected),1)

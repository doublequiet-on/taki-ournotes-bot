"""Single-image-first output keeps measured rows and never downsizes text."""
import io
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from PIL import Image

from ournotes_bot.rendering import readable_pages as pages
from ournotes_bot.rendering.image_output import ImageBudgetError, encode_image


class ReadablePagesTests(unittest.TestCase):
    def test_complete_result_is_one_image_at_original_size(self):
        result = pages.render_parts((20,) * 31, 200, 40,
                                    lambda start, stop, height: Image.new('RGB', (200, height), 'white'))
        self.assertEqual([(p.start, p.stop) for p in result], [(0, 31)])
        with Image.open(io.BytesIO(result[0].image)) as im:
            self.assertEqual(im.size, (200, 660))

    def test_overflow_splits_complete_rows_without_gaps_or_duplicates(self):
        with patch.object(pages, 'MAX_IMAGE_PIXELS', 100_000):
            result = pages.render_parts((100,) * 11, 200, 100,
                                        lambda start, stop, height: Image.new('RGB', (200, height), 'white'))
        self.assertEqual([(p.start, p.stop) for p in result], [(0, 4), (4, 8), (8, 11)])

    def test_byte_budget_reduces_rows_not_scale(self):
        def encoder(image, **kwargs):
            self.assertTrue(kwargs['preserve_size'])
            h = image.height
            image.close()
            if h > 300:
                raise ImageBudgetError('controlled dense image')
            return b'encoded'
        with patch.object(pages, 'encode_image', side_effect=encoder):
            result = pages.render_parts((100,) * 7, 200, 0,
                                        lambda start, stop, height: Image.new('RGB', (200, height)))
        self.assertEqual([(p.start, p.stop) for p in result], [(0, 3), (3, 6), (6, 7)])

    def test_other_painter_errors_are_not_disguised_as_budget_failures(self):
        with self.assertRaisesRegex(RuntimeError, 'paint failed'):
            pages.render_parts((100,), 200, 0, lambda *args: (_ for _ in ()).throw(RuntimeError('paint failed')))

    def test_preserve_size_rejects_oversize_without_resampling(self):
        im = Image.new('RGB', (100, 100))
        with patch('ournotes_bot.rendering.image_output.MAX_IMAGE_PIXELS', 1000):
            with self.assertRaises(ImageBudgetError):
                encode_image(im, close_input=True, preserve_size=True)
        with self.assertRaises(ValueError):
            im.getpixel((0, 0))

    def test_page_scope_is_once_per_maximal_candidate_and_closes_before_next(self):
        events = []
        active = False
        @contextmanager
        def scope(start, stop):
            nonlocal active
            self.assertFalse(active)
            active = True
            events.append(('enter', start, stop))
            try:
                yield
            finally:
                active = False
                events.append(('exit', start, stop))
        def paint(start, stop, height):
            self.assertTrue(active)
            events.append(('paint', start, stop))
            return Image.new('RGB', (200, height))
        def encode(image, **kwargs):
            height = image.height
            image.close()
            if height > 200:
                raise ImageBudgetError('fixture byte budget')
            return b'page'
        with patch.object(pages, 'encode_image', encode):
            result = pages.render_parts((100,)*5, 200, 0, paint, page_scope=scope)
        self.assertEqual([(p.start,p.stop) for p in result], [(0,2),(2,4),(4,5)])
        self.assertEqual([e for e in events if e[0]=='enter'], [('enter',0,5),('enter',2,5),('enter',4,5)])
        self.assertEqual([e for e in events if e[0]=='exit'], [('exit',0,5),('exit',2,5),('exit',4,5)])
        self.assertFalse(active)

    def test_prepare_and_paint_budget_errors_are_not_encoding_retries(self):
        for phase in ('prepare','paint'):
            events=[]
            @contextmanager
            def scope(start, stop):
                events.append('enter')
                try:
                    if phase=='prepare':raise ImageBudgetError('prepare failed')
                    yield
                finally:
                    events.append('exit')
            def paint(*args):
                events.append('paint')
                raise ImageBudgetError('paint failed')
            with self.subTest(phase=phase), patch.object(pages,'encode_image') as encode:
                with self.assertRaisesRegex(ImageBudgetError,phase+' failed'):
                    pages.render_parts((100,)*3,200,0,paint,page_scope=scope)
                encode.assert_not_called()
                self.assertEqual(events,['enter','exit'] if phase=='prepare' else ['enter','paint','exit'])

    def test_scope_exits_after_nonbudget_encoder_failure(self):
        events=[]
        @contextmanager
        def scope(*args):
            try:yield
            finally:events.append('closed')
        def encode(image,**kwargs):
            image.close()
            raise OSError('encoder failed')
        with patch.object(pages,'encode_image',encode):
            with self.assertRaisesRegex(OSError,'encoder failed'):
                pages.render_parts((100,),200,0,lambda *a:Image.new('RGB',(200,100)),page_scope=scope)
        self.assertEqual(events,['closed'])

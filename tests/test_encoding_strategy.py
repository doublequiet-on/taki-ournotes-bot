"""Explicit same-canvas codec experiment and byte-only upload contract."""
import asyncio
import base64
import io
import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from PIL import Image
from ournotes_bot.rendering import image_output as enc
from ournotes_bot.platforms.qq.qq import _upload_image

class EncodingStrategyTests(unittest.TestCase):
    def test_png_is_lossless_and_default_stays_jpeg(self):
        with Image.new('RGBA',(80,90),(15,34,89,170)) as source:
            default=enc.encode_image(source,preserve_size=True)
            result=enc.encode_image_result(source,preserve_size=True,strategy='png-first')
            self.assertTrue(default.startswith(b'\xff\xd8'))
            self.assertTrue(result.data.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertEqual((result.format,result.mime,result.size,len(result.attempts)),('PNG','image/png',(80,90),1))
            with Image.open(io.BytesIO(result.data)) as decoded:
                self.assertEqual(decoded.mode,source.mode)
                self.assertEqual(decoded.tobytes(),source.tobytes())

    def test_png_over_budget_uses_original_jpeg_sequence_and_canvas(self):
        with Image.frombytes('RGB',(128,128),random.Random(2).randbytes(128*128*3)) as source:
            legacy=enc.encode_image(source,preserve_size=True)
            with patch.object(enc,'MAX_IMAGE_BYTES',len(legacy)):
                result=enc.encode_image_result(source,preserve_size=True,strategy='png-first')
            self.assertEqual(result.data,legacy)
            self.assertEqual([x.format for x in result.attempts],['PNG','JPEG'])
            self.assertEqual(result.attempts[0].failure,'byte_budget')
            self.assertEqual(result.size,source.size)

    def test_all_candidates_fail_without_resize_and_owned_canvas_closes(self):
        source=Image.new('RGB',(80,90))
        with patch.object(enc,'MAX_IMAGE_BYTES',1), patch.object(Image.Image,'resize',side_effect=AssertionError('resize')):
            with self.assertRaises(enc.ImageBudgetError) as caught:
                enc.encode_image_result(source,close_input=True,preserve_size=True,strategy='png-first',quality_steps=(95,90,72))
        attempts=caught.exception.encoding_attempts
        self.assertEqual([a.format for a in attempts],['PNG','JPEG','JPEG','JPEG'])
        self.assertTrue(all(a.failure=='byte_budget' for a in attempts))
        with self.assertRaises(ValueError):source.getpixel((0,0))

    def test_geometry_fails_before_conversion_or_encoding(self):
        with Image.new('RGBA',(80,90)) as source, patch.object(Image,'new',side_effect=AssertionError('allocation')), patch.object(Image.Image,'save',side_effect=AssertionError('encoding')):
            with self.assertRaises(enc.ImageBudgetError) as caught:
                enc.encode_image_result(source,preserve_size=True,strategy='png-first',max_pixels=100)
        self.assertEqual(caught.exception.encoding_attempts,())

    def test_optimized_jpeg_error_retains_original_retry(self):
        save=Image.Image.save
        def fail(image,out,**kwargs):
            if kwargs.get('optimize'):raise OSError('libjpeg')
            return save(image,out,**kwargs)
        with Image.new('RGB',(80,90)) as source, patch.object(Image.Image,'save',fail):
            result=enc.encode_image_result(source,preserve_size=True)
        self.assertEqual(result.attempts[0].failure,'OSError')
        self.assertEqual(dict(result.attempts[1].parameters)['optimize'],False)
        self.assertTrue(result.data.startswith(b'\xff\xd8'))

    def test_png_failure_is_reported_not_silently_hidden(self):
        with Image.new('RGB',(80,90)) as source, patch.object(Image.Image,'save',side_effect=OSError('encoder')):
            with self.assertRaises(OSError) as caught:
                enc.encode_image_result(source,preserve_size=True,strategy='png-first')
        self.assertEqual(caught.exception.encoding_attempts[0].failure,'OSError')

    def test_no_implicit_resize_strategy(self):
        with Image.new('RGB',(80,90)) as source:
            with self.assertRaises(ValueError):enc.encode_image(source,strategy='png-first')


    def test_png_upload_keeps_raw_bytes_and_existing_request_fields(self):
        with Image.new('RGB',(80,90)) as source:
            payload=enc.encode_image(source,preserve_size=True,strategy='png-first')
        async def run():
            for group in (True,False):
                request=AsyncMock(return_value={'file_info':'offline'})
                await _upload_image(SimpleNamespace(_http=SimpleNamespace(request=request)),'fixture',payload,group)
                body=request.call_args.kwargs['json']
                self.assertEqual(set(body),{'file_type','file_data','srv_send_msg'})
                self.assertEqual(base64.b64decode(body['file_data']),payload)
        asyncio.run(run())

    def test_gallery_explicit_strategy_exports_correct_extension(self):
        import subprocess, sys, tempfile, json
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run([sys.executable,'-B',str(root/'scripts/export_output_gallery.py'),
                '--only','song-list-17-page1','--encoding-strategy','png-first','--output',folder],check=True,capture_output=True)
            item=json.loads((Path(folder)/'manifest.json').read_bytes())['samples'][0]['files'][0]
            self.assertTrue(item['image_file'].endswith('.png'))
            self.assertEqual(item['mime'],'image/png')
            self.assertTrue((Path(folder)/item['image_file']).read_bytes().startswith(b'\x89PNG'))

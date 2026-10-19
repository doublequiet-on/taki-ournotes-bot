"""Page-bounded parallel artwork preparation retains only repeated art across pages."""
import unittest
import threading
from unittest.mock import patch
from PIL import Image
from ournotes_bot.data import Song
from ournotes_bot.rendering import song_layout
from ournotes_bot.rendering.image_output import ImageBudgetError

class SongAssetReuseTests(unittest.TestCase):
    def songs(self,urls):
        return tuple(Song(i,'固定歌名',('固定歌名',),'乐队','','','','',url,()) for i,url in enumerate(urls))

    def test_repeated_art_loaded_once_per_render_and_released(self):
        owned=[]
        def load(url,size):
            image=Image.new('RGBA',size,'red');owned.append(image);return image
        songs=self.songs(['same']*17)
        first,_=song_layout.render_list(songs,'查询',load=load)
        self.assertEqual(len(owned),1)
        with self.assertRaises(ValueError):owned[0].getpixel((0,0))
        second,_=song_layout.render_list(songs,'查询',load=load)
        self.assertEqual(len(owned),2);self.assertEqual(first,second)
        with self.assertRaises(ValueError):owned[1].getpixel((0,0))

    def test_unique_art_released_after_page(self):
        owned=[]
        def load(url,size):
            image=Image.new('RGBA',size,'red');owned.append(image);return image
        song_layout.render_list(self.songs(['first','second']),'查询',load=load)
        self.assertEqual(len(owned),2)
        for image in owned:
            with self.assertRaises(ValueError):image.getpixel((0,0))

    def test_six_loads_overlap_before_drawing(self):
        barrier=threading.Barrier(6,timeout=10)
        owned=[]
        def load(url,size):
            barrier.wait()
            image=Image.new('RGBA',size,'red');owned.append(image);return image
        parts,ranges=song_layout.render_list(self.songs([str(i) for i in range(6)]),'查询',load=load)
        self.assertEqual(ranges,((0,6),))
        self.assertEqual(len(owned),6)
        for image in owned:
            with self.assertRaises(ValueError):image.getpixel((0,0))

    def test_loader_failure_closes_other_parallel_results(self):
        owned=[]
        def load(url,size):
            if url=='broken':raise RuntimeError('fixture load failure')
            image=Image.new('RGBA',size);owned.append(image);return image
        with self.assertRaisesRegex(RuntimeError,'fixture load failure'):
            song_layout.render_list(self.songs(['good','broken','good2']),'查询',load=load)
        self.assertEqual(len(owned),2)
        for image in owned:
            with self.assertRaises(ValueError):image.getpixel((0,0))

    def test_unique_art_released_before_next_page_fetch(self):
        owned=[]
        def load(url,size):
            if url in ('third','fourth'):
                for image in owned[:2]:
                    with self.assertRaises(ValueError):image.getpixel((0,0))
            image=Image.new('RGBA',size);owned.append(image);return image
        with patch.object(song_layout,'MAX_IMAGE_PIXELS',1760*500):
            _,ranges=song_layout.render_list(self.songs(['first','second','third','fourth']),'查询',load=load)
        self.assertEqual(ranges,((0,2),(2,4)))

    def test_byte_budget_retries_reuse_and_release_owned_art(self):
        owned=[]
        def load(url,size):
            image=Image.new('RGBA',size);owned.append(image);return image
        def reject(image,**kwargs):
            image.close();raise ImageBudgetError('offline byte failure')
        with patch.object(song_layout,'encode_image',reject):
            with self.assertRaises(ImageBudgetError):song_layout.render_list(self.songs(['same']*2),'查询',load=load)
        self.assertEqual(len(owned),1)
        with self.assertRaises(ValueError):owned[0].getpixel((0,0))

    def test_missing_repeated_art_is_not_refetched(self):
        calls=[]
        def load(url,size):calls.append((url,size));return None
        song_layout.render_list(self.songs(['missing']*17),'查询',load=load)
        self.assertEqual(calls,[('missing',(128,128))])

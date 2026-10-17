"""Only repeated artwork is retained within one song-list render."""
import unittest
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

    def test_unique_art_keeps_immediate_release(self):
        owned=[]
        def load(url,size):
            if owned:
                with self.assertRaises(ValueError):owned[-1].getpixel((0,0))
            image=Image.new('RGBA',size,'red');owned.append(image);return image
        song_layout.render_list(self.songs(['first','second']),'查询',load=load)
        self.assertEqual(len(owned),2)
        with self.assertRaises(ValueError):owned[-1].getpixel((0,0))

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

# L3
# Input: Measured text, colours and validated artwork supplied by renderers.
# Output: Haneoka-style Pillow canvases and bounded immutable font/icon caches.
# Pos: Rendering / Core; approved offline sample palette, fonts and alignment.
# Effects: Reads packaged fonts and caller-supplied game art; no query, network or platform calls.
"""Shared visual primitives from the approved samples, at final image scale."""
from functools import lru_cache
from pathlib import Path
import re
from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont, ImageOps

ASSETS = Path(__file__).with_name('assets')
BG, INK, MUTED, ACCENT = '#fcf8fd', '#1b1b1f', '#46464f', '#555994'
LINE, SURFACE, SOFT = '#c7c5d0', '#f0edf1', '#e0e0ff'
DIFFICULTIES = {'EASY': ('#def0df','#407f52'), 'NORMAL': ('#dcf2fa','#25768e'),
                'HARD': ('#fff0d7','#a36920'), 'EXPERT': ('#ffe0ec','#bb4f81')}


@lru_cache(maxsize=128)
def font(size, bold=False, numeric=False):
    result = ImageFont.truetype(str(ASSETS/('Roboto.ttf' if numeric else 'NotoSansSC.ttf')), size)
    axes = result.get_variation_axes()
    result.set_variation_by_axes([(600 if bold else 400) if a['name']==b'Weight' else a['default'] for a in axes])
    return result


@lru_cache(maxsize=4096)
def lines(text, width, size, bold=False):
    """Wrap all content; numeric tokens stay together when they fit."""
    f = font(size, bold, str(text).isascii())
    result = []
    for paragraph in str(text).split('\n'):
        current = ''
        for token in re.findall(r'[+≥×]?\d+(?:\.\d+)?(?:→\d+(?:\.\d+)?)?(?:%|秒|s|pt)?|[A-Za-z]+[!！?？]*|.', paragraph):
            pieces = [token] if f.getlength(token) <= width else list(token)
            for piece in pieces:
                if current and f.getlength(current+piece) > width:
                    if piece[0] in '，。、；：！？）】」』〉》)]}%' and len(current)>1:
                        result.append(current[:-1])
                        current=current[-1]
                    elif current[-1] in '（【「『〈《([{':
                        result.append(current[:-1])
                        current=current[-1]
                    else:
                        result.append(current)
                        current = ''
                current += piece
        result.append(current)
    return tuple(result)


_GAME_ASSETS = ContextVar('game_assets', default=None)


@contextmanager
def game_assets(loader):
    token = _GAME_ASSETS.set(loader)
    try:
        yield
    finally:
        _GAME_ASSETS.reset(token)


def game_asset(name):
    loader = _GAME_ASSETS.get()
    return loader(name) if loader else None


def prepare_assets(requests, load, game_names=()):
    """Load a render's unique artwork concurrently, using its captured UI loader."""
    loader = _GAME_ASSETS.get()
    requests = sorted(set(requests))
    with ThreadPoolExecutor(max_workers=6) as pool:
        icons = [pool.submit(loader, name) for name in sorted(set(game_names))] if loader else []
        assets = dict(zip(requests, pool.map(lambda key: load(*key), requests)))
        for job in icons:
            job.result()
    return assets


@lru_cache(maxsize=96)
def _icon(raw, width, height):
    with Image.open(BytesIO(raw)) as original:
        image = original.convert('RGBA')
    box = image.getbbox()
    if box:
        image = image.crop(box)
    return ImageOps.contain(image, (width,height), Image.Resampling.LANCZOS)


def icon(name, width, height):
    raw = game_asset(name)
    return _icon(raw, width, height) if raw else None


def mission_label(canvas, x, cy, value, size=29, symbol=36, bold=False):
    """Original game silhouettes; preview palette pending in-game colour verification."""
    colors = {'COMBO': '#bd528b', 'JUST': '#328faf', 'LUCK': '#a87822'}
    art = icon(f'gekisou-{value.lower()}.png', symbol, symbol) if value in colors else None
    if art is not None:
        tinted = Image.new('RGBA', art.size, colors[value])
        tinted.putalpha(art.getchannel('A'))
        canvas.paste(tinted, x, round(cy-symbol/2), symbol, symbol, True)
        tinted.close()
    canvas.text(x+symbol+8, cy, value, size, MUTED, bold)


def left_logo(canvas, image, x, cy, width, height):
    """Align the visible logo edge, returning the next text position."""
    if image is None:return x
    cropped=image.crop(image.getbbox())
    scaled=ImageOps.contain(cropped,(width,height),Image.Resampling.LANCZOS)
    canvas.paste(scaled,x,round(cy-scaled.height/2),scaled.width,scaled.height)
    end=x+scaled.width+14
    scaled.close();cropped.close()
    return end


@lru_cache(maxsize=48)
def frame(width, height, rarity):
    colors = {2:['#567eaf','#d5eaff','#749ecc','#e0efff','#567eaf'],
              3:['#a87824','#fff1aa','#cfaa48','#fff5bf','#a87824'],
              4:['#ca81d2','#f1b8d6','#f3dc89','#91d9c4','#86b6ec','#bb94df'],
              20:['#b84f89','#ffe1ef','#db83b3','#fff0ce','#b84f89'],
              10:['#5abdaf','#bbfff0','#6cd1c2','#c4fff4','#5abdaf']}.get(rarity,[LINE,LINE])
    rgb = [ImageColor.getrgb(c) for c in colors]
    # Small cached gradient enlarged once; no per-card full-size numpy arrays.
    layer = Image.new('RGB',(64,64))
    pixels = []
    for y in range(64):
        for x in range(64):
            v=(x*.55+y*.45)/63*(len(rgb)-1); k=min(int(v),len(rgb)-2); t=v-k
            pixels.append(tuple(round(a+(b-a)*t) for a,b in zip(rgb[k],rgb[k+1])))
    layer.putdata(pixels)
    layer=layer.resize((width+8,height+8),Image.Resampling.BILINEAR).convert('RGBA')
    mask=Image.new('L',layer.size)
    ImageDraw.Draw(mask).rounded_rectangle((0,0,width+7,height+7),10,fill=255)
    layer.putalpha(mask)
    return layer


class Canvas:
    def __init__(self,width,height):
        self.image=Image.new('RGB',(width,height),BG)
        self.draw=ImageDraw.Draw(self.image)
        self.width,self.height=width,height

    def text(self,x,cy,value,size=28,color=INK,bold=False,align='left',stroke=0):
        value=str(value);f=font(size,bold,value.isascii())
        box=self.draw.textbbox((0,0),value,font=f)
        w,h=box[2]-box[0],box[3]-box[1]
        x=x-(w if align=='right' else w/2 if align=='center' else 0)
        self.draw.text((round(x-box[0]),round(cy-h/2-box[1])),value,font=f,fill=color,stroke_width=stroke,stroke_fill="#252330")

    def paragraph(self,x,y,text,width,size=28,step=None,color=INK,bold=False):
        step=step or round(size*1.4)
        for line in lines(text,width,size,bold):
            self.text(x,y+step/2,line,size,color,bold)
            y+=step
        return y

    def box(self,box,fill=SURFACE,radius=16):
        self.draw.rounded_rectangle(box,radius,fill=fill)

    def line(self,x,y,right,color=LINE):
        self.draw.line((x,y,right,y),fill=color,width=1)

    def paste(self,image,x,y,width,height,contain=False,radius=0,trim=False):
        if image is None:
            self.box((x,y,x+width,y+height))
            self.text(x+width/2,y+height/2,'暂无图片',min(20,width//5),MUTED,align='center')
            return
        if trim and image.mode=='RGBA' and image.getbbox():image=image.crop(image.getbbox())
        fitted=(ImageOps.contain if contain else ImageOps.fit)(image,(width,height),Image.Resampling.LANCZOS)
        if radius:
            fitted=fitted.convert('RGBA')
            mask=Image.new('L',fitted.size)
            ImageDraw.Draw(mask).rounded_rectangle((0,0,fitted.width-1,fitted.height-1),radius,fill=255)
            fitted.putalpha(ImageChops.darker(mask,fitted.getchannel('A')))
        self.image.paste(fitted,(round(x+(width-fitted.width)/2),round(y+(height-fitted.height)/2)),
                         fitted if fitted.mode=='RGBA' else None)

    def badge(self,name,x,cy,width,height=None):
        image=icon(name,width,height or width)
        if image is None:
            label = name.removeprefix('RarityIconCenter_').removesuffix('.png') if name.startswith('Rarity') else '觉'+name.removesuffix('.png')[-1]
            self.text(x+width/2,cy,label,min(18,height or width),ACCENT,True,'center',stroke=0)
            return
        self.image.paste(image,(round(x),round(cy-image.height/2)),image)

    def header(self,title,subtitle):
        self.text(52,65,title,48,bold=True)
        self.box((self.width-154,51,self.width-52,91),SOFT,20)
        self.text(self.width-103,71,'Taki',22,'#3d417b',True,'center')
        return self.paragraph(54,102,subtitle,self.width-108,24,color=MUTED)+24
